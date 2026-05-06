"""Phase 0 headless worker probe.

Goal: reuse the dedicated automation profile in headless mode and verify that a
Facebook group target can be opened without keeping a foreground browser window.

Usage:
    python scripts/phase0_worker_probe.py "https://www.facebook.com/groups/<group_id>"
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = ROOT / "data" / "profiles" / "phase0_default"
LOG_PATH = ROOT / "logs" / "phase0_worker_probe.log"


def log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"{timestamp} {message}"
    print(line)
    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(line + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a minimal headless Facebook probe.")
    parser.add_argument("target_url", help="Facebook group URL to open in headless mode.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not PROFILE_DIR.exists():
        raise SystemExit(f"Profile does not exist yet. Run phase0_setup_login.py first: {PROFILE_DIR}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=True,
            viewport={"width": 1366, "height": 900},
        )
        page = context.new_page()
        page.goto(args.target_url, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        title = page.title()
        body_text = page.locator("body").inner_text(timeout=10000)
        log(f"opened url={page.url!r} title={title!r} body_chars={len(body_text)}")
        context.close()


if __name__ == "__main__":
    main()
