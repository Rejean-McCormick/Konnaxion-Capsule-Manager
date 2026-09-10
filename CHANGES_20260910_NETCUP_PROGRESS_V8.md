# Netcup Progress v8

- Fixes Bootstrap Droplet Agent when the service is already running.
- After replacing `/opt/konnaxion/manager`, Bootstrap now explicitly restarts `konnaxion-agent`.
- This guarantees the remote Python process loads the current Agent API schema, including the current `/v1/network/set-profile` public VPS host fields.
- Adds a focused regression test for the restart behavior.
