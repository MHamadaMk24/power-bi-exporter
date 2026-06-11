import logging
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

logger = logging.getLogger(__name__)

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


@dataclass(frozen=True)
class SharePointConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    site_hostname: str
    site_path: str
    doc_lib: str
    target_folder: str

    @classmethod
    def from_env(cls) -> "SharePointConfig | None":
        tenant_id = os.environ.get("TENANT_ID", "").strip()
        client_id = os.environ.get("CLIENT_ID", "").strip()
        client_secret = os.environ.get("CLIENT_SECRET", "").strip()
        site_name = os.environ.get("SHAREPOINT_SITE_NAME", "").strip()
        doc_lib = os.environ.get("SHAREPOINT_DOC_LIB", "Documents").strip()
        target_folder = os.environ.get("TARGET_FOLDER_PATH", "").strip().strip("/")

        if not all((tenant_id, client_id, client_secret, site_name, target_folder)):
            return None

        hostname, site_path = _parse_site(site_name)
        return cls(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            site_hostname=hostname,
            site_path=site_path,
            doc_lib=doc_lib,
            target_folder=target_folder,
        )


def _parse_site(site_name: str) -> tuple[str, str]:
    site_name = site_name.strip().rstrip("/")
    if site_name.startswith("http"):
        parsed = urlparse(site_name)
        return parsed.netloc, parsed.path

    if "/" in site_name:
        hostname, _, path = site_name.partition("/")
        return hostname, f"/{path}"

    return site_name, ""


class SharePointUploader:
    def __init__(self, config: SharePointConfig) -> None:
        self._config = config
        self._token: str | None = None
        self._site_id: str | None = None
        self._drive_id: str | None = None

    def upload_file(self, local_path: Path, *, folder: str | None = None) -> str:
        local_path = Path(local_path)
        if not local_path.is_file():
            raise FileNotFoundError(f"File not found: {local_path}")

        token = self._get_token()
        site_id = self._get_site_id(token)
        drive_id = self._get_drive_id(token, site_id)

        target_folder = (folder or self._config.target_folder).strip().strip("/")
        remote_path = f"{target_folder}/{local_path.name}"
        url = (
            f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root:"
            f"/{quote(remote_path, safe='/')}:/content"
        )

        with local_path.open("rb") as file_handle:
            response = requests.put(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/pdf",
                },
                data=file_handle,
                timeout=120,
            )

        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"SharePoint upload failed ({response.status_code}): {response.text}"
            )

        payload = response.json()
        web_url = payload.get("webUrl", remote_path)
        logger.info("Uploaded to SharePoint: %s", web_url)
        return web_url

    def _get_token(self) -> str:
        if self._token:
            return self._token

        response = requests.post(
            f"https://login.microsoftonline.com/{self._config.tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "scope": GRAPH_SCOPE,
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        response.raise_for_status()
        self._token = response.json()["access_token"]
        return self._token

    def _get_site_id(self, token: str) -> str:
        if self._site_id:
            return self._site_id

        site_ref = self._config.site_hostname
        if self._config.site_path:
            site_ref = f"{site_ref}:{self._config.site_path}:"

        response = requests.get(
            f"{GRAPH_BASE}/sites/{site_ref}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        response.raise_for_status()
        self._site_id = response.json()["id"]
        return self._site_id

    def _get_drive_id(self, token: str, site_id: str) -> str:
        if self._drive_id:
            return self._drive_id

        response = requests.get(
            f"{GRAPH_BASE}/sites/{site_id}/drives",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        response.raise_for_status()

        drives = response.json().get("value", [])
        doc_lib = self._config.doc_lib.casefold()
        for drive in drives:
            if drive.get("name", "").casefold() == doc_lib:
                self._drive_id = drive["id"]
                return self._drive_id

        if drives:
            logger.warning(
                'Document library "%s" not found; using "%s"',
                self._config.doc_lib,
                drives[0].get("name"),
            )
            self._drive_id = drives[0]["id"]
            return self._drive_id

        raise RuntimeError(f'No document libraries found on site "{site_id}"')
