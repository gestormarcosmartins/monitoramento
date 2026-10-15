from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import unittest
from fastapi import HTTPException

from central_monitor import auth


def _settings(**over):
    base = dict(dev_auth_bypass=False, oidc_issuer="https://x/auth/realms/central-monitor", oidc_jwks_url="https://x/certs",
                oidc_audience="central-monitor", oidc_extra_clients="nexora-mobile")
    base.update(over)
    return SimpleNamespace(**base)


def _identity(claims, **over):
    with patch.object(auth, "get_settings", return_value=_settings(**over)), \
         patch.object(auth, "jwks_client", return_value=MagicMock(get_signing_key_from_jwt=lambda t: SimpleNamespace(key="k"))), \
         patch.object(auth.jwt, "decode", return_value=claims):
        return auth.current_identity(authorization="Bearer abc")


class ClientAudienceTests(unittest.TestCase):
    def test_aceita_token_do_client_mobile_por_azp_ou_audience(self):
        self.assertEqual(_identity({"sub": "u", "azp": "nexora-mobile"}).subject, "u")
        self.assertEqual(_identity({"sub": "u", "azp": "nexora-mobile", "aud": ["central-monitor"]}).subject, "u")
        self.assertEqual(_identity({"sub": "u", "azp": "central-monitor"}).subject, "u")

    def test_rejeita_client_desconhecido(self):
        with self.assertRaises(HTTPException) as e:
            _identity({"sub": "u", "azp": "outro-client", "aud": ["account"]})
        self.assertEqual(e.exception.status_code, 401)

    def test_extra_clients_vazio_so_aceita_o_principal(self):
        with self.assertRaises(HTTPException):
            _identity({"sub": "u", "azp": "nexora-mobile"}, oidc_extra_clients="")
