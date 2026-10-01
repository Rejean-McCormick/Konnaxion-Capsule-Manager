# Konnaxion Manager GUI Shared Variable Contract

Use this as the shared variable contract across parallel branches.

Keep these names exact so `static.py`, `page_views.py`, `page_parts/*`,
`page_forms.py`, `form_targets.py`, `actions.py`, and tests line up.

## 1. Canonical page split contract

Every page-part module should export one renderer with this signature:

```python
from collections.abc import Mapping
from typing import Any

def render(context: Mapping[str, Any]) -> str:
    ...
````

`page_views.py` imports those renderers and wires them into `PAGE_VIEWS`.

```python
from collections.abc import Callable, Mapping
from typing import Any

PageBuilder = Callable[[Mapping[str, Any]], str]
```

No page-part file should call `html_response(...)`.

Only `page_views.py` may wrap page content in `html_response(...)`.

Deployment actions are split out of the Targets page and rendered by:

```text
kx_manager/ui/page_parts/deploy.py
```

## 2. Canonical route variables

Use these routes in `static.py` and `page_views.py`.

```python
UI_BASE_PATH = "/ui"

UI_PAGE_ROUTES: tuple[str, ...] = (
    "/ui",
    "/ui/capsules",
    "/ui/instances",
    "/ui/security",
    "/ui/network",
    "/ui/backups",
    "/ui/restore",
    "/ui/logs",
    "/ui/health",
    "/ui/settings",
    "/ui/targets",
    "/ui/deploy",
    "/ui/about",
)

PAGE_ROUTES: tuple[str, ...] = UI_PAGE_ROUTES
```

Use this page map shape in `page_views.py`:

```python
PAGE_VIEWS: dict[str, PageView] = {
    "/ui": PageView("/ui", "Dashboard", "...", dashboard.render),
    "/ui/capsules": PageView("/ui/capsules", "Capsules", "...", capsules.render),
    "/ui/instances": PageView("/ui/instances", "Instances", "...", instances.render),
    "/ui/security": PageView("/ui/security", "Security", "...", security.render),
    "/ui/network": PageView("/ui/network", "Network", "...", network.render),
    "/ui/backups": PageView("/ui/backups", "Backups", "...", backups.render),
    "/ui/restore": PageView("/ui/restore", "Restore", "...", restore.render),
    "/ui/logs": PageView("/ui/logs", "Logs", "...", logs.render),
    "/ui/health": PageView("/ui/health", "Health", "...", health.render),
    "/ui/settings": PageView("/ui/settings", "Settings", "...", settings.render),
    "/ui/targets": PageView("/ui/targets", "Targets", "...", targets.render),
    "/ui/deploy": PageView("/ui/deploy", "Deploy", "...", deploy.render),
    "/ui/about": PageView("/ui/about", "About", "...", about.render),
}
```

Navigation must include:

```text
/ui/deploy
```

## 3. Canonical default variables

Put these in:

```text
kx_manager/ui/page_parts/common.py
```

```python
DEFAULT_PUBLIC_EXPIRATION = "2026-04-30T22:00:00Z"
DEFAULT_PRIVATE_HOST = "konnaxion.local"

DEFAULT_DROPLET_NAME = "konnaxion-droplet"
DEFAULT_DROPLET_HOST = ""
DEFAULT_DROPLET_USER = "konnaxion"
DEFAULT_SSH_KEY_PATH = ""
DEFAULT_SSH_PORT = 22
DEFAULT_REMOTE_KX_ROOT = "/opt/konnaxion"
DEFAULT_REMOTE_CAPSULE_DIR = "/opt/konnaxion/capsules"
DEFAULT_DROPLET_DOMAIN = ""
DEFAULT_REMOTE_AGENT_URL = ""
```

Do not hardcode a real IP as a default. Let it come from submitted/context values.

Do not silently invent `domain` from `droplet_host`.

`domain` must be submitted explicitly by the UI form or caller.

`remote_agent_url` defaults to blank. Blank means Droplet Agent uses SSH-local transport:

```text
Manager on Windows
  -> ssh droplet_user@droplet_host
  -> curl http://127.0.0.1:8765/v1/... on the Droplet
```

Do not default Droplet Agent transport to a local forwarded tunnel URL such as:

```text
http://127.0.0.1:18765/v1
```

## 4. Canonical payload builders

These should live in:

```text
kx_manager/ui/page_parts/common.py
```

### 4.1 Default payload

```python
from collections.abc import Mapping
from typing import Any

def default_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "instance_id": context.get("instance_id", DEFAULT_INSTANCE_ID),
        "capsule_id": context.get("capsule_id", DEFAULT_CAPSULE_ID),
        "capsule_version": context.get("capsule_version", DEFAULT_CAPSULE_VERSION),
        "capsule_file": context.get("capsule_file", DEFAULT_CAPSULE_FILE),
        "capsule_path": context.get(
            "capsule_path",
            context.get("capsule_file", DEFAULT_CAPSULE_FILE),
        ),
        "source_dir": context.get("source_dir", DEFAULT_SOURCE_DIR),
        "capsule_output_dir": context.get(
            "capsule_output_dir",
            DEFAULT_CAPSULE_OUTPUT_DIR,
        ),
        "target_mode": context.get("target_mode", "intranet"),
        "network_profile": context.get("network_profile", "intranet_private"),
        "exposure_mode": context.get("exposure_mode", "private"),
        "runtime_root": context.get("runtime_root", DEFAULT_RUNTIME_ROOT),
        "capsule_dir": context.get(
            "capsule_dir",
            f"{DEFAULT_RUNTIME_ROOT}\\capsules",
        ),
        "host": context.get("host", DEFAULT_PRIVATE_HOST),
        "domain": context.get("domain", ""),
        "runtime_url": context.get("runtime_url", "http://127.0.0.1"),
    }
```

### 4.2 Local payload

```python
def local_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    payload = default_payload(context)
    payload.update(
        {
            "target_mode": "local",
            "network_profile": "local_only",
            "exposure_mode": "private",
            "host": "127.0.0.1",
            "domain": "",
            "confirmed": "true",
        }
    )
    return payload
```

### 4.3 Intranet payload

```python
def intranet_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    payload = default_payload(context)
    payload.update(
        {
            "target_mode": "intranet",
            "network_profile": "intranet_private",
            "exposure_mode": context.get("exposure_mode", "private"),
            "host": context.get("host", DEFAULT_PRIVATE_HOST),
            "domain": "",
            "confirmed": "true",
        }
    )
    return payload
```

### 4.4 Dedicated Droplet payload

```python
def droplet_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    capsule_file = (
        context.get("capsule_file")
        or context.get("capsule_path")
        or DEFAULT_CAPSULE_FILE
    )

    droplet_host = (
        context.get("droplet_host")
        or context.get("host")
        or context.get("target_host")
        or DEFAULT_DROPLET_HOST
    )

    domain = (
        context.get("domain")
        or context.get("droplet_domain")
        or DEFAULT_DROPLET_DOMAIN
    )

    remote_kx_root = (
        context.get("remote_kx_root")
        or context.get("runtime_root")
        or context.get("remote_root")
        or context.get("droplet_kx_root")
        or DEFAULT_REMOTE_KX_ROOT
    )

    remote_capsule_dir = (
        context.get("remote_capsule_dir")
        or context.get("capsule_dir")
        or context.get("target_capsule_dir")
        or context.get("droplet_capsule_dir")
        or DEFAULT_REMOTE_CAPSULE_DIR
    )

    return {
        "instance_id": context.get("instance_id", DEFAULT_INSTANCE_ID),
        "capsule_id": context.get("capsule_id", DEFAULT_CAPSULE_ID),
        "capsule_version": context.get("capsule_version", DEFAULT_CAPSULE_VERSION),
        "capsule_file": capsule_file,
        "capsule_path": capsule_file,
        "source_dir": context.get("source_dir", DEFAULT_SOURCE_DIR),
        "capsule_output_dir": context.get(
            "capsule_output_dir",
            DEFAULT_CAPSULE_OUTPUT_DIR,
        ),

        "target_mode": "droplet",
        "network_profile": "public_vps",
        "exposure_mode": "public",
        "public_mode_enabled": "true",
        "public_mode_expires_at": "",

        "droplet_name": context.get("droplet_name", DEFAULT_DROPLET_NAME),
        "droplet_host": droplet_host,
        "host": droplet_host,
        "droplet_user": (
            context.get("droplet_user")
            or context.get("ssh_user")
            or context.get("user")
            or DEFAULT_DROPLET_USER
        ),
        "ssh_key_path": (
            context.get("ssh_key_path")
            or context.get("droplet_ssh_key")
            or context.get("ssh_key")
            or DEFAULT_SSH_KEY_PATH
        ),
        "ssh_port": context.get("ssh_port", DEFAULT_SSH_PORT),
        "remote_kx_root": remote_kx_root,
        "runtime_root": remote_kx_root,
        "remote_capsule_dir": remote_capsule_dir,
        "capsule_dir": remote_capsule_dir,
        "domain": domain,
        "droplet_domain": domain,
        "remote_agent_url": (
            context.get("remote_agent_url")
            or context.get("droplet_agent_url")
            or DEFAULT_REMOTE_AGENT_URL
        ),
        "confirmed": "true",
    }
```

Important: Droplet operation forms must use `droplet_payload(context)`, never `default_payload(context)`.

Important: `droplet_payload(context)` may return `domain=""` when no domain was supplied. That is intentional. Validation must reject missing `domain`; the payload builder must not hide the missing value by inventing one from the IP.

## 5. Canonical Droplet action sets

Use the same sets in `form_targets.py`, tests, `page_parts/targets.py`, and `page_parts/deploy.py`.

```python
DROPLET_ACTIONS: frozenset[str] = frozenset(
    {
        "set_target_droplet",
        "deploy_droplet",
        "check_droplet_agent",
        "copy_capsule_to_droplet",
        "start_droplet_instance",
    }
)

DROPLET_CAPSULE_REQUIRED_ACTIONS: frozenset[str] = frozenset(
    {
        "deploy_droplet",
        "copy_capsule_to_droplet",
        "start_droplet_instance",
    }
)
```

For these operation actions, force/default:

```python
target_mode = "droplet"
network_profile = "public_vps"
exposure_mode = "public"
confirmed = True
```

`set_target_droplet` must still reject missing `confirmed`.

Operation actions may force confirmation because they are explicit Droplet operation forms.

## 6. Canonical target form names

Use these exact form field names in the Droplet target form and operation forms:

```text
target_mode
network_profile
exposure_mode
instance_id
capsule_file
capsule_path
droplet_name
droplet_host
droplet_user
ssh_key_path
ssh_port
remote_kx_root
remote_capsule_dir
domain
droplet_domain
remote_agent_url
confirmed
```

Required visible Droplet target fields:

```text
instance_id
droplet_name
droplet_host
droplet_user
ssh_key_path
ssh_port
remote_kx_root
remote_capsule_dir
domain
confirmed
```

Required visible Droplet operation fields when the action copies/deploys a capsule:

```text
capsule_file
```

Aliases are allowed only as input aliases:

```text
target_host -> droplet_host / host
ssh_user -> droplet_user
user -> droplet_user
droplet_ssh_key -> ssh_key_path
ssh_key -> ssh_key_path
remote_root -> remote_kx_root
droplet_kx_root -> remote_kx_root
droplet_capsule_dir -> remote_capsule_dir
target_capsule_dir -> capsule_dir
droplet_domain -> domain
droplet_agent_url -> remote_agent_url
```

## 7. Canonical alias normalization additions

In `static.py::normalize_payload_aliases(...)`, ensure these are present:

```python
if data.get("droplet_host") and not data.get("host"):
    data["host"] = data["droplet_host"]
if (
    data.get("host")
    and not data.get("droplet_host")
    and data.get("target_mode") == "droplet"
):
    data["droplet_host"] = data["host"]

if data.get("remote_kx_root") and not data.get("runtime_root"):
    data["runtime_root"] = data["remote_kx_root"]
if (
    data.get("runtime_root")
    and not data.get("remote_kx_root")
    and data.get("target_mode") == "droplet"
):
    data["remote_kx_root"] = data["runtime_root"]

if data.get("remote_capsule_dir") and not data.get("capsule_dir"):
    data["capsule_dir"] = data["remote_capsule_dir"]
if (
    data.get("capsule_dir")
    and not data.get("remote_capsule_dir")
    and data.get("target_mode") == "droplet"
):
    data["remote_capsule_dir"] = data["capsule_dir"]

if data.get("domain") and not data.get("droplet_domain"):
    data["droplet_domain"] = data["domain"]
if data.get("droplet_domain") and not data.get("domain"):
    data["domain"] = data["droplet_domain"]

if data.get("capsule_file") and not data.get("capsule_path"):
    data["capsule_path"] = data["capsule_file"]
if data.get("capsule_path") and not data.get("capsule_file"):
    data["capsule_file"] = data["capsule_path"]
```

Do not add this fallback:

```python
if not data.get("domain"):
    data["domain"] = data.get("droplet_host") or data.get("host")
```

That would bypass required-domain validation.

## 8. Canonical page-part imports

Every page-part file should import shared helpers from `common.py`, not from `page_views.py`.

Example:

```python
from kx_manager.ui.page_parts.common import (
    DEFAULT_DROPLET_DOMAIN,
    DEFAULT_DROPLET_HOST,
    DEFAULT_DROPLET_NAME,
    DEFAULT_DROPLET_USER,
    DEFAULT_REMOTE_AGENT_URL,
    DEFAULT_REMOTE_CAPSULE_DIR,
    DEFAULT_REMOTE_KX_ROOT,
    DEFAULT_SSH_KEY_PATH,
    DEFAULT_SSH_PORT,
    action_bar,
    action_form,
    button_form,
    capsule_file_field,
    confirmed_field,
    default_payload,
    droplet_operation_form,
    droplet_payload,
    field,
    instance_id_field,
)
```

Avoid underscored cross-module helpers in the new split. Use public helper names:

```text
field
action_form
button_form
action_bar
default_payload
droplet_payload
droplet_operation_form
```

Do not use:

```text
_field
_action_form
_button_form
_action_bar
_default_payload
_droplet_payload
_droplet_operation_form
```

## 9. Compatibility `page_forms.py`

`page_forms.py` should become a facade with this stable function:

```python
from collections.abc import Mapping
from typing import Any

from kx_manager.ui.page_parts import render_page_body

def render_page_forms(route: str, data: Mapping[str, Any] | None = None) -> str:
    return render_page_body(route, dict(data or {}))
```

It must not own page bodies anymore.

## 10. Target page rendering rules

`page_parts/targets.py` should render only target configuration forms:

```text
set_target_local
set_target_intranet
set_target_temporary_public
set_target_droplet
```

The Droplet target form must render `domain` as required:

```python
field("domain", "Domain", payload["domain"], required=True)
```

The Targets page must not render these deployment/operation actions anymore:

```text
deploy_local
deploy_intranet
deploy_droplet
check_droplet_agent
copy_capsule_to_droplet
start_droplet_instance
```

Those belong in:

```text
page_parts/deploy.py
```

## 11. Deploy page rendering rules

`page_parts/deploy.py` should render deployment and operation actions:

```text
deploy_local
deploy_intranet
deploy_droplet
check_droplet_agent
copy_capsule_to_droplet
start_droplet_instance
```

For these actions, use full Droplet operation forms, not hidden-only buttons:

```text
deploy_droplet
check_droplet_agent
copy_capsule_to_droplet
start_droplet_instance
```

`deploy_droplet`, `copy_capsule_to_droplet`, and `start_droplet_instance` must include visible `capsule_file`.

`check_droplet_agent` does not require `capsule_file` and must not submit `capsule_file`, even as hidden input.

`deploy_local` and `deploy_intranet` may remain compact button forms, but their payloads must come from `local_payload(context)` and `intranet_payload(context)`.

## 12. Test payload alignment

In tests, `action_payload(...)` must use Droplet values for all Droplet actions:

```python
if action in {
    "set_target_droplet",
    "deploy_droplet",
    "check_droplet_agent",
    "copy_capsule_to_droplet",
    "start_droplet_instance",
}:
    base.update(
        {
            "target_mode": "droplet",
            "network_profile": "public_vps",
            "exposure_mode": "public",
            "droplet_name": "ubuntu-s-1vcpu-2gb-tor1",
            "droplet_host": "203.0.113.10",
            "host": "203.0.113.10",
            "droplet_user": "konnaxion",
            "ssh_key_path": str(ssh_key_path),
            "ssh_port": "22",
            "remote_kx_root": "/opt/konnaxion",
            "runtime_root": "/opt/konnaxion",
            "remote_capsule_dir": "/opt/konnaxion/capsules",
            "capsule_dir": "/opt/konnaxion/capsules",
            "domain": "203.0.113.10.sslip.io",
            "droplet_domain": "203.0.113.10.sslip.io",
            "remote_agent_url": "",
            "confirmed": "true",
        }
    )
```

Use `konnaxion` as SSH user in UI test payloads, matching the created Droplet user.

Use blank `remote_agent_url` in canonical Droplet tests. Blank means private Agent over SSH-local transport. Only use a non-loopback `remote_agent_url` in tests explicitly covering direct public Agent mode.

## 13. Main invariant

This must be true after the split:

```python
assert droplet_payload(context)["target_mode"] == "droplet"
assert droplet_payload(context)["network_profile"] == "public_vps"
assert droplet_payload(context)["exposure_mode"] == "public"
```

This must never happen again:

```json
{
  "action": "copy_capsule_to_droplet",
  "target_mode": "intranet"
}
```

That submitted payload is the exact bug to prevent.

This validation must remain true:

```python
payload = droplet_payload({"droplet_host": "203.0.113.10"})
assert payload["domain"] == ""

# Later validation must reject this until domain is supplied.
```

## 14. Droplet Agent transport invariant

Droplet mode must keep the Agent private by default:

```text
remote_agent_url = ""
agent_transport = "ssh"
agent_health_url = "http://127.0.0.1:8765/v1/health"
```

Manager must execute Agent calls as:

```text
ssh droplet_user@droplet_host "curl http://127.0.0.1:8765/v1/<endpoint>"
```

A loopback `remote_agent_url` such as this must not be treated as a direct HTTP transport:

```text
http://127.0.0.1:18765/v1
http://localhost:18765/v1
http://0.0.0.0:8765/v1
```

Those values should either be normalized to blank SSH-local mode or rejected by target validation.

## 15. Agent network payload invariant

Manager must send Agent network profile payload using the canonical public-host fields:

```text
host
host_aliases
```

`host` is the canonical public hostname. `host_aliases` is an optional list of
additional public hostnames that route to the same Konnaxion instance.

Do not send the Manager/UI alias fields:

```text
domain
droplet_domain
public_host
```

`domain`, `droplet_domain`, and `public_host` are Manager/UI aliases only.

Before calling Agent `/v1/network/set-profile`, normalize:

```python
payload["host"] = payload.get("domain") or payload.get("droplet_domain") or payload.get("host")
payload["host_aliases"] = normalized_public_host_aliases
payload.pop("domain", None)
payload.pop("droplet_domain", None)
payload.pop("public_host", None)
```

The Agent schema must accept `host_aliases`, normalize/deduplicate it, remove the
canonical `host` if repeated, and forward the resulting aliases into runtime
Compose/env generation. The Agent may accept legacy host-name aliases leniently,
but the Manager should not rely on them.

## 16. Runtime public VPS invariant

For `target_mode="droplet"` and `network_profile="public_vps"`, generated runtime files must contain the explicit domain/public host.

Expected:

```text
KX_HOST=<domain>
KX_HOST_ALIASES=<comma-separated aliases or empty>
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost,<domain>,<aliases>,django-api,kx-<instance_id>-django-api
DJANGO_CSRF_TRUSTED_ORIGINS=https://<domain>,https://<alias-1>,...
NEXT_PUBLIC_API_BASE=/api
NEXT_PUBLIC_BACKEND_BASE=https://<domain>
```

Forbidden for public VPS:

```text
KX_HOST=127.0.0.1
DJANGO_ALLOWED_HOSTS=127.0.0.1
NEXT_PUBLIC_API_BASE=https://127.0.0.1/api
NEXT_PUBLIC_BACKEND_BASE=https://127.0.0.1
```

## 17. Traefik runtime invariant

Runtime compose for public VPS must route by the public host.

If Traefik uses file provider, the generated dynamic file must include:

```yaml
http:
  routers:
    kx-frontend:
      rule: "Host(`<domain>`) && PathPrefix(`/`)"
      entryPoints:
        - websecure
      tls: {}
      service: kx-frontend
      priority: 1

    kx-api:
      rule: "Host(`<domain>`) && PathPrefix(`/api/`)"
      entryPoints:
        - websecure
      tls: {}
      service: kx-api
      priority: 100

    kx-admin:
      rule: "Host(`<domain>`) && PathPrefix(`/admin/`)"
      entryPoints:
        - websecure
      tls: {}
      service: kx-api
      priority: 100

  services:
    kx-frontend:
      loadBalancer:
        servers:
          - url: "http://kx-<instance_id>-frontend-next:3000"

    kx-api:
      loadBalancer:
        servers:
          - url: "http://kx-<instance_id>-django-api:5000"
```

If Traefik uses Docker labels, labels must be attached to the correct target services and `traefik.enable=true` must be present.

Do not rely on labels when the runtime Traefik instance is configured only with file provider.

## 18. Healthcheck invariant

Django healthcheck must not depend on tools missing from the image.

Do not generate this:

```text
wget -qO- http://127.0.0.1:5000/api/health/
```

Use a Python socket check:

```text
python -c "import socket; sock=socket.create_connection(('127.0.0.1',5000),5); sock.close()"
```

Do not generate malformed hybrid commands such as:

```text
python -c "... sock.close()"api/health/ >/dev/null 2>&1 || exit 1
```

## 19. Builder/runtime image invariant

A Droplet-deployable capsule must include runtime image archives.

A verified capsule must not pass if it only contains:

```text
images/README.json
```

The capsule must include required `.oci.tar` images or verification must fail.

Required app-owned image archives:

```text
images/frontend-next.oci.tar
images/django-api.oci.tar
```

Any required proxy/runtime images declared as capsule-owned must also exist as image archives.

The frontend runtime image must not download tooling at runtime. It must run Next.js directly:

```text
node node_modules/next/dist/bin/next start -H 0.0.0.0 -p 3000
```

The frontend runtime image must include:

```text
package.json
node_modules
.next
public
next.config.*
env.mjs
```


