# Konnaxion Capsule Manager

Konnaxion Capsule Manager packages Konnaxion v14 into signed, portable `.kxcap` capsules and runs them through a local Manager, privileged Agent, Docker Compose runtime, private-by-default network profiles, Security Gate checks, backups, restores, rollback, GUI workflows, first-time Droplet bootstrap, and the canonical `kx` CLI.

## Purpose

The project turns Konnaxion into a portable, secure, plug-and-play appliance system.

```text
Konnaxion Source
→ Konnaxion Capsule Builder
→ Signed .kxcap Capsule
→ Konnaxion Capsule Manager
→ Konnaxion Agent
→ Docker Compose Runtime
→ Konnaxion Instance
````

## Core Components

* `kx_shared/` — canonical constants, paths, states, profiles, services, and validation
* `kx_agent/` — privileged local service for runtime, security, network, backup, restore, rollback, and capsule verification actions
* `kx_manager/` — user-facing API and local GUI layer
* `kx_builder/` — capsule build, manifest, checksum, image, and signature tooling
* `kx_cli/` — canonical `kx` operator/developer CLI
* `profiles/` — approved network profiles
* `policies/` — runtime and Security Gate policies
* `templates/` — Docker Compose and environment templates
* `docs/` — technical contracts and operator documentation
* `tests/` — contract and integration tests

## Default Runtime

Konnaxion runs through Docker Compose with canonical service names only:

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

Forbidden aliases include `backend`, `api`, `frontend`, `db`, `cache`, `worker`, `scheduler`, and `agent`.

## Security Model

Konnaxion is private by default.

The system enforces:

* signed capsules only
* checksum-verified capsule contents
* extracted capsule and archive verification
* generated secrets on install
* deny-by-default networking
* Traefik-only HTTP/S entrypoint
* no public PostgreSQL or Redis
* no public Konnaxion Agent API
* no Docker socket mounts
* no privileged app containers
* no host networking for app containers
* canonical network profiles only
* blocking Security Gate checks before startup
* backup safety checks before restore and rollback workflows

The Konnaxion Agent must bind to a local interface by default:

```text
127.0.0.1:8765
```

Public VPS users must reach the runtime through Traefik on HTTP/S only:

```text
80
443
```

Do not expose the Agent API port `8765` publicly.

## Network Profiles

Supported canonical profiles:

```text
local_only
intranet_private
private_tunnel
public_temporary
public_vps
offline
```

Supported exposure modes:

```text
private
lan
vpn
temporary_tunnel
public
```

Default:

```text
network_profile = intranet_private
exposure_mode = private
```

Temporary public mode requires an expiration. Public VPS mode requires explicit operator confirmation.

## GUI

The Manager GUI is intended to run locally at:

```text
http://127.0.0.1:8714/ui
```

The GUI contract covers:

```text
select Konnaxion source folder
select capsule output folder
build capsule
rebuild capsule
verify capsule
import capsule
list capsules
view capsule
create instance
update instance
start instance
stop instance
restart instance
view status
view health
view logs
open instance
run Security Gate
set network profile
disable public mode
create backup
list backups
verify backup
restore backup
restore backup into new instance
test restore backup
rollback instance
set local target
set intranet target
set temporary public target
set droplet target
deploy local
deploy intranet
deploy droplet
bootstrap droplet agent
check droplet agent
copy capsule to droplet
start droplet instance
open Manager docs
open Agent docs
```

The GUI must remain local-only by default and must not execute arbitrary shell commands. Every GUI action must map to an allowlisted Manager route, Agent API endpoint, Builder service, Deploy service, Target service, first-time Droplet bootstrap service, or browser-link result.

Browser-only actions are rendered as links, not POST routes:

```text
open_instance
open_manager_docs
open_agent_docs
```

GUI technical contracts:

```text
docs/DOC-16_Konnaxion_Manager_GUI_Technical_Contract.md
docs/DOC-17_Konnaxion_GUI_Action_Coverage_Contract.md
docs/DOC-17A_Konnaxion_GUI_Action_Payload_Contract.md
docs/DOC-18_Konnaxion_GUI_Target_Modes.md
docs/DOC-19_Konnaxion_GUI_Page_Split_Droplet_Payload_Contract.md
```

## GUI Theme and Assets

Shared GUI styling is owned by:

```text
kx_manager/ui/styles.py
```

Current primary theme color:

```text
#1e6864
```

The HTML rendering layer imports shared CSS from `kx_manager.ui.styles`, keeping `kx_manager/ui/render.py` focused on safe HTML rendering helpers.

Optional local GUI assets, such as a logo, should live under:

```text
kx_manager/ui/assets/
```

Recommended logo path:

```text
kx_manager/ui/assets/konnaxion-logo.svg
```

If static assets are mounted by the Manager, the logo is served from:

```text
http://127.0.0.1:8714/ui/assets/konnaxion-logo.svg
```

## Target Modes

The GUI and Manager support these target modes:

```text
local
intranet
temporary_public
droplet
```

Target mapping:

| Target mode        | Network profile    | Exposure mode      | Purpose                         |
| ------------------ | ------------------ | ------------------ | ------------------------------- |
| `local`            | `local_only`       | `private`          | Same-machine development        |
| `intranet`         | `intranet_private` | `private` or `lan` | Private LAN/internal deployment |
| `temporary_public` | `public_temporary` | `temporary_tunnel` | Time-limited public demo        |
| `droplet`          | `public_vps`       | `public`           | Remote VPS/Droplet deployment   |

## Droplet Runtime Layout

Canonical hardened VPS/Droplet runtime paths:

```text
/opt/konnaxion
/opt/konnaxion/capsules
/opt/konnaxion/instances
/opt/konnaxion/backups
/opt/konnaxion/shared
/opt/konnaxion/releases
/opt/konnaxion/manager
/opt/konnaxion/agent
```

Purpose:

```text
/opt/konnaxion/capsules   signed .kxcap files copied to the Droplet
/opt/konnaxion/instances  installed instance state
/opt/konnaxion/backups    backup artifacts and metadata
/opt/konnaxion/shared     shared Manager/Agent state
/opt/konnaxion/releases   release metadata and installed release pointers
/opt/konnaxion/manager    remote Manager/Agent Python project code
/opt/konnaxion/agent      Agent runtime/config area
```

The `.kxcap` capsule is the application artifact. It is not the installer for the Manager/Agent control plane.

The remote Konnaxion Agent must exist before a Droplet can import, verify, create, update, secure, or start a Konnaxion Instance.

## First-Time Droplet Bootstrap

A new or partially prepared Droplet may have Docker and `/opt/konnaxion` folders but no remote Agent.

The GUI action:

```text
bootstrap_droplet_agent
```

is responsible for first-time remote control-plane setup.

Expected bootstrap behavior:

```text
1. SSH to the Droplet using the configured Droplet target.
2. Create the canonical /opt/konnaxion folder layout.
3. Copy or install trusted Konnaxion Capsule Manager / Agent code to /opt/konnaxion/manager.
4. Install required Python runtime tooling such as uv.
5. Install or refresh dependencies.
6. Write a localhost-only systemd service for the Konnaxion Agent.
7. Start konnaxion-agent.service.
8. Verify http://127.0.0.1:8765/v1/health from inside the Droplet.
```

The Agent must remain private:

```text
KX_AGENT_HOST=127.0.0.1
KX_AGENT_PORT=8765
```

Do not require public access to:

```text
http://<droplet-ip>:8765
```

The GUI may use SSH to verify localhost Agent health from inside the Droplet.

## Droplet GUI Workflow

Use the GUI workflow in this order:

```text
1. Capsules → Build Capsule using profile public_vps
2. Capsules → Verify Capsule
3. Targets → Set Droplet Target
4. Deploy → Bootstrap Droplet Agent
5. Deploy → Check Droplet Agent
6. Deploy → Copy Capsule to Droplet
7. Deploy → Deploy Droplet
8. Deploy → Start Droplet Instance, only if Deploy Droplet did not already start it
```

`Set Droplet Target` must be done once and persisted into GUI state. The Deploy page should prefill Droplet fields from the saved target.

Required Droplet target fields:

```text
target_mode=droplet
network_profile=public_vps
exposure_mode=public
instance_id
droplet_name
droplet_host
droplet_user
ssh_key_path
ssh_port
remote_kx_root
remote_capsule_dir
domain
confirmed=true
```

Recommended defaults:

```text
instance_id=demo-001
droplet_user=root
ssh_port=22
remote_kx_root=/opt/konnaxion
remote_capsule_dir=/opt/konnaxion/capsules
```

For IP-only testing, a DNS helper domain may be used explicitly, for example:

```text
138.197.174.76.sslip.io
```

The GUI must not silently invent a domain from a Droplet IP. The operator must provide the domain value.

## Canonical CLI

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

## Development with uv

Create and install the environment:

```powershell
uv venv
uv pip install -e ".[dev]"
```

Run compile and tests:

```powershell
uv run python -m compileall kx_shared kx_agent kx_manager kx_builder kx_cli tests
uv run pytest -q
```

Current expected baseline:

```text
500 passed
0 skipped
```

## Run the Agent

```powershell
uv run kx-agent run
```

Default Agent URL:

```text
http://127.0.0.1:8765
```

Useful endpoints:

```text
http://127.0.0.1:8765/docs
http://127.0.0.1:8765/v1/health
http://127.0.0.1:8765/v1/agent/info
```

A `404` at `/` is normal because the Agent does not define a homepage route.

## Run the Manager

```powershell
uv run kx-manager --host 127.0.0.1 --port 8714
```

Default Manager URLs:

```text
http://127.0.0.1:8714
http://127.0.0.1:8714/docs
http://127.0.0.1:8714/ui
```

## Local Launcher

For local development, you can use a `.bat` launcher that starts both the Agent and Manager in separate terminal windows.

Recommended file name:

```text
StartKonnaxionLocal.bat
```

Recommended behavior:

```text
start Agent on http://127.0.0.1:8765
start Manager on http://127.0.0.1:8714
open http://127.0.0.1:8714/ui
```

## Windows Runtime Defaults

For local Windows development:

```powershell
$env:KX_ROOT="C:\mycode\Konnaxion\runtime"
$env:KX_SOURCE_DIR="C:\mycode\Konnaxion\Konnaxion"
$env:KX_AGENT_HOST="127.0.0.1"
$env:KX_AGENT_PORT="8765"
$env:KX_MANAGER_HOST="127.0.0.1"
$env:KX_MANAGER_PORT="8714"
```

Runtime folders:

```text
C:\mycode\Konnaxion\runtime\capsules
C:\mycode\Konnaxion\runtime\instances
C:\mycode\Konnaxion\runtime\backups
C:\mycode\Konnaxion\runtime\shared
```

Canonical appliance runtime paths remain:

```text
/opt/konnaxion
/opt/konnaxion/capsules
/opt/konnaxion/instances
/opt/konnaxion/backups
/opt/konnaxion/shared
/opt/konnaxion/releases
/opt/konnaxion/manager
/opt/konnaxion/agent
```

## Typical Local Workflow

Build and verify a demo capsule:

```powershell
uv run kx-builder capsule build `
  --source-dir C:\mycode\Konnaxion\Konnaxion `
  --output C:\mycode\Konnaxion\runtime\capsules\konnaxion-v14-demo-2026.04.30.kxcap `
  --channel demo `
  --capsule-id konnaxion-v14-demo-2026.04.30 `
  --version 2026.04.30-demo.1 `
  --profile intranet_private `
  --force

uv run kx-builder capsule verify C:\mycode\Konnaxion\runtime\capsules\konnaxion-v14-demo-2026.04.30.kxcap
```

Import and run locally or on the intranet profile:

```powershell
uv run kx capsule import C:\mycode\Konnaxion\runtime\capsules\konnaxion-v14-demo-2026.04.30.kxcap

uv run kx instance create demo-001
uv run kx security check demo-001
uv run kx instance start demo-001
uv run kx instance status demo-001
uv run kx instance health demo-001
```

Create and verify a backup:

```powershell
uv run kx instance backup demo-001 --class manual
uv run kx backup list demo-001
uv run kx backup verify <BACKUP_ID>
uv run kx backup test-restore <BACKUP_ID>
```

## Typical Droplet Workflow

Build and verify a public VPS capsule:

```powershell
uv run kx-builder capsule build `
  --source-dir C:\mycode\Konnaxion\Konnaxion `
  --output C:\mycode\Konnaxion\runtime\capsules\konnaxion-v14-demo-2026.04.30.kxcap `
  --channel demo `
  --capsule-id konnaxion-v14-demo-2026.04.30 `
  --version 2026.04.30-demo.1 `
  --profile public_vps `
  --force

uv run kx-builder capsule verify C:\mycode\Konnaxion\runtime\capsules\konnaxion-v14-demo-2026.04.30.kxcap
```

Then use the GUI:

```text
Targets → Set Droplet Target
Deploy → Bootstrap Droplet Agent
Deploy → Check Droplet Agent
Deploy → Copy Capsule to Droplet
Deploy → Deploy Droplet
```

Do not continue to `Copy Capsule to Droplet` until `Check Droplet Agent` succeeds.

Do not continue to `Deploy Droplet` until the capsule exists locally and has been copied to the Droplet.

## Droplet Diagnostics

On the Droplet console, use:

```bash
echo "== WHOAMI =="
whoami
hostname
hostname -I

echo
echo "== KX DIRS =="
ls -ld /opt /opt/konnaxion /opt/konnaxion/agent /opt/konnaxion/manager /opt/konnaxion/capsules /opt/konnaxion/instances /opt/konnaxion/backups /opt/konnaxion/shared 2>&1 || true

echo
echo "== KX PROCESSES =="
ps aux | grep -Ei 'konnaxion|kx-agent|kx-manager|uvicorn' | grep -v grep || true

echo
echo "== LISTENING PORTS =="
ss -ltnp | grep -E ':8765|:8714|:80|:443' || true

echo
echo "== LOCAL AGENT HEALTH =="
curl -i --max-time 5 http://127.0.0.1:8765/v1/health || true

echo
echo "== SYSTEMD KX SERVICES =="
systemctl list-units --type=service --all | grep -Ei 'konnaxion|kx' || true

echo
echo "== DOCKER =="
docker --version 2>&1 || true
docker compose version 2>&1 || true
```

A fresh or incomplete Droplet may show:

```text
/opt/konnaxion/agent missing
/opt/konnaxion/manager missing
/opt/konnaxion/shared missing
no Konnaxion processes
curl to 127.0.0.1:8765 fails
```

That means `Bootstrap Droplet Agent` must run before `Check Droplet Agent`.

## Launch Readiness

Before launch or test-drive:

```powershell
uv run python -m compileall kx_manager kx_agent kx_builder kx_cli kx_shared tests
uv run pytest -q
uv run python -c "from kx_manager.ui.server import app; print(len(app.routes)); print('server ok')"
```

Expected Manager import output:

```text
server ok
```

The exact route count may change when GUI actions are added.

Launch local/intranet first. Do not enable `public_temporary`, `public_vps`, or Droplet deployment until the local capsule build, verify, import, create, Security Gate, start, health, backup, and restore-test flow passes end-to-end.

For Droplet launch, do not deploy until:

```text
Set Droplet Target succeeds
Bootstrap Droplet Agent succeeds
Check Droplet Agent succeeds
Copy Capsule to Droplet succeeds
Deploy Droplet succeeds
Security Gate passes
Instance health passes
Backup and restore-test pass
```

## Target

Konnaxion as a signed, portable, private-by-default capsule system deployable on:

```text
Konnaxion Box
local host
intranet server
private tunnel
temporary public demo
hardened VPS/Droplet
```


## Capsule build progress and persistent logs

`Build Capsule` and `Rebuild Capsule` run as asynchronous Manager jobs. The
browser is redirected immediately to `/ui/build-jobs/<job-id>`, which displays
status, phase, a progress bar, and the live log tail. The page refreshes every
two seconds while the job is active.

By default, build evidence is stored under:

```text
<KX_ROOT>/manager/build-jobs/
  <job-id>.json
  <job-id>.log
  <job-id>.progress.json
  latest-job.txt
```

On the standard Windows local layout this is:

```text
C:\mycode\Konnaxion\runtime\manager\build-jobs\
```

`StartCapsuleManager.bat` creates this directory before starting the Agent and
Manager. Capsule builds are serialized by default with
`KX_CAPSULE_BUILD_CONCURRENCY=1` to limit RAM pressure. The value can be raised
explicitly when the host has sufficient capacity.

## Installed artifact registry and composition boundary

Capsule now distinguishes `library`, `product`, and `composition_host` artifacts.
New builds include a public `artifact.yaml` descriptor, imported artifacts are
registered under `<KX_ROOT>/shared/registry/installed-artifacts.json`, and the
registry exposes a generation-based discovery projection for optional
composition hosts such as kOA Spaces.

Canonical contract: `docs/DOC-23_Capsule_Installed_Artifact_and_Composition_Contract.md`.

Public discovery/removal CLI:

```text
kx artifact list
kx artifact show <artifact-id>
kx artifact remove <artifact-id>
```

Product UX contributions remain owned by each product; Capsule validates and
publishes their public manifest reference but does not construct navigation,
routes, commands, or inspectors.
