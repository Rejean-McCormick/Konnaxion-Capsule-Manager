# Konnaxion Capsule Manager Patch v1.11.9

Fixes Restore -> Test Restore on droplet targets.

## Fix

The Restore page test-restore form previously submitted a backup ID but no isolated target instance ID. The remote Agent correctly rejected the request with:

`Missing required field: target_instance_id, new_instance_id, test_instance_id`

This patch adds the required `target_instance_id` field to the Test Restore form, labelled **Test Instance ID**, with default value:

`konnaxion-restore-test`

The field remains editable. Test Restore continues to route to the Droplet over SSH and does not restore over the source production instance.

## Validation

39 targeted tests passed:
- `tests/test_ui_droplet_runtime_routing.py`
- `tests/test_backup_restore.py`
- `tests/test_agent_backup_action_v15.py`
