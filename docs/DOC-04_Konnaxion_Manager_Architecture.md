---
doc_id: DOC-04
title: Konnaxion Manager Architecture
project: Konnaxion
app_version: v14
param_version: kx-param-2026.04.30
status: draft
depends_on:
  - DOC-00_Konnaxion_Canonical_Variables.md
  - DOC-02_Konnaxion_Capsule_Architecture.md
  - DOC-03_Konnaxion_Capsule_Format.md
  - DOC-05_Konnaxion_Agent_Security_Model.md
  - DOC-06_Konnaxion_Network_Profiles.md
  - DOC-07_Konnaxion_Security_Gate.md
---

# DOC-04 — Konnaxion Manager Architecture

## 1. Purpose

`DOC-04_Konnaxion_Manager_Architecture.md` defines the architecture of the **Konnaxion Capsule Manager**.

The Konnaxion Capsule Manager is the user-facing control layer responsible for turning a signed `.kxcap` file into a running **Konnaxion Instance** with minimal configuration.

It must provide a plug-and-play experience while enforcing security controls by default.

The Manager does **not** replace Konnaxion. It manages Konnaxion.

```text
Konnaxion Capsule Manager
= import capsule
+ verify capsule
+ create instance
+ generate secrets
+ apply network profile
+ start/stop/update Konnaxion
+ show URLs, health, logs, backup status
+ enforce safe defaults
```

---

## 2. Canonical Product Position

The Manager is part of the larger appliance model.

```text
Konnaxion Box
  └── Konnaxion Capsule Manager
        └── Konnaxion Agent
              └── Docker Compose Runtime
                    └── Konnaxion Instance
```

The Manager is the local operator interface.

The Agent is the privileged execution layer.

Docker Compose is the runtime layer.

Konnaxion is the application layer.

---

## 3. Design Goals

The Manager must satisfy the following goals.

### 3.1 Plug-and-play first

The user should not manually configure:

```text
Docker Compose
Traefik
Nginx
PostgreSQL
Redis
Celery
Django env files
Next.js env files
firewall rules
certificates
ports
systemd services
migrations
```

The normal path should be:

```text
1. Open Konnaxion Capsule Manager
2. Import .kxcap
3. Choose network profile
4. Click Start
5. Open Konnaxion URL
```

### 3.2 Private-by-default

The default mode must be private.

```env
KX_NETWORK_PROFILE=intranet_private
KX_EXPOSURE_MODE=private
KX_PUBLIC_MODE_ENABLED=false
```

The Manager must never default to public exposure.

### 3.3 Security as a gate, not a warning

Security validation must run before the instance starts.

If a critical rule fails, the Manager must block startup.

```text
Security Gate result: FAIL_BLOCKING
Action: do not start instance
```

### 3.4 Zero manual secrets

The Manager must generate secrets locally during instance creation.

The capsule must contain templates only.

The Manager must never import real production secrets from a `.kxcap`.

### 3.5 Reproducible instances

Given the same capsule and the same profile, the Manager must produce a predictable instance layout.

Runtime data must remain outside the capsule.

```text
Capsule = immutable package
Instance = mutable state
```

---

## 4. Non-Goals

The Manager is not:

```text
a general Docker GUI
a replacement for Docker Compose
a Kubernetes orchestrator
a cloud hosting platform
a public control panel
a remote admin panel exposed to the web
a tool for running arbitrary containers
a secrets vault for unrelated services
```

The Manager should not allow users to run arbitrary Docker images, arbitrary shell commands, arbitrary port mappings, or arbitrary host mounts.

---

## 5. High-Level Architecture

```text
┌──────────────────────────────────────────────┐
│              User / Operator                 │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│        Konnaxion Capsule Manager UI          │
│  Desktop UI or local-only web UI             │
└──────────────────────┬───────────────────────┘
                       │ Local API
                       ▼
┌──────────────────────────────────────────────┐
│              Konnaxion Agent                 │
│  Privileged service with restricted actions  │
└──────────────────────┬───────────────────────┘
                       │
        ┌──────────────┼────────────────┐
        ▼              ▼                ▼
┌─────────────┐ ┌─────────────┐ ┌──────────────┐
│ Docker      │ │ Firewall    │ │ Filesystem   │
│ Compose     │ │ / Network   │ │ / Backups    │
└──────┬──────┘ └─────────────┘ └──────────────┘
       │
       ▼
┌──────────────────────────────────────────────┐
│              Konnaxion Runtime               │
│ Traefik + Next.js + Django + Postgres + Redis│
└──────────────────────────────────────────────┘
```

---

## 6. Internal Components

### 6.1 Manager UI

Canonical name:

```text
Konnaxion Capsule Manager
```

Possible implementation:

```text
Tauri + React
or
local-only web UI served on 127.0.0.1
```

Recommended target:

```text
Tauri + React frontend
Rust or Go local backend bridge
```

Responsibilities:

```text
display installed instances
import capsules
show health state
show security state
show current network profile
start/stop instances
initiate backups
initiate restores
initiate updates
show logs
show URLs
request profile changes
```

The UI must not directly call Docker or manipulate firewall rules.

All privileged actions must go through the Konnaxion Agent.

---

### 6.2 Konnaxion Agent

Canonical name:

```text
Konnaxion Agent
```

The Agent is a local privileged service.

It exposes a restricted local API to the Manager UI.

It performs only allowlisted operations.

Allowed operations:

```text
capsule.verify
capsule.import
instance.create
instance.start
instance.stop
instance.status
instance.logs
instance.backup
instance.restore
instance.update
instance.rollback
security.check
network.set_profile
```

Forbidden operations:

```text
run arbitrary shell command
run arbitrary Docker image
run arbitrary Docker Compose file
mount arbitrary host path
bind arbitrary host port
enable privileged containers
mount Docker socket into a container
enable host network mode
disable Security Gate
disable signature validation
```

---

### 6.3 Capsule Importer

The Capsule Importer is responsible for reading `.kxcap` files.

Pipeline:

```text
1. Receive .kxcap path
2. Verify file exists
3. Verify extension is .kxcap
4. Unpack to temporary quarantine path
5. Validate manifest schema
6. Verify checksums
7. Verify signature
8. Validate service allowlist
9. Validate network policy
10. Move capsule to /opt/konnaxion/capsules/
```

Canonical storage path:

```text
/opt/konnaxion/capsules/<CAPSULE_ID>.kxcap
```

The importer must not start any service.

Import and start are separate actions.

---

### 6.4 Instance Controller

The Instance Controller creates and manages `Konnaxion Instance` directories.

Canonical path:

```text
/opt/konnaxion/instances/<INSTANCE_ID>/
```

Canonical structure:

```text
/opt/konnaxion/instances/<INSTANCE_ID>/
├── env/
├── postgres/
├── redis/
├── media/
├── logs/
├── backups/
├── state/
└── compose/
```

Responsibilities:

```text
create instance directory
generate instance ID
link capsule to instance
generate secrets
render env files from templates
render docker-compose from profile
prepare volumes
run migrations
create initial admin flow
track lifecycle state
```

---

### 6.5 Network Profile Controller

The Network Profile Controller applies one of the canonical network profiles.

Canonical profiles:

```text
local_only
intranet_private
private_tunnel
public_temporary
public_vps
offline
```

Responsibilities:

```text
select profile
render Traefik config
configure local firewall
configure bind addresses
configure allowed ports
configure tunnel if enabled
set public/private URLs
enforce blocked ports
```

The Manager must never expose internal services directly.

Always internal:

```text
frontend-next direct port
django-api direct port
postgres
redis
celeryworker
celerybeat
flower unless private
docker daemon
```

---

### 6.6 Security Gate

The Security Gate is a blocking validation layer.

It must run before:

```text
instance.start
network.set_profile
instance.update
public_temporary enablement
public_vps enablement
```

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

Canonical result values:

```text
PASS
WARN
FAIL_BLOCKING
SKIPPED
UNKNOWN
```

If any critical check returns `FAIL_BLOCKING`, startup is refused.

---

### 6.7 Runtime Adapter

The Runtime Adapter is the abstraction between the Agent and Docker Compose.

Initial runtime:

```text
Docker Compose
```

Runtime commands are generated, not user-written.

The Manager should own the runtime files under:

```text
/opt/konnaxion/instances/<INSTANCE_ID>/compose/
```

The generated compose must include only canonical services:

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
```

The Runtime Adapter must reject:

```text
unknown services
unknown images
privileged: true
network_mode: host
ports exposing postgres
ports exposing redis
ports exposing django directly
ports exposing frontend directly
volumes mounting /, /etc, /root, /var/run/docker.sock
```

---

### 6.8 Backup Controller

The Backup Controller manages backup and restore.

Backup scope:

```text
PostgreSQL dump
media files
instance env metadata without leaking secrets in logs
capsule reference
profile reference
manager state
```

Backups must not include:

```text
Docker images already stored in capsule
temporary files
runtime sockets
raw logs with secrets
unverified external files
```

Canonical path:

```text
/opt/konnaxion/instances/<INSTANCE_ID>/backups/
```

Default retention:

```env
KX_BACKUP_ENABLED=true
KX_BACKUP_RETENTION_DAYS=14
```

---

### 6.9 Update and Rollback Controller

Each capsule is immutable.

Update flow:

```text
1. Verify new capsule
2. Backup current instance
3. Stage new runtime config
4. Run compatibility checks
5. Apply migrations
6. Verify migration history against the actual managed schema
7. Start new version
8. Run runtime and application-readiness healthchecks
9. Mark new capsule as current only after readiness passes
```

Rollback flow:

```text
1. Stop failed runtime
2. Restore previous compose configuration
3. Restore previous capsule reference
4. Restore backup if required
5. Start previous version
6. Run healthchecks
```

The Manager must keep at least one rollback point by default.

For fresh deployments, the Manager may explicitly authorize the Agent's narrow
schema-drift self-heal only when an affected app has no managed tables at all
and no applied external migration depends on it. Update and restore flows must
never authorize automatic migration-history repair. Application readiness must
include schema-backed API probes; Docker container health alone is insufficient.

---

## 7. Manager UI Screens

### 7.1 Home Screen

Required information:

```text
Instance name
Instance state
Network profile
Exposure mode
Primary URL
Security state
Backup state
App version
Capsule version
```

Example:

```text
Konnaxion Demo

Status: Running
Network: Intranet Private
URL: https://konnaxion.local
Security: PASS
Backups: Enabled
App Version: v14
Capsule: konnaxion-v14-demo-2026.04.30
```

Actions:

```text
Open Konnaxion
Start
Stop
Restart
Backup
View Logs
Security Check
Change Network Profile
Update
```

---

### 7.2 Capsule Import Screen

Fields:

```text
Capsule file
Capsule ID
Capsule version
App version
Signature status
Required RAM
Recommended RAM
Included services
Supported profiles
```

Actions:

```text
Verify
Import
Cancel
```

Startup is not allowed from this screen until verification passes.

---

### 7.3 Network Profile Screen

The user chooses only from predefined profiles.

Options:

```text
Local Only
Intranet Private
Private Tunnel
Public Temporary
Public VPS
Offline
```

The screen must show consequences clearly.

Example:

```text
Profile: Intranet Private
Accessible from: local network only
Internet exposure: disabled
Allowed public ports: none
Allowed LAN ports: 443
Database exposure: blocked
Redis exposure: blocked
```

---

### 7.4 Security Screen

Required checklist:

```text
Capsule signature
Image checksums
Secrets
Firewall
Dangerous ports
Postgres exposure
Redis exposure
Docker socket
Privileged containers
Host network
Unknown images
Admin surface
Backup configuration
```

Each check must show one canonical status:

```text
PASS
WARN
FAIL_BLOCKING
SKIPPED
UNKNOWN
```

---

### 7.5 Logs Screen

The Logs screen must support:

```text
traefik logs
frontend-next logs
django-api logs
postgres logs
redis logs
celeryworker logs
celerybeat logs
flower logs
manager logs
agent logs
```

Logs must redact secrets.

Never display full values of:

```text
DJANGO_SECRET_KEY
POSTGRES_PASSWORD
DATABASE_URL
API keys
tokens
private keys
session secrets
```

---

### 7.6 Backup and Restore Screen

Required actions:

```text
Create Backup
Restore Backup
Download Backup
Delete Backup
Verify Backup
```

Required metadata:

```text
Backup ID
Created at
App version
Capsule version
Database size
Media size
Profile at backup time
Restore compatibility status
```

---

### 7.7 Update Screen

Required information:

```text
Current capsule
New capsule
Compatibility result
Migration required
Backup required
Rollback point available
Expected downtime
Security check result
```

Required actions:

```text
Verify Update
Apply Update
Rollback
Cancel
```

---

## 8. Lifecycle States

Canonical lifecycle states:

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

State transitions:

```text
created -> verifying -> ready
ready -> starting -> running
running -> stopping -> stopped
running -> updating -> running
running -> updating -> rolling_back -> running
ready -> security_blocked
starting -> failed
running -> degraded
```

The UI must always show the current state.

---

## 9. Local API Contract

The UI communicates with the Agent through a local API.

The API must be local-only.

Allowed binding:

```text
127.0.0.1
```

Forbidden binding:

```text
0.0.0.0
public interface
LAN interface by default
```

Recommended transport:

```text
Unix socket on Linux
Named pipe on Windows
localhost HTTPS only if needed
```

Canonical endpoints:

```text
GET  /v1/instances
POST /v1/capsules/verify
POST /v1/capsules/import
POST /v1/instances
GET  /v1/instances/{INSTANCE_ID}
POST /v1/instances/{INSTANCE_ID}/start
POST /v1/instances/{INSTANCE_ID}/stop
POST /v1/instances/{INSTANCE_ID}/restart
GET  /v1/instances/{INSTANCE_ID}/logs
POST /v1/instances/{INSTANCE_ID}/backup
POST /v1/instances/{INSTANCE_ID}/restore
POST /v1/instances/{INSTANCE_ID}/update
POST /v1/instances/{INSTANCE_ID}/rollback
POST /v1/instances/{INSTANCE_ID}/security-check
POST /v1/instances/{INSTANCE_ID}/network-profile
```

Every write operation must be authenticated locally.

The UI and Agent should use a locally generated pairing token or OS-level permissions.

---

## 10. Configuration Model

Manager configuration path:

```text
/opt/konnaxion/manager/config.yaml
```

Example:

```yaml
manager:
  version: "0.1.0"
  bind: "127.0.0.1"
  log_level: "INFO"

security:
  require_signed_capsule: true
  allow_unknown_images: false
  allow_privileged_containers: false
  allow_host_network: false
  allow_docker_socket_mount: false

defaults:
  network_profile: "intranet_private"
  exposure_mode: "private"
  backup_enabled: true
  backup_retention_days: 14

paths:
  root: "/opt/konnaxion"
  capsules: "/opt/konnaxion/capsules"
  instances: "/opt/konnaxion/instances"
  backups: "/opt/konnaxion/backups"
```

Instance state path:

```text
/opt/konnaxion/instances/<INSTANCE_ID>/state/instance.yaml
```

Example:

```yaml
instance_id: "demo-001"
capsule_id: "konnaxion-v14-demo-2026.04.30"
capsule_version: "2026.04.30-demo.1"
app_version: "v14"
network_profile: "intranet_private"
exposure_mode: "private"
state: "running"
primary_url: "https://konnaxion.local"
created_at: "2026-04-30T00:00:00Z"
updated_at: "2026-04-30T00:00:00Z"
```

---

## 11. Environment Variables

The Manager and Agent use `KX_*` variables.

Required:

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
KX_BACKUP_RETENTION_DAYS=14
```

---

## 12. Service Boundaries

### 12.1 Manager UI boundary

Can:

```text
display state
request operations
show results
```

Cannot:

```text
directly run Docker
directly modify firewall
directly write secrets
directly edit compose files
directly expose ports
```

### 12.2 Agent boundary

Can:

```text
execute allowlisted operations
manage instance directories
render env files
render compose files
call Docker Compose
configure approved network rules
run backups
run healthchecks
```

Cannot:

```text
accept arbitrary shell commands
accept arbitrary Docker commands
run unsigned capsules
skip security checks
mount Docker socket into Konnaxion containers
```

### 12.3 Runtime boundary

Can:

```text
run canonical Konnaxion services
use private Docker networks
persist data in instance volumes
```

Cannot:

```text
open unmanaged public ports
run unknown containers
share host root filesystem
depend on secrets embedded in capsule
```

---

## 13. Security Defaults

Default values:

```yaml
default_network_profile: intranet_private
default_exposure_mode: private
public_mode_enabled: false
require_signed_capsule: true
allow_unknown_images: false
allow_privileged_containers: false
allow_host_network: false
allow_docker_socket_mount: false
backup_enabled: true
```

Always blocked:

```text
3000/tcp
5000/tcp
5432/tcp
6379/tcp
5555/tcp
8000/tcp
Docker daemon TCP
```

Allowed only through Traefik:

```text
/
 /api/
 /admin/
 /media/
```

---

## 14. Error Model

Canonical error classes:

```text
CAPSULE_INVALID
CAPSULE_SIGNATURE_FAILED
CAPSULE_CHECKSUM_FAILED
MANIFEST_INVALID
IMAGE_NOT_ALLOWED
SERVICE_NOT_ALLOWED
PORT_NOT_ALLOWED
SECRET_GENERATION_FAILED
FIREWALL_CONFIG_FAILED
SECURITY_GATE_FAILED
INSTANCE_ALREADY_EXISTS
INSTANCE_NOT_FOUND
RUNTIME_START_FAILED
RUNTIME_HEALTHCHECK_FAILED
BACKUP_FAILED
RESTORE_FAILED
UPDATE_FAILED
ROLLBACK_FAILED
```

Every error must include:

```text
error_code
human_message
technical_message
suggested_action
blocking
timestamp
```

Example:

```yaml
error_code: "PORT_NOT_ALLOWED"
human_message: "This profile cannot expose PostgreSQL."
technical_message: "Service postgres attempted to bind host port 5432."
suggested_action: "Remove the port binding or choose an approved network profile."
blocking: true
timestamp: "2026-04-30T00:00:00Z"
```

---

## 15. Observability

The Manager must expose a local health summary.

Required health categories:

```text
manager
agent
docker
traefik
frontend-next
django-api
postgres
redis
celeryworker
celerybeat
media-nginx
backup
security
network
```

Example status values:

```text
healthy
degraded
unhealthy
unknown
stopped
```

The Manager must show enough information to troubleshoot without revealing secrets.

---

## 16. Implementation Recommendation

Recommended technology stack:

```text
Manager UI: Tauri + React + TypeScript
Agent: Rust or Go
Runtime: Docker Compose
Reverse proxy: Traefik
Local storage: YAML state files + SQLite optional
Packaging: signed installers + .kxcap capsules
```

Why this choice:

```text
Tauri keeps the desktop app lighter than Electron.
React aligns with Konnaxion frontend skills.
Rust or Go is suitable for a small privileged service.
Docker Compose matches the existing Konnaxion deployment model.
Traefik is already part of the production routing model.
```

Do not introduce Kubernetes in the MVP.

---

## 17. MVP Scope

The first implementation of the Manager should include:

```text
capsule verify
capsule import
instance create
instance start
instance stop
instance status
logs
network profile: local_only
network profile: intranet_private
network profile: public_temporary
security check
backup
restore
```

MVP may defer:

```text
multi-instance management
remote fleet management
plugin system
automatic OS imaging
public VPS provisioning
advanced RBAC
full GUI theme customization
```

---

## 18. Acceptance Criteria

DOC-04 is implemented correctly when:

```text
A user can import a signed .kxcap file.
A user can create a Konnaxion Instance without editing env files.
Secrets are generated automatically.
The default network profile is intranet_private.
The Manager refuses to start if security checks fail.
Postgres is never public.
Redis is never public.
Next.js direct port is never public.
Django direct port is never public.
Docker socket is never mounted into containers.
The user receives a working Konnaxion URL.
The user can stop, restart, backup and restore the instance.
```

---

## 19. Open Decisions

The following decisions remain open:

```text
Should the first Manager UI be Tauri desktop or local web UI?
Should the Agent be written in Rust or Go?
Should local secrets be stored in OS keychain or protected env files?
Should public_temporary use Cloudflare Tunnel, Tailscale Funnel, or both?
Should the appliance image be based on Ubuntu Server or Debian?
Should the first release support Windows hosts or Linux hosts only?
```

Default recommendation for MVP:

```text
Linux host first
Ubuntu Server LTS first
Docker Compose runtime
Local web UI first if speed matters
Tauri UI second if product polish matters
Cloudflare Tunnel optional
Tailscale private tunnel optional
```

---

## 20. Summary

The Konnaxion Capsule Manager is the plug-and-play control surface for Konnaxion.

It must hide infrastructure complexity while enforcing strict security defaults.

The correct architecture is:

```text
Manager UI
  -> local Agent
  -> verified capsule
  -> generated instance
  -> Docker Compose runtime
  -> Traefik-only exposure
  -> private-by-default network profile
```

The Manager must make the safe path easy and the unsafe path impossible by default.
