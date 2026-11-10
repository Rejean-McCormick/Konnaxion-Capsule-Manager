# Branch 08 — Agent-measured ReleaseSet and independent KonnaxionDiag admission

**Status:** owner source implementation + synthetic bilateral tests; no live VPS activation or deployment.

## Authority and order

`kx_agent/security/release_admission.py` reads the Agent-owned policy for the instance, pins an immutable ReleaseSet and trusted public keys, measures actual Git source identity, capsule manifest, component file inventory, rendered Docker Compose service images and their immutable image IDs, then calls `KonnaxionDiag.assurance.pep.verify_activation_admission`. The signed ReleaseSet **selects** what is measured but cannot provide its own measurement. Any missing/modified component, untrusted key, changed image, signature mismatch, policy mismatch or denial blocks before down/migration/up.

The Agent is the only owner of real action execution. KonnaxionDiag verifies admission evidence; diagnostic PASS does not grant a host credential or bypass the PEP. A Manager UI acceptance is not evidence that a privileged host effect has completed.

## Optional real two-repository test

From this repository, with the KonnaxionDiag source checkout independently supplied:

```console
KONNAXIONDIAG_SOURCE=/path/to/KonnaxionDiag python -m pytest -q tests/test_branch08_bilateral_admission.py
```

The test generates an isolated temporary Git source tree, deterministic component files and an ephemeral Ed25519 authority. It verifies one end-to-end Agent measurement → **real** KonnaxionDiag PEP admission, then independently rejects changed image identity and tampered signed verdict. Docker is simulated and no real host, service, private operator key or deployed credential is used. Tests skip without the other checkout.

## Remaining target gates

Real host verification must select the exact profile/release/channel, use actual Docker image measurement and privileged agent PEP, issue a durable activation receipt, verify product readiness, and prove rollback/last-known-good. The selected Linux/Docker VPS profile is not a universal requirement for every kOA product. See KonnaxionDiag branch notes and the kOA-Linux release contract before any activation request.
