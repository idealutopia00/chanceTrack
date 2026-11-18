import fcntl
import hashlib
import subprocess
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, time, timedelta
from pathlib import Path

from chinese_calendar import is_workday

from . import storage
from .analysis import RULE, analyze
from .configuration import load_configuration
from .data import TZ, fetch
from .notifications import NOTIFICATION_TITLE, build_message, notify
from .strategies import evaluate

ROOT = Path(__file__).resolve().parent.parent
MAX_ATTEMPTS = 3
RETRY_INTERVAL = timedelta(minutes=5)
RETRY_CUTOFF = time(15)


class CheckInProgress(Exception):
    pass


@contextmanager
def check_lock(db_path):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CheckInProgress("已有检查正在运行，请稍后查看新报告") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def is_running(db_path):
    try:
        with check_lock(db_path):
            return False
    except CheckInProgress:
        return True


def retry_due(previous, now):
    return (
        any(item["status"] == "error" for item in previous["items"])
        and previous.get("attempt", 1) < MAX_ATTEMPTS
        and now.time() < RETRY_CUTOFF
        and now >= datetime.fromisoformat(previous["created_at"]) + RETRY_INTERVAL
    )


def revision():
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True
        ).strip()
        return head + ("+dirty" if dirty else "")
    except (subprocess.SubprocessError, FileNotFoundError):
        deployment = ROOT / "deployment-revision.txt"
        return deployment.read_text().strip() if deployment.exists() else "uncommitted"


def code_hash():
    digest = hashlib.sha256()
    for file in sorted((ROOT / "chance_track").rglob("*")):
        if file.suffix in (".py", ".html", ".js", ".css", ".txt"):
            digest.update(str(file.relative_to(ROOT)).encode())
            digest.update(file.read_bytes())
    digest.update(
        (ROOT / "uv.lock").read_bytes() if (ROOT / "uv.lock").exists() else b""
    )
    return digest.hexdigest()


def due(now):
    # Chinese weekends remain excluded even when designated administrative workdays.
    return now.weekday() < 5 and is_workday(now.date()) and now.time() >= time(14, 35)


def deliver(db_path, report_id, sender):
    previous = storage.notification(db_path, report_id)
    if previous and previous["sent_at"]:
        return
    report = storage.report(db_path, report_id)
    message, pending_keys = build_message(report, storage.notified_event_keys(db_path))
    try:
        sender(NOTIFICATION_TITLE, message)
    except Exception as exc:
        storage.record_notification(db_path, report_id, message, error=str(exc))
        raise
    storage.record_notification(
        db_path,
        report_id,
        message,
        sent_at=datetime.now(TZ).isoformat(),
        event_keys=pending_keys,
    )


def run(
    db_path,
    watchlist=None,
    scheduled=False,
    send=True,
    now=None,
    fetcher=fetch,
    sender=notify,
):
    # CLI, scheduler and HTTP updates share the same cross-process lock.
    with check_lock(db_path):
        storage.initialize(db_path)
        return _run(db_path, watchlist, scheduled, send, now, fetcher, sender)


def _run(db_path, watchlist, scheduled, send, now, fetcher, sender):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    if scheduled and not due(now):
        return None
    day = now.date().isoformat()
    assets = (
        watchlist if watchlist is not None else load_configuration(db_path)["assets"]
    )
    config = load_configuration(db_path)
    assets = [a for a in assets if not a.get("archived")]
    previous = (
        storage.latest_scheduled(db_path, day, RULE["version"]) if scheduled else None
    )
    if previous and not retry_due(previous, now):
        if send:
            deliver(db_path, previous["id"], sender)
        return previous["id"]
    if previous and previous.get("configuration"):
        config = previous["configuration"]
    attempt = previous.get("attempt", 1) + 1 if previous else 1
    report = {
        "created_at": now.isoformat(),
        "day": day,
        "kind": "scheduled" if scheduled else "manual",
        "attempt": attempt,
        "parent_report_id": previous["id"] if previous else None,
        "late": now.time() >= time(15),
        "rule": RULE.copy(),
        "configuration": config,
        "code_revision": revision(),
        "code_sha256": code_hash(),
        "items": [],
    }
    previous_items = (
        {item["asset"]["id"]: item for item in previous["items"]} if previous else {}
    )
    if previous:
        # A retry completes the original watchlist, even if configuration changed meanwhile.
        assets = [item["asset"] for item in previous["items"]]
    for asset in assets:
        old = previous_items.get(asset["id"])
        if old and old["status"] != "error":
            item = deepcopy(old)
            item["carried_forward"] = True
            item.setdefault("origin_report_id", previous["id"])
            report["items"].append(item)
            continue
        item = {"asset": asset, "checked_at": now.isoformat(), "carried_forward": False}
        if not asset["enabled"]:
            item.update(status="disabled", error=asset.get("reason", "未启用"))
        else:
            try:
                snapshot = fetcher(asset, day)
                snapshot.setdefault("fetched_at", now.isoformat())
                item["snapshot"] = snapshot
                item["result"] = analyze(snapshot, day)
                if "strategies" in asset:
                    strategies, layers, calculated = evaluate(
                        snapshot, config["strategies"], asset, day
                    )
                    item["result"]["strategies"] = strategies
                    item["result"]["chart"]["overlays"] = layers
                    item["result"]["chart"]["macd_configs"] = [
                        {
                            "id": s["id"],
                            "name": s["name"],
                            "points": calculated[s["id"]]["macd"],
                        }
                        for s in config["strategies"]
                        if s["kind"] == "macd" and s["id"] in calculated
                    ]
                    selected_macd = next(
                        (
                            calculated[s["id"]]["macd"]
                            for s in strategies
                            if s["kind"] == "macd"
                        ),
                        None,
                    )
                    if selected_macd:
                        item["result"]["chart"]["macd"] = selected_macd
                    item["result"]["chart"]["strategy_events"] = [
                        {
                            "time": rows_date,
                            "name": s["name"],
                            "event": event,
                            "provisional": snapshot.get("provisional_date")
                            == rows_date,
                        }
                        for s in strategies
                        for rows_date, event in zip(
                            [r["date"] for r in snapshot["rows"]],
                            calculated[s["id"]]["events"],
                        )
                        if event
                    ]
                item["status"] = "warning" if item["result"]["warnings"] else "ok"
            except Exception as exc:
                item.update(status="error", error=f"{type(exc).__name__}: {exc}")
        report["items"].append(item)
    report_id, _ = storage.save_report(db_path, report)
    if send:
        deliver(db_path, report_id, sender)
    return report_id
