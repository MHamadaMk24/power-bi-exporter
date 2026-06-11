"""Restore packed PLAYWRIGHT_STORAGE_STATE secret to a Playwright session file."""

import os
import sys
from pathlib import Path

from session_state import ROOT, unpack_session_to_file


def main() -> None:
    packed = os.environ.get("PLAYWRIGHT_STORAGE_STATE", "").strip()
    if not packed:
        print("PLAYWRIGHT_STORAGE_STATE is not set", file=sys.stderr)
        sys.exit(1)

    destination = Path(
        os.environ.get(
            "PLAYWRIGHT_STORAGE_STATE_PATH",
            str(ROOT / "playwright-state" / "session.json"),
        )
    )
    unpack_session_to_file(packed, destination)
    print(f"Restored session to {destination}")


if __name__ == "__main__":
    main()
