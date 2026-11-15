import httpx

from app.config import settings
from app.repositories.sqlite_repository import get_dashboard_for_user


class GeminiUnavailableError(RuntimeError):
    pass


def _generic_guidance(role: str) -> str | None:
    if settings.gemini_api_key is None:
        return None

    role_guidance = {
        "elder": "an older adult managing their own care",
        "caregiver": "a caregiver coordinating support",
        "family": "a family member staying connected to care"
    }.get(role, "a care-circle member")
    prompt = (
        "Write three brief, practical, non-medical care-coordination reminders "
        f"for {role_guidance}. Do not diagnose, recommend medication changes, "
        "or invent personal details. No patient records or identifiers are "
        "provided. Keep the reminders general and encourage users to consult "
        "their qualified care team for medical decisions."
    )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    try:
        response = httpx.post(
            url,
            headers={
                "x-goog-api-key": settings.gemini_api_key.get_secret_value()
            },
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 180}
            },
            timeout=10.0
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise GeminiUnavailableError(
            "The configured Gemini service is temporarily unavailable."
        ) from error

    try:
        parts = payload["candidates"][0]["content"]["parts"]
        text = "\n".join(part["text"] for part in parts if "text" in part).strip()
    except (KeyError, IndexError, TypeError) as error:
        raise GeminiUnavailableError(
            "The configured Gemini service returned an invalid response."
        ) from error
    if not text:
        raise GeminiUnavailableError(
            "The configured Gemini service returned an empty response."
        )
    return text


def create_care_summary(user_id: str) -> dict | None:
    dashboard = get_dashboard_for_user(user_id)
    if dashboard is None:
        return None

    circles = dashboard["care_circles"]
    medication_count = sum(len(circle["medications"]) for circle in circles)
    appointment_count = sum(len(circle["appointments"]) for circle in circles)
    activity_count = sum(len(circle["activities"]) for circle in circles)
    summary = (
        f"Your account is linked to {len(circles)} care circle(s), "
        f"with access to {medication_count} medication record(s), "
        f"{appointment_count} appointment(s), and "
        f"{activity_count} care activity update(s)."
    )
    guidance = _generic_guidance(dashboard["profile"]["role"])
    return {
        "summary": summary,
        "general_guidance": guidance,
        "ai_status": "available" if guidance is not None else "not_configured",
        "care_records_shared_with_gemini": False
    }