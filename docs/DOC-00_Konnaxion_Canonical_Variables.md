---
doc_id: DOC-00
title: Konnaxion Canonical Variables
project: Konnaxion
app_version: v14
param_version: kx-param-2026.04.30
status: canonical-draft
owner: Konnaxion
last_updated: 2026-04-30
depends_on: []
---

# DOC-00 — Konnaxion Canonical Variables

## 0. Purpose

This document is the canonical reference for Konnaxion naming, paths, variables, profiles, ports, services, instance states, backup/restore/rollback resource statuses, CLI commands, and documentation alignment rules.

Every other Konnaxion documentation file must align with this document.

This document defines the target architecture for the new Konnaxion portable appliance/capsule system, not only the legacy VPS deployment.

---

## 1. Canonical Product Names

| Concept | Canonical Name | Do Not Use |
|---|---|---|
| Main platform | `Konnaxion` | Connexion, Konexion, Konnexion |
| Current application version | `Konnaxion v14` | V14 app, Konnaxion app, platform without version |
| Portable deployment file | `Konnaxion Capsule` | magic file, bundle, random archive |
| Capsule file extension | `.kxcap` | `.zip`, `.tar.gz`, `.konnaxion` |
| Management application | `Konnaxion Capsule Manager` | launcher, installer, dashboard |
| Local privileged system service | `Konnaxion Agent` | daemon, root helper, service helper |
| Build tool | `Konnaxion Capsule Builder` | packager, compiler, exporter |
| Plug-and-play dedicated machine | `Konnaxion Box` | local server, mini PC, generic appliance |
| Installed runtime environment | `Konnaxion Instance` | installation, copy, local deploy |
| Generic physical or virtual host | `Konnaxion Host` | random server, local machine |

---

## 2. Canonical Application Stack

Konnaxion must always be described using the following stack:

```text
Frontend: Next.js / React / TypeScript
Backend: Django + Django REST Framework
Database: PostgreSQL
Background jobs: Celery
Broker/result backend: Redis
Reverse proxy: Traefik
Media/static service: Nginx
Runtime target: Docker Compose
```

Do not describe the target appliance architecture as Kubernetes, serverless, pure systemd, or static hosting unless a future architecture decision explicitly changes this document.

---

## 3. Canonical Product Architecture

```text
Konnaxion Box
  └── Konnaxion Capsule Manager
        └── Konnaxion Agent
              └── Docker Compose Runtime
                    └── Konnaxion Instance
                          ├── traefik
                          ├── frontend-next
                          ├── django-api
                          ├── postgres
                          ├── redis
                          ├── celeryworker
                          ├── celerybeat
                          ├── flower
                          └── media-nginx
```

Canonical separation:

```text
Capsule = code + images + manifest + profiles + templates
Instance = data + secrets + logs + backups + media
```

The capsule is portable and mostly immutable.

The instance is local, stateful, and environment-specific.

---

## 4. Canonical Capsule Format

### 4.1 File Naming

Canonical capsule filename pattern:

```text
konnaxion-v14-demo-YYYY.MM.DD.kxcap
```

Example:

```text
konnaxion-v14-demo-2026.04.30.kxcap
```

Canonical variables:

```text
CAPSULE_ID=konnaxion-v14-demo-2026.04.30
CAPSULE_VERSION=2026.04.30-demo.1
APP_VERSION=v14
```

### 4.2 Capsule Internal Structure

Canonical `.kxcap` structure:

```text
.kxcap
├── manifest.yaml
├── docker-compose.capsule.yml
├── images/
│   ├── frontend-next.oci.tar
│   ├── django-api.oci.tar
│   ├── traefik.oci.tar
│   └── media-nginx.oci.tar
├── profiles/
│   ├── local_only.yaml
│   ├── intranet_private.yaml
│   ├── private_tunnel.yaml
│   ├── public_temporary.yaml
│   ├── public_vps.yaml
│   └── offline.yaml
├── env-templates/
│   ├── django.env.template
│   ├── postgres.env.template
│   ├── redis.env.template
│   └── frontend.env.template
├── migrations/
├── seed-data/
├── healthchecks/
├── checksums.txt
└── signature.sig
```

### 4.3 Capsule Must Never Contain

The capsule must never contain real production secrets.

Forbidden inside `.kxcap`:

```text
real DJANGO_SECRET_KEY
real POSTGRES_PASSWORD
real DATABASE_URL
SSH private key
API token
Git token
provider token
unencrypted production DB dump
complete .env file containing secrets
private certificate key
```

The capsule may contain templates, defaults, non-secret examples, and schema definitions.

---

## 5. Canonical Paths

## 5.1 Legacy VPS Paths

The following paths are legacy deployment paths from the historical VPS environment:

```text
/home/deploy/apps/Konnaxion
/home/deploy/apps/Konnaxion/backend
/home/deploy/apps/Konnaxion/frontend
```

They must be marked as:

```text
legacy_vps
```

They are not the canonical target paths for the capsule/appliance architecture.

## 5.2 Target Appliance Paths

Canonical root path:

```text
/opt/konnaxion
```

Canonical directory layout:

```text
/opt/konnaxion/
├── capsules/
├── instances/
├── shared/
├── releases/
├── manager/
├── agent/
└── backups/
```

Canonical backup storage:

```text
/opt/konnaxion/backups/<INSTANCE_ID>/ = canonical backup storage root
/opt/konnaxion/instances/<INSTANCE_ID>/backups/ = optional instance-local pointer/cache/state directory
```

Canonical installed-artifact registry:

```text
<KX_ROOT>/shared/registry/installed-artifacts.json
```

Canonical artifact descriptor names:

```text
source tree: capsule-artifact.yaml
packaged capsule: artifact.yaml
product-owned integrated contributions: contributions/*
```

The registry is deployment/discovery state. It must not contain product-owned
routes, navigation, commands, inspectors, or presentation state.


Backups must be treated as **application data recovery artifacts**, not full host snapshots.

Normal Konnaxion backup/restore must never preserve or restore:

```text
full disk image
/tmp
/dev/shm
system crontabs
user crontabs
old authorized_keys
old sudoers files
unknown Docker volumes
Docker daemon state
Docker socket
unverified host binaries
```

Canonical instance layout:

```text
/opt/konnaxion/instances/<INSTANCE_ID>/
├── env/
├── postgres/
├── redis/
├── media/
├── logs/
├── backups/
└── state/
```

Canonical release layout:

```text
/opt/konnaxion/releases/<RELEASE_ID>/
/opt/konnaxion/current -> /opt/konnaxion/releases/<RELEASE_ID>
```

Canonical capsule storage:

```text
/opt/konnaxion/capsules/<CAPSULE_ID>.kxcap
```

---

## 6. Canonical Identifiers

| Entity | Canonical Variable | Example |
|---|---|---|
| Instance | `INSTANCE_ID` | `demo-001` |
| Release | `RELEASE_ID` | `20260430_173000` |
| Capsule | `CAPSULE_ID` | `konnaxion-v14-demo-2026.04.30` |
| Application version | `APP_VERSION` | `v14` |
| Capsule version | `CAPSULE_VERSION` | `2026.04.30-demo.1` |
| Parameter version | `PARAM_VERSION` | `kx-param-2026.04.30` |
| Network profile | `NETWORK_PROFILE` | `intranet_private` |
| Exposure mode | `EXPOSURE_MODE` | `private` |

---

## 7. Canonical Network Profiles

| Profile | Variable Value | Description | Default |
|---|---|---|---|
| Local only | `local_only` | Accessible only from the local machine | No |
| Intranet private | `intranet_private` | Accessible from the LAN only | Yes |
| Private tunnel | `private_tunnel` | Accessible through a private tunnel/VPN | No |
| Public temporary | `public_temporary` | Temporarily exposed for demos | No |
| Public VPS | `public_vps` | Full public VPS deployment | No |
| Offline | `offline` | No external network exposure | No |

Canonical default:

```env
NETWORK_PROFILE=intranet_private
EXPOSURE_MODE=private
```

Public exposure must never be the default.

---

## 8. Canonical Exposure Modes

| Mode | Variable Value | Rule |
|---|---|---|
| Private | `private` | Default mode |
| LAN | `lan` | Local network only |
| VPN | `vpn` | Private tunnel only |
| Temporary tunnel | `temporary_tunnel` | Public tunnel with mandatory expiration |
| Public | `public` | Public deployment only for approved `public_vps` profile |

Canonical public mode variables:

```env
KX_EXPOSURE_MODE=private
KX_PUBLIC_MODE_ENABLED=false
KX_PUBLIC_MODE_DURATION_HOURS=
KX_PUBLIC_MODE_EXPIRES_AT=
```

Rule:

```text
If KX_PUBLIC_MODE_ENABLED=true,
then KX_PUBLIC_MODE_EXPIRES_AT is mandatory.
```

---

## 9. Canonical Ports

### 9.1 Allowed Entry Ports

| Port | Usage | Public VPS | Intranet | Local |
|---:|---|---:|---:|---:|
| `443` | HTTPS via Traefik | Yes | Yes | Optional |
| `80` | HTTP redirect to HTTPS | Yes | Optional | No |
| `22` | SSH admin access | Restricted | Not recommended | No |

### 9.2 Always-Internal Ports

| Port | Service | Rule |
|---:|---|---|
| `3000` | Next.js direct | Never public |
| `5000` | Django/Gunicorn internal | Never public |
| `5432` | PostgreSQL | Never public |
| `6379` | Redis | Never public |
| `5555` | Flower/dashboard | Never public |
| `8000` | Django development server | Never public |

### 9.3 Forbidden Public Surfaces

The following must never be exposed directly:

```text
Next.js direct port
Django direct port
PostgreSQL
Redis
Flower/dashboard
Docker daemon TCP socket
Docker socket mount into app containers
```

---

## 10. Canonical Routing

All Konnaxion deployments must use the following routing model:

```text
https://<HOST>/          -> frontend-next
https://<HOST>/api/      -> django-api
https://<HOST>/admin/    -> django-api
https://<HOST>/media/    -> media-nginx
```

Traefik is the canonical public or LAN entry point.

No user-facing request should directly target `frontend-next`, `django-api`, `postgres`, `redis`, or `celeryworker`.

---

## 11. Canonical Docker Services

| Canonical Service Name | Role |
|---|---|
| `traefik` | Reverse proxy and only HTTP(S) entry point |
| `frontend-next` | Next.js production frontend |
| `django-api` | Django/Gunicorn API service |
| `postgres` | PostgreSQL database |
| `redis` | Redis broker/result backend |
| `celeryworker` | Celery workers |
| `celerybeat` | Celery scheduler |
| `flower` | Celery monitoring, private only |
| `media-nginx` | Media/static file service |
| `kx-agent` | Local privileged agent for Manager actions |

Avoid inconsistent names such as:

```text
backend
api
web
next
frontend
db
cache
worker
```

unless they are explicitly mapped to the canonical service names.

---

## 12. Canonical Environment Variables

## 12.1 Django

```env
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SECRET_KEY=<GENERATED_ON_INSTALL>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=<GENERATED_FROM_PROFILE>
DJANGO_ADMIN_URL=admin/
USE_DOCKER=yes
SENTRY_DSN=
```

## 12.2 Database

```env
DATABASE_URL=postgres://konnaxion:<POSTGRES_PASSWORD>@postgres:5432/konnaxion
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=konnaxion
POSTGRES_USER=konnaxion
POSTGRES_PASSWORD=<GENERATED_ON_INSTALL>
```

## 12.3 Redis and Celery

```env
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
```

## 12.4 Frontend

```env
NEXT_PUBLIC_API_BASE=https://<PUBLIC_OR_PRIVATE_HOST>/api
NEXT_PUBLIC_BACKEND_BASE=https://<PUBLIC_OR_PRIVATE_HOST>
NEXT_TELEMETRY_DISABLED=1
NODE_OPTIONS=--max-old-space-size=4096
```

## 12.5 Konnaxion Manager Variables

All capsule/manager variables must use the `KX_` prefix.

```env
KX_INSTANCE_ID=demo-001
KX_CAPSULE_ID=konnaxion-v14-demo-2026.04.30
KX_CAPSULE_VERSION=2026.04.30-demo.1
KX_APP_VERSION=v14
KX_PARAM_VERSION=kx-param-2026.04.30
KX_NETWORK_PROFILE=intranet_private
KX_EXPOSURE_MODE=private
KX_PUBLIC_MODE_ENABLED=false
KX_PUBLIC_MODE_EXPIRES_AT=
KX_REQUIRE_SIGNED_CAPSULE=true
KX_GENERATE_SECRETS_ON_INSTALL=true
KX_ALLOW_UNKNOWN_IMAGES=false
KX_ALLOW_PRIVILEGED_CONTAINERS=false
KX_ALLOW_DOCKER_SOCKET_MOUNT=false
KX_ALLOW_HOST_NETWORK=false
KX_BACKUP_ENABLED=true
KX_BACKUP_ROOT=/opt/konnaxion/backups
KX_BACKUP_RETENTION_DAYS=14
KX_DAILY_BACKUP_RETENTION_DAYS=14
KX_WEEKLY_BACKUP_RETENTION_WEEKS=8
KX_MONTHLY_BACKUP_RETENTION_MONTHS=12
KX_PRE_UPDATE_BACKUP_RETENTION_COUNT=5
KX_PRE_RESTORE_BACKUP_RETENTION_COUNT=5
KX_COMPOSE_FILE=/opt/konnaxion/instances/<KX_INSTANCE_ID>/state/docker-compose.runtime.yml
KX_BACKUP_DIR=/opt/konnaxion/backups/<KX_INSTANCE_ID>/<BACKUP_CLASS>/<BACKUP_ID>
KX_HOST=<GENERATED_FROM_PROFILE>
KX_ALLOWED_INTEGRATION_CONTRACTS=koali-ui/v1,module-interface-manifest/v1
```

`KX_ALLOWED_INTEGRATION_CONTRACTS` controls admission of public integrated-UI
manifest contracts only. It does not make a composition host mandatory for a
standalone product.

---

## 13. Canonical Security Gate

Every Konnaxion Instance must pass a Security Gate before startup.

### 13.1 Security Gate Status Values

| Status | Meaning |
|---|---|
| `PASS` | Compliant |
| `WARN` | Non-blocking issue |
| `FAIL_BLOCKING` | Startup forbidden |
| `SKIPPED` | Not applicable for the selected profile |
| `UNKNOWN` | Could not be verified |

### 13.2 Mandatory Security Gate Checks

```text
capsule_signature
image_checksums
manifest_schema
secrets_present
secrets_not_default
firewall_enabled
dangerous_ports_blocked
postgres_not_public
redis_not_public
docker_socket_not_mounted
no_privileged_containers
no_host_network
allowed_images_only
admin_surface_private
backup_configured
```

### 13.3 Blocking Failures

The following checks must block startup if they fail:

```text
capsule_signature
image_checksums
manifest_schema
secrets_present
secrets_not_default
dangerous_ports_blocked
postgres_not_public
redis_not_public
docker_socket_not_mounted
no_privileged_containers
no_host_network
allowed_images_only
```

---

## 14. Canonical Instance States

| State | Canonical Value |
|---|---|
| Created but never started | `created` |
| Capsule import in progress | `importing` |
| Verification in progress | `verifying` |
| Ready to start | `ready` |
| Starting | `starting` |
| Running | `running` |
| Stopping | `stopping` |
| Stopped | `stopped` |
| Updating | `updating` |
| Rolling back | `rolling_back` |
| Recoverable issue | `degraded` |
| Failed | `failed` |
| Blocked by security | `security_blocked` |

## 14.1 Canonical Backup Resource Statuses

Backup statuses are **resource statuses**, not Konnaxion Instance states.

| Backup State | Canonical Value |
|---|---|
| Backup record created | `created` |
| Backup running | `running` |
| Backup verification running | `verifying` |
| Backup verified and usable | `verified` |
| Backup failed | `failed` |
| Backup expired by retention policy | `expired` |
| Backup deleted | `deleted` |
| Backup quarantined by safety check | `quarantined` |

## 14.2 Canonical Restore Resource Statuses

Restore statuses are **resource statuses**, not Konnaxion Instance states.

| Restore State | Canonical Value |
|---|---|
| Restore planned | `planned` |
| Restore preflight running | `preflight` |
| Creating pre-restore backup | `creating_pre_restore_backup` |
| Restoring database | `restoring_database` |
| Restoring media | `restoring_media` |
| Running migrations | `running_migrations` |
| Running Security Gate | `running_security_gate` |
| Running healthchecks | `running_healthchecks` |
| Restore completed | `restored` |
| Restore completed with issues | `degraded` |
| Restore failed | `failed` |
| Restore rolled back | `rolled_back` |

## 14.3 Canonical Rollback Resource Statuses

Rollback statuses are **resource statuses**, not Konnaxion Instance states.

| Rollback State | Canonical Value |
|---|---|
| Rollback planned | `planned` |
| Rollback running | `running` |
| Capsule pointer restored | `capsule_repointed` |
| Data restored | `data_restored` |
| Healthchecks running | `healthchecking` |
| Rollback completed | `completed` |
| Rollback failed | `failed` |

---

## 15. Canonical CLI

The canonical CLI command is:

```text
kx
```

Canonical public/operator command groups:

```bash
kx capsule build
kx capsule verify
kx capsule import

kx instance create
kx instance start
kx instance stop
kx instance status
kx instance logs
kx instance backup
kx instance restore
kx instance restore-new
kx instance update
kx instance rollback
kx instance health

kx backup list
kx backup verify
kx backup test-restore

kx security check
kx network set-profile
```

Internal Agent operations may exist, but must not be documented as ordinary operator commands unless explicitly promoted here.

Internal-only examples:

```bash
kx backup preflight
kx backup postflight
kx instance stop-services
kx instance fix-permissions
```

These internal operations are allowlisted Agent actions and should normally be invoked by the Manager, not by end users.

Examples:

```bash
kx capsule build --profile demo --output konnaxion-v14-demo-2026.04.30.kxcap
kx capsule verify konnaxion-v14-demo-2026.04.30.kxcap
kx capsule import konnaxion-v14-demo-2026.04.30.kxcap
kx instance start demo-001 --network intranet_private
kx instance backup demo-001 --class manual
kx backup verify demo-001_20260430_230000_manual
kx instance restore-new --from demo-001_20260430_230000_manual --new-instance-id demo-restore-001
kx instance health demo-001
kx security check demo-001
```

---

## 16. Canonical Documentation Files

All Konnaxion appliance/capsule documentation must follow this naming plan:

```text
DOC-00_Konnaxion_Canonical_Variables.md
DOC-01_Konnaxion_Product_Vision.md
DOC-02_Konnaxion_Capsule_Architecture.md
DOC-03_Konnaxion_Capsule_Format.md
DOC-04_Konnaxion_Manager_Architecture.md
DOC-05_Konnaxion_Agent_Security_Model.md
DOC-06_Konnaxion_Network_Profiles.md
DOC-07_Konnaxion_Security_Gate.md
DOC-08_Konnaxion_Runtime_Docker_Compose.md
DOC-09_Konnaxion_Backup_Restore_Rollback.md
DOC-10_Konnaxion_Builder_CLI.md
DOC-11_Konnaxion_Box_Appliance_Image.md
DOC-12_Konnaxion_Install_Runbook.md
DOC-13_Konnaxion_Threat_Model.md
DOC-14_Konnaxion_Operator_Guide.md
DOC-15_Konnaxion_Developer_Guide.md
```

---

## 17. Canonical Document Header

Every documentation file must begin with this metadata block:

```yaml
---
doc_id: DOC-XX
title: <Document Title>
project: Konnaxion
app_version: v14
param_version: kx-param-2026.04.30
status: draft
owner: Konnaxion
last_updated: 2026-04-30
depends_on:
  - DOC-00_Konnaxion_Canonical_Variables.md
---
```

---

## 18. Terms to Avoid

| Avoid | Use Instead |
|---|---|
| magic file | `Konnaxion Capsule` |
| app | `Konnaxion Capsule Manager` when referring to the manager |
| daemon | `Konnaxion Agent` |
| local server | `Konnaxion Box` or `Konnaxion Host` |
| open to web | `KX_EXPOSURE_MODE=public` |
| private mode | `NETWORK_PROFILE=intranet_private` or `private_tunnel` |
| bundle | `Konnaxion Capsule` |
| deployment | `Konnaxion Instance` when installed locally |

---

## 19. Documentation Alignment Rules

Every future document must use:

```text
Konnaxion Capsule
Konnaxion Capsule Manager
Konnaxion Agent
Konnaxion Box
Konnaxion Host
Konnaxion Instance
.kxcap
KX_* variables
canonical NETWORK_PROFILE values
canonical Docker service names
canonical ports
canonical instance states
canonical backup/restore/rollback resource statuses
canonical CLI commands
canonical backup paths and variables
```

Every future document must avoid inventing:

```text
new service names
new profile names
new public ports
new directory roots
new state names
new backup/restore/rollback statuses
new security statuses
new environment variable prefixes
new backup path conventions
```

If a future document needs a new variable, profile, service, state, or term, update `DOC-00_Konnaxion_Canonical_Variables.md` first.

---

## 20. Canonical Target Statement

The target system is:

```text
Konnaxion as a portable, signed, plug-and-play capsule,
managed by a local Capsule Manager and privileged Agent,
private-by-default,
deployable on a Konnaxion Box, intranet, private tunnel, temporary public tunnel, or hardened VPS,
with minimal user configuration and mandatory security gates.
```

The legacy VPS deployment remains useful as historical reference, but the new target is the capsule/appliance architecture.

---

## 21. Fixed Decisions

The following decisions are fixed by this document:

```text
Source of truth document:
DOC-00_Konnaxion_Canonical_Variables.md

Target architecture:
Konnaxion Capsule + Konnaxion Capsule Manager + Konnaxion Agent + Docker Compose Runtime

Default network profile:
intranet_private

Default exposure:
private

Security model:
private-by-default, deny-by-default, signed capsules only

Runtime:
Traefik + Next.js + Django + PostgreSQL + Redis + Celery + Nginx/media

Target root path:
 /opt/konnaxion

Manager variable prefix:
KX_

Capsule extension:
.kxcap

Canonical backup root:
/opt/konnaxion/backups/<INSTANCE_ID>/

Canonical CLI:
kx
```
