"""Resolve private CLI targets using the same server-owned account IDs as HTTP."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from memoria.accounts import AccountStore, user_directory
from memoria.config import Settings


def directory_for(username: str, config=None) -> Path:
    root = Settings.load(config).database.parent.resolve()
    accounts = AccountStore(root / "auth/accounts.db")
    try:
        user = accounts.user(username)
        if not user:
            raise ValueError("Account missing; first run scripts/manage_users.py reserve USERNAME")
        return user_directory(root, user["id"])
    finally:
        accounts.close()
