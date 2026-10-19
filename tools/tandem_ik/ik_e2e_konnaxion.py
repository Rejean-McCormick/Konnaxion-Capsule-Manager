"""Local FakePlant E2E qualification. Executed through manage.py shell -c.

Modes: inspect | apply | status. Only apply writes. Never prints tokens.
"""
import json
import os
from uuid import UUID

from django.conf import settings
from django.db import connections
from django.utils import timezone
from konnaxion.ethikos.models import DecisionRecord, InteractionEmission, OrgoImpactPublication
from konnaxion.integrations.interaction_kernel.services import (
    enqueue_decision_execution,
    publish_decision_record,
)
from konnaxion.worlds.models import World, WorldRelease
from konnaxion.worlds.services.tasks import current_world_task_scope

PREFIX = "KOA_FAKEPLANT_E2E_JSON="
EXPECTED_ORG = "0c2d7244-a0c0-4a10-8f11-0389f275f133"
EXPECTED_WORLD = "fp-electromecanique-informatique"
EXPECTED_RELEASE = 132
EXPECTED_URL = "http://127.0.0.1:4000/api/v3/ik/interactions"
TITLE = "KOA IK E2E FakePlant - test synthétique unique V1"
MODE = os.getenv("KOA_FAKEPLANT_E2E_MODE", "inspect").strip().lower()


def emit(**data):
    print(PREFIX + json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":")))


def check():
    if MODE not in {"inspect", "apply", "status"}:
        raise RuntimeError("Mode interdit")
    if connections["default"].settings_dict.get("NAME") != "neondb":
        raise RuntimeError("Base Konnaxion inattendue")
    if not settings.DEBUG:
        raise RuntimeError("Refus hors DEBUG local")
    if str(getattr(settings, "IK_ORGO_TARGET_ORGANIZATION", "")) != EXPECTED_ORG:
        raise RuntimeError("UUID IK incorrect")
    if str(getattr(settings, "IK_ORGO_INTERACTIONS_URL", "")) != EXPECTED_URL:
        raise RuntimeError("URL Orgo non locale/inattendue")
    if not getattr(settings, "IK_ORGO_TOKEN", "") or not getattr(settings, "IK_ORGO_INBOUND_TOKEN", ""):
        raise RuntimeError("Jetons IK non charges")
    world = World.objects.select_related("universe", "current_release").get(key=EXPECTED_WORLD)
    release = world.current_release
    if world.universe.key != "fakeplant" or world.status != World.STATUS_ACTIVE:
        raise RuntimeError("World FakePlant inactif ou Universe invalide")
    if release is None or release.pk != EXPECTED_RELEASE or release.world_id != world.pk:
        raise RuntimeError("Release courante modifiee")
    if release.status != WorldRelease.STATUS_CURRENT or release.is_dirty:
        raise RuntimeError("Release courante non propre")
    return world, release


def snapshot():
    rows = list(DecisionRecord.objects.filter(title=TITLE).order_by("pk")[:2])
    if len(rows) > 1:
        raise RuntimeError("Plusieurs decisions E2E trouvees : intervention manuelle requise")
    decision = rows[0] if rows else None
    emissions = list(InteractionEmission.objects.filter(
        subject_type="decision", subject_id=str(decision.pk)
    ).order_by("pk")) if decision else []
    if len(emissions) > 1:
        raise RuntimeError("Plusieurs emissions liees a la decision E2E")
    emission = emissions[0] if emissions else None
    impacts = []
    if emission is not None:
        for item in OrgoImpactPublication.objects.filter(organization_id=UUID(EXPECTED_ORG), published_at__gte=decision.created_at).order_by("pk"):
            envelope = item.request_json if isinstance(item.request_json, dict) else {}
            payload = envelope.get("data", {}) if isinstance(envelope.get("data"), dict) else {}
            signal_input = payload.get("interaction", {}) if isinstance(payload.get("interaction"), dict) else {}
            if (signal_input.get("interaction_id") == str(emission.interaction_id)
                or item.correlation_id == f"decision:{decision.pk}"):
                impacts.append({"id": item.pk, "external_reference": item.external_reference, "checkpoint": item.checkpoint})
    return {
        "decision_id": decision.pk if decision else None,
        "decision_status": decision.status if decision else None,
        "interaction_id": str(emission.interaction_id) if emission else None,
        "emission_status": emission.status if emission else None,
        "emission_attempts": emission.attempts if emission else 0,
        "emission_last_error_code": emission.last_error_code if emission else None,
        "emission_receipt_status": (emission.receipt_json or {}).get("status") if emission else None,
        "impact_count": len(impacts),
        "impacts": impacts,
    }


try:
    world, release = check()
    # World-owned decision/emission operations must use the exact World/release scope.
    # A status check is also done within that scope to avoid crossing World schemas.
    with current_world_task_scope(world_id=world.pk, release_id=release.pk):
        before = snapshot()
        if MODE == "apply" and before["decision_id"] is None:
            row = DecisionRecord.objects.create(
                title=TITLE,
                description="Decision purement synthetique pour qualification IK Konnaxion ↔ Orgo; aucune action externe.",
                status=DecisionRecord.STATUS_CLOSED,
                closed_at=timezone.now(),
                baseline_result_json={"result": "approved", "synthetic": True, "demo": "fakeplant-ik-e2e-v1"},
                reading_result_refs=[],
            )
            publish_decision_record(decision_id=row.pk)
            enqueue_decision_execution(
                decision_id=row.pk,
                target_organization=EXPECTED_ORG,
                target_world=EXPECTED_WORLD,
                execution_scope={"synthetic": True, "demo_id": "fakeplant-ik-e2e-v1", "world": EXPECTED_WORLD},
            )
        elif MODE == "apply" and before["decision_id"] is not None:
            print("[INFO] Test déjà créé; aucune deuxième décision ni émission ajoutée.")
        after = snapshot()
    emit(ok=True, mode=MODE, world=world.key, world_id=world.id, release_id=release.pk, organization_id=EXPECTED_ORG,
         **after)
except Exception as exc:
    emit(ok=False, mode=MODE, error=type(exc).__name__, detail=str(exc)[:350])
    raise
