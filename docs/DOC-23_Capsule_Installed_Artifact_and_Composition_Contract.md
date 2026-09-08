doc_id: DOC-23
title: Capsule Installed Artifact and Composition Contract
project: Konnaxion Capsule
app_version: v14
param_version: kx-param-2026.04.30
status: canonical
owner: Konnaxion Architecture
last_updated: 2026-09-08
depends_on:
  - DOC-02_Konnaxion_Capsule_Architecture.md
  - DOC-03_Konnaxion_Capsule_Format.md
  - DOC-20_Konnaxion_Package_Types_and_Artifact_Lifecycle.md
---

# DOC-23 — Capsule Installed Artifact and Composition Contract

## 1. Architectural rule

> Capsule manages the presence and lifecycle of an artifact; a product remains
> owner of its product experience; a composition host may compose public
> contributions; none becomes a business-runtime prerequisite for the others.

Capsule answers:

```text
What is installed?
Which version?
Which artifact class?
Which public entrypoints exist?
Which public integration manifest is available?
Which dependencies/capabilities are required or provided?
What is the lifecycle/readiness state?
```

Capsule does **not** answer:

```text
How should an Orgo Case page render?
Which Konnaxion navigation item belongs in which menu?
How should a Koali inspector behave?
How should Spaces compose product presentation state?
```

## 2. Boundary

```text
                   CAPSULE
                      │
        install / remove / discover / launch
                      │
        ┌─────────────┼─────────────┐
        │             │             │
      Orgo        Konnaxion      Kristal
        │             │             │
   standalone     standalone     standalone
        │             │             │
        └────── public contributions ──────┘
                      │
                 kOA Spaces
            integrated composition
```

## 3. Artifact classes

The canonical descriptor uses `artifact.kind`:

```text
library
product
composition_host
```

Examples:

```text
library           koali-ui, interface-contracts
product           orgo, konnaxion, kristal
composition_host  koa-spaces
```

Only `product` artifacts are candidates for a product switcher projection.
Libraries and composition hosts remain discoverable in the full registry.

## 4. Descriptor contract

A source tree may provide:

```text
capsule-artifact.yaml
```

The Builder validates it and packages the canonical public descriptor as:

```text
artifact.yaml
```

Schema:

```text
kx-artifact-descriptor/v1
```

Example:

```yaml
schema_version: kx-artifact-descriptor/v1
artifact:
  id: orgo
  version: 1.4.0
  kind: product

lifecycle:
  removable: true
  standalone: true

runtime:
  backend:
    entrypoint: /orgo/api
    healthcheck: /orgo/health

ui:
  standalone:
    enabled: true
    entrypoint: /orgo
  integrated:
    enabled: true
    manifest: koali-integration.yaml
    contract: koali-ui/v1

dependencies:
  required:
    - id: koali-ui
      version: ">=1"
      interface: public
  optional_integrations:
    - product: konnaxion
      capability: konnaxion.case-link

capabilities:
  provides:
    - orgo.cases
  requires:
    - koali-ui-contract
```

The Builder copies a declared integrated manifest into `contributions/` and
rewrites `artifact.yaml` to the packaged relative path. Capsule verifies file
presence/trust/contract admission but does not parse product UX semantics.

If `capsule-artifact.yaml` is absent, the current Konnaxion Builder emits a
backward-compatible Konnaxion `product` descriptor automatically.

## 5. Runtime manifest versus artifact descriptor

These are deliberately separate contracts:

```text
manifest.yaml
  runtime topology, images, profiles, package/runtime metadata

artifact.yaml
  installable identity, lifecycle, public entrypoints, dependencies,
  capabilities, public contribution reference

contributions/*
  product-owned integration manifests
```

Do not move product routes/navigation/commands/inspectors into `manifest.yaml`.

## 6. Standalone and integrated independence

The registry tracks separate projections:

```text
runtime.status
standalone_ui.status
integration.status
functional
```

An unavailable or rejected integration contribution does not make an otherwise
working product unhealthy.

Example:

```text
runtime          available
standalone_ui    available
integration      rejected
functional       true
```

Spaces may be absent and the product remains standalone-capable.

## 7. Installed registry

The Agent owns the persistent registry:

```text
<KX_ROOT>/shared/registry/installed-artifacts.json
```

Schema:

```text
kx-installed-artifact-registry/v1
```

Every mutation increments:

```text
generation
```

A composition host can refresh only when the generation changes.

Manager/Agent discovery projections include:

```text
GET /v1/artifacts
GET /v1/artifacts?products_only=true
GET /v1/artifacts?composition_candidates_only=true
GET /v1/artifacts/{artifact_id}
GET /v1/artifacts/{artifact_id}/integration-manifest
```

The ProductSwitcher must derive candidates from the installed registry. For an
integrated ProductSwitcher, the canonical projection is
`composition_candidates_only=true`: it contains only installed `product`
artifacts whose public integration contribution was admitted.
`products_only=true` remains the broader inventory of installed products. Neither
projection may hard-code Orgo/Konnaxion/Kristal.

## 8. Integration admission

A product can remain installed and functional while its integrated contribution
is rejected.

Admission requires at least:

```text
artifact kind is product
integrated UI enabled
capsule verified/trusted
manifest path stays inside extracted capsule
manifest exists
integration contract is allowlisted/supported
```

Default supported contract identifiers are currently:

```text
koali-ui/v1
module-interface-manifest/v1
```

They can be configured with:

```text
KX_ALLOWED_INTEGRATION_CONTRACTS
```

Capsule does not turn the admitted manifest into navigation. Spaces does.

## 9. Dependency rules

Required artifact dependencies must already be installed before registration.
Required capabilities must have an installed provider.

Private cross-artifact dependencies are forbidden in the descriptor:

```yaml
# forbidden
required:
  - id: konnaxion
    interface: private
```

Use a public artifact contract/capability instead:

```yaml
required:
  - id: koali-ui
    interface: public
```

Optional product integration belongs under `optional_integrations` and does not
make either product a prerequisite of the other.

Code-level private imports between repositories/packages remain a CI/static
architecture responsibility in addition to this manifest-level validation.

## 10. Removal contract

Canonical CLI:

```text
kx artifact remove <artifact-id>
```

Removal is fail-closed. The Agent blocks removal when:

```text
artifact.lifecycle.removable = false
another installed artifact requires it
its exclusively-provided capability is still required
an active instance still references its capsule
```

When guards pass:

```text
remove capsule/extracted runtime artifact
remove registry entry
increment registry generation
preserve instance/business data by default
```

Optional integrations do not block removal.

This implements the architectural sequence while refusing to destroy active
runtime/data implicitly. Stopping/detaching active instances remains an
explicit lifecycle action before artifact removal.

## 11. kOA Spaces contract

Spaces consumes the registry and public manifests only:

```text
Capsule installed registry
        ↓
composition_candidates_only projection
        ↓
public admitted contribution manifest
        ↓
kOA Spaces composition
        ↓
ProductSwitcher / surfaces / navigation / commands / inspectors
```

Spaces must not require a compile-time array such as:

```ts
const products = [orgo, konnaxion, kristal]
```

## 12. Mandatory invariants/tests

```text
install Orgo alone
→ standalone remains functional

install Orgo + Spaces
→ Orgo public contribution discoverable

remove Spaces
→ Orgo remains functional

install Orgo + Konnaxion + Spaces
→ both products discoverable

remove Orgo
→ Konnaxion unaffected
→ Spaces can refresh from registry generation
→ Orgo disappears from products projection

remove Konnaxion
→ Orgo unaffected

missing optional integration
→ product remains functional

private cross-artifact dependency
→ descriptor rejected

shared koali-ui library
→ allowed
→ absent from products_only projection

unknown/untrusted UI manifest
→ integration rejected
→ product remains functional

required dependent exists
→ removal blocked
```

## 13. Final ownership table

```text
CAPSULE
installation/removal/version/dependency resolution/runtime entrypoints/
standalone entrypoints/installed registry/manifest discovery/readiness projection

kOA SPACES
integrated frame/ProductSwitcher/surface selection/navigation composition/
commands/inspectors/presentation state

PRODUCT
business behavior/product routes/product surfaces/product commands/
product inspectors/product standalone app

KOALI-UI
shared shell primitives/design system/navigation primitives/context header/
command palette/inspector mechanics/responsive behavior
```
