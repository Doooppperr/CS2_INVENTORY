from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from werkzeug.security import generate_password_hash

from .models import User, utcnow


def _fernet() -> Fernet:
    configured = str(current_app.config.get("PASSWORD_VAULT_KEY") or "").strip()
    secret = configured or str(current_app.config["SECRET_KEY"])
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def set_user_password(user: User, password: str) -> None:
    """Update the authentication hash and the admin-recoverable copy together."""
    user.password_hash = generate_password_hash(password)
    user.password_ciphertext = _fernet().encrypt(password.encode("utf-8")).decode("ascii")
    user.password_changed_at = utcnow()


def recover_user_password(user: User) -> str | None:
    if not user.password_ciphertext:
        return None
    try:
        return _fernet().decrypt(user.password_ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, ValueError):
        return None
