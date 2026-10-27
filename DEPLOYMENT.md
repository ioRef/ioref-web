# Production Deployment

ioref-web production runs on **Red Hat Enterprise Linux 9**, on the same host
as ioref-inventory: `ioref-web-01.andrew.cmu.edu`. This document assumes
ioref-inventory's own `DEPLOYMENT.md` was followed first, and defers to it for
anything that isn't specific to this application: the reasoning behind the
shared pieces (the Quadlet approach, the runner proxy setup, why the deploy
workflow skips checkout and GHCR login) lives there, once, rather than twice.

The application runs as a rootless Podman container under the same `deploy`
account. Production configuration lives in `/etc/ioref-web/production.env`.
There is no database and no persistent volume: the guides are read-only files
baked into the image at build time, and the container itself needs no
writable state to run.

Deployments are initiated manually from GitHub Actions, over a self-hosted
runner on the production server, the same shape as ioref-inventory's but
registered separately: a runner is scoped to one repository, so two
applications on one host means two runner installations, not one shared.

## Deployment model

```text
                       GitHub
                         |
            +------------+------------+
            |                         |
       existing CI                GitHub Actions
    builds sha-* image           "Run workflow"
            |                         |
            v                         |
           GHCR                       |
            ^                         |
            | HTTPS through `proxy.andrew.cmu.edu:3128` |
            +-------------------------+
                         |
                  ioref-web-01
                         |
                 deploy user
                         |
        self-hosted runner (ioref-web-production)
                         |
                 rootless Podman
                         |
                    ioref-web
```

The important separation is the same as ioref-inventory's, minus the two
pieces this application does not have:

* **Application image:** built by GitHub CI and stored in GHCR with an
  immutable `sha-<git-sha>` tag.
* **Production configuration and secrets:** `/etc/ioref-web/production.env`.
* **Deployment control:** a repository-scoped GitHub Actions runner running as
  `deploy`, separate from ioref-inventory's runner.
* **Container lifecycle:** a rootless Podman Quadlet managed by the same
  `deploy` user's systemd instance ioref-inventory already uses.

No persistent volume, because there is nothing to persist: the guide content
is committed to the repository and baked into the image at build time by the
Dockerfile's `COPY . .`, the same instruction that puts the application code
there — `collectstatic` is not involved and must not be: it handles `static/`
(CSS, JS, favicons) only, and `public/`, where the guide images and videos
live, is served by WhiteNoise straight from the source tree. A deploy that
replaces the container replaces the content; there is no data migration step
and never will be one, short of adding a database, which is a design decision
this application deliberately does not make (see the repository's own
`config/settings/base.py`).

---

# One-time host setup

These steps are performed once when adding ioref-web to a host. On
`ioref-web-01`, most of this is already done for ioref-inventory and only
needs to be checked, not repeated. See ioref-inventory's `DEPLOYMENT.md`
sections 1–3 for what each of these actually sets up and why.

## 1. Required packages

Already installed if ioref-inventory's setup ran first: `podman`, `curl`,
`tar`. Nothing additional is required for this application.

## 2. The deploy account

Reuse the existing `deploy` account. Do not create a second one, and do not
re-run the subordinate UID/GID allocation: that range is assigned to the
Linux account, not to an individual application.

```bash
sudo -iu deploy podman info
```

## 3. The deploy user's systemd instance

Already enabled if ioref-inventory's setup ran first. Confirm:

```bash
loginctl show-user deploy | grep Linger
```

should show `Linger=yes`.

## 4. Create the production configuration file

```bash
install -d -o root -g deploy -m 0750 /etc/ioref-web
install -o root -g deploy -m 0640 /dev/null /etc/ioref-web/production.env
```

Edit `/etc/ioref-web/production.env` using the project's `.env.example` as the
reference. Generate a fresh secret key the same way as for ioref-inventory:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(50))'
```

The full environment this application reads, and nothing else:

```dotenv
SECRET_KEY=<generated-value>
DEBUG=False
ALLOWED_HOSTS=ioref.org

# The only channel to the inventory application. Generate a READ-scoped key
# in the inventory admin under "API keys". This site must never hold a
# write-scoped key: nothing here writes stock.
INVENTORY_API_URL=https://ioref.org/inventory
INVENTORY_API_KEY=<read-scoped-key-from-ioref-inventory-admin>
INVENTORY_API_TIMEOUT=3.0
INVENTORY_CACHE_SECONDS=120
```

`CONTENT_DIR` is deliberately not set here: it defaults to the checked-in
`content/` directory, which is what production should serve. There is no
`DATABASE_URL`, no session secret beyond `SECRET_KEY`, and no auth
configuration of any kind: this application has no sign-in.

## 5. Configure the rootless Podman Quadlet

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
PublishPort=127.0.0.1:8989:8000

[Service]
Restart=on-failure
TimeoutStartSec=300

[Install]
WantedBy=default.target
```

**Port 8989, not 8000.** `02-ioref.org.conf`'s apex `/` vhost already proxies
to `127.0.0.1:8989` (see ioref-inventory's `DEPLOYMENT.md` §6), matching this
application's place in the architecture per CLAUDE.md ("ioref-web...at `/`").
Not 8000: ioref-inventory's container already publishes there.

No `Volume=` line: there is no directory this container needs to persist.

```bash
chown deploy:deploy \
  /home/deploy/.config/containers/systemd/ioref-web-production.container
chmod 0644 \
  /home/deploy/.config/containers/systemd/ioref-web-production.container
```

Do not start the service yet; the local `localhost/ioref-web:production`
image is created by the first deployment, the same as ioref-inventory's.

## 6. Configure Apache

`02-ioref.org.conf` (see ioref-inventory's `DEPLOYMENT.md` §6) already
proxies the apex `/` to `127.0.0.1:8989`, and this container's Quadlet
publishes to that same port (§5 above).

Stock reports read the reporter from the `Eppn` header, so part pages need a
lazy Shibboleth session. Inside the apex `VirtualHost`, alongside the
`/inventory` blocks:

```apache
<LocationMatch "^/parts/[^/]+/(report/)?$">
  AuthType shibboleth
  ShibRequestSetting requireSession 0
  ShibUseHeaders On
  Require shibboleth
</LocationMatch>
```

`requireSession 0` keeps the pages public. The vhost's
`RequestHeader unset Eppn early` is what makes the header trustworthy, and it
must stay vhost-wide, not inside an `/inventory` block.

`guides.ioref.org` keeps redirecting to the apex, unchanged: that redirect
exists because no Shibboleth endpoint is registered for that name, which
matters for `/inventory`'s login, so it stays as documented in
ioref-inventory's `DEPLOYMENT.md` §6.

### Verifying

```bash
apachectl configtest
systemctl reload httpd
curl -sSI https://ioref.org/
```

Should return `200` once the container is running (see "First deployment"
below).

## 7. Install a second GitHub Actions runner

A runner registers to exactly one repository, so ioref-inventory's existing
runner cannot also serve ioref-web: this needs its own installation. See
ioref-inventory's `DEPLOYMENT.md` §7 for why each step below matters (the
labels prompt in particular: skipping it produces a runner `deploy.yml` can
never target, with no error to explain why).

```bash
mkdir -p /opt/github-actions-runner-web
chown deploy:deploy /opt/github-actions-runner-web
sudo -iu deploy
cd /opt/github-actions-runner-web
export HTTP_PROXY=http://proxy.andrew.cmu.edu:3128
export HTTPS_PROXY=http://proxy.andrew.cmu.edu:3128
export NO_PROXY=.cmu.edu,.cmu.local,localhost,127.0.0.1
```

In GitHub: open **ioref/ioref-web** → **Settings** → **Actions > Runners** →
**New self-hosted runner** → choose Linux and the server's architecture. Use
the download and extraction commands shown there, into
`/opt/github-actions-runner-web`.

```bash
./config.sh \
  --url https://github.com/ioref/ioref-web \
  --token <TIME-LIMITED-TOKEN>
```

Press Enter for the runner group and name defaults. **At the labels prompt,
type `ioref-web-production`** and confirm `√ Runner successfully added`.
Press Enter for the work folder prompt.

```bash
cat > /opt/github-actions-runner-web/.env <<'EOF'
http_proxy=http://proxy.andrew.cmu.edu:3128
https_proxy=http://proxy.andrew.cmu.edu:3128
no_proxy=.cmu.edu,.cmu.local,localhost,127.0.0.1
EOF
chown deploy:deploy /opt/github-actions-runner-web/.env
chmod 0644 /opt/github-actions-runner-web/.env

sudo ./svc.sh install deploy
sudo ./svc.sh start
sudo ./svc.sh status
```

**GitHub's generated systemd unit name is derived from the runner's
configured name, not from the directory it runs from**, so verify it does not
collide with ioref-inventory's runner service before starting:

```bash
systemctl list-units 'actions.runner.*' --all
```

Two distinct unit names should be listed once both runners exist. In GitHub,
**Settings > Actions > Runners** should show it as **Idle** with the
`ioref-web-production` label.

---

# Repository setup

`.github/workflows/build.yml` and `.github/workflows/deploy.yml` are already
in this repository, matching ioref-inventory's pair: the same test-then-image
pipeline, the same manual `workflow_dispatch` deploy with a `sha` rollback
input, health-check gating, and automatic rollback on a failed deploy. Only
the names differ (`ghcr.io/ioref/ioref-web`, `ioref-web-production` labels
and unit).

Use the `production` GitHub Environment to record production deployments and,
if desired, require approval before deployment.

The GHCR package (`ioref/ioref-web`) needs to be set public after the first
push, the same as `ioref/ioref-inventory` was, or `deploy.yml`'s unauthenticated
`podman pull` will fail. **Settings > Packages** in GitHub, or on the package's
own page under **Package settings > Change visibility**.

---

# First deployment

1. Confirm the desired commit has passed **Build image**.
2. Confirm CI produced `ghcr.io/ioref/ioref-web:sha-<commit-sha>`.
3. In GitHub Actions, select **Deploy production**, choose the branch or
   commit, and click **Run workflow**.

The runner pulls the image, tags it `localhost/ioref-web:production`,
restarts the Quadlet, and waits on the container's own healthcheck before
reporting success.

Verify:

```bash
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
podman ps --filter name=ioref-web-production
```

should show the container `(healthy)`, and `curl -sSI https://ioref.org/`
should return `200`.

---

# Deploying updates

Same procedure as first deployment: merge to `main`, confirm **Build image**
succeeded, run **Deploy production**. The workflow itself waits on the
healthcheck and rolls back automatically if the new image never turns
healthy, so a green run is already a verified deploy.

---

# Rollback

Same mechanism as ioref-inventory's — see its `DEPLOYMENT.md` "Rollback"
section for the full reasoning and the emergency manual procedure. Substitute
these values:

| | |
|---|---|
| Image | `ghcr.io/ioref/ioref-web:sha-<KNOWN-GOOD-SHA>` |
| Local tag | `localhost/ioref-web:production` |
| Unit | `ioref-web-production.service` |

For a deliberate rollback, re-run **Deploy production** with the known-good
commit SHA in the optional `sha` input.

---

# Changing production configuration

Edit `/etc/ioref-web/production.env` as root, then:

```bash
sudo -u deploy \
  XDG_RUNTIME_DIR="/run/user/$(id -u deploy)" \
  systemctl --user restart ioref-web-production.service
```

Keep `.env.example` in sync with supported settings. Never commit production
secret values.

---

# Runner maintenance

Same as ioref-inventory's runner (see its `DEPLOYMENT.md`); it normally
updates itself automatically.

```bash
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
sudo -iu deploy sudo /opt/github-actions-runner-web/svc.sh status
```

If it stops appearing as **Idle** in **Settings > Actions > Runners**, check
its service and, if needed, re-register it following section 7 above.

---

# Security notes

The self-hosted runner executes repository workflow commands as `deploy`.
Treat changes to this repository's deployment workflow as production access,
the same as ioref-inventory's — see its `DEPLOYMENT.md` "Security notes" for
the shared list (runner scoping, no personal account, no root, no
unrestricted `sudo`, immutable `sha-*` artifacts, no inbound SSH). Specific to
this application:

* **`INVENTORY_API_KEY` must be READ-scoped, never WRITE-scoped.** This
  application has no legitimate reason to write stock, and a write-scoped
  key here would mean a compromise of this application is a compromise of
  inventory data too. Generate the key with `read` scope specifically, in
  the ioref-inventory admin.
* Restrict `/etc/ioref-web/production.env` to root and the `deploy` group.

---

# References

* ioref-inventory's `DEPLOYMENT.md`: the pattern this document mirrors, the
  reasoning behind the shared pieces, and the full Apache vhost this app's
  apex block lives inside.
* GitHub: Adding self-hosted runners
  https://docs.github.com/actions/hosting-your-own-runners/adding-self-hosted-runners
* GitHub: Configuring a self-hosted runner as a service
  https://docs.github.com/actions/hosting-your-own-runners/managing-self-hosted-runners/configuring-the-self-hosted-runner-application-as-a-service
* GitHub: Using a proxy server with a self-hosted runner
  https://docs.github.com/actions/how-tos/manage-runners/use-proxy-servers
* Podman: Quadlet/systemd units
  https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html
