import argparse

from app.database import initialize_database
from app.services.auth_service import create_invitation


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an invitation for an independent elder account."
    )
    parser.add_argument("--name", required=True, help="Name supplied by the account owner.")
    arguments = parser.parse_args()

    initialize_database()
    code = create_invitation(name=arguments.name, role="elder")
    print(f"One-time 8-character invitation code: {code}")
    print("Share it only with the intended user; it expires in 7 days.")


if __name__ == "__main__":
    main()
