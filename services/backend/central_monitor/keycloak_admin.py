import secrets
from typing import Any

import httpx

from .settings import get_settings


class KeycloakUserExistsError(RuntimeError):
    """Raised when Keycloak already has a user with the given email/username."""


class KeycloakAdminClient:
    """Thin wrapper around the Keycloak Admin REST API, used only to provision the
    login account for a user created from the Nexora "Usuários" screen. Nexora never
    stores passwords itself - the account always lives in Keycloak (see auth.py's
    OIDC/JWT flow); this client exists so an admin doesn't have to open the Keycloak
    console by hand for every new person.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.keycloak_base_url.rstrip("/")
        self.realm = settings.keycloak_realm
        self.admin_username = settings.keycloak_admin_username
        self.admin_password = settings.keycloak_admin_password

    def _admin_token(self) -> str:
        response = httpx.post(
            f"{self.base_url}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": self.admin_username,
                "password": self.admin_password,
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def create_user(self, email: str, display_name: str) -> dict[str, Any]:
        """Creates the Keycloak user with a random temporary password the person must
        change on first login. Returns {"id": <keycloak user id>, "temporary_password": str}.
        """
        token = self._admin_token()
        headers = {"Authorization": f"Bearer {token}"}
        first_name, _, last_name = display_name.strip().partition(" ")
        temporary_password = secrets.token_urlsafe(12)
        response = httpx.post(
            f"{self.base_url}/admin/realms/{self.realm}/users",
            headers=headers,
            json={
                "username": email,
                "email": email,
                "firstName": first_name or display_name,
                "lastName": last_name,
                "enabled": True,
                "emailVerified": False,
                "credentials": [{"type": "password", "value": temporary_password, "temporary": True}],
            },
            timeout=15,
        )
        if response.status_code == 409:
            raise KeycloakUserExistsError(f"Keycloak already has a user for {email}")
        response.raise_for_status()
        location = response.headers["Location"]
        user_id = location.rstrip("/").rsplit("/", 1)[-1]
        return {"id": user_id, "temporary_password": temporary_password}
