"""Move server config out of data/config after stopping Memoria; accounts stay private."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from memoria.config_layout import migrate_server_configuration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config.toml')
    args = parser.parse_args()
    print(json.dumps(migrate_server_configuration(args.config), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
