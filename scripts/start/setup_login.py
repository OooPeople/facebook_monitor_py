"""正式維運入口：開啟專用 profile 供 Facebook 登入與檢查。"""

from pathlib import Path
import sys

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.automation.profile_lease import acquire_profile_lease

PROFILE_DIR = ROOT / "data" / "profiles" / "automation_default"
START_URL = "https://www.facebook.com/groups/"


def main() -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with acquire_profile_lease(PROFILE_DIR, "setup login"):
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
