from datetime import datetime

from app.repositories import sqlite_repository
from app.time_utils import normalize_utc


def list_medications(user_id: str) -> dict:
    return sqlite_repository.get_medications_for_user(user_id)


def get_medication(user_id: str, medication_id: str) -> dict | None:
    return sqlite_repository.get_medication_for_user(user_id, medication_id)


def create_medication(user_id: str, data: dict) -> dict | None:
    return sqlite_repository.create_medication_for_user(
        user_id=user_id,
        care_circle_id=data["care_circle_id"],
        name=data["name"],
        dosage=data["dosage"],
        frequency=data["frequency"],
        start_date=data["start_date"].isoformat(),
        end_date=data["end_date"].isoformat() if data["end_date"] else None,
        instructions=data["instructions"]
    )


def update_medication(
    user_id: str,
    medication_id: str,
    data: dict
) -> dict | None:
    return sqlite_repository.update_medication_for_user(
        user_id=user_id,
        medication_id=medication_id,
        name=data["name"],
        dosage=data["dosage"],
        frequency=data["frequency"],
        start_date=data["start_date"].isoformat(),
        end_date=data["end_date"].isoformat() if data["end_date"] else None,
        instructions=data["instructions"]
    )


def delete_medication(user_id: str, medication_id: str) -> bool:
    return sqlite_repository.delete_medication_for_user(user_id, medication_id)


def record_dose(
    user_id: str,
    medication_id: str,
    scheduled_at: datetime,
    status: str
) -> dict | None:
    return sqlite_repository.create_medication_dose_for_user(
        user_id,
        medication_id,
        normalize_utc(scheduled_at).isoformat(),
        status
    )


def update_dose(
    user_id: str,
    medication_id: str,
    dose_id: str,
    status: str
) -> dict | None:
    return sqlite_repository.update_medication_dose_for_user(
        user_id,
        medication_id,
        dose_id,
        status
    )