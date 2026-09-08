---
doc_id: DOC-14
title: Konnaxion Operator Guide
project: Konnaxion
app_version: v14
param_version: kx-param-2026.04.30
status: draft
owner: Konnaxion
last_updated: 2026-04-30
audience:
  - operators
  - demo facilitators
  - local administrators
  - support staff
depends_on:
  - DOC-00_Konnaxion_Canonical_Variables.md
  - DOC-02_Konnaxion_Capsule_Architecture.md
  - DOC-03_Konnaxion_Capsule_Format.md
  - DOC-04_Konnaxion_Manager_Architecture.md
  - DOC-05_Konnaxion_Agent_Security_Model.md
  - DOC-06_Konnaxion_Network_Profiles.md
  - DOC-07_Konnaxion_Security_Gate.md
  - DOC-08_Konnaxion_Runtime_Docker_Compose.md
  - DOC-09_Konnaxion_Backup_Restore_Rollback.md
---

# DOC-14 — Konnaxion Operator Guide

## 1. Purpose

This document explains how to operate a **Konnaxion Instance** using the **Konnaxion Capsule Manager**.

It is written for operators, demo facilitators, support staff, and local administrators.

The operator should not need to understand Docker, Traefik, PostgreSQL, Redis, Celery, Django, Next.js, firewall rules, or environment files.

The normal operating model is:

```text
Open Konnaxion Capsule Manager
Choose an instance
Start / stop / backup / update / restore
Use the displayed URL
Respect Security Gate results
```

---

## 2. Operator Scope

Operators may:

```text
start Konnaxion
stop Konnaxion
restart Konnaxion
view instance status
view safe logs
run Security Gate checks
change approved network profiles
create backups
restore approved backups
apply verified capsule updates
rollback after failed update
export support bundles
```

Operators must not:

```text
edit Docker Compose files manually
edit firewall rules manually
edit .env files manually
open internal ports manually
run arbitrary containers
mount Docker socket
disable Security Gate
import unsigned capsules
restore old compromised disks
copy secrets into chat, tickets, or logs
```

---

## 3. Canonical Terms

Use these names consistently.

| Term | Meaning |
|---|---|
| **Konnaxion Capsule** | Signed `.kxcap` package containing app images, manifest, profiles, templates, and checksums |
| **Konnaxion Capsule Manager** | User-facing app used by operators |
| **Konnaxion Agent** | Local privileged service that performs approved actions |
| **Konnaxion Instance** | Installed runtime instance with data, secrets, media, logs, and backups |
| **Konnaxion Box** | Dedicated machine running the Manager and the instance |
| **Security Gate** | Blocking safety validation before start/update/network exposure |
| **Network Profile** | Predefined exposure mode such as `local_only`, `intranet_private`, or `public_temporary` |

---

## 4. Default Safety Position

The default state is private.

```env
KX_NETWORK_PROFILE=intranet_private
KX_EXPOSURE_MODE=private
KX_PUBLIC_MODE_ENABLED=false
```

The Manager must not expose Konnaxion publicly unless the operator explicitly chooses an approved public profile.

The preferred operating profiles are:

```text
local_only
intranet_private
private_tunnel
```

The high-risk profiles are:

```text
public_temporary
public_vps
```

High-risk profiles require Security Gate validation before activation.

---

## 5. Operator Dashboard

The Manager home screen must show:

```text
Instance name
Lifecycle state
Network profile
Exposure mode
Primary URL
Security Gate status
Backup status
App version
Capsule version
Last health check
Last backup
```

Example:

```text
Instance: demo-001
State: running
Network: intranet_private
Exposure: private
URL: https://konnaxion.local
Security: PASS
Backups: enabled
App Version: v14
Capsule: konnaxion-v14-demo-2026.04.30
```

Required buttons:

```text
Open Konnaxion
Start
Stop
Restart
Security Check
View Logs
Backup
Restore
Update
Change Network Profile
Export Support Bundle
```

---

## 6. Lifecycle States

The operator may see these states.

| State | Meaning | Operator action |
|---|---|---|
| `created` | Instance exists but has not been verified | Run verification |
| `verifying` | Capsule or instance checks are running | Wait |
| `ready` | Instance can be started | Start |
| `starting` | Runtime is starting | Wait |
| `running` | Instance is online | Operate normally |
| `stopping` | Instance is shutting down | Wait |
| `stopped` | Instance is offline | Start or leave stopped |
| `updating` | Update is in progress | Do not interrupt |
| `rolling_back` | Rollback is in progress | Do not interrupt |
| `degraded` | Instance is running with warnings | Review health/logs |
| `failed` | Instance failed | Export support bundle |
| `security_blocked` | Security Gate blocked action | Do not bypass; fix cause |

---

## 7. Daily Operating Procedure

### 7.1 Start of Day

1. Open **Konnaxion Capsule Manager**.
2. Confirm the correct `Konnaxion Instance` is selected.
3. Confirm the intended `KX_NETWORK_PROFILE`.
4. Run **Security Check**.
5. Confirm result is `PASS`.
6. Click **Start**.
7. Wait for state `running`.
8. Click **Open Konnaxion**.
9. Confirm the Konnaxion login/home page loads.

CLI equivalent:

```bash
kx instance status demo-001
kx security check demo-001
kx instance start demo-001
kx instance status demo-001
```

Success criteria:

```text
State: running
Security: PASS
Primary URL responds
No public internal ports
Last backup status visible
```

---

### 7.2 End of Day

1. Confirm no active demo or user session is required.
2. Create a backup.
3. Wait for backup verification.
4. Stop the instance if the box is not needed overnight.
5. Confirm state is `stopped`.

CLI equivalent:

```bash
kx instance backup demo-001
kx instance stop demo-001
kx instance status demo-001
```

Success criteria:

```text
Backup completed
Backup verified
State: stopped
No public temporary tunnel active
```

---

## 8. Starting an Instance

### 8.1 Normal Start

Use the Manager button:

```text
Start
```

The Manager must automatically run:

```text
manifest validation
signature check
image checksum validation
secret presence check
firewall check
dangerous port check
runtime health check
```

If all required checks pass, the instance starts.

### 8.2 Start Blocked by Security Gate

If startup is blocked, the Manager displays:

```text
State: security_blocked
Security result: FAIL_BLOCKING
```

Operator action:

```text
Do not retry blindly.
Read the failed check.
Export support bundle if the cause is not obvious.
Escalate to technical maintainer.
```

Do not use manual Docker commands to bypass the block.

---

## 9. Stopping an Instance

Use the Manager button:

```text
Stop
```

The Manager must stop services in a safe order:

```text
public tunnel if enabled
Traefik exposure
frontend-next
django-api
celerybeat
celeryworker
redis
postgres
media-nginx
```

The operator should wait for:

```text
State: stopped
```

CLI equivalent:

```bash
kx instance stop demo-001
```

---

## 10. Restarting an Instance

Use restart when:

```text
the app is sluggish
a configuration profile was changed
a support instruction requests restart
the instance is degraded but not failed
```

Do not restart during:

```text
backup
restore
update
rollback
migration
```

CLI equivalent:

```bash
kx instance stop demo-001
kx instance start demo-001
```

Success criteria:

```text
State returns to running
Health is healthy or acceptable
Security remains PASS
```

---

## 11. Network Profile Operations

The operator must choose only predefined network profiles.

| Profile | Use case | Exposure |
|---|---|---|
| `local_only` | Demo on the Konnaxion Box itself | Local machine only |
| `intranet_private` | LAN/intranet demo | Local network only |
| `private_tunnel` | Private remote access | VPN/tailnet only |
| `public_temporary` | Time-limited external demo | Temporary public tunnel |
| `public_vps` | Managed public server | Public 80/443 only |
| `offline` | No network access | No external access |

---

### 11.1 Local Only

Use for:

```text
testing before a demo
private local review
offline presentation
```

Expected URL:

```text
https://localhost
```

Allowed exposure:

```text
localhost only
```

Operator checks:

```text
No LAN URL shown
No public URL shown
No tunnel active
```

---

### 11.2 Intranet Private

Use for:

```text
school network
office network
community center
private LAN demo
```

Expected URL examples:

```text
https://konnaxion.local
https://konnaxion.lan
```

Allowed exposure:

```text
LAN HTTPS only
```

Operator checks:

```text
Internet exposure: disabled
Allowed LAN port: 443
Postgres: blocked
Redis: blocked
Docker socket: blocked
```

---

### 11.3 Private Tunnel

Use for:

```text
remote team demo
trusted collaborator access
support session
```

Allowed exposure:

```text
private VPN/tailnet only
```

Operator checks:

```text
No router port forwarding required
Access limited to approved users/devices
Public URL not displayed unless explicitly configured
```

---

### 11.4 Public Temporary

Use only for:

```text
client demo
public preview
external stakeholder review
```

Required conditions:

```text
expiration time set
Security Gate PASS
authentication enabled if available
operator confirms public exposure
no internal service exposed
```

Required variables:

```env
KX_PUBLIC_MODE_ENABLED=true
KX_PUBLIC_MODE_EXPIRES_AT=<timestamp>
```

The Manager must reject public temporary mode if `KX_PUBLIC_MODE_EXPIRES_AT` is empty.

Operator checklist:

```text
Set duration: 1h / 2h / 8h
Confirm generated public URL
Send only the public URL
Monitor session
Disable public mode after demo
Confirm tunnel closed
```

CLI equivalent:

```bash
kx network set-profile demo-001 public_temporary --duration-hours 2
kx security check demo-001
```

---

### 11.5 Public VPS

Use only for a production-style environment.

Required conditions:

```text
clean VPS or trusted host
SSH key only
firewall enabled
80/443 only public
22 restricted
Security Gate PASS
backups enabled
monitoring enabled
```

This profile is not the default demo mode.

---

## 12. Security Rules for Operators

Operators must remember one rule:

```text
Only the Manager exposes Konnaxion.
Never expose internal services directly.
```

Always blocked:

```text
3000/tcp  frontend-next direct
5000/tcp  django-api direct
5432/tcp  PostgreSQL
6379/tcp  Redis
5555/tcp  Flower/dashboard
8000/tcp  Django development server
Docker daemon TCP
```

Allowed through Traefik only:

```text
/
 /api/
 /admin/
 /media/
```

If a user asks to connect to Postgres, Redis, or Docker remotely, escalate to a technical maintainer.

Do not open the ports manually.

---

## 13. Security Gate Results

### 13.1 PASS

Meaning:

```text
All required checks passed.
Operation may continue.
```

Action:

```text
Proceed.
```

### 13.2 WARN

Meaning:

```text
Non-blocking issue detected.
```

Action:

```text
Proceed only if the warning is understood.
Document the warning in the operator log.
Escalate if repeated.
```

### 13.3 FAIL_BLOCKING

Meaning:

```text
The operation is unsafe.
```

Action:

```text
Do not proceed.
Do not bypass.
Export support bundle.
Escalate.
```

### 13.4 SKIPPED

Meaning:

```text
Check does not apply to this profile.
```

Action:

```text
No action unless unexpected.
```

### 13.5 UNKNOWN

Meaning:

```text
The Manager could not verify the check.
```

Action:

```text
Treat as suspicious if related to firewall, ports, secrets, or signatures.
Escalate if it affects startup or public exposure.
```

---

## 14. Logs

Operators may view logs through the Manager.

Allowed log categories:

```text
manager
agent
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

The Manager must redact secrets.

Never copy full values of:

```text
DJANGO_SECRET_KEY
POSTGRES_PASSWORD
DATABASE_URL
API keys
tokens
private keys
session secrets
SSH keys
```

Safe support excerpt:

```text
timestamp
service
error code
redacted message
instance ID
capsule version
network profile
```

Unsafe support excerpt:

```text
full .env file
full DATABASE_URL
secret keys
private keys
tokens
passwords
```

---

## 15. Backups

Backups are mandatory operational safety controls, not optional convenience features.

The operator-facing rule is:

```text
Create backup.
Verify backup.
Only then update, restore, rollback, or perform risky maintenance.
```

### 15.1 Backup Scope

A valid Konnaxion backup includes:

```text
PostgreSQL logical dump
media files
instance metadata
capsule reference
network profile reference
redacted environment metadata
backup manifest
checksums
verification result
healthcheck snapshot
```

A normal operator backup must not include:

```text
temporary files
runtime sockets
unknown Docker volumes
raw old disk images
/tmp
/dev/shm
crontabs
authorized_keys
sudoers files
Docker daemon state
Docker socket
unredacted logs containing secrets
```

The operator must not attempt to recover Konnaxion by restoring a full old server image. Restore must use a verified backup set plus a trusted capsule.

---

### 15.2 Backup Classes

Operators may see these backup classes:

| Class | Meaning |
|---|---|
| `daily` | Automatic routine backup |
| `weekly` | Automatic longer-retention backup |
| `monthly` | Automatic archive backup |
| `pre-update` | Created before applying a capsule update |
| `pre-restore` | Created before restoring over an existing instance |
| `manual` | Created by operator action |

---

### 15.3 Create Backup

Use the Manager button:

```text
Backup
```

Recommended operator flow:

```text
1. Open Konnaxion Capsule Manager.
2. Select the instance.
3. Click Backup.
4. Choose backup class: manual.
5. Wait for backup creation.
6. Wait for verification.
7. Confirm Backup health: PASS.
```

The Manager must show:

```text
Backup ID
backup class
created_at
capsule version
database size
media size
checksum result
verification result
```

CLI equivalent:

```bash
kx instance backup demo-001 --class manual
kx backup verify <BACKUP_ID>
```

Success criteria:

```text
Backup status: verified
Verification: PASS
Backup visible in backup list
No secret leak warning
No forbidden path warning
```

---

### 15.4 Verify Backup

Every backup must be verifiable before it is trusted.

Operator action:

```text
Open Backups
Select latest backup
Confirm Verification: PASS
Confirm Security: PASS or WARN only
```

CLI equivalent:

```bash
kx backup verify <BACKUP_ID>
```

If verification fails:

```text
Do not use this backup for restore.
Do not delete the last known-good backup.
Create another backup.
Export support bundle.
Escalate if repeated.
```

If verification reports leaked secrets:

```text
Do not export or share the backup.
Quarantine the backup.
Escalate.
Rotate affected secrets.
```

---

### 15.5 Test Restore

When time allows, the safest validation is a test restore into a temporary local-only instance.

Operator action:

```text
Open Backups
Select backup
Click Test Restore
Choose temporary instance
Confirm network profile: local_only
Run test
Destroy temporary instance after PASS
```

CLI equivalent:

```bash
kx backup test-restore <BACKUP_ID> \
  --temporary-instance restore-test-YYYYMMDD_HHMMSS \
  --network local_only \
  --destroy-after-pass
```

Success criteria:

```text
Temporary instance created
Restore completed
Security Gate: PASS
Health: healthy
Temporary instance destroyed after test, if requested
```

---

### 15.6 Restore Backup Into New Instance

This is the preferred restore method for high-risk recovery.

Use this when:

```text
the current instance may be damaged
the operator wants to validate data before replacing current state
the host was recently rebuilt
support requests a safe restore
```

Operator flow:

```text
1. Open Backups.
2. Select a verified backup.
3. Click Restore.
4. Choose Restore into new instance.
5. Set network profile to local_only or intranet_private.
6. Run restore.
7. Run Security Gate.
8. Run healthchecks.
9. Switch users to the restored instance only after PASS.
```

CLI equivalent:

```bash
kx instance restore-new \
  --from <BACKUP_ID> \
  --new-instance-id demo-restore-001 \
  --network local_only
```

Success criteria:

```text
New instance created
Restore completed
Security: PASS
Health: healthy
Data appears correct
Original instance remains unchanged
```

---

### 15.7 Restore Backup Over Existing Instance

Restoring over an existing instance is allowed, but it is riskier.

Use only when:

```text
data was accidentally deleted
support confirms the restore path
restore-new is not practical
operator accepts downtime
```

Before restore, the Manager must create a `pre-restore` backup unless the instance is already unrecoverable.

Operator flow:

```text
1. Select backup.
2. Confirm Verification: PASS.
3. Confirm selected target instance.
4. Confirm what will be overwritten.
5. Confirm pre-restore backup will be created.
6. Type the required confirmation phrase.
7. Run restore.
8. Run Security Gate.
9. Run healthchecks.
```

Required confirmation phrase:

```text
RESTORE demo-001
```

CLI equivalent:

```bash
kx instance restore demo-001 \
  --from <BACKUP_ID> \
  --mode full
```

Success criteria:

```text
Pre-restore backup created
Database restored
Media restored
Migrations completed if required
Security: PASS
Health: healthy
User data appears correct
```

---

### 15.8 Backup Failure

If backup fails:

```text
Do not update.
Do not restore.
Do not delete older backups.
Check logs.
Export support bundle.
Escalate if repeated.
```

If the failed backup was required before an update, the update must be cancelled.

---

## 16. Updates

### 16.1 Update Capsule

Use the Manager button:

```text
Update
```

Update flow:

```text
1. Verify new capsule signature.
2. Verify new capsule checksums.
3. Check compatibility.
4. Create pre-update backup.
5. Verify pre-update backup.
6. Stage update.
7. Apply new capsule.
8. Run migrations if required.
9. Start updated instance.
10. Run Security Gate.
11. Run healthchecks.
12. Mark update successful only after PASS.
```

Operator must confirm:

```text
New capsule is signed
Pre-update backup completed
Pre-update backup verification passed
Compatibility check passed
Security Gate passed
Rollback point exists
```

CLI equivalent:

```bash
kx capsule verify konnaxion-v14-demo-YYYY.MM.DD.kxcap

kx instance update demo-001 \
  --capsule konnaxion-v14-demo-YYYY.MM.DD.kxcap \
  --auto-rollback true
```

Success criteria:

```text
State: running
Security: PASS
Health: healthy
Backup: verified
Current capsule updated
Previous capsule kept as rollback point
```

---

### 16.2 Update Failure

If update fails, the Manager may show:

```text
State: failed
State: degraded
State: rolling_back
State: security_blocked
```

Operator action:

```text
Do not force start.
Do not expose publicly.
Wait for automatic capsule rollback if active.
Check Security Gate result.
Export support bundle.
Escalate if rollback fails.
```

If the update failed before database migrations changed data, capsule rollback is usually enough.

If the update changed database or media state, support may instruct the operator to perform data rollback from the pre-update backup.

---

### 16.3 Update With Public Exposure

If the instance is using `public_temporary` or `public_vps`, the Manager must reduce risk during update.

Required behavior:

```text
create pre-update backup
verify backup
pause or close public temporary tunnel if applicable
apply update
run Security Gate
run healthchecks
restore exposure only after PASS
```

Operator must not manually re-enable public exposure after a failed update.

---

## 17. Rollback

Rollback returns the instance to a previous known-good state.

There are three operator-visible rollback levels:

| Level | Meaning | Default use |
|---|---|---|
| Capsule rollback | Return to previous capsule version | First response after failed update |
| Data rollback | Restore database/media from backup | Only when data changed or became corrupted |
| Full rollback | Restore capsule reference plus data backup | Last resort after failed update |

---

### 17.1 Capsule Rollback

Capsule rollback is the safest first rollback.

Use when:

```text
update fails
healthchecks fail after update
critical feature is broken after update
Security Gate blocks the new capsule
support instructs rollback
```

CLI equivalent:

```bash
kx instance rollback demo-001 --level capsule
```

Success criteria:

```text
Previous capsule restored
Database unchanged unless required
State: running
Security: PASS
Health: healthy
User data consistent
```

---

### 17.2 Data Rollback

Data rollback restores database and/or media from a verified backup.

Use when:

```text
data was corrupted
migration changed data and update failed
media changed during a failed update
support instructs data rollback
```

CLI equivalent:

```bash
kx instance rollback demo-001 \
  --level data \
  --from <BACKUP_ID>
```

Success criteria:

```text
Pre-rollback backup created
Selected backup verified
Database/media restored
Security: PASS
Health: healthy
```

---

### 17.3 Full Rollback

Full rollback combines capsule rollback and data rollback.

Use only when:

```text
capsule rollback did not fix the instance
database or media state is incompatible with the previous capsule
support confirms full rollback is required
```

CLI equivalent:

```bash
kx instance rollback demo-001 \
  --level full \
  --from <BACKUP_ID>
```

Success criteria:

```text
Previous capsule restored
Backup data restored
Security: PASS
Health: healthy
Network profile remains safe
```

Rollback does not replace backups. A rollback path is only safe when backup verification has passed.

---

## 18. Health Checks

The Manager should show health for:

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

Canonical health values:

```text
healthy
degraded
unhealthy
unknown
stopped
```

Operator response:

| Health | Action |
|---|---|
| `healthy` | Continue |
| `degraded` | Check logs and monitor |
| `unhealthy` | Restart if safe, then escalate |
| `unknown` | Run Security Check and health refresh |
| `stopped` | Start if expected |

---

## 19. Common Problems

### 19.1 Konnaxion URL Does Not Load

Check:

```text
Instance state is running
Network profile is correct
Primary URL is correct
Traefik health is healthy
frontend-next health is healthy
django-api health is healthy
```

Operator action:

```text
Run health check
Restart if no backup/update is running
Export support bundle if unresolved
```

---

### 19.2 Login Page Loads but API Fails

Likely affected services:

```text
django-api
postgres
redis
Traefik route /api/
```

Operator action:

```text
Check django-api logs
Check postgres health
Check Traefik health
Do not expose backend directly
Escalate if unresolved
```

---

### 19.3 Background Tasks Not Running

Likely affected services:

```text
redis
celeryworker
celerybeat
```

Operator action:

```text
Check worker health
Check redis health
Restart instance if safe
Escalate if repeated
```

---

### 19.4 Backup Fails

Operator action:

```text
Check available disk space in Manager
Check postgres health
Check media size
Retry once
Escalate if repeated
```

Do not delete old backups until a new verified backup exists.

---

### 19.5 Security Gate Blocks Public Mode

Operator action:

```text
Do not bypass.
Read failed checks.
Return to intranet_private if demo can continue privately.
Export support bundle.
Escalate.
```

---

## 20. Emergency Procedures

### 20.1 Suspected Compromise

Indicators:

```text
unknown containers
unknown Docker images
unexpected public ports
unknown admin users
unexpected sudoers entries
unexpected cron jobs
/tmp or /dev/shm executables
miner-like CPU usage
unexpected outbound network traffic
security checks failing suddenly
```

Immediate action:

```text
1. Disable public_temporary or public_vps exposure.
2. Disconnect from public network if needed.
3. Stop Konnaxion Instance.
4. Do not delete evidence unless instructed.
5. Export support bundle.
6. Rotate secrets if exposure is confirmed.
7. Rebuild on clean host if compromise is credible.
```

Never trust a compromised host as the long-term fix.

---

### 20.2 Public Tunnel Left Open

Action:

```text
Open Network Profile screen
Disable public_temporary
Confirm KX_PUBLIC_MODE_ENABLED=false
Confirm public URL no longer works
Run Security Check
```

CLI equivalent:

```bash
kx network set-profile demo-001 intranet_private
kx security check demo-001
```

---

### 20.3 Lost Admin Password

Action:

```text
Use Manager's Reset Admin flow if available.
Require local operator confirmation.
Generate temporary password.
Force password change at next login if supported.
Log the action.
```

Do not expose database or shell access to reset passwords manually unless a technical maintainer authorizes it.

---

### 20.4 Host Will Be Retired

Before retiring a Konnaxion Box:

```text
Create final verified backup
Export backup to approved storage
Stop instance
Remove public tunnel
Wipe secrets if decommissioning
Record capsule version and backup ID
```

---

## 21. Support Bundle

Operators may export a support bundle.

Allowed contents:

```text
instance.yaml
manager version
agent version
capsule ID
capsule version
network profile
Security Gate results
health summary
redacted logs
backup metadata
runtime service list
redacted compose summary
```

Forbidden contents:

```text
full .env files
private keys
tokens
database passwords
Django secret key
raw database dump
user-uploaded media unless explicitly requested
unredacted logs
```

Support bundle filename:

```text
konnaxion-support-<INSTANCE_ID>-<YYYYMMDD-HHMMSS>.zip
```

---

## 22. Operator Checklists

### 22.1 Pre-Demo Checklist

```text
[ ] Correct instance selected
[ ] Network profile selected
[ ] Security Gate PASS
[ ] Backup exists
[ ] Primary URL loads
[ ] Login works
[ ] Demo account works
[ ] No public tunnel unless needed
[ ] Public tunnel has expiration if enabled
```

### 22.2 Post-Demo Checklist

```text
[ ] Disable public temporary access
[ ] Confirm private mode
[ ] Create backup if data changed
[ ] Export notes if needed
[ ] Stop instance if not needed
[ ] Confirm state stopped or private running
```

### 22.3 Update Checklist

```text
[ ] New capsule received
[ ] Capsule verified
[ ] Backup created
[ ] Compatibility passed
[ ] Security Gate PASS
[ ] Update applied
[ ] Health checks passed
[ ] Rollback point retained
```

### 22.4 Incident Checklist

```text
[ ] Public exposure disabled
[ ] Instance stopped if necessary
[ ] Evidence preserved
[ ] Support bundle exported
[ ] Secrets identified for rotation
[ ] Clean rebuild considered
[ ] Incident notes recorded
```

---

## 23. CLI Quick Reference

The operator should prefer the Manager UI.

CLI is available for support or advanced operation.

```bash
kx instance status demo-001
kx security check demo-001
kx instance start demo-001
kx instance stop demo-001
kx instance logs demo-001

kx instance backup demo-001 --class manual
kx backup list demo-001
kx backup verify <BACKUP_ID>
kx backup test-restore <BACKUP_ID>

kx instance restore-new --from <BACKUP_ID> --new-instance-id demo-restore-001
kx instance restore demo-001 --from <BACKUP_ID> --mode full

kx capsule verify <CAPSULE_FILE>.kxcap
kx capsule import <CAPSULE_FILE>.kxcap

# Installed artifact discovery/lifecycle
kx artifact list
kx artifact list --products-only
kx artifact list --composition-candidates
kx artifact show <ARTIFACT_ID>
kx artifact remove <ARTIFACT_ID>

kx instance update demo-001 --capsule <CAPSULE_FILE>.kxcap --auto-rollback true

kx instance rollback demo-001 --level capsule
kx instance rollback demo-001 --level data --from <BACKUP_ID>
kx instance rollback demo-001 --level full --from <BACKUP_ID>

kx network set-profile demo-001 intranet_private
```

Never use raw Docker commands unless operating under technical maintainer instructions.

Artifact removal is fail-closed. If the target is required by another installed
artifact, provides a still-required capability, or is referenced by an active
instance, the operator must resolve that dependency/runtime relationship first.
Business/instance data is preserved by default.

---

## 24. Operator Log

Operators should record:

```text
date/time
operator name
instance ID
capsule version
network profile
action performed
result
backup ID if relevant
Security Gate result
notes
```

Example:

```text
2026-04-30 14:05
operator: local-admin
instance: demo-001
capsule: konnaxion-v14-demo-2026.04.30
network: intranet_private
action: start
security: PASS
result: running
notes: demo ready at https://konnaxion.local
```

---

## 25. Escalation Rules

Escalate to technical maintainer when:

```text
Security Gate returns FAIL_BLOCKING
Security Gate returns UNKNOWN for firewall, ports, signatures, or secrets
unknown containers appear
public tunnel cannot be disabled
backup verification fails repeatedly
restore fails
update fails and rollback fails
database health is unhealthy
Postgres or Redis appears exposed
Docker socket exposure is detected
operator sees suspicious files or users
```

Do not troubleshoot security failures by weakening controls.

---

## 26. Acceptance Criteria

This operator guide is valid when an operator can:

```text
start a Konnaxion Instance
stop a Konnaxion Instance
choose the correct network profile
avoid public exposure by default
run Security Gate checks
create and verify backups
restore from backup
apply capsule updates
rollback failed updates
read safe logs
export support bundles
respond to common failures
escalate security incidents
```

The operator must be able to perform normal operations without editing infrastructure files manually.

---

## 27. Summary

The operator experience must remain simple:

```text
Start
Open URL
Backup
Update
Stop
```

The system must handle the complexity:

```text
secrets
ports
firewall
Docker Compose
Traefik
Postgres
Redis
Celery
healthchecks
Security Gate
rollback
```

The safe path must be the easy path.

The unsafe path must be blocked by default.
