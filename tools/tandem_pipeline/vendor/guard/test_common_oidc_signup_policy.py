"""No automatic human account provisioning from a shared OIDC issuer."""
from django.test import override_settings
from konnaxion.users.adapters import SocialAccountAdapter


def test_common_oidc_prelinked_only_by_default():
    with override_settings(COMMON_OIDC_ENABLED=True, COMMON_OIDC_ALLOW_AUTO_SIGNUP=False,
                           ACCOUNT_ALLOW_REGISTRATION=True):
        assert SocialAccountAdapter().is_open_for_signup(None, None) is False


def test_no_change_to_local_standalone_social_signup_policy():
    with override_settings(COMMON_OIDC_ENABLED=False, COMMON_OIDC_ALLOW_AUTO_SIGNUP=False,
                           ACCOUNT_ALLOW_REGISTRATION=True):
        assert SocialAccountAdapter().is_open_for_signup(None, None) is True


def test_explicit_exemption_still_respects_registration_policy():
    with override_settings(COMMON_OIDC_ENABLED=True, COMMON_OIDC_ALLOW_AUTO_SIGNUP=True,
                           ACCOUNT_ALLOW_REGISTRATION=False):
        assert SocialAccountAdapter().is_open_for_signup(None, None) is False
