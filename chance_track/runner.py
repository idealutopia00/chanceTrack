import fcntl
import hashlib
import inspect
import subprocess
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, time, timedelta
from pathlib import Path

from chinese_calendar import is_workday

from . import storage
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


def failed_benchmark(item):
    return any(
        ref.get("error")
        for ref in (item.get("snapshot") or {}).get("benchmarks", {}).values()
    )


def retry_due(previous, now):
    return (
        any(
            item["status"] == "error" or failed_benchmark(item)
            for item in previous["items"]
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


def _plan_probe_order(db_path, assets, previous_items, previous_report):
    by_provider = {}
    for asset in assets:
        old = previous_items.get(asset["id"])
        if old and old["status"] != "error" and not failed_benchmark(old):
            continue
        if not asset.get("enabled") or not asset.get("provider"):
            continue
        by_provider.setdefault(asset["provider"], []).append(asset["id"])
    prior_report = previous_report or storage.report(db_path) or {}
    prior_order = prior_report.get("probe_order") or {}
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


def _run(db_path, watchlist, scheduled, send, now, fetcher, sender):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    if scheduled and not due(now):
        return None
    day = now.date().isoformat()
    config = load_configuration(db_path)
    assets = watchlist if watchlist is not None else config["assets"]
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
    previous_items = (
        {item["asset"]["id"]: item for item in previous["items"]} if previous else {}
    )
    if previous:
        # A retry completes the original watchlist, even if configuration changed meanwhile.
        assets = [item["asset"] for item in previous["items"]]
    prior_report = previous or storage.report(db_path) or {}
    prior_order = prior_report.get("probe_order") or {}
    probe_order = _plan_probe_order(db_path, assets, previous_items, previous)
    report = {
        "created_at": now.isoformat(),
        "day": day,
        "kind": "scheduled" if scheduled else "manual",
        "attempt": attempt,
        "parent_report_id": previous["id"] if previous else None,
        "late": now.time() >= time(15),
        "rule": RULE.copy(),
        "configuration": config,
        "probe_order": probe_order,
        "code_revision": revision(),
        "code_sha256": code_hash(),
        "items": [],
    }
    assets_by_id = {asset["id"]: asset for asset in config["assets"]}
    active_by_id = {asset["id"]: asset for asset in assets}
    market_cache = {}

    def market_snapshot(asset):
        asset_id = asset["id"]
        if asset_id not in market_cache:
            carried = previous_items.get(asset_id)
            try:
                if carried and carried["status"] in ("ok", "warning"):
                    market_cache[asset_id] = carried["snapshot"]
                else:
                    snapshot = _call_fetcher(fetcher, asset, day, now)
                    snapshot.setdefault("fetched_at", now.isoformat())
                    market_cache[asset_id] = snapshot
            except Exception as exc:
                market_cache[asset_id] = exc
        result = market_cache[asset_id]
        if isinstance(result, Exception):
            raise result
        return result

    for ids in probe_order.values():
        for asset_id in ids:
            try:
                market_snapshot(active_by_id[asset_id])
            except Exception:
                pass
    recorded_probe_order = {}
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
    if recorded_probe_order:
        report["probe_order"] = recorded_probe_order
    else:
        report.pop("probe_order", None)

    for asset in assets:
        old = previous_items.get(asset["id"])
        if old and old["status"] != "error" and not failed_benchmark(old):
            item = deepcopy(old)
            item["carried_forward"] = True
            item.setdefault("origin_report_id", previous["id"])
            report["items"].append(item)
            continue
        item = {"asset": asset, "checked_at": now.isoformat(), "carried_forward": False}
        if old and old["status"] in ("ok", "warning"):
            item.update(
                checked_at=old["checked_at"],
                carried_forward=True,
                origin_report_id=old.get("origin_report_id", previous["id"]),
            )
        if not asset["enabled"]:
            item.update(status="disabled", error=asset.get("reason", "未启用"))
        else:
            try:
                snapshot = market_snapshot(asset)
                references = dict(snapshot.get("benchmarks", {}))
                for benchmark_id in relative_benchmarks(config["strategies"], asset):
                    frozen = references.get(benchmark_id)
                    if frozen and not frozen.get("error"):
                        continue
                    try:
                        references[benchmark_id] = benchmark_context(
                            market_snapshot(assets_by_id[benchmark_id])
                        )
                    except Exception as exc:
                        references[benchmark_id] = {
                            "error": f"基准 {benchmark_id} 采集失败：{exc}"
                        }
                if references:
                    snapshot = {**snapshot, "benchmarks": references}
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
                    item["result"]["chart"]["strategy_events"] = chart_events(
                        snapshot, strategies, calculated
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
        report["items"].append(item)
    report_id, _ = storage.save_report(db_path, report)
    if send:
        deliver(db_path, report_id, sender)
    return report_id
