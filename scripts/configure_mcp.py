"""Configure an existing account in config/mcp.toml; no per-user config files."""
import argparse
from user_paths import directory_for
from memoria.config import Settings
from memoria.config_store import update_document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True)
    parser.add_argument('--config')
    parser.add_argument('--enable', nargs='+', help='Replace enabled list; Feishu requires account OAuth')
    args = parser.parse_args()
    settings = Settings.load(args.config)
    owner = directory_for(args.user, args.config).name
    def publish(document):
        definitions = document.get('servers', {})
        current = document.setdefault('accounts', {}).setdefault(owner, {}).setdefault('mcp', {})
        names = args.enable if args.enable is not None else current.get('enabled_servers', ['search', 'github'])
        if any(name not in definitions for name in names) or len(set(names)) != len(names):
            raise ValueError('Unknown or duplicate server; edit [servers] in config/mcp.toml')
        current['enabled_servers'] = names
    update_document(settings.mcp_config_file, publish)
    print('Account selection saved to config/mcp.toml. Reload MCP or restart backend.')


if __name__ == '__main__':
    main()
