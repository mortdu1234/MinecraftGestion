import argparse
import logging
import signal
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config
from .backup import run_all


def main() -> int:
    parser = argparse.ArgumentParser(description="Backups Pterodactyl -> Nextcloud")
    parser.add_argument("--once", action="store_true", help="Lance une sauvegarde immédiatement puis quitte")
    parser.add_argument("--server", action="append", help="Limite à ce serveur (nom ou identifiant), répétable")
    args = parser.parse_args()

    try:
        cfg = config.load()
    except config.ConfigError as e:
        print(f"Erreur de configuration : {e}", file=sys.stderr)
        return 2

    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)-7s %(message)s")
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    log = logging.getLogger("main")

    if args.once:
        return 0 if run_all(cfg, args.server) else 1

    try:
        trigger = CronTrigger.from_crontab(cfg.cron, timezone=cfg.timezone)
    except ValueError as e:
        print(f"BACKUP_CRON invalide : {e}", file=sys.stderr)
        return 2

    scheduler = BlockingScheduler(timezone=cfg.timezone)
    scheduler.add_job(
        lambda: run_all(cfg, args.server),
        trigger,
        id="backup",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    if cfg.run_on_start:
        scheduler.add_job(lambda: run_all(cfg, args.server), "date", run_date=datetime.now(), id="startup")

    def stop(*_):
        log.info("Arrêt demandé")
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    log.info("Planification : '%s' (%s), conservation : %d backup(s)/serveur", cfg.cron, cfg.timezone, cfg.keep)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
