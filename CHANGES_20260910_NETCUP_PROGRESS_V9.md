# Netcup Progress v9

- Fixed the current Manager/Agent `/v1/network/set-profile` contract mismatch.
- `NetworkSetProfileRequest` now accepts and forwards `capsule_id` and `capsule_version`, which the Agent dispatcher needs when regenerating runtime compose/env state.
- Fixed the next analogous `/v1/instances/start` contract mismatch proactively: current capsule identity and `force_recreate_after_image_load` are now accepted.
- Added focused regression tests for both current deploy payloads.
- Bootstrap/restart is still required once after installing v9 so the VPS Agent loads the corrected API schema.
