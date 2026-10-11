"""Local administrator CLI; passwords are entered privately and never printed."""
from __future__ import annotations
import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from memoria.accounts import AccountStore
from memoria.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["reserve", "set-password"])
    parser.add_argument("username")
    parser.add_argument("--config")
    args = parser.parse_args()
    accounts = AccountStore(Settings.load(args.config).database.parent / "auth/accounts.db")
    try:
        if args.action == "reserve":
            user = accounts.reserve(args.username)
            print("Reserved local account:", user["username"])
        else:
            password = getpass.getpass("Password (12–128 characters, hidden): ")
            if password != getpass.getpass("Repeat password (hidden): "):
                raise SystemExit("Passwords do not match")
            accounts.set_password(args.username, password)
            print("Password updated; existing login sessions revoked.")
    finally:
        accounts.close()


if __name__ == "__main__":
    main()
