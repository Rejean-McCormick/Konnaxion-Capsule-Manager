# Netcup / progress update — 2026-09-09

- Droplet defaults are isolated from local/intranet state so Netcup fields do not inherit `demo-001` or a Windows runtime path.
- Netcup defaults remain `konnaxion-prod`, `2.56.97.41`, `/opt/konnaxion`, `/opt/konnaxion/capsules`, and `2.56.97.41.sslip.io`.
- **Copy Capsule to Droplet** now runs as a persistent background operation job with a browser progress page.
- Capsule upload progress reports transferred bytes, percentage, and approximate MiB/s.
- **Deploy Droplet** now runs as a persistent background operation job with phase progress.
- Deploy step 4 defaults to `copy_capsule=false` because step 3 already uploaded the capsule; this avoids a second ~1.3 GB transfer.
- Build progress no longer shows a transient `Build job not found` 404 while the job file is becoming visible; it waits and refreshes.
- Existing synchronous service/API behavior remains available when `background_job` is not requested.

## v4 — target-aware runtime observability

- Fixed **Logs** for Droplet targets: `view_logs` now calls the private remote Agent through SSH instead of the local Windows Agent.
- Fixed **Health** and **Instance Status** with the same target-aware routing.
- Droplet routing fields now survive runtime form validation through an explicit allowlist; unrelated submitted fields are still discarded.
- Logs/Health/Status pages preserve the current Droplet target metadata when submitting their forms.
- If the remote compose state does not exist yet, the GUI now explains that **Deploy Droplet** must run first instead of surfacing a confusing local Windows compose path.
- Added focused target-routing tests. Full-suite comparison remains at the same 107 pre-existing failures; v3: 828 passed, v4: 833 passed (+5), 2 skipped.
