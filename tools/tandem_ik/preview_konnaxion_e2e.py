"""FakePlant IK E2E preflight. Read-only Django management-shell payload."""
import json
from django.conf import settings
from django.db import connections
from konnaxion.worlds.models import World

EXPECTED_WORLD = "fp-electromecanique-informatique"
EXPECTED_RELEASE_ID = 132
EXPECTED_ORG = "0c2d7244-a0c0-4a10-8f11-0389f275f133"

world = World.objects.select_related("universe", "current_release").get(key=EXPECTED_WORLD)
release = world.current_release
result = {
    "ok": bool(
        connections["default"].settings_dict.get("NAME") == "neondb"
        and world.universe.key == "fakeplant"
        and world.status == "active"
        and release is not None
        and release.id == EXPECTED_RELEASE_ID
        and release.world_id == world.id
        and release.status == "current"
        and not release.is_dirty
        and str(getattr(settings, "IK_ORGO_TARGET_ORGANIZATION", "")) == EXPECTED_ORG
        and str(getattr(settings, "IK_ORGO_INTERACTIONS_URL", "")) == "http://127.0.0.1:4000/api/v3/ik/interactions"
        and bool(getattr(settings, "IK_ORGO_TOKEN", ""))
        and bool(getattr(settings, "IK_ORGO_INBOUND_TOKEN", ""))
        and bool(getattr(settings, "DEBUG", False))
    ),
    "database": connections["default"].settings_dict.get("NAME"),
    "universe": world.universe.key,
    "world": world.key,
    "world_id": world.id,
    "world_status": world.status,
    "release_id": release.id if release else None,
    "release_number": release.release_number if release else None,
    "release_status": release.status if release else None,
    "release_dirty": release.is_dirty if release else None,
    "debug_local_http_allowed": bool(getattr(settings, "DEBUG", False)),
    "ik_target_uuid_matches": str(getattr(settings, "IK_ORGO_TARGET_ORGANIZATION", "")) == EXPECTED_ORG,
    "ik_origo_url_matches": str(getattr(settings, "IK_ORGO_INTERACTIONS_URL", "")) == "http://127.0.0.1:4000/api/v3/ik/interactions",
    "ik_outbound_configured": bool(getattr(settings, "IK_ORGO_TOKEN", "")),
    "ik_inbound_configured": bool(getattr(settings, "IK_ORGO_INBOUND_TOKEN", "")),
}
print("IK_E2E_KX_JSON=" + json.dumps(result, separators=(",", ":")))
