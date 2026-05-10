from __future__ import annotations

import argparse
from datetime import datetime, timezone

from .config import load_config
from .http import JsonHttpClient
from .notifier import TelegramNotifier
from .pipeline import build_adapters, run_pipeline
from .storage import build_store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan Taiwan-origin flight deals and send Telegram alerts.")
    parser.add_argument("--dry-run", action="store_true", help="Do not send Telegram alerts or record alert cooldowns.")
    parser.add_argument("--init-db", action="store_true", help="Create database tables and exit.")
    parser.add_argument("--list-sources", action="store_true", help="Print enabled/disabled source adapters and exit.")
    parser.add_argument("--test-telegram", action="store_true", help="Send a Telegram test message and exit.")
    args = parser.parse_args(argv)

    config = load_config()
    adapters = build_adapters()

    if args.list_sources:
        for adapter in adapters:
            state = "enabled" if adapter.enabled(config) else "disabled"
            print(f"{adapter.name}: {state}")
        return 0

    if args.test_telegram:
        notifier = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id, JsonHttpClient())
        notifier.send_text(
            "機票價格查詢機器人測試推播成功\n"
            f"時間：{datetime.now(timezone.utc).isoformat(timespec='seconds')}"
        )
        print("Telegram test message sent.")
        return 0

    if args.init_db:
        store = build_store(config.database_url)
        store.setup()
        print("Database initialized.")
        return 0

    run_pipeline(config=config, dry_run=args.dry_run, adapters=adapters)
    return 0
