# Netcup Progress v10

- Deploy Droplet now verifies the local capsule with the same trusted public-key file exposed by the launcher/Verify Capsule UI when available.
- Deploy verification always targets the local `.kxcap`; remote capsule verification remains the Agent/Security Gate responsibility.
- Failed Deploy verification now records the Builder return code/stdout/stderr instead of collapsing every cause to `Capsule verification failed.`.
- Added focused regression tests for public-key propagation and detailed verification failures.
