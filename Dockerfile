# syntax=docker/dockerfile:1

# Follows ioref-inventory's Dockerfile, minus everything to do with a database.
# The previous file here was the stock Wagtail scaffold and had never been run:
# it installed from a requirements.txt this project does not have, on Python
# 3.12 against a project that requires 3.13, and ran `migrate` at startup.

# Pinned, and given its own stage rather than an inline `COPY --from=ghcr.io/...`:
# Dependabot's docker ecosystem reads FROM lines, so this is what makes the uv
# version something it can raise a pull request against.
FROM ghcr.io/astral-sh/uv:0.12.21 AS uv

FROM python:3.14-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies resolve from the lockfile in their own layer, so application
# edits do not force a reinstall on every build.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# collectstatic needs *a* key but not the real one; the runtime value arrives
# through the environment. This handles css/js only: the guide media lives in
# public/, which WhiteNoise serves straight from the source directory at the
# root of the URL space, and which collectstatic must not touch.
RUN SECRET_KEY=build-only python manage.py collectstatic --noinput

# No writable data directory: the guides are read-only files baked into the
# image and there is no database to keep anywhere.
RUN useradd --system --create-home --uid 1001 ioref
USER ioref

EXPOSE 8000

# The home page renders the whole category taxonomy from content/, so a 200
# here means the markdown parsed. It makes no call to inventory.
#
# Sends Host: <first entry of ALLOWED_HOSTS> rather than the connection's own
# 127.0.0.1:8000, because Django's ALLOWED_HOSTS check runs on the Host header
# regardless of where the connection actually came from; a production
# ALLOWED_HOSTS otherwise 400s every single probe, forever, which urlopen
# raises as an uncaught exception. Falls back to 127.0.0.1 for a bare local
# run with nothing configured (see config/settings/dev.py's ALLOWED_HOSTS).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; host=os.environ.get('ALLOWED_HOSTS','').split(',')[0].strip() or '127.0.0.1'; req=urllib.request.Request('http://127.0.0.1:8000/', headers={'Host': host}); sys.exit(0 if urllib.request.urlopen(req).status==200 else 1)"

# Four workers rather than inventory's two: there is no SQLite writer lock to
# contend for here, and each worker parses content/ once at startup into its
# own memory.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "60"]
