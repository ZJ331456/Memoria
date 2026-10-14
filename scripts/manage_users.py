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
    parser.add_argument("action", choices=["reserve", "set-password", "set-role", "disable-login"])
    parser.add_argument("username")
    parser.add_argument("--config")
    parser.add_argument('--role',choices=['user','superadmin'])
    args = parser.parse_args()
    if (args.action=='set-role') != (args.role is not None):
        parser.error('--role is required only for set-role')
    accounts = AccountStore(Settings.load(args.config).database.parent / "auth/accounts.db")
    try:
        if args.action == "reserve":
            user = accounts.reserve(args.username)
            print("Reserved local account:", user["username"])
        elif args.action=='set-role':
            user=accounts.set_role(args.username,args.role)
            print('Local account role updated:',user['username'],user['role'])
        elif args.action=='disable-login':
            accounts.disable_login(args.username)
            print('Local login disabled; identity and files retained.')
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
