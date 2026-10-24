import os
from dataclasses import dataclass


class ConfigError(Exception):
    pass


def _req(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise ConfigError(f"Variable d'environnement manquante : {name}")
    return v


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "oui")


def _int(name: str, default: int, minimum: int = 0) -> int:
    v = os.getenv(name, "").strip()
    if not v:
        return default
    try:
        n = int(v)
    except ValueError:
        raise ConfigError(f"{name} doit être un entier (reçu : {v!r})")
    if n < minimum:
        raise ConfigError(f"{name} doit être >= {minimum}")
    return n


def _list(name: str) -> list[str]:
    return [x.strip().lower() for x in os.getenv(name, "").split(",") if x.strip()]


@dataclass(frozen=True)
class Config:
    ptero_url: str
    ptero_api_key: str
    ptero_verify_tls: bool
    ptero_list_type: str
    servers_include: list[str]
    servers_exclude: list[str]

    nc_url: str
    nc_user: str
    nc_password: str
    nc_base_path: str
    nc_verify_tls: bool
    nc_chunk_threshold: int  # octets
    nc_chunk_size: int  # octets

    keep: int
    cron: str
    timezone: str
    run_on_start: bool
    backup_timeout: int
    keep_panel_backup: bool
    folder_include_id: bool
    tmp_dir: str
    log_level: str


def load() -> Config:
    mb = 1024 * 1024
    return Config(
        ptero_url=_req("PTERO_URL").rstrip("/"),
        ptero_api_key=_req("PTERO_API_KEY"),
        ptero_verify_tls=_bool("PTERO_VERIFY_TLS", True),
        ptero_list_type=os.getenv("PTERO_LIST_TYPE", "").strip(),
        servers_include=_list("SERVERS_INCLUDE"),
        servers_exclude=_list("SERVERS_EXCLUDE"),
        nc_url=_req("NC_URL").rstrip("/"),
        nc_user=_req("NC_USER"),
        nc_password=_req("NC_PASSWORD"),
        nc_base_path=os.getenv("NC_BASE_PATH", "Backups").strip().strip("/"),
        nc_verify_tls=_bool("NC_VERIFY_TLS", True),
        nc_chunk_threshold=_int("NC_CHUNK_THRESHOLD_MB", 100, 1) * mb,
        nc_chunk_size=_int("NC_CHUNK_SIZE_MB", 100, 5) * mb,
        keep=_int("BACKUP_KEEP", 5, 1),
        cron=os.getenv("BACKUP_CRON", "0 4 * * *").strip(),
        timezone=os.getenv("TZ", "Europe/Paris").strip(),
        run_on_start=_bool("RUN_ON_START", False),
        backup_timeout=_int("BACKUP_TIMEOUT", 3600, 60),
        keep_panel_backup=_bool("KEEP_PANEL_BACKUP", False),
        folder_include_id=_bool("FOLDER_INCLUDE_ID", False),
        tmp_dir=os.getenv("TMP_DIR", "/tmp/backups").strip(),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
    )
