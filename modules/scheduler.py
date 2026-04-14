"""
Scheduler — Daily 5 PM automatic sanity check.
Auto-runs checks, writes to sheet, logs to file.
"""

import asyncio
import threading
import logging
import os
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from config.settings import DAILY_RUN_HOUR, DAILY_RUN_MINUTE

# Ensure logs dir exists
os.makedirs('logs', exist_ok=True)

# File handler for scheduler logs
_file_handler = logging.FileHandler('logs/scheduler.log')
_file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))

logger = logging.getLogger(__name__)
logger.addHandler(_file_handler)

_scheduler = None
_lock = threading.Lock()
_last_run = None
_last_result = None


def _run_daily_check():
    """Run daily sanity check + write results to sheet."""
    global _last_run, _last_result
    logger.info(f"[Scheduler] Starting daily sanity check at {datetime.now()}")

    with _lock:
        try:
            from modules.sanity_engine import run_batch_sanity_check
            result = asyncio.run(run_batch_sanity_check())
            _last_result = result
            _last_run = datetime.now()

            # Auto-write results to sheet
            try:
                from modules.sheets_writer import write_results
                write_result = write_results(result.get('results', []))
                logger.info(f"[Scheduler] Write-back: {write_result}")
            except Exception as e:
                logger.error(f"[Scheduler] Write-back failed: {e}")

            total = result.get('total_merchants', 0)
            passed = result.get('passed', 0)
            failed = result.get('failed', 0)
            logger.info(f"[Scheduler] Done: {total} merchants, {passed} passed, {failed} failed")

            # Write summary to a separate file for history tracking
            with open('logs/daily_runs.log', 'a') as f:
                f.write(f"{datetime.now().isoformat()} | total={total} | passed={passed} | failed={failed}\n")

        except Exception as e:
            logger.exception(f"[Scheduler] Error: {e}")
            _last_result = {"error": str(e)}
            _last_run = datetime.now()


def start_scheduler():
    """Start background scheduler."""
    global _scheduler
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _run_daily_check,
        'cron',
        hour=DAILY_RUN_HOUR,
        minute=DAILY_RUN_MINUTE,
        id='daily_sanity_check',
        name='Daily Sanity Check',
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.start()
    logger.info(f"[Scheduler] Started — daily run at {DAILY_RUN_HOUR}:{DAILY_RUN_MINUTE:02d}")


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None


def get_status():
    job = _scheduler.get_job('daily_sanity_check') if _scheduler else None
    return {
        "running": _scheduler is not None and _scheduler.running,
        "next_run": str(job.next_run_time) if job else None,
        "last_run": str(_last_run) if _last_run else None,
        "last_result_summary": {
            "total": _last_result.get('total_merchants', 0),
            "passed": _last_result.get('passed', 0),
            "failed": _last_result.get('failed', 0),
        } if _last_result and 'total_merchants' in _last_result else None,
    }


def run_now():
    """Trigger check in background."""
    thread = threading.Thread(target=_run_daily_check, daemon=True)
    thread.start()
    return {"message": "Sanity check started in background"}
