# Production Deployment

ioref-web production runs on `ioref-web-01.andrew.cmu.edu` (RHEL 9), the
same host as ioref-inventory, as a rootless Podman container under the same
`deploy` account, deployed manually from GitHub Actions.

Shared host setup and deployment conventions live in
[`ioref-infrastructure`](https://github.com/ioref/ioref-infrastructure):

1. Complete [host setup](https://github.com/ioref/ioref-infrastructure/blob/main/docs/HOST_SETUP.md) for a new machine.
2. Review the [standard deployment model](https://github.com/ioref/ioref-infrastructure/blob/main/docs/DEPLOYMENT.md).

This document contains the concrete, application-specific procedure.

```text
Application image:               GHCR, ghcr.io/ioref/ioref-web, sha-GIT-SHA
Production configuration:        /etc/ioref-web/production.env
Persistent application data:     none
Deployment control:              GitHub Actions, runner labeled ioref-web-production
Container lifecycle:             a rootless Podman Quadlet
```

There is no database and no persistent volume: the guides are read-only
files baked into the image at build time by the Dockerfile's `COPY . .`, the
same instruction that puts the application code there. `collectstatic` is
not involved and must not be: it handles `static/` (CSS, JS, favicons) only,
and `public/`, where the guide images and videos live, is served by
WhiteNoise straight from the source tree. A deploy that replaces the
container replaces the content; there is no data migration step and never
will be one, short of adding a database, which is a design decision this
application deliberately does not make (see `config/settings/base.py`).

---

# 1. Production configuration

```bash
install -d -o root -g deploy -m 0750 /etc/ioref-web
install -o root -g deploy -m 0640 /dev/null /etc/ioref-web/production.env
```

Edit `/etc/ioref-web/production.env`, using `.env.example` as the reference.
Generate a fresh secret key the same way as for any other application on
this host:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(50))'
```

The full environment this application reads, and nothing else:

```dotenv
SECRET_KEY=GENERATED-VALUE
DEBUG=False
ALLOWED_HOSTS=ioref.org

# The only channel to the inventory application. Generate a READ-scoped key
# in the inventory admin under "API keys". This site must never hold a
# write-scoped key: nothing here writes stock.
INVENTORY_API_URL=http://inventory.ioref.org:8000
INVENTORY_API_KEY=READ-SCOPED-KEY-FROM-IOREF-INVENTORY-ADMIN
INVENTORY_API_TIMEOUT=3.0
INVENTORY_CACHE_SECONDS=120
```

`CONTENT_DIR` is deliberately not set here: it defaults to the checked-in
`content/` directory, which is what production should serve. There is no
`DATABASE_URL`, no session secret beyond `SECRET_KEY`, and no auth
configuration of any kind: this application has no sign-in.

---

# 2. Quadlet

Host setup creates the production network Quadlet. Confirm it exists before
installing this application:

```bash
test -f /home/deploy/.config/containers/systemd/ioref-production.network
```

```bash
touch /home/deploy/.config/containers/systemd/ioref-web-production.container
```

```ini
[Unit]
Description=ioref-web Production
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=ioref-web-production
Image=localhost/ioref-web:production
EnvironmentFile=/etc/ioref-web/production.env
Network=ioref-production.network
PublishPort=127.0.0.1:8989:8000
HealthCmd=python -c "import os,urllib.request,sys; host=os.environ.get('ALLOWED_HOSTS','').split(',')[0].strip() or '127.0.0.1'; req=urllib.request.Request('http://127.0.0.1:8000/', headers={'Host': host}); sys.exit(0 if urllib.request.urlopen(req).status==200 else 1)"
HealthInterval=30s
HealthTimeout=5s
HealthStartPeriod=10s
HealthRetries=3

[Service]
Restart=on-failure
TimeoutStartSec=300

[Install]
WantedBy=default.target
```

**The healthcheck is defined here, not read from the image.** The Dockerfile's own `HEALTHCHECK` instruction only takes effect for a local `docker build`; the build workflow pushes a plain OCI image to GHCR, and the OCI Image Spec has no field for Docker's `HEALTHCHECK` at all, so it's silently absent from every image this pipeline builds. `deploy.yml`'s `podman healthcheck run` has nothing to run without these lines.

**Port 8989, not 8000.** `02-ioref-web.conf`'s apex `/` vhost already
proxies to `127.0.0.1:8989`, matching this application's place in the
architecture per `CLAUDE.md` ("ioref-web...at `/`"). Not 8000:
ioref-inventory's container already publishes there.

No `Volume=` line: there is no directory this container needs to persist.

```bash
chown deploy:deploy \
  /home/deploy/.config/containers/systemd/ioref-web-production.container
chmod 0644 \
  /home/deploy/.config/containers/systemd/ioref-web-production.container

sudo -u deploy XDG_RUNTIME_DIR=/run/user/$(id -u deploy) \
  systemctl --user daemon-reload
```

Do not start the service yet; the local `localhost/ioref-web:production`
image is created by the first deployment.

---

# 3. Configure Apache

`02-ioref-web.conf` proxies the apex `/` to `127.0.0.1:8989` (see the shared
[Apache conventions](https://github.com/ioref/ioref-infrastructure/blob/main/docs/DEPLOYMENT.md#apache-reverse-proxy)),
and this container's Quadlet publishes to that same port (§2 above).

Stock reports read the reporter from the `Eppn` header, so the vhost needs
three things inside the `ioref.org` `<VirtualHost *:443>`:

```apache
RequestHeader unset Eppn early
# ...and Mail, Displayname, Remote-User, Persistent-Id, Subject-Id, Pairwise-Id

<LocationMatch "^/parts/[^/]+/(report/)?$">
  AuthType shibboleth
  ShibRequestSetting requireSession 0
  ShibUseHeaders On
  Require shibboleth
</LocationMatch>

ProxyPass /Shibboleth.sso !
```

The unset is what makes the header trustworthy: without it any client can
send `Eppn` and file reports under any name. It must be vhost-wide and keep
`early`, or mod_headers runs after mod_shib and strips the authentic value
instead. `requireSession 0` keeps the pages public. The `ProxyPass` exclusion
goes before the catch-all, or sign-in requests are proxied into this
application.

Because the vhost uses Shibboleth directives, its file must load after
`01-shib.conf`, and it must load before `03-ioref-inventory.conf` to stay the
default `*:443` block. It is installed as `conf.d/02-ioref-web.conf`.

`guides.ioref.org` redirects to the apex because no Shibboleth endpoint is
registered for that name, so the stock-report sign-in works only on
`ioref.org`.

### Verifying

```bash
apachectl configtest
systemctl reload httpd
curl -sSI https://ioref.org/
```

Should return `200` once the container is running.

---

# 4. Install the GitHub Actions runner

Follow the shared [runner installation guide](https://github.com/ioref/ioref-infrastructure/blob/main/docs/DEPLOYMENT.md#github-actions-runner) with:

```text
Repository:        ioref/ioref-web
Directory:          /opt/github-actions-runner-web
Label:               ioref-web-production
```

---

# 5. First deployment

1. Confirm the desired commit has passed **Build image**.
2. Confirm CI produced `ghcr.io/ioref/ioref-web:sha-COMMIT-SHA`.
3. In GitHub Actions, select **Deploy production**, choose the branch or
   commit, and click **Run workflow**.

Verify:

```bash
sudo -u deploy XDG_RUNTIME_DIR=/run/user/$(id -u deploy) \
  podman ps --filter name=ioref-web-production
```

should show the container `(healthy)`, and `curl -sSI https://ioref.org/`
should return `200`.

---

# Security notes specific to this application

See the infrastructure repository's shared security guidance. Specific to
ioref-web:

* **`INVENTORY_API_KEY` must be READ-scoped, never WRITE-scoped.** This
  application has no legitimate reason to write stock, and a write-scoped
  key here would mean a compromise of this application is a compromise of
  inventory data too. Generate the key with `read` scope specifically, in
  the ioref-inventory admin.
* Restrict `/etc/ioref-web/production.env` to root and the `deploy` group.
