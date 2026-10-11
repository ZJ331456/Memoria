"""Privately configure a DeepSeek experiment profile; does not activate production."""
import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from memoria.experiments.history import HistoryWorkspace
from memoria.private_files import save_private_json
from scripts.user_paths import directory_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--config")
    parser.add_argument("--model", default="deepseek-flash", help="Current provider model ID; verify /models for your account")
    args = parser.parse_args()
    workspace = HistoryWorkspace(directory_for(args.user, args.config))
    if not args.model.startswith("deepseek-"):
        parser.error("This private profile is for DeepSeek model IDs")
    key = getpass.getpass("DeepSeek API Key (hidden, never paste in chat): ").strip()
    if not key:
        parser.error("Empty API key")
    path = workspace.path("secrets", "deepseek.research-model.dpapi")
    save_private_json(path, {"owner_user_id": workspace.owner, "model": args.model,
                            "base_url": "https://api.deepseek.com/v1", "thinking": "disabled", "api_key": key})
    print("Encrypted account-specific DeepSeek research profile saved; production model unchanged.")


if __name__ == "__main__":
    main()
