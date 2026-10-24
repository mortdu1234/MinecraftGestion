import json
import logging
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Config
from .minecraft import MinecraftPingError, ping
from .pterodactyl import PterodactylClient, Server

log = logging.getLogger(__name__)


def _matches(server: Server, patterns: list[str]) -> bool:
    return server.identifier.lower() in patterns or server.name.lower() in patterns or server.uuid.lower() in patterns


class AutoStopper:
    """Arrête les serveurs restés vides pendant AUTOSTOP_EMPTY_HOURS heures d'affilée.

    - 0 joueur détecté : un décompte démarre (date mémorisée dans un fichier, donc conservée au redémarrage).
    - Un joueur détecté : le décompte est remis à zéro.
    - Décompte écoulé et serveur toujours vide : arrêt propre.

    Règle de sécurité : si l'état des joueurs est inconnu (ping impossible), on n'arrête jamais le serveur.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ptero = PterodactylClient(cfg.ptero_url, cfg.ptero_api_key, cfg.ptero_verify_tls)
        self.tz = ZoneInfo(cfg.timezone)
        self._dirty = False
        self.empty_since: dict[str, float] = self._load()  # identifiant -> timestamp du début du décompte

    # ---------- persistance ----------
    def _load(self) -> dict[str, float]:
        try:
            with open(self.cfg.autostop_state_file) as f:
                return {str(k): float(v) for k, v in json.load(f).items()}
        except FileNotFoundError:
            return {}
        except Exception as e:
            log.warning("[autostop] état illisible (%s), décomptes réinitialisés", e)
            return {}

    def _save(self) -> None:
        path = self.cfg.autostop_state_file
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = f"{path}.tmp"
            with open(tmp, "w") as f:
                json.dump(self.empty_since, f)
            os.replace(tmp, path)
            self._dirty = False
        except OSError as e:
            log.warning("[autostop] impossible d'enregistrer l'état (%s) : décomptes perdus au redémarrage", e)

    def _start(self, sid: str, now: float) -> None:
        self.empty_since[sid] = now
        self._dirty = True

    def _reset(self, sid: str) -> bool:
        if sid in self.empty_since:
            del self.empty_since[sid]
            self._dirty = True
            return True
        return False

    # ---------- vérification ----------
    def check_all(self) -> None:
        cfg = self.cfg
        try:
            servers = self.ptero.list_servers(cfg.ptero_list_type)
        except Exception as e:
            log.error("[autostop] Impossible de lister les serveurs : %s", e)
            return
        if cfg.autostop_include:
            servers = [s for s in servers if _matches(s, cfg.autostop_include)]
        if cfg.autostop_exclude:
            servers = [s for s in servers if not _matches(s, cfg.autostop_exclude)]

        seen = {s.identifier for s in servers}
        for sid in list(self.empty_since):
            if sid not in seen:
                self._reset(sid)

        for server in servers:
            try:
                self._check(server)
            except Exception as e:
                log.error("[autostop] %s : %s", server.name, e)  # état inconnu : on ne touche à rien

        if self._dirty:
            self._save()

    def _address(self, server: Server) -> tuple[str, int]:
        attrs = self.ptero.get_server(server.identifier)
        allocs = [a["attributes"] for a in attrs["relationships"]["allocations"]["data"]]
        if not allocs:
            raise RuntimeError("aucune allocation de port")
        alloc = next((a for a in allocs if a.get("is_default")), allocs[0])
        host = self.cfg.autostop_host or alloc.get("ip_alias") or alloc.get("ip") or ""
        if host in ("", "0.0.0.0"):
            raise RuntimeError("adresse inconnue : renseigne AUTOSTOP_HOST")
        return host, int(alloc["port"])

    def _check(self, server: Server) -> None:
        cfg, sid = self.cfg, server.identifier
        res = self.ptero.get_resources(sid)

        # Serveur éteint, en démarrage ou suspendu : rien à arrêter, le décompte n'a plus de sens
        if res.get("is_suspended") or res.get("current_state") != "running":
            self._reset(sid)
            return

        host, port = self._address(server)
        try:
            online = ping(host, port)["players"]["online"]
            if not isinstance(online, int):
                raise MinecraftPingError("nombre de joueurs invalide")
        except (MinecraftPingError, KeyError, TypeError) as e:
            # Joueurs inconnus : on garde le décompte tel quel, mais on ne peut pas arrêter
            log.warning("[autostop] %s : état des joueurs inconnu (%s) -> aucune action", server.name, e)
            return

        now = time.time()
        if online > 0:
            if self._reset(sid):
                log.info("[autostop] %s : %d joueur(s) connecté(s), décompte remis à zéro", server.name, online)
            return

        since = self.empty_since.get(sid)
        if since is None:
            self._start(sid, now)
            deadline = datetime.fromtimestamp(now + cfg.autostop_empty_hours * 3600, self.tz)
            log.info("[autostop] %s : vide, décompte de %d h lancé (arrêt prévu le %s)",
                     server.name, cfg.autostop_empty_hours, deadline.strftime("%d/%m %H:%M"))
            return

        elapsed_h = (now - since) / 3600
        if elapsed_h < cfg.autostop_empty_hours:
            log.debug("[autostop] %s : vide depuis %.1f h / %d h", server.name, elapsed_h, cfg.autostop_empty_hours)
            return

        if cfg.autostop_dry_run:
            log.info("[autostop] [DRY-RUN] %s serait arrêté (vide depuis %.1f h)", server.name, elapsed_h)
            return
        log.info("[autostop] Arrêt de %s (vide depuis %.1f h)", server.name, elapsed_h)
        self.ptero.send_power(sid, "stop")  # arrêt propre, jamais "kill"
        self._reset(sid)
