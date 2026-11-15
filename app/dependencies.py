from fastapi import HTTPException, Request

from app.services.auth_service import SESSION_COOKIE, verify_session


def get_current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    user = verify_session(token) if token else None

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required."
        )

    return user
