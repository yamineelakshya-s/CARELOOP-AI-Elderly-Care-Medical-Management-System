import json
from urllib.request import Request, urlopen

from app.config import settings

BREVO_EMAIL_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_TIMEOUT_SECONDS = 5


def send_transactional_email(
    recipient_email: str,
    subject: str,
    text_content: str
) -> None:
    if settings.brevo_api_key is None or settings.sender_email is None:
        raise RuntimeError("Brevo email configuration is incomplete.")

    payload = json.dumps({
        "sender": {"email": settings.sender_email},
        "to": [{"email": recipient_email}],
        "subject": subject,
        "textContent": text_content
    }).encode("utf-8")
    request = Request(
        BREVO_EMAIL_URL,
        data=payload,
        headers={
            "accept": "application/json",
            "api-key": settings.brevo_api_key.get_secret_value(),
            "content-type": "application/json"
        },
        method="POST"
    )
    with urlopen(request, timeout=BREVO_TIMEOUT_SECONDS) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError("Brevo returned a non-success status.")
