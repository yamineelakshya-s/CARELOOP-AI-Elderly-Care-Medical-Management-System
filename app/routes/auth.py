import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator

from app.config import settings
from app.dependencies import get_current_user
from app.repositories import sqlite_repository
from app.services.auth_service import (
    SESSION_COOKIE,
    SESSION_HOURS,
    authenticate_account,
    create_care_circle_invitation,
    create_session,
    hash_password,
    normalize_email,
    redeem_join_code,
    validate_email
)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

# In-memory protection is suitable for the single-process demo deployment.
# Use a shared rate limiter when deploying multiple application instances.
login_attempts = {}
login_locked_until = {}
MAX_ATTEMPTS = 5
WINDOW_SECONDS = 900
LOCKOUT_SECONDS = 900


class JoinRequest(BaseModel):
    join_code: str = Field(
        min_length=8,
        max_length=8,
        pattern=r"^[A-Za-z0-9]{8}$"
    )
    email: str | None = Field(default=None, max_length=254)
    password: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_optional_credentials(self):
        if self.email is None and self.password is None:
            return self
        if self.email is None or self.password is None:
            raise ValueError("Provide both email and password.")
        if not validate_email(self.email):
            raise ValueError("Enter a valid email address.")
        if len(self.password) < 8:
            raise ValueError("Passwords must be at least 8 characters.")
        return self


class AccountLogin(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_email_address(self):
        if not validate_email(self.email):
            raise ValueError("Enter a valid email address.")
        return self


class CredentialSetup(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)

    @model_validator(mode="after")
    def validate_email_address(self):
        if not validate_email(self.email):
            raise ValueError("Enter a valid email address.")
        return self


class InvitationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: Literal["caregiver", "family"]
    permissions: list[
        Literal[
            "medications",
            "medications_manage",
            "appointments",
            "appointments_manage",
            "activities",
            "activities_manage",
            "relay",
            "relay_manage",
            "approvals"
        ]
    ]


def check_rate_limit(client_ip: str):
    now = time.time()
    locked_until = login_locked_until.get(client_ip, 0)
    if locked_until > now:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Please try again in 15 minutes.",
            headers={"Retry-After": str(max(1, int(locked_until - now)))}
        )
    if locked_until:
        login_locked_until.pop(client_ip, None)

    attempts = [
        stamp for stamp in login_attempts.get(client_ip, [])
        if now - stamp < WINDOW_SECONDS
    ]
    login_attempts[client_ip] = attempts



def record_failed_attempt(client_ip: str):
    now = time.time()
    attempts = [
        stamp for stamp in login_attempts.get(client_ip, [])
        if now - stamp < WINDOW_SECONDS
    ]
    attempts.append(now)
    if len(attempts) >= MAX_ATTEMPTS:
        login_attempts.pop(client_ip, None)
        login_locked_until[client_ip] = now + LOCKOUT_SECONDS
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Please try again in 15 minutes.",
            headers={"Retry-After": str(LOCKOUT_SECONDS)}
        )
    login_attempts[client_ip] = attempts


def clear_rate_limit_state():
    login_attempts.clear()
    login_locked_until.clear()


def _set_session_cookie(response: Response, user: dict) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_session(user),
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        path="/"
    )


def _account_response(user: dict) -> dict:
    return {
        **user,
        "has_credentials": sqlite_repository.has_user_credentials(user["id"])
    }


@router.post("/join")
def join_account(data: JoinRequest, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit(client_ip)

    try:
        user = redeem_join_code(data.join_code, data.email, data.password)
    except sqlite_repository.EmailAlreadyRegisteredError as error:
        raise HTTPException(
            status_code=409,
            detail="That email is already registered. Sign in to the existing account."
        ) from error

    if not user:
        record_failed_attempt(client_ip)
        raise HTTPException(
            status_code=401,
            detail=(
                "The invitation code is invalid, expired, or has already been used."
            )
        )

    login_attempts.pop(client_ip, None)
    login_locked_until.pop(client_ip, None)
    _set_session_cookie(response, user)

    return {
        "message": "Successfully joined CareLoop AI.",
        "user": _account_response(user)
    }


@router.post("/login")
def login_account(data: AccountLogin, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit(client_ip)
    user = authenticate_account(data.email, data.password)
    if user is None:
        record_failed_attempt(client_ip)
        raise HTTPException(
            status_code=401,
            detail="Email or password is incorrect."
        )
    login_attempts.pop(client_ip, None)
    login_locked_until.pop(client_ip, None)
    _set_session_cookie(response, user)
    return {
        "message": "You are signed in.",
        "user": _account_response(user)
    }


@router.post("/credentials", status_code=201)
def set_account_credentials(
    data: CredentialSetup,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    response.headers["Cache-Control"] = "no-store"
    try:
        updated = sqlite_repository.set_user_credentials(
            current_user["id"],
            normalize_email(data.email),
            hash_password(data.password)
        )
    except sqlite_repository.EmailAlreadyRegisteredError as error:
        raise HTTPException(
            status_code=409,
            detail="That email is already registered."
        ) from error
    if not updated:
        raise HTTPException(
            status_code=409,
            detail="Sign-in details are already set or this account cannot use password sign-in."
        )
    return {"message": "Sign-in details saved.", "has_credentials": True}


@router.post("/invitations")
def create_invitation(
    data: InvitationRequest,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    response.headers["Cache-Control"] = "no-store"
    try:
        code = create_care_circle_invitation(
            inviter=current_user,
            name=data.name,
            role=data.role,
            permissions=tuple(data.permissions)
        )
    except ValueError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error

    return {
        "message": "Invitation created. Share this one-time code with the invited person.",
        "join_code": code
    }


@router.get("/me")
def current_account(
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    response.headers["Cache-Control"] = "no-store"
    return {"user": _account_response(current_user)}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax"
    )
    return {"message": "You have been logged out."}
