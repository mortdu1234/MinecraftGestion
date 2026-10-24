import argparse
import logging
import signal
import sys
import threading
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import config
from .autostop import AutoStopper
from .backup import run_all


def main() -> int:
    parser = argparse.ArgumentParser(description="Backups Pterodactyl -> Nextcloud + arrêt auto")
    parser.add_argument("--once", action="store_true", help="Lance une sauvegarde immédiatement puis quitte")
    parser.add_argument("--autostop", action="store_true",
                        help="Lance une vérification d'arrêt automatique immédiatement puis quitte")
    parser.add_argument("--server", action="append", help="Limite les sauvegardes à ce serveur (nom ou identifiant)")
    args = parser.parse_args()

    try:
        cfg = config.load()
    except config.ConfigError as e:
        print(f"Erreur de configuration : {e}", file=sys.stderr)
        return 2

    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)-7s %(message)s")
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    log = logging.getLogger("main")

    if args.autostop:
        AutoStopper(cfg).check_all()
        return 0
    if args.once:
        return 0 if run_all(cfg, args.server) else 1

    try:
        trigger = CronTrigger.from_crontab(cfg.cron, timezone=cfg.timezone)
    except ValueError as e:
        print(f"BACKUP_CRON invalide : {e}", file=sys.stderr)
        return 2

    # Verrou partagé : on n'arrête jamais un serveur pendant qu'une sauvegarde est en cours
    lock = threading.Lock()

    def backup_job():
        with lock:
            run_all(cfg, args.server)

    stopper = AutoStopper(cfg)

    def autostop_job():
        if not lock.acquire(blocking=False):
            log.info("[autostop] sauvegarde en cours, vérification ignorée")
            return
        try:
            stopper.check_all()
        finally:
            lock.release()

    scheduler = BlockingScheduler(timezone=cfg.timezone)
    scheduler.add_job(backup_job, trigger, id="backup", max_instances=1, coalesce=True, misfire_grace_time=3600)
    if cfg.run_on_start:
        scheduler.add_job(backup_job, "date", run_date=datetime.now(), id="startup")
    if cfg.autostop_enabled:
        scheduler.add_job(autostop_job, IntervalTrigger(minutes=cfg.autostop_check_minutes),
                          id="autostop", max_instances=1, coalesce=True)
        log.info("Arrêt auto : serveurs vides pendant %d h d'affilée, ping toutes les %d min%s",
                 cfg.autostop_empty_hours, cfg.autostop_check_minutes,
                 " [DRY-RUN]" if cfg.autostop_dry_run else "")

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
