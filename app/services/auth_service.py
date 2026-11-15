import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time

from app.config import settings
from app.repositories import sqlite_repository

SESSION_COOKIE = "careloop_session"
SESSION_HOURS = 8
INVITATION_DAYS = 7
JOIN_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PASSWORD_ITERATIONS = 600_000
PASSWORD_MIN_LENGTH = 8
_DUMMY_PASSWORD_SALT = b"careloop-login-dummy"
VALID_ROLES = {"elder", "caregiver", "family"}
VALID_PERMISSIONS = {
    "medications",
    "medications_manage",
    "appointments",
    "appointments_manage",
    "activities",
    "activities_manage",
    "relay",
    "relay_manage",
    "approvals"
}


def hash_join_code(code: str) -> str:
    return hmac.new(
        settings.session_secret.encode("utf-8"),
        code.strip().upper().encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_email(email: str) -> bool:
    normalized = normalize_email(email)
    return (
        len(normalized) <= 254
        and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized) is not None
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS
    )
    return "$".join((
        "pbkdf2_sha256",
        str(PASSWORD_ITERATIONS),
        base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(password_hash).decode("ascii").rstrip("=")
    ))


def verify_password(password: str, encoded_hash: str | None) -> bool:
    try:
        algorithm, iteration_text, salt_text, expected_text = encoded_hash.split("$")
        iterations = int(iteration_text)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= iterations <= 1_000_000:
            return False
        salt_padding = "=" * (-len(salt_text) % 4)
        expected_padding = "=" * (-len(expected_text) % 4)
        salt = base64.urlsafe_b64decode(salt_text + salt_padding)
        expected = base64.urlsafe_b64decode(expected_text + expected_padding)
    except (AttributeError, ValueError):
        hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _DUMMY_PASSWORD_SALT,
            PASSWORD_ITERATIONS
        )
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(actual, expected)


def _generate_join_code() -> str:
    return "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(8))


def create_invitation(
    name: str,
    role: str,
    care_circle_id: str | None = None,
    permissions: tuple[str, ...] = ()
) -> str:
    name = name.strip()
    role = role.strip().lower()
    if not name or len(name) > 100:
        raise ValueError("The invitation must include a name of 1 to 100 characters.")
    if role not in VALID_ROLES:
        raise ValueError("Choose elder, caregiver, or family as the account role.")
    if role == "elder" and care_circle_id is not None:
        raise ValueError("An elder invitation creates an independent care circle.")
    if role != "elder" and care_circle_id is None:
        raise ValueError("Caregiver and family invitations must be linked to a care circle.")
    if not set(permissions).issubset(VALID_PERMISSIONS):
        raise ValueError("The invitation contains an unsupported access permission.")

    expires_at = int(time.time()) + INVITATION_DAYS * 24 * 60 * 60
    for _ in range(10):
        code = _generate_join_code()
        code_hash = hash_join_code(code)
        if sqlite_repository.code_hash_exists(code_hash):
            continue
        try:
            sqlite_repository.add_invitation(
                code_hash=code_hash,
                name=name,
                role=role,
                care_circle_id=care_circle_id,
                permissions=permissions,
                expires_at=expires_at
            )
        except sqlite3.IntegrityError:
            if sqlite_repository.code_hash_exists(code_hash):
                continue
            raise
        return code
    raise RuntimeError("Unable to generate a unique invitation code.")


def create_care_circle_invitation(
    inviter: dict,
    name: str,
    role: str,
    permissions: tuple[str, ...]
) -> str:
    if inviter["is_demo"]:
        raise ValueError("Synthetic demo accounts cannot invite real users.")
    if inviter["role"] != "elder":
        raise ValueError("Only an elder can invite a care-circle member.")
    if role not in {"caregiver", "family"}:
        raise ValueError("Elders may invite caregivers or family members.")

    circle_id = sqlite_repository.get_owned_care_circle(inviter["id"])
    if circle_id is None:
        raise RuntimeError("The elder account does not have a care circle.")
    return create_invitation(
        name=name,
        role=role,
        care_circle_id=circle_id,
        permissions=permissions
    )


def redeem_join_code(
    code: str,
    email: str | None = None,
    password: str | None = None
) -> dict | None:
    if (email is None) != (password is None):
        raise ValueError("Provide both email and password, or neither.")
    code_hash = hash_join_code(code)
    email = normalize_email(email) if email is not None else None
    password_hash = hash_password(password) if password is not None else None
    try:
        user = sqlite_repository.redeem_invitation(
            code_hash,
            email=email,
            password_hash=password_hash
        )
    except sqlite_repository.EmailAlreadyRegisteredError:
        raise
    except sqlite3.IntegrityError as error:
        if email is not None:
            raise sqlite_repository.EmailAlreadyRegisteredError from error
        raise
    if user is not None:
        return user
    if settings.enable_demo_accounts:
        return sqlite_repository.get_demo_user(code_hash)
    return None


def authenticate_account(email: str, password: str) -> dict | None:
    authentication = sqlite_repository.get_authentication_record(
        normalize_email(email)
    )
    if authentication is None or authentication["password_hash"] is None:
        hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _DUMMY_PASSWORD_SALT,
            PASSWORD_ITERATIONS
        )
        return None
    if not verify_password(password, authentication["password_hash"]):
        return None
    return authentication["user"]


def encode_token(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(
        settings.session_secret.encode("utf-8"),
        payload.encode("ascii"),
        hashlib.sha256
    ).digest()
    signed = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return payload + "." + signed


def create_session(user: dict) -> str:
    return encode_token({
        "user_id": user["id"],
        "expires_at": int(time.time()) + SESSION_HOURS * 3600
    })


def verify_session(token: str) -> dict | None:
    try:
        payload, supplied_signature = token.split(".", 1)
        expected_signature = base64.urlsafe_b64encode(
            hmac.new(
                settings.session_secret.encode("utf-8"),
                payload.encode("ascii"),
                hashlib.sha256
            ).digest()
        ).decode("ascii").rstrip("=")
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return None

        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        if not isinstance(data.get("expires_at"), int):
            return None
        if data["expires_at"] <= int(time.time()):
            return None
        user_id = data.get("user_id")
        if not isinstance(user_id, str):
            return None

        user = sqlite_repository.get_user(user_id)
        if user is None or (user["is_demo"] and not settings.enable_demo_accounts):
            return None
        return user
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
