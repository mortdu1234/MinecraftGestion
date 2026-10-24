import logging
import time
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)


class PterodactylError(Exception):
    pass


@dataclass
class Server:
    identifier: str
    uuid: str
    name: str


class PterodactylClient:
    def __init__(self, url: str, api_key: str, verify_tls: bool = True):
        self.base = f"{url.rstrip('/')}/api/client"
        self.verify = verify_tls
        self.s = requests.Session()
        self.s.verify = verify_tls
        self.s.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "Application/vnd.pterodactyl.v1+json",
                "Content-Type": "application/json",
            }
        )

    def _req(self, method: str, path: str, **kw) -> requests.Response:
        try:
            r = self.s.request(method, f"{self.base}{path}", timeout=30, **kw)
        except requests.RequestException as e:
            raise PterodactylError(f"{method} {path} : {e}") from e
        if not r.ok:
            raise PterodactylError(f"{method} {path} -> HTTP {r.status_code} : {r.text[:300]}")
        return r

    # ---------- Serveurs ----------
    def list_servers(self, list_type: str = "") -> list[Server]:
        servers: list[Server] = []
        page = 1
        while True:
            params = {"page": page}
            if list_type:
                params["type"] = list_type
            data = self._req("GET", "", params=params).json()
            for item in data["data"]:
                a = item["attributes"]
                servers.append(Server(a["identifier"], a["uuid"], a["name"]))
            pag = data["meta"]["pagination"]
            if pag["current_page"] >= pag["total_pages"]:
                break
            page += 1
        return servers

    # ---------- Infos / alimentation ----------
    def get_server(self, server_id: str) -> dict:
        return self._req("GET", f"/servers/{server_id}").json()["attributes"]

    def get_resources(self, server_id: str) -> dict:
        return self._req("GET", f"/servers/{server_id}/resources").json()["attributes"]

    def send_power(self, server_id: str, signal: str) -> None:
        self._req("POST", f"/servers/{server_id}/power", json={"signal": signal})

    # ---------- Backups ----------
    def list_backups(self, server_id: str) -> list[dict]:
        out, page = [], 1
        while True:
            data = self._req("GET", f"/servers/{server_id}/backups", params={"page": page}).json()
            out.extend(b["attributes"] for b in data["data"])
            pag = data["meta"]["pagination"]
            if pag["current_page"] >= pag["total_pages"]:
                break
            page += 1
        return out

    def create_backup(self, server_id: str, name: str) -> dict:
        r = self._req("POST", f"/servers/{server_id}/backups", json={"name": name, "is_locked": False})
        return r.json()["attributes"]

    def get_backup(self, server_id: str, backup_uuid: str) -> dict:
        return self._req("GET", f"/servers/{server_id}/backups/{backup_uuid}").json()["attributes"]

    def delete_backup(self, server_id: str, backup_uuid: str) -> None:
        self._req("DELETE", f"/servers/{server_id}/backups/{backup_uuid}")

    def wait_for_backup(self, server_id: str, backup_uuid: str, timeout: int, poll: int = 5) -> dict:
        deadline = time.monotonic() + timeout
        last_log = time.monotonic()
        while time.monotonic() < deadline:
            attrs = self.get_backup(server_id, backup_uuid)
            if attrs.get("completed_at"):
                if attrs.get("is_successful"):
                    return attrs
                raise PterodactylError("Le backup a échoué côté panel/Wings")
            if time.monotonic() - last_log > 30:
                log.info("  ... création du backup en cours")
                last_log = time.monotonic()
            time.sleep(poll)
        raise PterodactylError(f"Timeout ({timeout}s) en attendant la fin du backup")

    def download_url(self, server_id: str, backup_uuid: str) -> str:
        r = self._req("GET", f"/servers/{server_id}/backups/{backup_uuid}/download")
        return r.json()["attributes"]["url"]

    def download_to_file(self, url: str, dest: str) -> int:
        """Télécharge l'archive depuis Wings (URL signée, sans header d'auth) en streaming."""
        written = 0
        try:
            with requests.get(url, stream=True, timeout=(30, 300), verify=self.verify) as r:
                if not r.ok:
                    raise PterodactylError(f"Téléchargement -> HTTP {r.status_code} : {r.text[:300]}")
                expected = int(r.headers.get("Content-Length", 0)) or None
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
                            written += len(chunk)
        except requests.RequestException as e:
            raise PterodactylError(f"Téléchargement : {e}") from e
        if expected and written != expected:
            raise PterodactylError(f"Téléchargement incomplet ({written}/{expected} octets)")
        return written
