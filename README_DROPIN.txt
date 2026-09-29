Konnaxion Capsule Manager - Drop-In v1.12.3
=============================================

Target repository:
  C:\mycode\Konnaxion\Konnaxion_Capsule_Manager

Copy this package over that repository root and replace the matching files.

Current production VPS target already present in the source:
  2.56.97.41

This patch therefore does NOT change the production IP. It hardens the local
Konnaxion tools after the Netcup rebuild:

- SSH/SCP now use StrictHostKeyChecking=yes instead of accept-new.
- Auxiliary diagnostic / repair / refresh tools use the same fail-closed host
  key policy.
- BAT SCP/SSH calls now use BatchMode=yes and bounded connection timeouts.
- Regression test asserts that the Manager cannot silently reintroduce
  StrictHostKeyChecking=accept-new.

Operational consequence:
  The VPS host key must already be present in the operator's known_hosts.
  This is intentional. If Netcup reimages the VPS again, explicitly remove the
  old key, verify the new fingerprint, then enroll it before using Manager.

Current verified operator state from this rebuild:
  Host: 2.56.97.41
  known_hosts: %USERPROFILE%\.ssh\known_hosts

Validation performed on this patch:
  python -m py_compile ... : PASS
  pytest test_netcup_ssh_runtime_v7.py,
         test_ui_netcup_defaults.py,
         test_global_droplet_identity.py : 11 passed
