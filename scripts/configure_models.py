"""Inspect model sources safely, or explicitly probe one slot (may incur API cost)."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from memoria.config import Settings
from memoria.models_config import apply_overrides, public_models, test_model_slot
from user_paths import directory_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('show', 'test'))
    parser.add_argument('--config')
    parser.add_argument('--user', help='Apply this existing account override; never copies credentials to global defaults')
    parser.add_argument('--slot', choices=('main', 'fast', 'embedding'), default='main')
    parser.add_argument('--allow-remote', action='store_true', help='Allow a remote test call that may cost money')
    args = parser.parse_args()
    settings = Settings.load(args.config)
    defaults_file = settings.model_override_file
    if args.user:
        settings.model_override_file = directory_for(args.user, args.config) / 'config/models.override.toml'
        apply_overrides(settings)
    if args.action == 'show':
        result = public_models(settings)
        result.update(server_config=str(settings.source.resolve()), defaults_file=str(defaults_file),
                      private_config_directory=str(settings.private_config_directory),
                      scope='account' if args.user else 'server_defaults', inference_calls=0)
    else:
        selected = getattr(settings, args.slot)
        if not (selected.is_local_process() or selected.is_local_http()) and not args.allow_remote:
            parser.error('Remote test requires --allow-remote; show performs no inference')
        try:
            result = asyncio.run(test_model_slot(settings, args.slot))
        except Exception as exc:
            # Provider errors may echo requests; do not print their raw text.
            result = {'ok': False, 'slot': args.slot, 'error_type': type(exc).__name__, 'message': '模型探测失败，请核对本机配置与服务状态'}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get('ok') is False:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
