"""Phase 0 headed setup probe.

Goal: launch a dedicated Playwright profile in headed mode so the user can log in to
Facebook and manually navigate to a group or post target.

This script is intentionally minimal. The full target capture flow should only be
built after the headless worker probe proves that the execution model is viable.
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = ROOT / "data" / "profiles" / "phase0_default"
START_URL = "https://www.facebook.com/groups/"


def main() -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1366, "height": 900},
        )
        page = context.new_page()
        page.goto(START_URL, wait_until="domcontentloaded")
        print("Headed setup is open. Log in and navigate to the target page.")
        print("Current profile:", PROFILE_DIR)
        input("Press Enter here after setup is complete, then the browser will close...")
        context.close()


if __name__ == "__main__":
    main()
