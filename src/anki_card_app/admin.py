from __future__ import annotations

import argparse
import getpass

from sqlalchemy.orm import Session

from anki_card_app.auth_service import AccountError, create_account, set_password
from anki_card_app.database import get_engine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage private-alpha accounts.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create-user", "set-password"):
        command = commands.add_parser(name)
        command.add_argument("--email", required=True)
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    try:
        with Session(get_engine()) as session:
            if arguments.command == "create-user":
                account = create_account(session, email=arguments.email, password=password)
            else:
                account = set_password(session, email=arguments.email, password=password)
            account_email = account.email
            session.commit()
    except AccountError as error:
        raise SystemExit(str(error)) from error
    print(f"Updated account {account_email}.")


if __name__ == "__main__":
    main()
