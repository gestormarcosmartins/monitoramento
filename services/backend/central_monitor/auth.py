from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, Header, HTTPException, status
from jwt import PyJWKClient

from .settings import get_settings


@dataclass(frozen=True)
class Identity:
    subject: str
    email: str
    display_name: str


@lru_cache
def jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url)


def current_identity(
    authorization: str | None = Header(default=None),
    x_dev_user: str | None = Header(default=None),
    x_dev_email: str | None = Header(default=None),
) -> Identity:
    settings = get_settings()
    if settings.dev_auth_bypass:
        subject = x_dev_user or "local-admin"
        return Identity(
            subject=subject,
            email=x_dev_email or "admin@central-monitor.local",
            display_name=subject,
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    if not settings.oidc_issuer:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OIDC issuer not configured")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        jwks_url = settings.oidc_jwks_url or f"{settings.oidc_issuer.rstrip('/')}/protocol/openid-connect/certs"
        signing_key = jwks_client(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=settings.oidc_issuer,
            options={"verify_aud": False},
        )
        audiences = claims.get("aud", [])
        if isinstance(audiences, str):
            audiences = [audiences]
        if settings.oidc_audience not in audiences and claims.get("azp") != settings.oidc_audience:
            raise jwt.InvalidAudienceError("Token was not issued for this client")
    except jwt.PyJWTError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from error

    return Identity(
        subject=claims["sub"],
        email=claims.get("email", ""),
        display_name=claims.get("name") or claims.get("preferred_username") or claims["sub"],
    )


def tenant_header(x_tenant_id: str = Header(alias="X-Tenant-ID")) -> str:
    if not x_tenant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="X-Tenant-ID required")
    return x_tenant_id


IdentityDependency = Depends(current_identity)
TenantDependency = Depends(tenant_header)

