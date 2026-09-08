# DOC-02 — Konnaxion Capsule Architecture

```yaml
doc_id: DOC-02
title: Konnaxion Capsule Architecture
project: Konnaxion
app_version: v14
param_version: kx-param-2026.04.30
status: draft
depends_on:
  - DOC-00_Konnaxion_Canonical_Variables.md
related_docs:
  - DOC-03_Konnaxion_Capsule_Format.md
  - DOC-04_Konnaxion_Manager_Architecture.md
  - DOC-05_Konnaxion_Agent_Security_Model.md
  - DOC-06_Konnaxion_Network_Profiles.md
  - DOC-07_Konnaxion_Security_Gate.md
  - DOC-08_Konnaxion_Runtime_Docker_Compose.md
```

---

## 1. Purpose

This document defines the target architecture for the **Konnaxion Capsule** system.

The goal is to package Konnaxion as a portable, signed, plug-and-play deployment unit that can be imported by the **Konnaxion Capsule Manager** and launched on a dedicated machine, intranet server, demo box, or VPS with minimal configuration.

The architecture must support:

```text
Local demo
Private intranet
Private tunnel
Temporary public demo
Public VPS deployment
```

The architecture must also be **private-by-default**, because the previous VPS incident showed that a compromised deployment environment can include malicious Docker containers, cron persistence, attempted sudo backdoors, `/tmp/sshd`, exposed secrets, and miner activity. 

---

## 2. Canonical product model

The Konnaxion Capsule architecture is based on the following product components:

```text
Konnaxion Capsule
  Portable signed application bundle.

Konnaxion Capsule Manager
  User-facing application that imports, installs, starts, stops, updates, and monitors capsules.

Konnaxion Agent
  Local privileged service that performs controlled system actions on behalf of the Manager.

Konnaxion Instance
  Installed runtime copy of a capsule with its own data, secrets, logs, media, and backups.

Konnaxion Box
  Dedicated host machine or appliance running the Manager, Agent, Docker runtime, and instances.
```

The system must always separate:

```text
Capsule = immutable app package
Instance = mutable local runtime state
```

This distinction prevents secrets, database state, media files, and logs from being mixed into the portable capsule.

---

## 3. Existing Konnaxion application stack

Konnaxion v14 uses the following stack:

```text
Frontend: Next.js / React
Backend: Django 5.1 + Django REST Framework
Background jobs: Celery
Broker/result backend: Redis
Database: PostgreSQL
Runtime: Docker / Docker Compose
Reverse proxy: Traefik
Media/static service: Nginx
```

The technical reference identifies Konnaxion as a Django + DRF backend, Next.js/React frontend, PostgreSQL persistence layer, and Redis-backed Celery infrastructure. 

The existing repository also contains Docker Compose production/local files, Traefik configuration, production Django containers, Celery worker/beat/flower containers, Postgres maintenance scripts, and frontend deployment tooling. 

---

## 4. Target architecture overview

```text
┌─────────────────────────────────────────────────────────┐
│                    Konnaxion Box                       │
│  Linux host / appliance / VPS / intranet machine        │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│              Konnaxion Capsule Manager                  │
│  UI, lifecycle control, logs, backups, profiles          │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│                    Konnaxion Agent                      │
│  Controlled privileged service                          │
│  Docker, firewall, secrets, profiles, healthchecks       │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│                  Docker Compose Runtime                 │
│  Isolated networks, volumes, services, healthchecks      │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│                  Konnaxion Instance                     │
│  Traefik + Next.js + Django + Postgres + Redis + Celery  │
└─────────────────────────────────────────────────────────┘
```

The **Konnaxion Capsule** is not itself the running system. It is the signed source package used to create or update a **Konnaxion Instance**.

---

## 5. Capsule-to-instance lifecycle

```text
.kxcap file
  ↓
Import
  ↓
Signature verification
  ↓
Manifest validation
  ↓
Image loading
  ↓
Secret generation
  ↓
Instance creation
  ↓
Network profile selection
  ↓
Docker Compose startup
  ↓
Migrations
  ↓
Healthcheck
  ↓
Ready
```

The lifecycle must be deterministic. The same capsule should produce the same service topology every time, except for generated secrets, generated instance IDs, local hostnames, and runtime data.

---

## 6. Capsule boundary

A **Konnaxion Capsule** contains:

```text
Application images
Docker Compose template
Manifest
Network profiles
Environment templates
Migration commands
Seed data
Healthcheck definitions
Checksums
Signature
```

A capsule must not contain:

```text
Real production secrets
Real SSH keys
Private keys
Provider tokens
Production database credentials
Unencrypted production database dumps
Mutable runtime logs
Mutable runtime media
Host-specific firewall state
```

Secrets such as `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, `DATABASE_URL`, API keys, private keys, tokens, and deployment credentials must be treated as sensitive and regenerated or rotated after compromise. 

---

## 7. Instance boundary

A **Konnaxion Instance** contains all mutable runtime state:

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

Instance data survives capsule updates.

Capsules are replaceable. Instances are persistent.

This allows:

```text
Rollback to previous capsule
Upgrade to new capsule
Backup/restore instance data
Multiple local demo instances
Temporary public demos
Intranet deployments
```

---

## 8. Runtime service topology

The canonical runtime topology is:

```text
Traefik
  ├── /        → frontend-next
  ├── /api/    → django-api
  ├── /admin/  → django-api
  └── /media/  → media-nginx

Internal services
  ├── postgres
  ├── redis
  ├── celeryworker
  └── celerybeat

Private/optional service
  └── flower
```

Canonical service names:

```text
traefik
frontend-next
django-api
postgres
redis
celeryworker
celerybeat
flower
media-nginx
kx-agent
```

The current deployment already uses routing where `/` maps to the Next.js frontend, `/api/` maps to Django, `/admin/` maps to Django admin, and `/media/` maps to media service handling. 

---

## 9. Reverse proxy rule

All client access must go through **Traefik**.

Allowed external paths:

```text
/
 /api/
 /admin/
 /media/
```

Direct access to internal services is forbidden.

```text
Forbidden direct public access:
- Next.js port 3000
- Django/Gunicorn port 5000 or 8000
- PostgreSQL port 5432
- Redis port 6379
- Flower/dashboard port 5555
- Docker daemon TCP socket
```

The incident recovery notes explicitly identify `3000`, `5555`, `5432`, `6379`, `8000`, and Docker daemon ports as ports that must not be exposed publicly. Public traffic should reach only the reverse proxy on `80/443`. 

---

## 10. Network profiles

The architecture supports these canonical network profiles:

```text
local_only
intranet_private
private_tunnel
public_temporary
public_vps
offline
```

### 10.1 local_only

```text
Purpose:
  Demo on the same machine only.

Exposure:
  localhost only.

Public access:
  No.
```

### 10.2 intranet_private

```text
Purpose:
  LAN / school / organization / demo room.

Exposure:
  Private network only.

Public access:
  No.

Default profile:
  Yes.
```

### 10.3 private_tunnel

```text
Purpose:
  Controlled remote demo for trusted users.

Exposure:
  VPN or private tunnel only.

Public access:
  No.
```

### 10.4 public_temporary

```text
Purpose:
  Short-lived external demo.

Exposure:
  Temporary tunnel.

Requirements:
  Expiration required.
  Authentication recommended.
  Automatic shutdown required.
```

### 10.5 public_vps

```text
Purpose:
  Real public deployment.

Exposure:
  80/443 through cloud firewall and local firewall.

Requirements:
  Hardened SSH.
  Cloud firewall.
  UFW or equivalent.
  Backups/snapshots.
```

### 10.6 offline

```text
Purpose:
  No external network.

Exposure:
  None.
```

The default must be:

```env
KX_NETWORK_PROFILE=intranet_private
KX_EXPOSURE_MODE=private
KX_PUBLIC_MODE_ENABLED=false
```

---

## 11. Security architecture

Security must be enforced by architecture, not by user discipline.

The system must be:

```text
private-by-default
deny-by-default
signed-capsule-only
least-privilege
profile-driven
rollback-capable
secrets-generated-on-install
```

The **Konnaxion Capsule Manager** must never allow a user to accidentally expose internal services.

The **Konnaxion Agent** must reject dangerous runtime configurations, including:

```text
unknown images
unsigned capsules
privileged containers
host network mode
Docker socket mounts
public PostgreSQL
public Redis
public dashboard ports
public Next.js direct access
public Django direct access
```

---

## 12. Konnaxion Agent responsibility boundary

The **Konnaxion Agent** is the only component allowed to perform privileged actions.

It may:

```text
verify capsule signatures
load approved OCI images
create Docker networks
create Docker volumes
generate secrets
write instance env files
apply approved network profiles
start/stop approved Compose stacks
run migrations
run healthchecks
create backups
restore backups
collect logs
```

It must not:

```text
run arbitrary shell commands from the UI
start arbitrary containers
pull arbitrary images without approval
mount arbitrary host paths
mount /var/run/docker.sock into app containers
enable privileged containers
open arbitrary ports
disable security checks
```

This is required because the previous incident involved malicious Docker containers and Docker-based persistence. 

---

## 13. Manager responsibility boundary

The **Konnaxion Capsule Manager** is the user-facing control layer.

It provides:

```text
Import capsule
Start instance
Stop instance
Update instance
Rollback instance
Choose network profile
Show URLs
Show logs
Show health
Show security state
Create backup
Restore backup
Create temporary public access
Expire temporary public access
```

The Manager does not directly control Docker, firewall rules, or system services. It sends limited requests to the Agent.

---

## 14. Security Gate

Before an instance can start, the Agent must run a blocking **Security Gate**.

Required checks:

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

Allowed statuses:

```text
PASS
WARN
FAIL_BLOCKING
SKIPPED
UNKNOWN
```

If any critical check returns `FAIL_BLOCKING`, the instance must not start.

---

## 15. Build architecture

The developer-side build system is separate from the runtime Manager.

```text
Konnaxion Capsule Builder
  ├── validate source tree
  ├── build frontend
  ├── build backend image
  ├── build supporting images
  ├── run tests
  ├── generate manifest
  ├── export OCI images
  ├── calculate checksums
  ├── sign capsule
  └── produce .kxcap
```

Canonical build command:

```bash
kx capsule build \
  --profile demo \
  --version 2026.04.30-demo.1 \
  --output konnaxion-v14-demo-2026.04.30.kxcap
```

The existing frontend runbook requires `NODE_OPTIONS="--max-old-space-size=4096"` before production Next.js builds to avoid heap out-of-memory failures on limited-memory servers. 

---

## 16. Backend migration architecture

The capsule runtime must support Django migrations as a controlled lifecycle step.

Canonical migration step:

```text
Start database
Start Redis
Start backend image in migration mode
Run python manage.py migrate
Start full stack
Run healthchecks
```

The existing backend workflow uses Docker Compose to rebuild services, run `makemigrations`, apply `migrate`, verify container status, and optionally create a superuser. 

In production capsules, `makemigrations` should not run automatically. The capsule should already include migration files. Runtime should only run:

```bash
python manage.py migrate
```

---

## 17. Update and rollback architecture

Capsules are immutable releases.

```text
Instance demo-001
  current_capsule  -> konnaxion-v14-demo-2026.04.30.kxcap
  previous_capsule -> konnaxion-v14-demo-2026.04.20.kxcap
```

Update flow:

```text
1. Verify new capsule
2. Backup current instance
3. Stop application services
4. Keep database available if needed
5. Apply new images/config
6. Run migrations
7. Run healthcheck
8. Switch current capsule pointer
9. Start full stack
10. Mark update complete
```

Rollback flow:

```text
1. Stop failed instance
2. Restore previous capsule
3. Restore previous env/config if needed
4. Restore DB backup if migration is not backward-compatible
5. Start previous stack
6. Run healthcheck
```

---

## 18. Storage architecture

The host storage layout must follow:

```text
/opt/konnaxion/
├── capsules/
├── instances/
├── shared/
├── releases/
├── manager/
└── backups/
```

Instance-specific storage:

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

Capsule files are read-only after import.

Instance files are mutable.

Backups must be instance-scoped.

---

## 19. Observability architecture

The Manager must expose simple status information:

```text
Instance state
Network profile
Public exposure status
Service health
Security Gate result
Last backup
Current capsule version
Current app version
Public URL if enabled
Private URL if enabled
```

Canonical instance states:

```text
created
importing
verifying
ready
starting
running
stopping
stopped
updating
rolling_back
degraded
failed
security_blocked
```

---

## 20. User experience architecture

The user-facing flow must remain minimal.

Target first-run flow:

```text
1. Open Konnaxion Capsule Manager
2. Import .kxcap file
3. Choose mode:
   - Local only
   - Intranet private
   - Private tunnel
   - Public temporary
4. Create admin account or auto-generate one
5. Click Start
6. Open provided URL
```

The user must not manually configure:

```text
Docker Compose
Traefik
Nginx
PostgreSQL
Redis
Celery
Django settings
Next.js env files
ports
firewall
secrets
certificates
systemd
migrations
```

---

## 21. Architecture decisions

### ADR-02-001 — Use Docker Compose, not Kubernetes

Decision:

```text
Use Docker Compose as the capsule runtime.
```

Reason:

```text
Konnaxion already uses Docker Compose patterns.
The target is plug-and-play local/intranet deployment.
Kubernetes would add unnecessary operational complexity.
```

### ADR-02-002 — Use Traefik as the single entrypoint

Decision:

```text
All HTTP/HTTPS traffic enters through Traefik.
```

Reason:

```text
Traefik already exists in the current production deployment.
It allows route-based separation for frontend, API, admin, and media.
It prevents direct exposure of app internals.
```

### ADR-02-003 — Keep capsules immutable

Decision:

```text
Capsules are immutable after build.
```

Reason:

```text
Updates and rollback require predictable artifacts.
Runtime state belongs to instances, not capsules.
```

### ADR-02-004 — Generate secrets on install

Decision:

```text
Capsules contain templates only.
Secrets are generated by the Agent during instance creation.
```

Reason:

```text
Portable artifacts must not carry real secrets.
Prior incident recovery requires secret rotation and no trust in old server state.
```

### ADR-02-005 — Make public exposure explicit and temporary by default

Decision:

```text
Public temporary mode requires expiration.
Permanent public mode requires public_vps profile.
```

Reason:

```text
Konnaxion is intended to support private demos and intranet deployment.
Public exposure should never happen accidentally.
```

---

## 22. Non-goals

This architecture does not aim to provide:

```text
Multi-node orchestration
Kubernetes cluster management
Generic hosting for arbitrary apps
Arbitrary Docker control panel
Public cloud PaaS clone
Automatic migration of compromised servers
Secret recovery from old servers
```

The system is specifically for packaging, installing, running, updating, and securing Konnaxion instances.

---

## 23. Final target architecture

```text
Konnaxion Capsule Builder
  ↓ produces signed .kxcap

Konnaxion Capsule
  ↓ imported by

Konnaxion Capsule Manager
  ↓ controlled through

Konnaxion Agent
  ↓ manages

Docker Compose Runtime
  ↓ runs

Konnaxion Instance
  ├── Traefik
  ├── frontend-next
  ├── django-api
  ├── postgres
  ├── redis
  ├── celeryworker
  ├── celerybeat
  └── media-nginx
```

Default posture:

```text
Network: intranet_private
Exposure: private
Public mode: disabled
Firewall: deny-by-default
Secrets: generated on install
Capsules: signed only
Internal ports: never public
Rollback: supported
Backups: enabled
```

---

## 24. Summary

The Konnaxion Capsule Architecture turns Konnaxion into a portable, signed, reproducible deployment unit.

The architecture is designed around five rules:

```text
1. Plug-and-play for the user.
2. Private-by-default for safety.
3. Signed and verified capsules only.
4. Strong separation between capsule and instance.
5. Traefik-only public entrypoint with internal services isolated.
```

This gives Konnaxion a path toward local demos, intranet installations, temporary public demos, and VPS deployments without requiring the operator to manually configure Docker, firewall rules, secrets, reverse proxy routing, database services, Redis, Celery, or frontend build behavior.


---

## 25. Installed artifacts and optional composition

The capsule architecture also defines an installed-artifact discovery boundary.
The canonical contract is `DOC-23_Capsule_Installed_Artifact_and_Composition_Contract.md`.

```text
Capsule / Agent
  owns installation, removal, version, dependency resolution, public entrypoints,
  installed registry, public-manifest discovery, and readiness projection

Product
  owns business behavior and product UX

kOA Spaces (composition_host)
  may compose admitted public product contributions

koali-ui (library)
  provides shared public UI primitives/contracts; it is not a switchable product
```

A composition host is optional. The absence or removal of Spaces must not make a
standalone product unhealthy. Likewise, rejecting an integrated UI contribution
must not make a runtime/standalone-capable product broken.

The installed registry is generation-based and must replace compile-time product
lists in composition hosts. Capsule exposes discovery; it does not interpret the
product's routes, navigation, surfaces, commands, inspectors, or presentation
state.
