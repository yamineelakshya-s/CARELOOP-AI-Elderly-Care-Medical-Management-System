import argparse
from getpass import getpass

from app.database import initialize_database
from app.repositories import sqlite_repository
from app.services.auth_service import (
    PASSWORD_MIN_LENGTH,
    hash_password,
    normalize_email,
    validate_email
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Set up password sign-in for an existing account that has no credentials."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List local account IDs.")
    group.add_argument("--user-id", help="ID of the existing account to set up.")
    arguments = parser.parse_args()

    initialize_database()
    accounts = sqlite_repository.list_users_for_credential_setup()
    if arguments.list:
        if not accounts:
            print("No non-demo accounts are available.")
            return
        for account in accounts:
            status = "configured" if account["has_credentials"] else "needs setup"
            print(
                f'{account["id"]}  {account["name"]}  '
                f'{account["role"]}  {status}'
            )
        return

    account = next(
        (item for item in accounts if item["id"] == arguments.user_id),
        None
    )
    if account is None:
        parser.error("No non-demo account was found with that user ID.")
    if account["has_credentials"]:
        parser.error("This account already has sign-in details.")

    email = normalize_email(input("Email: "))
    if not validate_email(email):
        parser.error("Enter a valid email address.")
    password = getpass(f"New password (at least {PASSWORD_MIN_LENGTH} characters): ")
    if len(password) < PASSWORD_MIN_LENGTH or len(password) > 256:
        parser.error("Password length must be between 8 and 256 characters.")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        parser.error("The passwords do not match.")

    try:
        updated = sqlite_repository.set_user_credentials(
            account["id"], email, hash_password(password)
        )
    except sqlite_repository.EmailAlreadyRegisteredError:
        parser.error("That email is already registered.")
    if not updated:
        parser.error("Credentials could not be set for this account.")
    print("Sign-in details saved. The account can now sign in with email and password.")


if __name__ == "__main__":
    main()
