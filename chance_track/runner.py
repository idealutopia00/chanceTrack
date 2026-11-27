"""Scheduled and manual check orchestration writing to Schema v6 normalized tables."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, time, timedelta
import fcntl
import inspect
from pathlib import Path
import subprocess

from chinese_calendar import is_workday

from . import storage, workspace
from .analysis import RULE, analyze
from .configuration import load_configuration, relative_benchmarks
from .data import TZ, MarketFetchError, fetch, provider_label
from .notifications import NOTIFICATION_TITLE, build_message, notify
from .strategies import benchmark_context, chart_events, evaluate

ROOT = Path(__file__).resolve().parent.parent
MAX_ATTEMPTS = 3
CHECK_START = time(14, 35)
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
        any(
            item["status"] == "error" or item.get("failed_benchmark")
            for item in previous.get("items", [])
        )
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


def due(now):
    # Chinese weekends remain excluded even when designated administrative workdays.
    return now.weekday() < 5 and is_workday(now.date()) and now.time() >= CHECK_START


def next_regular_check(now):
    candidate = datetime.combine(now.date(), CHECK_START, tzinfo=TZ)
    if candidate <= now:
        candidate += timedelta(days=1)
    try:
        while not due(candidate):
            candidate += timedelta(days=1)
    except NotImplementedError:
        return None
    return candidate


def deliver(db_path, run_id, report_payload, sender):
    previous = storage.get_notification(db_path, run_id)
    if previous and previous["sent_at"]:
        return
    message, pending_keys = build_message(
        report_payload, storage.notified_event_keys(db_path)
    )
    try:
        sender(NOTIFICATION_TITLE, message)
    except Exception as exc:
        storage.record_notification(db_path, run_id, message, error=str(exc))
        raise
    storage.record_notification(
        db_path,
        run_id,
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
    with check_lock(db_path):
        storage.initialize(db_path)
        return _run(db_path, watchlist, scheduled, send, now, fetcher, sender)


def _call_fetcher(fetcher, asset, day, now):
    try:
        params = inspect.signature(fetcher).parameters
    except (TypeError, ValueError):
        return fetcher(asset, day)
    if "now" in params or any(
        param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()
    ):
        return fetcher(asset, day, now=now)
    return fetcher(asset, day)


def _actually_probed(cache_entry, provider):
    if cache_entry is None:
        return False
    if isinstance(cache_entry, dict):
        attempts = cache_entry.get("attempts")
        if not attempts:
            return True
        return any(
            entry.get("provider") == provider and entry.get("status") != "cooling_down"
            for entry in attempts
        )
    if isinstance(cache_entry, MarketFetchError):
        return any(
            entry.get("provider") == provider and entry.get("status") != "cooling_down"
            for entry in cache_entry.attempts
        )
    return type(cache_entry).__name__ != "SourceCoolingDown"


def _plan_probe_order(db_path, assets, previous_items, previous_run):
    by_provider = {}
    for asset in assets:
        old = previous_items.get(asset["id"])
        if old and old["status"] != "error":
            continue
        if not asset.get("enabled") or not asset.get("provider"):
            continue
        by_provider.setdefault(asset["provider"], []).append(asset["id"])
    prior_run = previous_run or storage.get_check_run(db_path) or {}
    prior_order = prior_run.get("probe_order") or {}
    probe_order = {}
    for provider, ids in by_provider.items():
        if len(ids) < 2:
            continue
        previous_ids = prior_order.get(provider) or []
        if previous_ids and previous_ids[0] in ids:
            start = (ids.index(previous_ids[0]) + 1) % len(ids)
            probe_order[provider] = ids[start:] + ids[:start]
        else:
            probe_order[provider] = list(ids)
    return probe_order


def _build_notification_payload(db_path, run_record, config):
    """Reconstruct lightweight report payload for notification rendering on retries/redeliveries."""
    assets_by_id = {a["id"]: a for a in config["assets"]}
    items = []
    for item in run_record.get("items", []):
        asset = assets_by_id.get(item["asset_id"])
        if not asset:
            continue
        entry = {
            "asset": asset,
            "status": item["status"],
            "error": item.get("error"),
            "provider": item.get("provider"),
            "fetch_status": item.get("fetch_status"),
            "attempts": item.get("attempts"),
            "last_quote": item.get("last_quote"),
        }
        if item["status"] in ("ok", "warning"):
            snap = storage.snapshot_from_bars(db_path, asset)
            if snap:
                refs = {}
                for benchmark_id in relative_benchmarks(config["strategies"], asset):
                    peer = assets_by_id.get(benchmark_id)
                    if not peer:
                        continue
                    peer_snap = storage.snapshot_from_bars(db_path, peer)
                    if peer_snap:
                        refs[benchmark_id] = benchmark_context(peer_snap)
                if refs:
                    snap = {**snap, "benchmarks": refs}
                res = analyze(snap, run_record["day"])
                strats, _, _ = evaluate(snap, config["strategies"], asset, run_record["day"])
                res["strategies"] = strats
                entry["snapshot"] = snap
                entry["result"] = res
        items.append(entry)
    return {
        "created_at": run_record["created_at"],
        "day": run_record["day"],
        "attempt": run_record.get("attempt", 1),
        "late": run_record.get("late", False),
        "rule": RULE.copy(),
        "items": items,
    }


def _run(db_path, watchlist, scheduled, send, now, fetcher, sender):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    if scheduled and not due(now):
        return None
    day = now.date().isoformat()
    config = load_configuration(db_path)
    assets = watchlist if watchlist is not None else config["assets"]
    assets = [a for a in assets if not a.get("archived")]
    previous = (
        storage.latest_scheduled_run(db_path, day, RULE["version"]) if scheduled else None
    )
    if previous and not retry_due(previous, now):
        if send:
            deliver(
                db_path,
                previous["id"],
                _build_notification_payload(db_path, previous, config),
                sender,
            )
        return previous["id"]

    attempt = previous.get("attempt", 1) + 1 if previous else 1
    previous_items = (
        {item["asset_id"]: item for item in previous.get("items", [])} if previous else {}
    )
    prior_run = previous or storage.get_check_run(db_path) or {}
    prior_order = prior_run.get("probe_order") or {}
    probe_order = _plan_probe_order(db_path, assets, previous_items, previous)

    assets_by_id = {asset["id"]: asset for asset in config["assets"]}
    active_by_id = {asset["id"]: asset for asset in assets}
    market_cache = {}

    def market_snapshot(asset):
        asset_id = asset["id"]
        if asset_id not in market_cache:
            carried = previous_items.get(asset_id)
            try:
                if carried and carried["status"] in ("ok", "warning"):
                    snap = storage.snapshot_from_bars(db_path, asset)
                    if snap:
                        market_cache[asset_id] = snap
                    else:
                        snap = _call_fetcher(fetcher, asset, day, now)
                        snap.setdefault("fetched_at", now.isoformat())
                        market_cache[asset_id] = snap
                else:
                    snap = _call_fetcher(fetcher, asset, day, now)
                    snap.setdefault("fetched_at", now.isoformat())
                    market_cache[asset_id] = snap
            except Exception as exc:
                market_cache[asset_id] = exc
        res = market_cache[asset_id]
        if isinstance(res, Exception):
            raise res
        return res

    for ids in probe_order.values():
        for asset_id in ids:
            try:
                market_snapshot(active_by_id[asset_id])
            except Exception:
                pass

    recorded_probe_order = {
        provider: list(ids) for provider, ids in prior_order.items() if ids
    }
    for provider, planned_ids in probe_order.items():
        probed = [
            aid for aid in planned_ids if _actually_probed(market_cache.get(aid), provider)
        ]
        if probed:
            recorded_probe_order[provider] = [
                probed[0],
                *(aid for aid in planned_ids if aid != probed[0]),
            ]
        elif prior_order.get(provider):
            recorded_probe_order[provider] = list(prior_order[provider])

    items = []
    for asset in assets:
        old = previous_items.get(asset["id"])
        item = {"asset": asset, "checked_at": now.isoformat(), "carried_forward": False}
        if old and old["status"] in ("ok", "warning") and not old.get("failed_benchmark"):
            item.update(
                carried_forward=True,
                origin_run_id=old.get("origin_run_id") or previous["id"],
            )
        if not asset.get("enabled", True):
            item.update(status="disabled", error=asset.get("reason", "未启用"))
        else:
            try:
                snapshot = market_snapshot(asset)
                references = dict(snapshot.get("benchmarks", {}))
                benchmark_errors = {}
                for benchmark_id in relative_benchmarks(config["strategies"], asset):
                    frozen = references.get(benchmark_id)
                    if frozen and not frozen.get("error"):
                        continue
                    try:
                        references[benchmark_id] = benchmark_context(
                            market_snapshot(assets_by_id[benchmark_id])
                        )
                    except Exception as exc:
                        err_msg = f"基准 {benchmark_id} 采集失败：{exc}"
                        references[benchmark_id] = {"error": err_msg}
                        benchmark_errors[benchmark_id] = err_msg
                if references:
                    snapshot = {**snapshot, "benchmarks": references}
                if benchmark_errors:
                    item["failed_benchmark"] = True
                    item["benchmark_errors"] = benchmark_errors
                item["snapshot"] = snapshot
                item["result"] = analyze(snapshot, day)
                strategies_out, layers, calculated = evaluate(
                    snapshot, config["strategies"], asset, day
                )
                item["result"]["strategies"] = strategies_out
                item["result"]["chart"]["overlays"] = layers
                item["result"]["chart"]["strategy_events"] = chart_events(
                    snapshot, strategies_out, calculated
                )
                item["status"] = "warning" if item["result"]["warnings"] else "ok"
                actual_provider = snapshot.get("provider") or asset.get("provider")
                item["provider"] = actual_provider
                item["provider_label"] = provider_label(actual_provider)
                item["fetch_status"] = snapshot.get("fetch_status") or (
                    "fallback_ok" if snapshot.get("fallback_used") else "primary_ok"
                )
                if snapshot.get("attempts"):
                    item["attempts"] = snapshot["attempts"]
            except Exception as exc:
                item.pop("result", None)
                last_quote = storage.last_market_quote(db_path, asset["id"])
                if isinstance(exc, MarketFetchError):
                    fetch_status = exc.fetch_status
                    attempts = exc.attempts
                else:
                    exc_name = type(exc).__name__
                    if "SourceCoolingDown" in exc_name:
                        fetch_status = "cooling_down"
                    elif any(
                        token in exc_name
                        for token in ("RemoteProtocolError", "Connect", "Timeout")
                    ):
                        fetch_status = "disconnected"
                    elif "StaleMarketSnapshot" in exc_name:
                        fetch_status = "stale"
                    elif "InsufficientMarketHistory" in exc_name:
                        fetch_status = "insufficient_history"
                    else:
                        fetch_status = "error"
                    provider = asset.get("provider")
                    attempts = (
                        [
                            {
                                "provider": provider,
                                "provider_label": provider_label(provider),
                                "role": "primary",
                                "status": fetch_status,
                                "detail": f"{exc_name}: {exc}",
                            }
                        ]
                        if provider
                        else []
                    )
                item.update(
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                    fetch_status=fetch_status,
                    attempts=attempts,
                    has_historical_snapshot=bool(last_quote),
                    last_quote=last_quote,
                )
        items.append(item)

    for asset in assets:
        provider = asset.get("provider")
        aid = asset["id"]
        if not provider or provider in probe_order or not asset.get("enabled", True):
            continue
        old = previous_items.get(aid)
        if old and old["status"] in ("ok", "warning"):
            continue
        if _actually_probed(market_cache.get(aid), provider):
            existing = recorded_probe_order.get(provider) or []
            recorded_probe_order[provider] = [
                aid,
                *(other for other in existing if other != aid),
            ]

    run_record = {
        "created_at": now.isoformat(),
        "day": day,
        "kind": "scheduled" if scheduled else "manual",
        "attempt": attempt,
        "parent_run_id": previous["id"] if previous else None,
        "rule_version": RULE["version"],
        "config_version": config["version"],
        "code_revision": revision(),
        "late": now.time() >= time(15),
        "probe_order": recorded_probe_order,
    }
    run_id, _ = storage.save_check_run(db_path, run_record, items)
    workspace.refresh_signal_summaries(
        db_path,
        asset_ids=[asset["id"] for asset in assets],
        now=now,
    )
    if send:
        notif_payload = {
            **run_record,
            "rule": RULE.copy(),
            "items": items,
        }
        deliver(db_path, run_id, notif_payload, sender)
    return run_id
