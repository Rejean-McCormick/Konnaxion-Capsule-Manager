---
doc_id: DOC-23
title: Capsule Product Registry and Koali Discovery Contract
project: Konnaxion Capsule Manager
status: canonical
last_updated: 2026-09-08
depends_on:
  - DOC-00_Konnaxion_Canonical_Variables.md
  - DOC-02_Konnaxion_Capsule_Architecture.md
  - DOC-03_Konnaxion_Capsule_Format.md
  - DOC-04_Konnaxion_Manager_Architecture.md
  - DOC-20_Konnaxion_Package_Types_and_Artifact_Lifecycle.md
---

# DOC-23 — Capsule Product Registry and Koali Discovery Contract

## 1. Purpose

This document defines the boundary between **Capsule deployment/lifecycle**, a
**product's own UX**, shared UI libraries such as `koali-ui`, and optional
composition hosts such as Koali Spaces.

The core invariant is:

> Capsule manages presence, version, dependencies, entrypoints and lifecycle.
> The product remains owner of product behavior and product UX. A composition
> host may discover and compose public contributions without becoming a required
> functional dependency of the product.

This contract is generic even though the current implementation lives in the
Konnaxion Capsule Manager repository.

## 2. Ownership

```text
CAPSULE
owns deployment identity and lifecycle

PRODUCT
owns product behavior and product UX

KOALI-UI
owns shared UI primitives/contracts

SPACES
owns optional multi-product composition
```

No integration may turn:

```text
Product -> Spaces
```

into a mandatory runtime dependency.

## 3. Capsule is not an UX registry

Capsule may know:

```text
artifact identity
artifact kind
version
installed state
required dependencies
optional integrations
runtime entrypoint
standalone entrypoint
public integration manifest location
declared capabilities
readiness/admission state
```

Capsule must not own or interpret:

```text
product routes
product navigation
sidebar construction
surface composition
inspectors
product commands
workspace layout
```

Those definitions remain product-owned.

## 4. Artifact classification

Deployment/discovery uses the closed `artifact_kind` set:

```text
library
product
composition_host
```

Examples:

```text
koali-ui   -> library
orgo       -> product
konnaxion  -> product
koa-spaces -> composition_host
```

This classification is different from package/archive `kind` values such as
`konnaxion-runtime` or `konnaxion-backup`.

A package answers **what archive is this?**

`artifact_kind` answers **what installed architectural role does this artifact
have?**

## 5. Product descriptor

Canonical location inside a Capsule-managed artifact:

```text
metadata/product.yaml
```

Canonical schema:

```yaml
schema_version: kx-product/v1

artifact:
  id: orgo
  kind: product
  version: 1.4.0
  display_name: Orgo

runtime:
  entrypoint: /
  healthcheck: /health

standalone:
  entrypoint: /
  healthcheck: /health

dependencies:
  required:
    - id: koali-ui
      version: ">=2"
  optional_integrations:
    - product: konnaxion
      capability: topic.read

capabilities:
  - case.read
  - task.execute

integration:
  manifest: metadata/koali-integration.yaml
  contract: koali-ui/v1
  required_capabilities:
    - local_module_surface
```

The descriptor may point at public integration metadata but must not contain
product UX definitions itself.

The following root keys are forbidden in `product.yaml`:

```text
routes
navigation
sidebar
surfaces
commands
inspectors
widgets
```

## 6. Product-owned integration manifest

Canonical recommended location:

```text
metadata/koali-integration.yaml
```

This file is owned by the product.

It may describe public product contributions such as:

```text
surfaces
routes
navigation contributions
commands
inspectors
capability requirements
```

Capsule does not transform those definitions.

Capsule admission may verify only:

```text
the file exists
it is inside the signed artifact boundary
the declared integration contract version is supported
required host capabilities are satisfiable
artifact trust/signature requirements are satisfied
```

## 7. Independent readiness

Each product has three independent readiness dimensions:

```text
runtime_status
standalone_ui_status
integration_status
```

Example:

```text
runtime_status       ready
standalone_ui_status ready
integration_status   unavailable
product_status       functional
```

The key invariant is:

```text
Spaces absent
-> product standalone remains functional
```

Integration status must not determine product health.

Canonical integration states:

```text
ready
unavailable
rejected
unknown
```

Canonical runtime/standalone states:

```text
ready
degraded
unavailable
unknown
```

Canonical derived product states:

```text
functional
degraded
unavailable
```

## 8. Admission

An installed product contribution is admitted only after:

```text
installed
-> product descriptor valid
-> integration manifest declared
-> artifact trusted
-> integration manifest present in signed artifact
-> integration contract version supported
-> required host capabilities satisfiable
-> admitted
```

An integration failure does not uninstall or invalidate the product.

Example:

```text
Orgo runtime          ready
Orgo standalone       ready
Koali contribution    rejected
Orgo overall          functional
```

## 9. Registry

Capsule maintains a persistent registry with a monotonic generation counter.

Canonical persistence schema:

```text
kx-product-registry/v1
```

Every semantic change to installed/discovery state increments:

```text
registry_generation N -> N+1
```

Re-writing an identical record must not increment generation.

Restarting Capsule Manager/Agent must reconstruct the same projection without
inventing a new generation when installed state did not change.

## 10. Registry classes

Internally the registry may expose:

```text
Registry
├── libraries
│   └── koali-ui
├── products
│   ├── orgo
│   └── konnaxion
└── composition_hosts
    └── koa-spaces
```

A product selector consumes only the `products` projection.

It must never be hard-coded with product IDs.

## 11. Public projection

Canonical public endpoint:

```text
GET /registry/products
```

The Manager proxies the Agent-owned registry state.

Example:

```json
{
  "schema_version": "kx-product-registry/v1",
  "generation": 43,
  "products": [
    {
      "id": "orgo",
      "kind": "product",
      "version": "1.4.0",
      "state": "functional",
      "runtime_status": "ready",
      "standalone_ui_status": "ready",
      "integration_status": "ready",
      "standalone": {
        "available": true,
        "entrypoint": "/"
      },
      "integrated": {
        "available": true,
        "manifest": "metadata/koali-integration.yaml",
        "contract": "koali-ui/v1",
        "status": "ready"
      }
    }
  ]
}
```

The public projection contains references to admitted public manifests. It does
not contain a normalized copy of product navigation or surfaces.

## 12. Dependency rules

Required deployment dependencies are authoritative for lifecycle blocking.

```text
remove required dependency
-> BLOCK
-> return dependent list
```

Optional integrations do not block removal:

```text
remove optional integration target
-> allowed
-> consumer remains functional
-> affected integration becomes unavailable/degraded as applicable
```

Manifest validation checks declared dependencies.

CI architecture checks remain responsible for finding undeclared private source
imports that a deployment manifest cannot detect.

## 13. Removal transaction

The target lifecycle contract is:

```text
resolve dependents
-> BLOCK if required dependency exists
-> stop managed runtime where required
-> export/preserve data according to policy
-> unregister runtime
-> unregister standalone entrypoint
-> unregister public integration manifest
-> remove installed artifact
-> publish registry generation N+1
```

Registry removal code already enforces the required-dependent block. Full host
artifact deletion must call that rule before destructive filesystem/runtime
operations.

## 14. Legacy capsules

Existing capsules without `metadata/product.yaml` remain importable during the
migration period.

Their behavior is:

```text
capsule runtime import -> allowed
public product discovery -> unavailable until descriptor exists
```

A descriptor that is present must validate.

This preserves compatibility without silently inventing UX or integration
contracts for old packages.

## 15. Required architecture tests

At minimum:

```text
Spaces absent
-> Orgo/Konnaxion standalone product remains functional

invalid integration manifest
-> product installed
-> standalone functional
-> integration rejected

unsupported integration contract
-> same behavior

remove required dependency
-> blocked with dependent list

remove optional integration target
-> allowed
-> consumer remains functional

library artifact
-> absent from ProductSwitcher projection

composition_host artifact
-> absent from ProductSwitcher projection

registry semantic change
-> generation increments

identical registry rewrite
-> generation unchanged

Agent/Manager restart
-> registry projection reconstructed identically
```

## 16. Final invariant

```text
Capsule discovers products.
Capsule does not design products.

Products may integrate with Spaces.
Products do not require Spaces to function.

Spaces consumes admitted product contributions.
Spaces does not become owner of product behavior or product UX.
```
