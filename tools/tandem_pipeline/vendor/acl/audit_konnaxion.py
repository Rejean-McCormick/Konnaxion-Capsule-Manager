"""Django shell -c payload. Query only, no secrets emitted."""
import json
from django.conf import settings
from django.contrib.auth import get_user_model
from konnaxion.worlds.models import UniverseMembership, WorldMembership, Universe, World

U = get_user_model()
people = list(U.objects.filter(username__startswith='demo_fp_', is_klone=True).only('id','is_active','account_type','is_klone'))
universe = Universe.objects.filter(key='fakeplant',status='active').first()
worlds = World.objects.filter(universe=universe,status='active').count() if universe else 0
universe_count = UniverseMembership.objects.filter(universe=universe, is_active=True).count() if universe else 0
world_count = WorldMembership.objects.filter(world__universe=universe, is_active=True).count() if universe else 0
human_count = (U.objects.filter(universe_memberships__universe=universe,
  universe_memberships__is_active=True, is_active=True, is_klone=False,
  account_type=U.TYPE_HUMAN).distinct().count() if universe else 0)
issuer = str(getattr(settings,'COMMON_OIDC_SERVER_URL','') or '').rstrip('/') if getattr(settings,'COMMON_OIDC_ENABLED',False) else ''
result = {
 'ok':True,'database_expected':str(settings.DATABASES['default']['NAME'])=='neondb',
 'universe_active':bool(universe),'world_count':worlds,
 'persona_count':len(people),'noninteractive_personas':sum(not x.can_interactive_login for x in people),
 'universe_memberships':universe_count,'world_memberships':world_count,'active_humans_in_universe':human_count,
 'oidc_enabled':bool(getattr(settings,'COMMON_OIDC_ENABLED',False)),
 'oidc_issuer':issuer,
 'oidc_client_configured':bool(getattr(settings,'COMMON_OIDC_CLIENT_ID','')),
 'oidc_client_secret_configured':bool(getattr(settings,'COMMON_OIDC_CLIENT_SECRET','')),
 'oidc_auto_signup_allowed':bool(getattr(settings,'COMMON_OIDC_ALLOW_AUTO_SIGNUP',True)),
 'email_auto_connect_enabled':bool(getattr(settings,'SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT',False)),
 'database_read_only':True,
}
print('KOA_SSO_KX_AUDIT='+json.dumps(result, separators=(',',':')))
