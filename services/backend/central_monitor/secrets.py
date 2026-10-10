import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .settings import get_settings


def _cipher() -> Fernet:
    digest = hashlib.sha256(get_settings().central_secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str | None) -> str | None:
    return _cipher().encrypt(value.encode("utf-8")).decode("ascii") if value else None


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    try:
        return _cipher().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as error:
        raise RuntimeError("Não foi possível descriptografar a credencial salva") from error
