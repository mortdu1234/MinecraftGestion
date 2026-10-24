import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Config
from .nextcloud import NextcloudClient
from .pterodactyl import PterodactylClient, Server

log = logging.getLogger(__name__)

PANEL_PREFIX = "nc-auto-"  # préfixe des backups créés (et nettoyés) par cette appli
FILE_RE = re.compile(r"^.+_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.tar\.gz$")


def sanitize(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return cleaned or "server"


def folder_for(cfg: Config, server: Server) -> str:
    name = sanitize(server.name)
    if cfg.folder_include_id:
        name = f"{name}_{server.identifier}"
    return f"{cfg.nc_base_path}/{name}" if cfg.nc_base_path else name


def rotate(nc: NextcloudClient, folder: str, keep: int) -> None:
    files = sorted((m.group(1), f.name) for f in nc.list_files(folder) if (m := FILE_RE.match(f.name)))
    excess = files[:-keep] if len(files) > keep else []
    for _, name in excess:
        log.info("  suppression de l'ancien backup : %s", name)
        nc.delete(f"{folder}/{name}")
    log.info("  %d backup(s) conservé(s) sur Nextcloud", len(files) - len(excess))


def cleanup_stale_panel_backups(ptero: PterodactylClient, server: Server) -> None:
    """Supprime d'anciens backups laissés sur le panel par une exécution interrompue."""
    for b in ptero.list_backups(server.identifier):
        if b["name"].startswith(PANEL_PREFIX) and not b.get("is_locked"):
            log.info("  nettoyage d'un backup résiduel du panel : %s", b["name"])
            try:
                ptero.delete_backup(server.identifier, b["uuid"])
            except Exception as e:
                log.warning("  impossible de supprimer %s : %s", b["uuid"], e)


def backup_server(cfg: Config, ptero: PterodactylClient, nc: NextcloudClient, server: Server) -> None:
    folder = folder_for(cfg, server)
    stamp = datetime.now(ZoneInfo(cfg.timezone)).strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{sanitize(server.name).replace(' ', '_')}_{stamp}.tar.gz"
    remote_path = f"{folder}/{filename}"
    local_path = os.path.join(cfg.tmp_dir, f"{server.identifier}_{stamp}.tar.gz")
    panel_backup_uuid = None

    try:
        cleanup_stale_panel_backups(ptero, server)

        log.info("  création du backup sur le panel")
        attrs = ptero.create_backup(server.identifier, f"{PANEL_PREFIX}{stamp}")
        panel_backup_uuid = attrs["uuid"]
        attrs = ptero.wait_for_backup(server.identifier, panel_backup_uuid, cfg.backup_timeout)
        log.info("  backup prêt (%.1f Mo)", attrs.get("bytes", 0) / 1024 / 1024)

        log.info("  téléchargement depuis Wings")
        url = ptero.download_url(server.identifier, panel_backup_uuid)
        size = ptero.download_to_file(url, local_path)

        log.info("  envoi vers Nextcloud : %s", remote_path)
        nc.mkdirs(folder)
        nc.upload(local_path, remote_path)

        remote_size = nc.get_size(remote_path)
        if remote_size != size:
            raise RuntimeError(f"Vérification échouée : local={size} octets, Nextcloud={remote_size}")
        log.info("  envoi vérifié (%d octets)", size)

        # Rotation seulement après un envoi réussi : un échec ne détruit jamais les anciens backups
        rotate(nc, folder, cfg.keep)
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)
        if panel_backup_uuid and not cfg.keep_panel_backup:
            try:
                ptero.delete_backup(server.identifier, panel_backup_uuid)
            except Exception as e:
                log.warning("  impossible de supprimer le backup du panel : %s", e)


def _matches(server: Server, patterns: list[str]) -> bool:
    return server.identifier.lower() in patterns or server.name.lower() in patterns or server.uuid.lower() in patterns


def run_all(cfg: Config, only: list[str] | None = None) -> bool:
    ptero = PterodactylClient(cfg.ptero_url, cfg.ptero_api_key, cfg.ptero_verify_tls)
    nc = NextcloudClient(cfg.nc_url, cfg.nc_user, cfg.nc_password, cfg.nc_verify_tls,
                         cfg.nc_chunk_threshold, cfg.nc_chunk_size)
    os.makedirs(cfg.tmp_dir, exist_ok=True)

    log.info("===== Début de la sauvegarde =====")
    try:
        nc.check_connection()
        servers = ptero.list_servers(cfg.ptero_list_type)
    except Exception as e:
        log.error("Initialisation impossible : %s", e)
        return False

    include = [x.lower() for x in only] if only else cfg.servers_include
    if include:
        servers = [s for s in servers if _matches(s, include)]
    if cfg.servers_exclude:
        servers = [s for s in servers if not _matches(s, cfg.servers_exclude)]

    log.info("%d serveur(s) à sauvegarder", len(servers))
    ok, failed = 0, []
    for i, server in enumerate(servers, 1):
        log.info("[%d/%d] %s (%s)", i, len(servers), server.name, server.identifier)
        try:
            backup_server(cfg, ptero, nc, server)
            ok += 1
        except Exception as e:
            log.error("  ÉCHEC : %s", e)
            failed.append(server.name)

    log.info("===== Terminé : %d réussi(s), %d échec(s) %s=====", ok, len(failed),
             f"({', '.join(failed)}) " if failed else "")
    return not failed
