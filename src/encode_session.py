"""Print packed session for GitHub secret PLAYWRIGHT_STORAGE_STATE."""

import sys

from session_state import pack_session_for_secret


def main() -> None:
    try:
        encoded = pack_session_for_secret()
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)

    print(encoded)
    print(
        "\nCopy the single line above into GitHub secret PLAYWRIGHT_STORAGE_STATE",
        file=sys.stderr,
    )
    print(f"Secret size: {len(encoded)} characters", file=sys.stderr)


if __name__ == "__main__":
    main()
