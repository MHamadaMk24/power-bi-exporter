import base64
import gzip
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SESSION_PATH = ROOT / "playwright-state" / "session.json"

CHROMIUM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

AUTH_COOKIE_DOMAINS = (
    "login.microsoftonline.com",
    "microsoftonline.com",
    "app.powerbi.com",
    "powerbi.com",
    "microsoft.com",
    "live.com",
)


def minimize_storage_state(data: dict) -> dict:
    """Keep auth cookies only — localStorage from Power BI is huge and not needed."""
    cookies = [
        cookie
        for cookie in data.get("cookies", [])
        if any(domain in cookie.get("domain", "") for domain in AUTH_COOKIE_DOMAINS)
    ]
    return {"cookies": cookies, "origins": []}


def pack_session_for_secret(path: Path | None = None) -> str:
    """Compress a session file into a GitHub-secret-sized base64 string."""
    session_path = path or DEFAULT_SESSION_PATH
    if not session_path.is_file():
        raise FileNotFoundError(
            f"Session file not found: {session_path}. Run: python save_session.py"
        )

    data = json.loads(session_path.read_text(encoding="utf-8"))
    minimized = minimize_storage_state(data)
    payload = json.dumps(minimized, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(payload, compresslevel=9)
    encoded = base64.b64encode(compressed).decode("ascii")
    logger.info(
        "Packed session: %s cookies, %s chars",
        len(minimized["cookies"]),
        len(encoded),
    )
    return encoded


def unpack_session_to_file(encoded: str, destination: Path) -> Path:
    """Restore a packed session file for Playwright."""
    compressed = base64.b64decode(encoded)
    payload = gzip.decompress(compressed)
    json.loads(payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return destination


def resolve_storage_state_path() -> Path | None:
    """Return a Playwright storage-state file path if one is configured."""
    explicit = os.environ.get("PLAYWRIGHT_STORAGE_STATE_PATH", "").strip()
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(f"Storage state file not found: {path}")
        logger.info("Using storage state from PLAYWRIGHT_STORAGE_STATE_PATH")
        return path

    inline_packed = os.environ.get("PLAYWRIGHT_STORAGE_STATE", "").strip()
    if inline_packed:
        destination = ROOT / "playwright-state" / "session.ci.json"
        unpack_session_to_file(inline_packed, destination)
        logger.info("Restored packed storage state from PLAYWRIGHT_STORAGE_STATE")
        return destination

    if DEFAULT_SESSION_PATH.is_file():
        logger.info("Using local storage state at %s", DEFAULT_SESSION_PATH)
        return DEFAULT_SESSION_PATH

    return None
