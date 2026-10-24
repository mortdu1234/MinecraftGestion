import logging
import os
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote, unquote

import requests

log = logging.getLogger(__name__)

PROPFIND_BODY = (
    '<?xml version="1.0"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop>'
    "<d:getcontentlength/><d:resourcetype/>"
    "</d:prop></d:propfind>"
)
NS = {"d": "DAV:"}


class NextcloudError(Exception):
    pass


@dataclass
class RemoteFile:
    name: str
    size: int


class NextcloudClient:
    def __init__(self, url, user, password, verify_tls=True, chunk_threshold=100 * 1024 * 1024,
                 chunk_size=100 * 1024 * 1024):
        base = url.rstrip("/")
        self.files_root = f"{base}/remote.php/dav/files/{quote(user, safe='')}"
        self.uploads_root = f"{base}/remote.php/dav/uploads/{quote(user, safe='')}"
        self.chunk_threshold = chunk_threshold
        self.chunk_size = chunk_size
        self.s = requests.Session()
        self.s.auth = (user, password)
        self.s.verify = verify_tls

    # ---------- helpers ----------
    def _url(self, path: str) -> str:
        return f"{self.files_root}/{quote(path.strip('/'), safe='/')}"

    def _call(self, method, url, ok, **kw) -> requests.Response:
        kw.setdefault("timeout", 60)
        try:
            r = self.s.request(method, url, **kw)
        except requests.RequestException as e:
            raise NextcloudError(f"{method} {url} : {e}") from e
        if r.status_code not in ok:
            raise NextcloudError(f"{method} {url} -> HTTP {r.status_code} : {r.text[:300]}")
        return r

    # ---------- opérations ----------
    def check_connection(self) -> None:
        self._call("PROPFIND", f"{self.files_root}/", (207,), headers={"Depth": "0"})

    def mkdirs(self, path: str) -> None:
        current = ""
        for part in [p for p in path.strip("/").split("/") if p]:
            current = f"{current}/{part}" if current else part
            # 201 = créé, 405 = existe déjà
            self._call("MKCOL", self._url(current), (201, 405))

    def list_files(self, folder: str) -> list[RemoteFile]:
        r = requests.Response()
        try:
            r = self.s.request("PROPFIND", self._url(folder), data=PROPFIND_BODY,
                               headers={"Depth": "1", "Content-Type": "application/xml"}, timeout=60)
        except requests.RequestException as e:
            raise NextcloudError(f"PROPFIND {folder} : {e}") from e
        if r.status_code == 404:
            return []
        if r.status_code != 207:
            raise NextcloudError(f"PROPFIND {folder} -> HTTP {r.status_code} : {r.text[:300]}")

        files = []
        root = ET.fromstring(r.content)
        for resp in root.findall("d:response", NS):
            prop = resp.find("d:propstat/d:prop", NS)
            if prop is None or prop.find("d:resourcetype/d:collection", NS) is not None:
                continue
            href = resp.findtext("d:href", default="", namespaces=NS)
            name = unquote(href.rstrip("/").split("/")[-1])
            size = int(prop.findtext("d:getcontentlength", default="-1", namespaces=NS) or -1)
            files.append(RemoteFile(name, size))
        return files

    def get_size(self, path: str) -> int | None:
        r = self.s.request("PROPFIND", self._url(path), data=PROPFIND_BODY,
                           headers={"Depth": "0", "Content-Type": "application/xml"}, timeout=60)
        if r.status_code == 404:
            return None
        if r.status_code != 207:
            raise NextcloudError(f"PROPFIND {path} -> HTTP {r.status_code}")
        prop = ET.fromstring(r.content).find("d:response/d:propstat/d:prop", NS)
        val = prop.findtext("d:getcontentlength", default="", namespaces=NS) if prop is not None else ""
        return int(val) if val else None

    def delete(self, path: str) -> None:
        self._call("DELETE", self._url(path), (200, 204, 404))

    def upload(self, local_path: str, remote_path: str) -> None:
        size = os.path.getsize(local_path)
        if size <= self.chunk_threshold:
            self._upload_simple(local_path, remote_path)
        else:
            self._upload_chunked(local_path, remote_path, size)

    def _upload_simple(self, local_path: str, remote_path: str) -> None:
        with open(local_path, "rb") as f:
            self._call("PUT", self._url(remote_path), (201, 204), data=f, timeout=(30, 3600))

    def _upload_chunked(self, local_path: str, remote_path: str, size: int) -> None:
        """Upload par morceaux (API chunking v2 de Nextcloud)."""
        upload_url = f"{self.uploads_root}/bk-{uuid.uuid4().hex}"
        dest = self._url(remote_path)
        headers = {"Destination": dest, "OC-Total-Length": str(size)}
        self._call("MKCOL", upload_url, (201,), headers=headers)
        total = (size + self.chunk_size - 1) // self.chunk_size
        try:
            with open(local_path, "rb") as f:
                n = 0
                while True:
                    chunk = f.read(self.chunk_size)
                    if not chunk:
                        break
                    n += 1
                    self._put_chunk(f"{upload_url}/{n:05d}", chunk, headers)
                    log.info("  ... morceau %d/%d envoyé", n, total)
            self._call("MOVE", f"{upload_url}/.file", (201, 204), headers=headers, timeout=(30, 7200))
        except Exception:
            try:
                self.s.request("DELETE", upload_url, timeout=30)
            except requests.RequestException:
                pass
            raise

    def _put_chunk(self, url: str, data: bytes, headers: dict, attempts: int = 3) -> None:
        for i in range(1, attempts + 1):
            try:
                self._call("PUT", url, (201, 204), data=data, headers=headers, timeout=(30, 1800))
                return
            except NextcloudError as e:
                if i == attempts:
                    raise
                log.warning("  morceau en échec (%s), nouvelle tentative %d/%d", e, i + 1, attempts)
                time.sleep(5 * i)
