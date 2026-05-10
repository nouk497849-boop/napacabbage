from __future__ import annotations

import argparse

from .config import load_config
from .pipeline import build_adapters, run_pipeline
from .storage import build_store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan Taiwan-origin flight deals and send Telegram alerts.")
    parser.add_argument("--dry-run", action="store_true", help="Do not send Telegram alerts or record alert cooldowns.")
    parser.add_argument("--init-db", action="store_true", help="Create database tables and exit.")
    parser.add_argument("--list-sources", action="store_true", help="Print enabled/disabled source adapters and exit.")
    args = parser.parse_args(argv)

    config = load_config()
    adapters = build_adapters()

    if args.list_sources:
        for adapter in adapters:
            state = "enabled" if adapter.enabled(config) else "disabled"
            print(f"{adapter.name}: {state}")
        return 0

    if args.init_db:
        store = build_store(config.database_url)
        store.setup()
        print("Database initialized.")
        return 0

    run_pipeline(config=config, dry_run=args.dry_run, adapters=adapters)
    return 0
