# DOC-08 — Konnaxion Runtime Docker Compose

```yaml
doc_id: DOC-08
filename: DOC-08_Konnaxion_Runtime_Docker_Compose.md
project: Konnaxion
app_version: v14
param_version: kx-param-2026.04.30
status: draft
depends_on:
  - DOC-00_Konnaxion_Canonical_Variables.md
  - DOC-03_Konnaxion_Capsule_Format.md
  - DOC-04_Konnaxion_Manager_Architecture.md
  - DOC-05_Konnaxion_Agent_Security_Model.md
  - DOC-06_Konnaxion_Network_Profiles.md
  - DOC-07_Konnaxion_Security_Gate.md
```

## 1. Purpose

This document defines the canonical **Docker Compose runtime** for a `Konnaxion Instance`.

It describes:

```text
- canonical Docker services
- container names
- networks
- volumes
- ports
- healthchecks
- runtime environment variables
- startup order
- security constraints
- compose profiles
- operational commands
```

This document does **not** define the capsule format itself. The capsule format is defined in `DOC-03_Konnaxion_Capsule_Format.md`.

This document does **not** define the graphical manager. The manager is defined in `DOC-04_Konnaxion_Manager_Architecture.md`.

---

## 2. Runtime decision

The target runtime for a Konnaxion Instance is:

```text
Docker Compose
```

Konnaxion v14 already uses a Docker-compatible production stack around Django, PostgreSQL, Redis, Celery, Traefik, Flower, and Nginx/media. The legacy VPS kept the frontend as a separate Node/pnpm host service on port `3000`.

For the capsule/appliance model, the frontend must be containerized.

Canonical target:

```text
Traefik
  ├── frontend-next
  ├── django-api
  └── media-nginx

Internal services
  ├── postgres
  ├── redis
  ├── celeryworker
  ├── celerybeat
  └── flower, optional/private only
```

Kubernetes is out of scope for the plug-and-play runtime.

The runtime must not require:

```text
- public Docker socket access
- Docker provider labels in Traefik
- host-level frontend systemd service
- public port 3000
- public port 5000
- runtime pnpm/Corepack download
```

---

## 3. Runtime goals

The Docker Compose runtime must satisfy these goals:

```text
1. Start Konnaxion with one controlled command.
2. Keep all internal services off the public network.
3. Expose only Traefik.
4. Support local, intranet, tunnel, and VPS profiles.
5. Generate secrets at install time, not capsule build time.
6. Preserve instance data outside the capsule.
7. Support backup, restore, update, and rollback.
8. Be readable and debuggable by an operator.
9. Be enforceable by Konnaxion Agent.
10. Work with a private Droplet Agent reachable only through SSH-local curl.
11. Use Traefik file-provider dynamic config, not Docker socket labels.
12. Persist public host changes durably when `/network/set-profile` is called.
13. Support custom public domains and aliases without manual runtime edits.
```

---

## 4. Canonical service names

All Compose files must use these service names.

| Service              | Canonical name  | Required | Public port allowed |
| -------------------- | --------------- | -------: | ------------------: |
| Reverse proxy        | `traefik`       |      yes |       yes, `80/443` |
| Frontend             | `frontend-next` |      yes |                  no |
| Backend API          | `django-api`    |      yes |                  no |
| Database             | `postgres`      |      yes |                  no |
| Redis broker         | `redis`         |      yes |                  no |
| Celery worker        | `celeryworker`  |      yes |                  no |
| Celery beat          | `celerybeat`    |      yes |                  no |
| Media/static service | `media-nginx`   |      yes |                  no |
| Celery dashboard     | `flower`        | optional |                  no |
| Runtime init job     | `kx-init`       | optional |                  no |
| Migration job        | `kx-migrate`    | optional |                  no |

Do not use alternate names such as:

```text
backend
api
web
next
frontend
db
cache
worker
beat
nginx
```

unless they are aliases inside comments only.

---

## 5. Canonical routing

Traefik is the only HTTP entrypoint.

```text
https://<KX_HOST>/          -> frontend-next
https://<KX_HOST>/api/      -> django-api
https://<KX_HOST>/admin/    -> django-api
https://<KX_HOST>/media/    -> media-nginx
```

Routing is rendered by the Agent into a Traefik file-provider dynamic config.

The runtime must not depend on Docker labels for routing, because the Docker socket is not mounted into Traefik.

Canonical rendered file:

```text
/opt/konnaxion/instances/<INSTANCE_ID>/state/traefik-dynamic.yml
```

Mounted inside Traefik as:

```text
/etc/traefik/dynamic/traefik-dynamic.yml
```

Traefik static args must include:

```text
--providers.file.filename=/etc/traefik/dynamic/traefik-dynamic.yml
--providers.file.watch=true
--entrypoints.web.address=:80
--entrypoints.websecure.address=:443
--entrypoints.web.http.redirections.entrypoint.to=websecure
--entrypoints.web.http.redirections.entrypoint.scheme=https
--api.dashboard=false
```

### 5.1 Host rule contract

For every HTTP router, the Agent must render a host rule from:

```text
KX_HOST
KX_HOST_ALIASES
```

`KX_HOST` is the canonical runtime host.

`KX_HOST_ALIASES` is optional and contains comma-separated additional public hostnames that should route to the same instance.

Example:

```env
KX_HOST=konnxion.com
KX_HOST_ALIASES=www.konnxion.com,138.197.174.76.sslip.io
```

The Agent must render an equivalent Traefik rule:

```text
Host(`konnxion.com`) || Host(`www.konnxion.com`) || Host(`138.197.174.76.sslip.io`)
```

All routers must use the same host rule.

The Agent must never render a public VPS router with only an old fallback hostname when a custom domain has been selected by the Manager.

---

## 6. Canonical ports

### 6.1 Allowed published ports

|            Port | Service   | Profiles                                                               |
| --------------: | --------- | ---------------------------------------------------------------------- |
|           `443` | `traefik` | `intranet_private`, `private_tunnel`, `public_temporary`, `public_vps` |
|            `80` | `traefik` | optional redirect, mostly `public_vps`                                 |
| `127.0.0.1:443` | `traefik` | `local_only`                                                           |
|  `127.0.0.1:80` | `traefik` | optional local redirect                                                |

### 6.2 Forbidden published ports

The following ports must never be published directly:

|       Port | Service                 | Rule                       |
| ---------: | ----------------------- | -------------------------- |
|     `3000` | `frontend-next`         | internal only              |
|     `5000` | `django-api` / Gunicorn | internal only              |
|     `5555` | `flower`                | private only, never public |
|     `5432` | `postgres`              | internal only              |
|     `6379` | `redis`                 | internal only              |
|     `8000` | Django dev server       | forbidden in runtime       |
| Docker TCP | Docker daemon           | forbidden                  |

Only Traefik may publish host ports.

---

## 7. Network model

The runtime uses internal Docker networks.

Canonical logical networks:

```text
kx-public
  - Traefik
  - receives published ports from host

kx-private
  - Traefik
  - frontend-next
  - django-api
  - media-nginx
  - postgres
  - redis
  - celeryworker
  - celerybeat
  - flower

kx-data
  - django-api
  - postgres
  - redis
  - celeryworker
  - celerybeat
```

Traefik attaches to:

```text
kx-public
kx-private
```

Application containers attach to:

```text
kx-private
```

Stateful/backend containers may also attach to:

```text
kx-data
```

No container except `traefik` may publish ports.

---

## 8. Volume model

Instance data must live outside the capsule.

Canonical persistent paths:

```text
/opt/konnaxion/instances/<INSTANCE_ID>/
├── env/
├── state/
├── logs/
├── data/
├── backups/
└── runtime/
```

Canonical Docker volumes or bind mounts:

| Volume/path           | Purpose           |        Persistent |
| --------------------- | ----------------- | ----------------: |
| `kx_postgres_data`    | PostgreSQL data   |               yes |
| `kx_postgres_backups` | PostgreSQL dumps  |               yes |
| `kx_redis_data`       | Redis persistence |               yes |
| `kx_django_media`     | Uploaded media    |               yes |
| `kx_traefik_acme`     | TLS certificates  | profile-dependent |
| `kx_logs`             | Runtime logs      |               yes |
| `kx_state`            | Instance state    |               yes |

The capsule must not contain live secrets, database files, Redis state, uploaded media, or runtime logs.

---

## 9. Runtime file model

Runtime env files live in the instance directory, not inside the capsule.

Canonical location:

```text
/opt/konnaxion/instances/<INSTANCE_ID>/env/
├── kx.env
├── django.env
├── postgres.env
├── redis.env
├── frontend.env
└── traefik.env
```

Rendered runtime compose location:

```text
/opt/konnaxion/instances/<INSTANCE_ID>/state/docker-compose.runtime.yml
```

Rendered Traefik file-provider config location:

```text
/opt/konnaxion/instances/<INSTANCE_ID>/state/traefik-dynamic.yml
```

The capsule may contain templates, but never real secrets.

Host-derived runtime values are mutable instance state. When `KX_HOST` or `KX_HOST_ALIASES` changes, the Agent must regenerate the relevant env files and Traefik dynamic config while preserving generated secrets.

---

## 10. Required runtime variables

### 10.1 `kx.env`

```env
KX_INSTANCE_ID=demo-001
KX_CAPSULE_ID=konnaxion-v14-demo-2026.04.30
KX_CAPSULE_VERSION=2026.04.30-demo.1
KX_APP_VERSION=v14
KX_PARAM_VERSION=kx-param-2026.04.30

KX_NETWORK_PROFILE=public_vps
KX_EXPOSURE_MODE=public
KX_PUBLIC_MODE_ENABLED=true
KX_PUBLIC_MODE_EXPIRES_AT=

KX_HOST=konnxion.com
KX_HOST_ALIASES=www.konnxion.com,138.197.174.76.sslip.io

KX_REQUIRE_SIGNED_CAPSULE=true
KX_GENERATE_SECRETS_ON_INSTALL=true
KX_ALLOW_UNKNOWN_IMAGES=false
KX_ALLOW_PRIVILEGED_CONTAINERS=false
KX_ALLOW_DOCKER_SOCKET_MOUNT=false
KX_ALLOW_HOST_NETWORK=false
KX_BACKUP_ENABLED=true
```

`KX_HOST` must be generated from the selected network profile and Manager payload.

For `public_vps`, `KX_HOST` must be the canonical public runtime hostname selected by the operator:

```text
domain or public_host or droplet_domain or sslip hostname or droplet_host fallback
```

The preference order must choose operator-facing public domain fields before the Droplet IP:

```text
domain
droplet_domain
public_host
public_url
url
host
kx_host
KX_HOST
droplet_host
target_host
```

It must never silently fall back to `127.0.0.1` for `public_vps`.

`KX_HOST_ALIASES` should include useful alternate public names, such as:

```text
www.<domain>
<droplet_ip>.sslip.io
```

when available.

`KX_HOST_ALIASES` must not include empty values, duplicate values, paths, schemes, or userinfo.

### 10.2 `django.env`

```env
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SECRET_KEY=<GENERATED_ON_INSTALL>
DJANGO_DEBUG=False

DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost,<KX_HOST>,<KX_HOST_ALIASES>,django-api,kx-<INSTANCE_ID>-django-api
DJANGO_CSRF_TRUSTED_ORIGINS=https://<KX_HOST>,http://<KX_HOST>,https://<KX_HOST_ALIAS>,http://<KX_HOST_ALIAS>
CSRF_TRUSTED_ORIGINS=https://<KX_HOST>,http://<KX_HOST>,https://<KX_HOST_ALIAS>,http://<KX_HOST_ALIAS>

USE_DOCKER=yes

DATABASE_URL=postgres://konnaxion:<POSTGRES_PASSWORD>@postgres:5432/konnaxion
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0

DJANGO_ADMIN_URL=admin/
SENTRY_DSN=
```

`DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`, and `CORS_ALLOWED_ORIGINS` must be regenerated when `KX_HOST` or `KX_HOST_ALIASES` changes.

They must include the public VPS hostname for `public_vps`.

They should include all public aliases rendered into Traefik Host rules.

### 10.3 `postgres.env`

```env
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=konnaxion
POSTGRES_USER=konnaxion
POSTGRES_PASSWORD=<GENERATED_ON_INSTALL>
```

### 10.4 `redis.env`

```env
REDIS_URL=redis://redis:6379/0
REDIS_APPENDONLY=yes
```

### 10.5 `frontend.env`

```env
NODE_ENV=production
NEXT_TELEMETRY_DISABLED=1
NODE_OPTIONS=--max-old-space-size=4096

NEXT_PUBLIC_API_BASE=https://<KX_HOST>/api
NEXT_PUBLIC_BACKEND_BASE=https://<KX_HOST>
```

The frontend must use the canonical `KX_HOST`, not an arbitrary alias.

The frontend image must not require runtime network access to download `pnpm`, Corepack packages, or build dependencies.

---

## 11. Frontend image runtime contract

The frontend image must be production-runnable without `pnpm` or Corepack at runtime.

The Builder-generated or canonical frontend image must include:

```text
/app/package.json
/app/node_modules
/app/.next
/app/public
/app/next.config.*
/app/env.mjs
```

The runtime command must be:

```text
node node_modules/next/dist/bin/next start -H 0.0.0.0 -p 3000
```

The runtime command must not be:

```text
pnpm start
corepack pnpm start
npm install
pnpm install
pnpm build
next dev
```

Build-time frontend generation may use:

```env
NODE_OPTIONS=--max-old-space-size=4096
```

The frontend build context must exclude:

```text
node_modules
.next
out
dist
coverage
reports
test-results
playwright-report
.cache
.turbo
.vercel
.git
storageState.json
*.log
Dockerfile.capsule
```

---

## 12. Canonical Compose file

Capsule file name:

```text
docker-compose.capsule.yml
```

Rendered runtime location:

```text
/opt/konnaxion/instances/<INSTANCE_ID>/state/docker-compose.runtime.yml
```

Reference Compose:

```yaml
services:
  traefik:
    image: ${KX_IMAGE_TRAEFIK:-traefik:v3.1}
    container_name: kx-${KX_INSTANCE_ID}-traefik
    restart: unless-stopped
    command:
      - --providers.file.filename=/etc/traefik/dynamic/traefik-dynamic.yml
      - --providers.file.watch=true
      - --entrypoints.web.address=:80
      - --entrypoints.websecure.address=:443
      - --entrypoints.web.http.redirections.entrypoint.to=websecure
      - --entrypoints.web.http.redirections.entrypoint.scheme=https
      - --api.dashboard=false
    env_file:
      - ../env/kx.env
      - ../env/traefik.env
    ports:
      - "${KX_BIND_HTTP:-0.0.0.0:80}:80"
      - "${KX_BIND_HTTPS:-0.0.0.0:443}:443"
    volumes:
      - ./traefik-dynamic.yml:/etc/traefik/dynamic/traefik-dynamic.yml:ro
      - ../logs/traefik:/var/log/traefik
    networks:
      - kx-public
      - kx-private
    security_opt:
      - no-new-privileges:true
    read_only: false
    privileged: false
    healthcheck:
      test: ["CMD", "traefik", "healthcheck", "--ping"]
      interval: 30s
      timeout: 5s
      retries: 5

  frontend-next:
    image: ${KX_IMAGE_FRONTEND:-konnaxion/frontend-next:v14}
    container_name: kx-${KX_INSTANCE_ID}-frontend-next
    restart: unless-stopped
    env_file:
      - ../env/kx.env
      - ../env/frontend.env
    expose:
      - "3000"
    networks:
      - kx-private
    security_opt:
      - no-new-privileges:true
    read_only: false
    privileged: false
    pull_policy: never
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "node -e \"require('http').get('http://127.0.0.1:3000', r => { process.exit(r.statusCode < 500 ? 0 : 1) }).on('error', () => process.exit(1))\""
        ]
      interval: 30s
      timeout: 5s
      retries: 10

  django-api:
    image: ${KX_IMAGE_BACKEND:-konnaxion/django-api:v14}
    container_name: kx-${KX_INSTANCE_ID}-django-api
    restart: unless-stopped
    command: /start
    env_file:
      - ../env/kx.env
      - ../env/django.env
      - ../env/postgres.env
      - ../env/redis.env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    expose:
      - "5000"
    volumes:
      - kx_django_media:/app/konnaxion/media
      - kx_logs:/app/logs
    networks:
      - kx-private
      - kx-data
    security_opt:
      - no-new-privileges:true
    read_only: false
    privileged: false
    pull_policy: never
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "python -c \"import socket; sock=socket.create_connection(('127.0.0.1',5000),5); sock.close()\""
        ]
      interval: 30s
      timeout: 5s
      retries: 10

  media-nginx:
    image: ${KX_IMAGE_MEDIA_NGINX:-nginx:stable}
    container_name: kx-${KX_INSTANCE_ID}-media-nginx
    restart: unless-stopped
    depends_on:
      - django-api
    expose:
      - "80"
    volumes:
      - kx_django_media:/usr/share/nginx/media:ro
    networks:
      - kx-private
    security_opt:
      - no-new-privileges:true
    read_only: false
    privileged: false
    healthcheck:
      test: ["CMD-SHELL", "nginx -t >/dev/null 2>&1 || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 10

  postgres:
    image: ${KX_IMAGE_POSTGRES:-postgres:16}
    container_name: kx-${KX_INSTANCE_ID}-postgres
    restart: unless-stopped
    env_file:
      - ../env/postgres.env
    volumes:
      - kx_postgres_data:/var/lib/postgresql/data
      - kx_postgres_backups:/backups
    expose:
      - "5432"
    networks:
      - kx-data
    security_opt:
      - no-new-privileges:true
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 30s
      timeout: 5s
      retries: 10

  redis:
    image: ${KX_IMAGE_REDIS:-redis:7}
    container_name: kx-${KX_INSTANCE_ID}-redis
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - kx_redis_data:/data
    expose:
      - "6379"
    networks:
      - kx-data
      - kx-private
    security_opt:
      - no-new-privileges:true
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 30s
      timeout: 5s
      retries: 10

  celeryworker:
    image: ${KX_IMAGE_BACKEND:-konnaxion/django-api:v14}
    container_name: kx-${KX_INSTANCE_ID}-celeryworker
    restart: unless-stopped
    command: /start-celeryworker
    env_file:
      - ../env/kx.env
      - ../env/django.env
      - ../env/postgres.env
      - ../env/redis.env
    depends_on:
      django-api:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - kx_django_media:/app/konnaxion/media
      - kx_logs:/app/logs
    networks:
      - kx-private
      - kx-data
    security_opt:
      - no-new-privileges:true
    pull_policy: never

  celerybeat:
    image: ${KX_IMAGE_BACKEND:-konnaxion/django-api:v14}
    container_name: kx-${KX_INSTANCE_ID}-celerybeat
    restart: unless-stopped
    command: /start-celerybeat
    env_file:
      - ../env/kx.env
      - ../env/django.env
      - ../env/postgres.env
      - ../env/redis.env
    depends_on:
      django-api:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - kx_logs:/app/logs
    networks:
      - kx-private
      - kx-data
    security_opt:
      - no-new-privileges:true
    pull_policy: never

  flower:
    image: ${KX_IMAGE_BACKEND:-konnaxion/django-api:v14}
    container_name: kx-${KX_INSTANCE_ID}-flower
    restart: unless-stopped
    command: /start-flower
    profiles:
      - observability
    env_file:
      - ../env/kx.env
      - ../env/django.env
      - ../env/postgres.env
      - ../env/redis.env
    depends_on:
      redis:
        condition: service_healthy
    expose:
      - "5555"
    networks:
      - kx-private
      - kx-data
    security_opt:
      - no-new-privileges:true
    pull_policy: never

  kx-migrate:
    image: ${KX_IMAGE_BACKEND:-konnaxion/django-api:v14}
    container_name: kx-${KX_INSTANCE_ID}-kx-migrate
    profiles:
      - jobs
    command: python manage.py migrate
    env_file:
      - ../env/kx.env
      - ../env/django.env
      - ../env/postgres.env
      - ../env/redis.env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    networks:
      - kx-private
      - kx-data
    security_opt:
      - no-new-privileges:true
    pull_policy: never

volumes:
  kx_postgres_data:
  kx_postgres_backups:
  kx_redis_data:
  kx_django_media:
  kx_traefik_acme:
  kx_logs:
  kx_state:

networks:
  kx-public:
    name: kx-${KX_INSTANCE_ID}-public
  kx-private:
    name: kx-${KX_INSTANCE_ID}-private
  kx-data:
    name: kx-${KX_INSTANCE_ID}-data
```

---

## 13. Traefik dynamic file

File name:

```text
traefik-dynamic.yml
```

Rendered location:

```text
/opt/konnaxion/instances/<INSTANCE_ID>/state/traefik-dynamic.yml
```

Mounted location:

```text
/etc/traefik/dynamic/traefik-dynamic.yml
```

Reference:

```yaml
http:
  routers:
    kx-frontend:
      rule: "(<KX_TRAEFIK_HOST_RULE>) && PathPrefix(`/`)"
      entryPoints:
        - websecure
      tls: {}
      service: kx-frontend
      priority: 1

    kx-api:
      rule: "(<KX_TRAEFIK_HOST_RULE>) && PathPrefix(`/api/`)"
      entryPoints:
        - websecure
      tls: {}
      service: kx-api
      priority: 100

    kx-admin:
      rule: "(<KX_TRAEFIK_HOST_RULE>) && PathPrefix(`/admin/`)"
      entryPoints:
        - websecure
      tls: {}
      service: kx-api
      priority: 100

    kx-media:
      rule: "(<KX_TRAEFIK_HOST_RULE>) && PathPrefix(`/media/`)"
      entryPoints:
        - websecure
      tls: {}
      service: kx-media
      priority: 100

  services:
    kx-frontend:
      loadBalancer:
        servers:
          - url: "http://kx-<INSTANCE_ID>-frontend-next:3000"

    kx-api:
      loadBalancer:
        servers:
          - url: "http://kx-<INSTANCE_ID>-django-api:5000"

    kx-media:
      loadBalancer:
        servers:
          - url: "http://kx-<INSTANCE_ID>-media-nginx:80"
```

`<KX_TRAEFIK_HOST_RULE>` is rendered from `KX_HOST` plus optional `KX_HOST_ALIASES`.

Example rendered rule:

```text
(Host(`konnxion.com`) || Host(`www.konnxion.com`) || Host(`138.197.174.76.sslip.io`)) && PathPrefix(`/api/`)
```

The Agent must render the same host rule for all routers.

The Agent must render `<KX_HOST>` and aliases from the instance env/profile state, not from stale generated files.

For `public_vps`, the host must be the public DNS name or sslip.io hostname selected by the Manager.

For `.local` or intranet hostnames, do not configure public Let's Encrypt.

---

## 14. Network profile bindings

The Agent must render `KX_BIND_HTTP`, `KX_BIND_HTTPS`, `KX_HOST`, `KX_HOST_ALIASES`, and exposure fields based on `KX_NETWORK_PROFILE`.

### 14.1 `local_only`

```env
KX_NETWORK_PROFILE=local_only
KX_HOST=127.0.0.1
KX_HOST_ALIASES=localhost
KX_BIND_HTTP=127.0.0.1:80
KX_BIND_HTTPS=127.0.0.1:443
KX_EXPOSURE_MODE=private
KX_PUBLIC_MODE_ENABLED=false
```

### 14.2 `intranet_private`

```env
KX_NETWORK_PROFILE=intranet_private
KX_HOST=<LAN_HOST_OR_DNS>
KX_HOST_ALIASES=
KX_BIND_HTTP=0.0.0.0:80
KX_BIND_HTTPS=0.0.0.0:443
KX_EXPOSURE_MODE=lan
KX_PUBLIC_MODE_ENABLED=false
```

Firewall must restrict exposure to LAN/private ranges.

### 14.3 `private_tunnel`

```env
KX_NETWORK_PROFILE=private_tunnel
KX_HOST=<TUNNEL_PRIVATE_HOST>
KX_HOST_ALIASES=
KX_BIND_HTTP=127.0.0.1:80
KX_BIND_HTTPS=127.0.0.1:443
KX_EXPOSURE_MODE=vpn
KX_PUBLIC_MODE_ENABLED=false
```

The tunnel agent exposes the service; Docker does not publish public ports.

### 14.4 `public_temporary`

```env
KX_NETWORK_PROFILE=public_temporary
KX_HOST=<TEMPORARY_PUBLIC_HOST>
KX_HOST_ALIASES=
KX_BIND_HTTP=127.0.0.1:80
KX_BIND_HTTPS=127.0.0.1:443
KX_EXPOSURE_MODE=temporary_tunnel
KX_PUBLIC_MODE_ENABLED=true
KX_PUBLIC_MODE_EXPIRES_AT=<REQUIRED>
```

Expiration is mandatory.

### 14.5 `public_vps`

```env
KX_NETWORK_PROFILE=public_vps
KX_HOST=<PUBLIC_DNS_OR_SSLIP_HOST>
KX_HOST_ALIASES=<OPTIONAL_PUBLIC_HOST_ALIASES>
KX_BIND_HTTP=0.0.0.0:80
KX_BIND_HTTPS=0.0.0.0:443
KX_EXPOSURE_MODE=public
KX_PUBLIC_MODE_ENABLED=true
KX_PUBLIC_MODE_EXPIRES_AT=
```

Firewall must allow only:

```text
80/tcp
443/tcp
22/tcp from admin IP or VPN only
```

`public_vps` must not require `KX_PUBLIC_MODE_EXPIRES_AT`.

`public_vps` must not use `127.0.0.1`, `localhost`, or an empty string as `KX_HOST`.

---

## 15. Manager-to-Agent Droplet transport

For Droplet/VPS deployment, the Agent must remain private on the Droplet:

```text
127.0.0.1:8765
```

The Manager must call the Droplet Agent through SSH-local curl:

```text
Manager on Windows
  -> ssh root@<droplet_host>
  -> curl http://127.0.0.1:8765/v1/<endpoint>
```

The Manager must not require a local tunnel such as:

```text
127.0.0.1:18765 -> tunnel -> 127.0.0.1:8765
```

The Manager must not bind the Agent publicly on:

```text
0.0.0.0:8765
```

For Droplet mode:

```text
remote_agent_url empty or loopback -> use SSH-local transport
remote_agent_url real non-loopback URL -> use direct HTTP only if explicitly configured
```

The following Agent calls must use the same transport:

```text
/health
/agent/info
/capsules/import
/instances/create
/instances/update
/network/set-profile
/security/check
/instances/start
/logs
/status
```

When sending network profile data, Manager must normalize public host fields into the Agent field named `host`.

Canonical normalization preference:

```text
domain
droplet_domain
public_host
public_url
url
host
kx_host
KX_HOST
droplet_host
target_host
```

The Manager must keep these meanings distinct:

```text
host/public_host/domain/droplet_domain = public runtime host, e.g. konnxion.com
droplet_host/target_host = SSH target, e.g. 138.197.174.76
```

The Agent schema should accept `host`, not arbitrary `domain`.

The Manager must send `host` during both:

```text
/instances/create
/network/set-profile
```

The Agent must persist the host change during `/network/set-profile` by regenerating:

```text
/opt/konnaxion/instances/<INSTANCE_ID>/env/kx.env
/opt/konnaxion/instances/<INSTANCE_ID>/env/django.env
/opt/konnaxion/instances/<INSTANCE_ID>/env/frontend.env
/opt/konnaxion/instances/<INSTANCE_ID>/state/docker-compose.runtime.yml
/opt/konnaxion/instances/<INSTANCE_ID>/state/traefik-dynamic.yml
```

`/network/set-profile` must not be validation-only.

It must preserve existing secrets while rewriting host-derived env values.

Preserve:

```text
DJANGO_SECRET_KEY
POSTGRES_PASSWORD
DATABASE_URL password component
```

Rewrite:

```text
KX_HOST
KX_HOST_ALIASES
KX_NETWORK_PROFILE
KX_EXPOSURE_MODE
KX_PUBLIC_MODE_ENABLED
KX_PUBLIC_MODE_EXPIRES_AT
DJANGO_ALLOWED_HOSTS
DJANGO_CSRF_TRUSTED_ORIGINS
CSRF_TRUSTED_ORIGINS
CORS_ALLOWED_ORIGINS
NEXT_PUBLIC_API_BASE
NEXT_PUBLIC_BACKEND_BASE
Traefik Host(...) rules
```

---

## 16. Startup sequence

Konnaxion Agent must start services in this order:

```text
1. Verify capsule signature.
2. Verify required image archives/checksums.
3. Resolve and validate network profile host.
4. Render runtime env files.
5. Render docker-compose.runtime.yml.
6. Render traefik-dynamic.yml.
7. Run Security Gate.
8. Start postgres and redis.
9. Run migrations.
10. Verify migration history against the actual managed database schema.
11. Start django-api.
12. Start frontend-next.
13. Start media-nginx.
14. Start celeryworker and celerybeat.
15. Start traefik.
16. Run runtime and application-readiness healthchecks.
17. Mark instance as running only when readiness has no blocking failures.
```

For dependencies that require Django health, the healthcheck must use a robust local socket check, not `wget`.

Equivalent CLI flow:

```bash
kx capsule verify konnaxion-v14-demo-2026.04.30.kxcap
kx instance create demo-001 --capsule konnaxion-v14-demo-2026.04.30.kxcap --profile public_vps --host konnxion.com
kx network set-profile demo-001 --profile public_vps --host konnxion.com --alias www.konnxion.com --alias 138.197.174.76.sslip.io
kx security check demo-001
kx instance start demo-001
kx instance status demo-001
```

---

## 17. Migration flow

Migrations must run as a one-off job.

```bash
docker compose --profile jobs run --rm kx-migrate
```

Equivalent manager command:

```bash
kx instance migrate demo-001
```

Django model changes require migrations before the schema is considered valid. A zero-exit
`migrate` or `migrate --check` result is not sufficient by itself: the Agent must compare the
final migration-derived managed table set with the tables actually present in PostgreSQL.

If migration rows say an app is applied but one or more required managed tables are missing,
the runtime is in `schema drift` and must not be reported healthy. On a Manager-declared fresh
deployment only, the Agent may self-heal an affected app when **none** of that app's expected
managed tables exist and no already-applied external migration depends on the affected app.
The repair clears only those stale migration-history rows, reruns normal migrations, and then
re-verifies the schema. Updates, restores, or partially existing schemas must fail closed and
require explicit operator recovery.

Public GO LIVE readiness must also exercise application-level endpoints that require the schema,
including `/api/control/universes/` and `/api/control/worlds/`; a listening Django/Uvicorn socket
alone does not establish application readiness.

---

## 18. Backup flow

The runtime must support PostgreSQL backups through the `postgres` service.

Canonical command:

```bash
docker compose exec -T postgres \
  pg_dump -U konnaxion -d konnaxion \
  > ../backups/postgres/konnaxion_${KX_INSTANCE_ID}_$(date +%Y%m%d_%H%M%S).sql
```

Equivalent manager command:

```bash
kx instance backup demo-001
```

Backups must include:

```text
PostgreSQL dump
media volume
runtime manifest
capsule reference
safe env metadata without secrets
```

Backups must not include:

```text
old full disk image
/tmp
/dev/shm
unknown crontabs
unknown systemd units
unknown Docker volumes
old authorized_keys
old sudoers fragments
```

---

## 19. Update and rollback flow

Each update uses a new immutable capsule.

```text
current capsule:  konnaxion-v14-demo-2026.04.30.kxcap
next capsule:     konnaxion-v14-demo-2026.05.07.kxcap
```

Update sequence:

```text
1. Verify new capsule.
2. Verify required image archives.
3. Backup current instance.
4. Stop frontend-next, celeryworker, celerybeat.
5. Load new images from capsule.
6. Render updated env and compose.
7. Preserve canonical network profile host and aliases unless the operator changes them.
8. Run migrations.
9. Verify migration/schema coherence; never auto-repair migration history on an update.
10. Start new services.
11. Run runtime and application-readiness healthchecks.
12. If healthy, mark new capsule current.
13. If unhealthy, rollback to previous capsule.
```

Rollback command:

```bash
kx instance rollback demo-001
```

---

## 20. Security requirements

The runtime must enforce:

```text
- no privileged containers
- no host network mode
- no Docker socket mount
- no unknown images
- no public database
- no public Redis
- no public frontend direct port
- no public Django direct port
- no public Flower dashboard
- no secrets in image layers
- no secrets inside .kxcap
- no Traefik Docker provider requiring docker.sock
```

Traefik must use the file provider.

The Docker socket must not be mounted into Traefik or any application container.

---

## 21. Security Gate checks

Before `docker compose up`, Konnaxion Agent must run:

```text
capsule_signature
image_archives_present
image_checksums
manifest_schema
compose_schema
traefik_dynamic_schema
forbidden_ports_not_published
docker_socket_not_mounted
no_privileged_containers
no_host_network
postgres_not_public
redis_not_public
frontend_not_public
django_not_public
flower_not_public
allowed_images_only
env_files_permissions
secrets_not_default
network_profile_valid
public_mode_expiration_valid
public_vps_host_present
public_vps_host_not_loopback
django_allowed_hosts_contains_kx_host
django_allowed_hosts_contains_kx_host_aliases
frontend_public_urls_match_kx_host
traefik_host_rules_contain_kx_host
traefik_host_rules_contain_kx_host_aliases
```

Blocking failures:

```text
FAIL_BLOCKING if port 3000 is published
FAIL_BLOCKING if port 5000 is published
FAIL_BLOCKING if port 5432 is published
FAIL_BLOCKING if port 6379 is published
FAIL_BLOCKING if Docker socket is mounted
FAIL_BLOCKING if privileged: true exists
FAIL_BLOCKING if network_mode: host exists
FAIL_BLOCKING if public_temporary has no expiration
FAIL_BLOCKING if public_vps has no KX_HOST
FAIL_BLOCKING if public_vps KX_HOST is 127.0.0.1 or localhost
FAIL_BLOCKING if capsule signature is invalid
FAIL_BLOCKING if required image archive is missing
FAIL_BLOCKING if image checksum mismatch
FAIL_BLOCKING if DJANGO_ALLOWED_HOSTS excludes KX_HOST
FAIL_BLOCKING if Traefik dynamic config excludes KX_HOST
FAIL_BLOCKING if Traefik dynamic config uses 127.0.0.1 for public_vps
```

---

## 22. Compose validation command

Konnaxion Agent must validate the rendered Compose file before starting.

```bash
docker compose -f docker-compose.runtime.yml config
```

Then inspect published ports:

```bash
docker compose -f docker-compose.runtime.yml config | grep -n "published\|target\|ports" || true
```

The final validation must prove that only Traefik publishes ports.

---

## 23. Healthcheck matrix

| Service         | Healthcheck                                      |
| --------------- | ------------------------------------------------ |
| `traefik`       | Traefik ping or container running                |
| `frontend-next` | Node HTTP request to `http://127.0.0.1:3000/`    |
| `django-api`    | Python socket connect to `127.0.0.1:5000`        |
| `postgres`      | `pg_isready`                                     |
| `redis`         | `redis-cli ping`                                 |
| `media-nginx`   | `nginx -t` or container running                  |
| `celeryworker`  | process running or Celery inspect ping, optional |
| `celerybeat`    | process running                                  |
| `flower`        | private HTTP health, optional                    |

External healthcheck must use only:

```text
https://<KX_HOST>/
https://<KX_HOST>/api/
https://<KX_HOST>/admin/
https://<KX_HOST>/media/
```

Alias healthchecks may also be used when `KX_HOST_ALIASES` is configured:

```text
https://<KX_HOST_ALIAS>/
https://<KX_HOST_ALIAS>/api/
```

Never:

```text
http://<HOST>:3000
http://<HOST>:5000
http://<HOST>:5555
http://<HOST>:5432
http://<HOST>:6379
```

`/api/` or `/api/health/` may return Django `404` if those exact routes do not exist. That is acceptable infrastructure-wise if the response comes from Django/Uvicorn and not Traefik `404`.

A Traefik plain-text response of:

```text
404 page not found
```

for `https://<KX_HOST>/` or `https://<KX_HOST>/api/` is a blocking routing failure for `public_vps`.

It means the Host rule did not match the public domain.

---

## 24. Observability

Minimum commands:

```bash
docker compose ps
docker compose logs --tail=100 traefik
docker compose logs --tail=100 django-api
docker compose logs --tail=100 frontend-next
docker compose logs --tail=100 celeryworker
docker compose logs --tail=100 postgres
docker compose logs --tail=100 redis
```

Canonical manager commands:

```bash
kx instance status demo-001
kx instance logs demo-001 --service traefik
kx instance logs demo-001 --service django-api
kx instance logs demo-001 --service frontend-next
kx security check demo-001
```

Useful direct diagnostic checks:

```bash
curl -k -I https://<KX_HOST>
curl -k -I https://<KX_HOST>/api/
curl -k -I https://<KX_HOST>/admin/
docker inspect kx-<INSTANCE_ID>-django-api --format '{{json .State.Health}}'
docker inspect kx-<INSTANCE_ID>-frontend-next --format '{{json .State.Health}}'
```

Useful host-rule diagnostics from the VPS:

```bash
curl -k -i -H 'Host: <KX_HOST>' https://127.0.0.1/
curl -k -i -H 'Host: <KX_HOST>' https://127.0.0.1/api/
curl -k -i -H 'Host: <KX_HOST_ALIAS>' https://127.0.0.1/
curl -k -i -H 'Host: <KX_HOST_ALIAS>' https://127.0.0.1/api/
grep -RniE 'Host|KX_HOST|NEXT_PUBLIC|DJANGO_ALLOWED_HOSTS' \
  /opt/konnaxion/instances/<INSTANCE_ID>/env \
  /opt/konnaxion/instances/<INSTANCE_ID>/state
```

For local DNS bypass testing:

```bash
curl -k -i --resolve <KX_HOST>:443:<DROPLET_IP> https://<KX_HOST>/
curl -k -i --resolve <KX_HOST>:443:<DROPLET_IP> https://<KX_HOST>/api/
```

---

## 25. Host-level runtime requirements

Minimum host:

```text
Linux host
Docker Engine
Docker Compose v2
4 GB RAM minimum
8 GB RAM recommended
SSD storage
Firewall available
```

For small demo VPS hosts, 2 GB RAM plus swap can work, but image build should preferably happen locally or in CI, then images should be loaded on the VPS.

For capsule/appliance deployment, recommended:

```text
Ubuntu Server LTS or Debian minimal
Docker from official repository
Konnaxion Agent installed as system service
Konnaxion Agent private on 127.0.0.1:8765
Konnaxion Manager local UI
UFW or equivalent firewall
Tailscale or tunnel agent optional
```

---

## 26. Forbidden runtime patterns

Do not use:

```text
frontend on host systemd as target architecture
public port 3000
public port 5000
public port 5555
public PostgreSQL
public Redis
Docker socket mounted into app containers
Traefik Docker provider requiring docker.sock
deployment user in docker group by default
unverified images
unknown containers
host network mode
privileged containers
manual edits inside running containers
runtime pnpm/Corepack network downloads
```

Manual edits to generated runtime files may be used only as emergency diagnostics. Permanent behavior belongs in Agent renderers and Manager payload generation.

---

## 27. Compatibility with legacy deployment

Legacy production shape:

```text
Backend: Docker Compose
Frontend: Node.js / pnpm on host
Database: Docker Postgres
Redis: Docker Redis
Proxy: Docker Traefik
```

Target capsule runtime:

```text
Frontend: Docker container
Backend: Docker container
Database: Docker Postgres
Redis: Docker Redis
Proxy: Docker Traefik file provider
Media: Docker Nginx
Workers: Docker Celery
```

Migration from legacy to capsule requires:

```text
1. Build frontend image.
2. Include frontend-next image archive in .kxcap.
3. Add frontend-next service to docker-compose.capsule.yml.
4. Route Traefik to frontend-next:3000.
5. Remove public host exposure of port 3000.
6. Preserve /api/, /admin/, and /media/ routing.
7. Generate KX_HOST, KX_HOST_ALIASES, DJANGO_ALLOWED_HOSTS, and NEXT_PUBLIC_* from network profile.
8. Ensure /network/set-profile persists runtime host changes.
```

---

## 28. Capsule image requirements

A deployable capsule must contain required runtime image archives.

Minimum required app images:

```text
images/frontend-next.oci.tar
images/django-api.oci.tar
```

Recommended full runtime image archive set:

```text
images/frontend-next.oci.tar
images/django-api.oci.tar
images/traefik.oci.tar
images/media-nginx.oci.tar
images/postgres.oci.tar
images/redis.oci.tar
```

A capsule with only:

```text
images/README.json
```

must fail verification for production or Droplet deployment.

Builder must write image archives before:

```text
checksums.txt
signature.sig
```

Verify must fail if manifest/compose requires images that are absent.

---

## 29. Acceptance criteria

`DOC-08` is implemented correctly when:

```text
[PASS] docker compose config succeeds
[PASS] only traefik publishes ports
[PASS] postgres has no published port
[PASS] redis has no published port
[PASS] frontend-next has no published port
[PASS] django-api has no published port
[PASS] flower has no published port
[PASS] / routes to frontend-next
[PASS] /api/ routes to django-api or returns Django/Uvicorn response
[PASS] /admin/ routes to django-api
[PASS] /media/ routes to media-nginx
[PASS] frontend-next runs without runtime pnpm/Corepack download
[PASS] django-api becomes healthy using robust socket healthcheck
[PASS] Traefik uses file provider and no Docker socket
[PASS] KX_HOST is correct for selected network profile
[PASS] KX_HOST_ALIASES are optional but correctly routed when present
[PASS] Traefik Host rules include KX_HOST
[PASS] Traefik Host rules include KX_HOST_ALIASES when configured
[PASS] DJANGO_ALLOWED_HOSTS includes KX_HOST
[PASS] DJANGO_ALLOWED_HOSTS includes KX_HOST_ALIASES when configured
[PASS] NEXT_PUBLIC_API_BASE uses canonical KX_HOST
[PASS] /network/set-profile regenerates env files
[PASS] /network/set-profile regenerates traefik-dynamic.yml
[PASS] /network/set-profile preserves existing secrets
[PASS] migrations run through kx-migrate
[PASS] backups can be created from postgres
[PASS] Security Gate blocks dangerous compose changes
[PASS] local_only profile binds to localhost
[PASS] intranet_private exposes only 80/443 to LAN
[PASS] public_temporary requires expiration
[PASS] public_vps exposes only 80/443 publicly
[PASS] public_vps never renders 127.0.0.1 as KX_HOST
[PASS] Manager reaches Droplet Agent through SSH-local curl when Agent is private
[PASS] Manager sends host during /instances/create
[PASS] Manager sends host during /network/set-profile
[PASS] capsule verify fails if required image archives are missing
```

---

## 30. Out of scope

This document does not define:

```text
Konnaxion Capsule file signing internals
Konnaxion Manager UI screens
Konnaxion Agent privilege boundary
Threat model details
Backup retention policy
Full VPS hardening guide
Frontend application architecture
Backend model architecture
```

Those belong to:

```text
DOC-03_Konnaxion_Capsule_Format.md
DOC-04_Konnaxion_Manager_Architecture.md
DOC-05_Konnaxion_Agent_Security_Model.md
DOC-07_Konnaxion_Security_Gate.md
DOC-09_Konnaxion_Backup_Restore_Rollback.md
DOC-13_Konnaxion_Threat_Model.md
```

---

## 31. Final decision

The canonical Konnaxion runtime is:

```text
Docker Compose
Traefik as only public entrypoint
Traefik file provider, not Docker socket provider
Next.js frontend in container
Django/Gunicorn backend in container
PostgreSQL internal only
Redis internal only
Celery internal only
Nginx/media internal only
Flower optional and private only
Security Gate before start
Network profiles rendered and persisted by Konnaxion Agent
KX_HOST propagated to Traefik, Django, and frontend env
KX_HOST_ALIASES propagated to Traefik and Django env when configured
No public app internals
No runtime package-manager downloads
No public Agent listener
Droplet Agent private on 127.0.0.1:8765 and reached by SSH-local curl
Manager preserves the distinction between public runtime host and SSH Droplet host
/network/set-profile regenerates runtime files and is not validation-only
```

This replaces the legacy hybrid VPS model for future Konnaxion Capsule and Konnaxion Box deployments.
