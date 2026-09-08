doc_id: DOC-20
title: Konnaxion Package Types and Artifact Lifecycle
project: Konnaxion
app_version: v14
param_version: kx-param-2026.04.30
status: draft
owner: Konnaxion Architecture
last_updated: 2026-05-03
depends_on:
  - DOC-00_Konnaxion_Canonical_Variables.md
  - DOC-03_Konnaxion_Capsule_Format.md
  - DOC-06_Konnaxion_Network_Profiles.md
  - DOC-07_Konnaxion_Security_Gate.md
  - DOC-08_Konnaxion_Runtime_Docker_Compose.md
  - DOC-10_Konnaxion_Builder_CLI.md
---

# DOC-20 — Konnaxion Package Types and Artifact Lifecycle

## 1. Purpose

This document defines the canonical package types used by Konnaxion and the lifecycle rules for creating, verifying, importing, exporting, signing, encrypting, deploying, and retiring those artifacts.

Konnaxion package design separates four concerns:

```text
runtime application
content/data
portable demo bundle
backup/disaster recovery
````

The core rule is:

```text
Runtime is portable.
Data is portable.
Secrets are local.
Deployment configuration is generated on the target.
```

---

## 2. Package Types

Konnaxion uses four package classes:

```text
.kxruntime   Konnaxion runtime package
.kxdata      Konnaxion data/content package
.kxportable  Runtime + data all-in-one package
.kxbackup    Encrypted backup/disaster recovery package
```

Backward compatibility:

```text
.kxcap       legacy runtime capsule alias
```

`.kxcap` remains supported during migration, but new package workflows should use `.kxruntime`, `.kxdata`, and `.kxportable`.

---

## 3. Product Analogy

The intended mental model is:

```text
Konnaxion Runtime   = reader/player
Konnaxion Data Pack = film/content/database
Konnaxion Portable  = reader + film in one transport file
Konnaxion Backup    = protected disaster recovery archive
```

Equivalent analogy:

```text
VLC / DVD player / console = .kxruntime
movie / DVD / cartridge    = .kxdata
demo package               = .kxportable
secure backup              = .kxbackup
```

---

## 4. Responsibilities by Package Type

| Package       | Primary purpose             | Runtime images | Database | Media |        Secrets |
| ------------- | --------------------------- | -------------: | -------: | ----: | -------------: |
| `.kxruntime`  | App/runtime reader          |            yes |       no |    no |             no |
| `.kxdata`     | Content/database pack       |             no |      yes |   yes |             no |
| `.kxportable` | One-file demo/deploy bundle |            yes |      yes |   yes |             no |
| `.kxbackup`   | Encrypted backup/DR archive |       optional |      yes |   yes | encrypted only |

No portable package may contain unencrypted runtime secrets.

---

## 5. `.kxruntime`

## 5.1 Purpose

A `.kxruntime` package contains the Konnaxion application runtime.

It is the “reader” that knows how to run compatible Konnaxion data.

## 5.2 Contains

```text
Django backend image
Next.js frontend image
runtime Docker Compose template
Traefik dynamic routing template
network profiles
healthchecks
Security Gate policies
migrations
runtime manifest
checksums
signature
metadata
```

## 5.3 Must Not Contain

```text
customer database
uploaded media
Django secret key
Postgres password
Redis password
Agent token
SSH private keys
TLS private keys
OAuth client secrets
external API keys
target-machine env files
```

## 5.4 Canonical Layout

```text
manifest.yaml
runtime/
  docker-compose.capsule.yml
  traefik-dynamic.template.yml
  healthchecks/
  profiles/
  policies/
  migrations/
images/
  django-api.oci.tar
  frontend-next.oci.tar
metadata/
  build.json
  source-inventory.json
checksums.txt
signature.sig
```

Optional image archives:

```text
images/media-nginx.oci.tar
images/traefik.oci.tar
images/postgres.oci.tar
images/redis.oci.tar
```

If optional images are not packaged, the manifest must declare whether target-side pulling is allowed.

---

## 6. `.kxdata`

## 6.1 Purpose

A `.kxdata` package contains portable Konnaxion content.

It is the “film” that a Konnaxion runtime can load.

## 6.2 Contains

```text
Postgres dump
media archive
schema version
required app version
required param version
source instance metadata
export metadata
checksums
signature
optional encryption metadata
```

## 6.3 Must Not Contain

```text
runtime Docker images
Django secret key
Postgres password
Redis password
Agent token
SSH private keys
TLS private keys
target-machine env files
Docker Compose runtime state
Traefik generated runtime state
```

## 6.4 Canonical Layout

```text
manifest.yaml
db/
  postgres.dump
media/
  media.tar.zst
metadata/
  source-instance.json
  schema-version.json
  app-version-required.json
  export.json
checksums.txt
signature.sig
```

Optional encrypted layout:

```text
manifest.yaml
payload.enc
metadata/
  encryption.json
checksums.txt
signature.sig
```

---

## 7. `.kxportable`

## 7.1 Purpose

A `.kxportable` package is a single-file bundle containing a runtime package and a data pack.

It is intended for:

```text
client demos
offline transfer
one-file VPS deployment
sales demos
training packs
archival demo snapshots
```

## 7.2 Contains

```text
one .kxruntime
one .kxdata
bundle manifest
checksums
signature
optional encryption metadata
```

## 7.3 Must Not Contain

```text
generated runtime secrets
target machine env files
SSH private keys
TLS private keys
Agent tokens
```

## 7.4 Canonical Layout

```text
manifest.yaml
runtime/
  konnaxion-reader-v14.kxruntime
data/
  demo-citoyen-2026.kxdata
metadata/
  bundle.json
checksums.txt
signature.sig
```

---

## 8. `.kxbackup`

## 8.1 Purpose

A `.kxbackup` package is for disaster recovery and controlled restoration.

It may contain sensitive application data and must be encrypted.

## 8.2 Contains

```text
database dump
media archive
backup metadata
restore metadata
checksums
signature
encryption metadata
```

## 8.3 May Contain

Depending on backup policy, it may contain more operational state than `.kxdata`, but machine secrets should still be excluded by default.

## 8.4 Required Encryption

`.kxbackup` must be encrypted when it contains:

```text
personal data
private organizational data
business data
tokens stored inside application database tables
sensitive media files
```

---

## 9. Manifest Kind Values

Every package must declare its kind.

Allowed values:

```text
konnaxion-runtime
konnaxion-data-pack
konnaxion-portable-bundle
konnaxion-backup
```

Examples:

```yaml
kind: konnaxion-runtime
```

```yaml
kind: konnaxion-data-pack
```

```yaml
kind: konnaxion-portable-bundle
```

```yaml
kind: konnaxion-backup
```

---

## 10. Runtime Manifest Contract

Example:

```yaml
kind: konnaxion-runtime
format_version: 1
runtime_id: konnaxion-reader-v14
app_version: v14
param_version: kx-param-2026.04.30

supported_schema_versions:
  - 2026.04.30
  - 2026.05.02

images:
  django-api:
    archive: images/django-api.oci.tar
    image: konnaxion/django-api:v14
    required: true

  frontend-next:
    archive: images/frontend-next.oci.tar
    image: konnaxion/frontend-next:v14
    required: true

runtime:
  compose_template: runtime/docker-compose.capsule.yml
  traefik_template: runtime/traefik-dynamic.template.yml
  profiles_dir: runtime/profiles
  policies_dir: runtime/policies
  healthchecks_dir: runtime/healthchecks

security:
  signed: true
  checksums: checksums.txt
  contains_runtime_secrets: false
```

---

## 11. Data Pack Manifest Contract

Example:

```yaml
kind: konnaxion-data-pack
format_version: 1
data_pack_id: demo-citoyen-2026
title: Demo Citoyen 2026

app_version_required: v14
param_version_required: kx-param-2026.04.30
schema_version: 2026.05.02

database:
  engine: postgres
  dump: db/postgres.dump
  format: pg_dump_custom

media:
  archive: media/media.tar.zst
  format: tar.zst

security:
  signed: true
  encrypted: false
  checksums: checksums.txt
  contains_runtime_secrets: false
  contains_user_data: true

export:
  exported_at: 2026-05-03T00:00:00Z
  source_instance_id: demo-001
  anonymized: false
```

---

## 12. Portable Bundle Manifest Contract

Example:

```yaml
kind: konnaxion-portable-bundle
format_version: 1
bundle_id: demo-citoyen-2026-v14

runtime:
  file: runtime/konnaxion-reader-v14.kxruntime
  app_version: v14
  param_version: kx-param-2026.04.30

data:
  file: data/demo-citoyen-2026.kxdata
  schema_version: 2026.05.02

security:
  signed: true
  encrypted: false
  checksums: checksums.txt
  contains_runtime_secrets: false
  first_boot_generates_secrets: true
```

---

## 13. Backup Manifest Contract

Example:

```yaml
kind: konnaxion-backup
format_version: 1
backup_id: demo-001-2026-05-03

source:
  instance_id: demo-001
  app_version: v14
  param_version: kx-param-2026.04.30
  schema_version: 2026.05.02

database:
  engine: postgres
  dump: db/postgres.dump
  format: pg_dump_custom

media:
  archive: media/media.tar.zst

security:
  signed: true
  encrypted: true
  encryption_required: true
  checksums: checksums.txt
  contains_runtime_secrets: false
  contains_sensitive_data: true

backup:
  created_at: 2026-05-03T00:00:00Z
  mode: disaster_recovery
```

---

## 14. Secret Rules

## 14.1 Core Rule

Runtime secrets are generated on the target machine.

They must not be packaged into:

```text
.kxruntime
.kxdata
.kxportable
```

## 14.2 Forbidden Secret Files

Packages must fail verification if they contain:

```text
.env
*.env
agent.env
django.env
postgres.env
redis.env
runtime.env
id_rsa
id_ed25519
*.pem
*.key
*.p12
*.pfx
```

Allowed exceptions:

```text
env-templates/*.template
public keys
public certificate chain files
example env files with placeholders
```

## 14.3 Forbidden Secret Content

Packages must fail verification if unencrypted package contents contain:

```text
DJANGO_SECRET_KEY=
POSTGRES_PASSWORD=
REDIS_PASSWORD=
DATABASE_URL=postgres://
KX_AGENT_TOKEN=
PRIVATE KEY
BEGIN OPENSSH PRIVATE KEY
BEGIN RSA PRIVATE KEY
BEGIN EC PRIVATE KEY
```

`.kxbackup` may contain sensitive application data only when encrypted.

---

## 15. Signature Rules

All package types must support signatures.

Signature proves:

```text
publisher identity
integrity
authenticity
```

Signature does not provide confidentiality.

Every signed package must include:

```text
checksums.txt
signature.sig
```

Verification order:

```text
read manifest
verify checksums
verify signature
verify package policy
verify package compatibility
```

---

## 16. Encryption Rules

Encryption is:

```text
optional for .kxruntime
recommended for .kxdata
optional for .kxportable demo use
required for sensitive .kxbackup
```

Supported encryption modes:

```text
password-derived key
recipient public key
age X25519
```

Encryption metadata example:

```yaml
encryption:
  enabled: true
  method: age-x25519
  recipients:
    - age1example...
```

Import must fail if encrypted package cannot be decrypted.

---

## 17. Compatibility Rules

## 17.1 Required Version Fields

Every `.kxdata` must declare:

```text
app_version_required
param_version_required
schema_version
```

Every `.kxruntime` must declare:

```text
app_version
param_version
supported_schema_versions
```

## 17.2 Compatibility Check

A runtime may load a data pack only if:

```text
runtime.app_version satisfies data.app_version_required
runtime.param_version satisfies data.param_version_required
runtime.supported_schema_versions includes data.schema_version
```

## 17.3 Incompatible Import

If incompatible, the Agent must block import and return:

```yaml
ok: false
reason: incompatible_runtime
required_app_version: <value>
current_app_version: <value>
required_schema_version: <value>
supported_schema_versions: [...]
```

## 17.4 Migration

If a migration path exists, the Agent may offer:

```text
import and migrate
```

Migration of production data must require explicit operator confirmation.

---

## 18. Artifact Lifecycle

All Konnaxion packages follow this lifecycle:

```text
build/export
verify
sign
optionally encrypt
store
transfer
import
validate compatibility
generate target secrets
render target runtime
start
audit
retire
```

Package state values:

```text
created
verified
signed
encrypted
imported
active
deprecated
revoked
archived
deleted
```

---

## 19. Build Lifecycle

## 19.1 Runtime Build

```text
1. Build app images.
2. Export app images as OCI archives.
3. Write runtime templates.
4. Write runtime manifest.
5. Write checksums.
6. Sign package.
7. Return .kxruntime.
```

## 19.2 Data Pack Export

```text
1. Validate source instance.
2. Enter export-safe mode.
3. Run pg_dump.
4. Archive media.
5. Write manifest.
6. Write metadata.
7. Write checksums.
8. Sign package.
9. Optionally encrypt.
10. Return .kxdata.
```

## 19.3 Portable Build

```text
1. Verify runtime package.
2. Verify data pack.
3. Check compatibility.
4. Write bundle manifest.
5. Write checksums.
6. Sign bundle.
7. Optionally encrypt.
8. Return .kxportable.
```

## 19.4 Backup Build

```text
1. Validate source instance.
2. Run backup preflight.
3. Dump database.
4. Archive media.
5. Write backup metadata.
6. Encrypt package.
7. Sign package.
8. Store backup.
9. Verify restoreability.
```

---

## 20. Import Lifecycle

## 20.1 Runtime Import

```text
1. Verify package.
2. Verify no runtime secrets are present.
3. Load Docker images.
4. Store runtime metadata.
5. Register runtime version.
```

## 20.2 Data Pack Import

```text
1. Verify package.
2. Decrypt if required.
3. Verify no runtime secrets are present.
4. Check runtime compatibility.
5. Create target instance.
6. Generate target secrets.
7. Start database services.
8. Restore Postgres dump.
9. Restore media.
10. Run migrations if required.
11. Render runtime config.
12. Start app services.
13. Run Security Gate.
```

## 20.3 Portable Import

```text
1. Verify bundle.
2. Extract runtime package.
3. Extract data pack.
4. Verify runtime package.
5. Verify data pack.
6. Check compatibility.
7. Import runtime.
8. Import data pack.
9. Generate target secrets.
10. Render target runtime.
11. Start instance.
12. Run Security Gate.
```

## 20.4 Backup Restore

```text
1. Verify backup.
2. Decrypt backup.
3. Verify restore policy.
4. Create restore target.
5. Generate target secrets unless policy says otherwise.
6. Restore DB and media.
7. Run migrations if approved.
8. Start in safe network profile.
9. Run Security Gate.
```

---

## 21. Target Machine State

Imported packages and instances must be stored under canonical paths:

```text
/opt/konnaxion/
  runtimes/
    <runtime_id>/
  data-packs/
    imported/
  portable/
    imported/
  backups/
  instances/
    <instance_id>/
      env/
      state/
      media/
      postgres/
      redis/
      logs/
```

Runtime secrets are stored only under instance or Agent env directories.

---

## 22. Manager Responsibilities

The Konnaxion Capsule Manager is responsible for:

```text
building packages
verifying packages
signing packages
encrypting packages when requested
uploading packages to target
calling Agent APIs
displaying package status
showing import/export diagnostics
coordinating deployments
```

The Manager must not:

```text
directly edit target Docker runtime manually
directly open firewalls
embed secrets in packages
require a local tunnel for private Droplet Agent access
```

For Droplet/VPS deployments, the Manager must use SSH-local Agent transport when the Agent listens on target loopback.

---

## 23. Agent Responsibilities

The Konnaxion Agent is responsible for:

```text
verifying uploaded packages
importing runtime packages
loading Docker images
importing data packs
restoring DB/media
generating target secrets
rendering Docker Compose runtime files
rendering Traefik dynamic config
starting/stopping instances
running Security Gate
writing audit logs
```

The Agent must reject packages that:

```text
contain runtime secrets
fail signature/checksum verification
declare unsupported versions
attempt to expose forbidden ports
attempt to mount Docker socket
attempt to run unknown services
```

---

## 24. Builder Responsibilities

The Konnaxion Builder is responsible for:

```text
building runtime packages
exporting runtime images as OCI tar archives
building data packs from source instances
building portable bundles
writing manifests
writing checksums
signing packages
verifying package completeness
```

The Builder must fail if:

```text
required runtime images are missing
manifest references missing files
checksums are incomplete
signature is required but no signing key is available
forbidden secret files are included
```

---

## 25. Security Gate Integration

Security Gate must validate package and runtime behavior.

Package-level checks:

```text
manifest_valid
checksums_valid
signature_valid
package_type_valid
version_compatible
no_forbidden_secret_files
no_forbidden_secret_markers
required_payloads_present
```

Runtime-level checks:

```text
required_images_present
compose_template_valid
traefik_file_provider_required
docker_socket_not_required
dangerous_ports_blocked
```

Data-level checks:

```text
db_dump_present
db_dump_restoreable
media_archive_valid
schema_version_declared
app_version_required_declared
```

Instance-level checks:

```text
secrets_present
secrets_not_default
runtime_env_host_matches_profile
traefik_dynamic_host_matches_profile
frontend_public_env_matches_profile
postgres_not_public
redis_not_public
no_privileged_containers
no_host_network
```

---

## 26. Audit Events

Every package operation must write an audit event.

Event types:

```text
package_built
package_verified
package_signed
package_encrypted
package_uploaded
package_imported
data_pack_exported
data_pack_imported
portable_bundle_built
portable_bundle_imported
backup_created
backup_restored
package_rejected
package_deleted
```

Canonical audit fields:

```yaml
event_type: package_imported
package_type: konnaxion-data-pack
package_id: demo-citoyen-2026
instance_id: demo-001
actor: <operator_or_system>
timestamp: <ISO8601>
result: PASS
details: {}
```

---

## 27. CLI Contract

Builder commands:

```bash
kx-builder runtime build \
  --source-dir <repo> \
  --output konnaxion-reader-v14.kxruntime

kx-builder data-pack build \
  --instance-id demo-001 \
  --output demo-citoyen-2026.kxdata

kx-builder portable build \
  --runtime konnaxion-reader-v14.kxruntime \
  --data demo-citoyen-2026.kxdata \
  --output demo-citoyen-2026-v14.kxportable
```

Agent/CLI commands:

```bash
kx runtime import konnaxion-reader-v14.kxruntime

kx data-pack import demo-citoyen-2026.kxdata \
  --instance-id demo-001

kx portable import demo-citoyen-2026-v14.kxportable \
  --instance-id demo-001 \
  --network-profile public_vps \
  --host 138.197.174.76.sslip.io
```

Backup commands:

```bash
kx backup create demo-001 \
  --output demo-001-2026-05-03.kxbackup \
  --encrypt

kx backup restore demo-001-2026-05-03.kxbackup \
  --instance-id restored-demo-001
```

---

## 28. GUI Contract

The Manager GUI must expose a package module.

Recommended page:

```text
Packages
```

Tabs:

```text
Runtime
Data Packs
Portable Bundles
Backups
```

Runtime actions:

```text
Build Runtime
Verify Runtime
Deploy Runtime
Update Runtime
```

Data Pack actions:

```text
Export Data Pack
Verify Data Pack
Import Data Pack
Create Instance from Data Pack
Anonymize Data Pack
Encrypt Data Pack
```

Portable Bundle actions:

```text
Build Portable Bundle
Verify Portable Bundle
Import Portable Bundle
Deploy Portable Bundle to Droplet/VPS
Split Bundle into Runtime + Data
```

Backup actions:

```text
Create Backup
Verify Backup
Restore Backup
Test Restore
```

---

## 29. Backward Compatibility

`.kxcap` compatibility rules:

```text
.kxcap with runtime images -> treat as legacy .kxruntime
.kxcap without runtime images -> fail strict verify or warn in legacy mode
.kxcap with DB/media content -> split into .kxruntime + .kxdata or convert to .kxportable
```

Deprecation warning:

```text
.kxcap is a legacy runtime capsule extension. Use .kxruntime for new runtime builds.
```

Strict mode should eventually require:

```text
required images/*.oci.tar are present
manifest declares package kind
no runtime secrets are present
```

---

## 30. Acceptance Criteria

`.kxruntime` acceptance:

```text
build succeeds
required images are present
verify fails when required images are missing
no runtime secrets are present
Agent can import runtime
Agent can docker load app images
```

`.kxdata` acceptance:

```text
export succeeds
database dump exists
media archive exists or no-media marker exists
manifest declares app/schema compatibility
verify fails if runtime secrets are detected
Agent can import into new instance
```

`.kxportable` acceptance:

```text
contains one valid runtime
contains one valid data pack
runtime/data compatibility passes
import generates fresh secrets
import starts app without manual Docker build
```

`.kxbackup` acceptance:

```text
encrypted when sensitive
verify succeeds
test restore succeeds
restore starts in safe network profile
```

---

## 31. Final Rule

Konnaxion package design must preserve this separation:

```text
.kxruntime  = the reader
.kxdata     = the content
.kxportable = reader + content
.kxbackup   = protected recovery archive
```

No portable package may depend on source-machine runtime secrets.

No target deployment may depend on source-machine host configuration.

The Agent must generate secrets and deployment-specific configuration on the target machine.



---

## 21. Installed artifact and composition boundary (2026-09-08)

Package type and artifact identity are separate dimensions. A runtime/data
package may carry an installable artifact descriptor, but Capsule does not own
product UX composition.

Canonical installable artifact contract, registry, discovery and removal rules
are defined in:

```text
DOC-23_Capsule_Installed_Artifact_and_Composition_Contract.md
```

New runtime capsules built from source include `artifact.yaml`. Product-owned
integrated UI manifests remain separate under `contributions/` and are only
admitted/discovered by Capsule; they are composed by kOA Spaces.
