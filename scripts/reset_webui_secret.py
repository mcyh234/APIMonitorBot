from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

from sqlalchemy import delete


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reset APIMonitorBot WebUI access secret.")
    parser.add_argument(
        "--database-url",
        help="Override DATABASE_URL, for example sqlite:///./data/apimonitor.sqlite3.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--clear",
        action="store_true",
        help="Clear the current WebUI secret. This is the default when no mode is provided.",
    )
    mode.add_argument(
        "--secret",
        help="Set a new WebUI secret directly. It must be at least 8 characters.",
    )
    mode.add_argument(
        "--generate",
        action="store_true",
        help="Generate a new WebUI secret, save it, and print it once.",
    )
    return parser


def generate_secret() -> str:
    return f"webui-{secrets.token_urlsafe(24)}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    from backend.app.db import SessionLocal, init_db
    from backend.app.models import AppSetting
    from backend.app.webui_auth import WEBUI_SECRET_HASH_KEY, set_webui_secret

    init_db()
    with SessionLocal() as session:
        if args.secret is not None:
            try:
                set_webui_secret(session, args.secret)
            except ValueError as exc:
                print(f"设置失败：{exc}", file=sys.stderr)
                return 2
            print("WebUI 进入密钥已重置为你提供的新密钥。旧登录 token 已失效。")
            return 0

        if args.generate:
            new_secret = generate_secret()
            set_webui_secret(session, new_secret)
            print("WebUI 进入密钥已自动生成并保存。请立刻记录下面这一行：")
            print(new_secret)
            print("旧登录 token 已失效。")
            return 0

        result = session.execute(delete(AppSetting).where(AppSetting.key == WEBUI_SECRET_HASH_KEY))
        session.commit()
        if result.rowcount:
            print("WebUI 进入密钥已清除。刷新 WebUI 后会回到首次设置密钥页面。")
        else:
            print("当前没有已设置的 WebUI 进入密钥。刷新 WebUI 后会显示首次设置页面。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
