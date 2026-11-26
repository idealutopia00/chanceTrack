"""v1.0 Asset-centric chart computation, live exploration preview, and config commands."""

from copy import deepcopy
from datetime import datetime
from uuid import uuid4

from . import storage
from .analysis import analyze
from .catalog import catalog_by_id, load_catalog
from .configuration import (
    ConfigurationConflict,
    load_configuration,
    relative_benchmarks,
    save_configuration,
    strategy_signature,
    validate_configuration,
)
from .data import TZ, fetch, provider_label
from .presentation import fetch_state_summary
from .strategies import benchmark_context, chart_events, evaluate


def search_catalog(path, query=""):
    config = load_configuration(path)
    known = load_catalog()
    by_id = {a["id"]: a for a in known}
    configured_ids = set()
    for asset in config["assets"]:
        configured_ids.add(asset["id"])
        preset = by_id.get(asset["id"])
        aliases = asset.get("aliases") or (preset.get("aliases") if preset else [])
        by_id[asset["id"]] = {
            **asset,
            "aliases": list(aliases),
            "in_workspace": not asset.get("archived", False) and asset.get("watchlisted", True),
        }
    for aid, item in by_id.items():
        item.setdefault("in_workspace", False)

    q = query.strip().casefold()
    results = []
    for a in by_id.values():
        haystack = " ".join(
            [
                *(str(a.get(k, "")) for k in ("label", "code", "ticker", "market", "group_name", "group")),
                *(str(alias) for alias in a.get("aliases", [])),
            ]
        ).casefold()
        if not q or q in haystack:
            results.append(a)
    return results


def _enrich_snapshot_benchmarks(path, snapshot, strategies, asset, assets_by_id):
    refs = dict(snapshot.get("benchmarks", {}))
    for benchmark_id in relative_benchmarks(strategies, asset) - refs.keys():
        peer = assets_by_id.get(benchmark_id)
        if not peer:
            continue
        peer_snap = storage.snapshot_from_bars(path, peer)
        if peer_snap:
            refs[benchmark_id] = benchmark_context(peer_snap)
    if refs:
        return {**snapshot, "benchmarks": refs}
    return snapshot


def compute_asset_chart(path, asset_id, *, strategies_override=None):
    """Compute OHLCV chart, overlays, MACD panes, strategy events & decision markers from market_bars."""
    config = load_configuration(path)
    assets_by_id = {a["id"]: a for a in config["assets"]}
    asset = assets_by_id.get(asset_id)
    if not asset:
        raise ValueError("标的不存在")

    snapshot = storage.snapshot_from_bars(path, asset)
    latest_run_items = storage.latest_run_items_by_asset(path)
    run_item = latest_run_items.get(asset_id)
    decisions = storage.list_decisions(path, asset_id=asset_id, limit=200)
    notes = storage.list_notes(path, asset_id=asset_id)

    if not snapshot:
        return {
            "asset": asset,
            "has_bars": False,
            "mode": "exploration" if strategies_override is not None else "formal",
            "status": run_item["status"] if run_item else "idle",
            "error": run_item.get("error") if run_item else None,
            "fetch_summary": fetch_state_summary({"asset": asset, **(run_item or {"status": "ok"})})
            if run_item
            else None,
            "result": None,
            "decisions": decisions,
            "notes": notes,
        }

    is_exploration = strategies_override is not None
    if is_exploration:
        if not isinstance(strategies_override, list) or len(strategies_override) > 24:
            raise ValueError("探索指标最多24个")
        validate_configuration({"assets": [], "strategies": strategies_override})
        eval_strategies = strategies_override
        eval_asset = {
            **asset,
            "strategies": [s["id"] for s in eval_strategies],
            "activated": {},
        }
    else:
        eval_strategies = config["strategies"]
        eval_asset = asset

    day = snapshot["rows"][-1]["date"]
    enriched = _enrich_snapshot_benchmarks(
        path, snapshot, eval_strategies, eval_asset, assets_by_id
    )
    result = analyze(enriched, day)
    strat_results, layers, computed = evaluate(enriched, eval_strategies, eval_asset, day)
    result["strategies"] = strat_results
    result["chart"]["overlays"] = layers
    macd_list = [
        {"id": s["id"], "name": s["name"], "points": computed[s["id"]]["macd"]}
        for s in eval_strategies
        if s["kind"] == "macd" and s["id"] in computed
    ]
    result["chart"]["macd_configs"] = macd_list
    if macd_list:
        result["chart"]["macd"] = macd_list[0]["points"]
    result["chart"]["strategy_events"] = chart_events(enriched, strat_results, computed)

    # Price change relative to previous bar
    candles = result["chart"]["candles"]
    prev_close = candles[-2]["close"] if len(candles) >= 2 else None
    last_close = candles[-1]["close"] if candles else None
    change_pct = (
        round((last_close - prev_close) / prev_close * 100, 2)
        if prev_close and last_close is not None
        else None
    )

    synthetic_item = {
        "asset": asset,
        "status": run_item["status"] if run_item else "ok",
        "error": run_item.get("error") if run_item else None,
        "provider": snapshot.get("provider"),
        "provider_label": provider_label(snapshot.get("provider")),
        "fetch_status": (run_item or {}).get("fetch_status"),
        "attempts": (run_item or {}).get("attempts"),
        "last_quote": storage.last_market_quote(path, asset_id)
        if (run_item and run_item["status"] == "error")
        else None,
        "snapshot": snapshot,
        "result": result,
    }

    return {
        "asset": asset,
        "has_bars": True,
        "mode": "exploration" if is_exploration else "formal",
        "status": run_item["status"] if run_item else "ok",
        "error": run_item.get("error") if run_item else None,
        "carried_forward": bool((run_item or {}).get("carried_forward", False)),
        "checked_at": (run_item or {}).get("checked_at") or snapshot.get("fetched_at"),
        "fetched_at": snapshot.get("fetched_at"),
        "provider": snapshot.get("provider"),
        "provider_label": provider_label(snapshot.get("provider")),
        "change_pct": change_pct,
        "fetch_summary": fetch_state_summary(synthetic_item),
        "result": result,
        "decisions": decisions,
        "notes": notes,
        "strategies_used": eval_strategies if is_exploration else None,
    }


def asset_summaries(path):
    """Build lightweight summary cards for every configured asset (used in /api/v1/bootstrap)."""
    config = load_configuration(path)
    latest_items = storage.latest_run_items_by_asset(path)
    recent_signals = storage.list_signals(path, limit=100)
    latest_sig_by_asset = {}
    for sig in recent_signals:
        if sig["asset_id"] not in latest_sig_by_asset:
            latest_sig_by_asset[sig["asset_id"]] = sig

    summaries = []
    with storage.connect(path) as db:
        for asset in config["assets"]:
            aid = asset["id"]
            bars = db.execute(
                """SELECT bar_date, close, is_provisional, provider, fetched_at
                   FROM market_bars WHERE asset_id=? ORDER BY bar_date DESC LIMIT 2""",
                (aid,),
            ).fetchall()
            latest_bar = dict(bars[0]) if bars else None
            prev_bar = dict(bars[1]) if len(bars) > 1 else None
            close = latest_bar["close"] if latest_bar else None
            prev_close = prev_bar["close"] if prev_bar else None
            change_pct = (
                round((close - prev_close) / prev_close * 100, 2)
                if close is not None and prev_close
                else None
            )
            run_item = latest_items.get(aid)
            sig = latest_sig_by_asset.get(aid)
            summaries.append(
                {
                    **asset,
                    "close": close,
                    "change_pct": change_pct,
                    "bar_date": latest_bar["bar_date"] if latest_bar else None,
                    "is_provisional": bool(latest_bar["is_provisional"]) if latest_bar else False,
                    "fetched_at": latest_bar["fetched_at"] if latest_bar else None,
                    "status": run_item["status"] if run_item else ("ok" if latest_bar else "idle"),
                    "error": run_item.get("error") if run_item else None,
                    "latest_signal": (
                        {
                            "event_date": sig["event_date"],
                            "event_kind": sig["event_kind"],
                            "strategy_name": sig["strategy_name"],
                            "is_provisional": sig["is_provisional"],
                        }
                        if sig
                        else None
                    ),
                }
            )
    return config["version"], summaries, config["strategies"]


def add_asset(path, payload, *, fetcher=None):
    fetcher = fetcher or fetch
    config = load_configuration(path)
    expected = payload["version"]
    if expected != config["version"]:
        raise ConfigurationConflict("配置已修改，请重新加载后重试")
    candidate = payload["asset"]
    captured = {}

    def capture(asset, day):
        snap = fetcher(asset, day)
        captured[asset["id"]] = snap
        return snap

    old = next((a for a in config["assets"] if a["id"] == candidate.get("id")), None)
    if old:
        old.update(enabled=True, archived=False, watchlisted=True)
        selected = old["id"]
    else:
        preset = catalog_by_id().get(candidate.get("id"))
        if preset:
            new_asset = deepcopy(preset)
            new_asset.update(enabled=True, archived=False, watchlisted=True, strategies=[])
        else:
            allowed = (
                "label",
                "ticker",
                "code",
                "provider",
                "session_profile",
                "secid",
                "expected_name",
                "market",
                "currency",
                "kind",
                "group",
                "group_name",
                "fallbacks",
            )
            new_asset = {k: v for k, v in candidate.items() if k in allowed and v not in ("", None)}
            new_asset.setdefault("group", new_asset.get("group_name") or "未分组")
            new_asset.update(
                id="asset_" + uuid4().hex[:12],
                enabled=True,
                archived=False,
                watchlisted=True,
                strategies=[],
            )
        config["assets"].append(new_asset)
        selected = new_asset["id"]

    new_version = save_configuration(path, config, expected, fetcher=capture)
    if selected in captured:
        storage.save_bars(path, selected, captured[selected])
    elif not storage.last_bar(path, selected):
        a = next(a for a in load_configuration(path)["assets"] if a["id"] == selected)
        snap = fetcher(a, datetime.now(TZ).date().isoformat())
        storage.save_bars(path, selected, snap)
    return {"version": new_version, "selected": selected}


def update_asset(path, asset_id, payload, *, fetcher=None):
    fetcher = fetcher or fetch
    config = load_configuration(path)
    expected = payload["version"]
    if expected != config["version"]:
        raise ConfigurationConflict("配置已修改，请重新加载后重试")
    asset = next((a for a in config["assets"] if a["id"] == asset_id), None)
    if not asset:
        raise ValueError("标的不存在")
    for key in ("priority", "watchlisted", "enabled", "archived"):
        if key in payload:
            if type(payload[key]) is not bool:
                raise ValueError(f"{key} 须为布尔值")
            asset[key] = payload[key]
    for key in ("label", "group", "group_name"):
        if key in payload and isinstance(payload[key], str):
            val = payload[key].strip()
            if key in ("group", "group_name"):
                asset["group"] = val or "未分组"
                asset["group_name"] = asset["group"]
            elif val:
                asset["label"] = val
    captured = {}

    def capture(a, day):
        snap = fetcher(a, day)
        captured[a["id"]] = snap
        return snap

    new_version = save_configuration(path, config, expected, fetcher=capture)
    if asset_id in captured:
        storage.save_bars(path, asset_id, captured[asset_id])
    return {"version": new_version, "asset_id": asset_id}


def reorder_assets(path, ids, expected_version):
    config = load_configuration(path)
    if expected_version != config["version"]:
        raise ConfigurationConflict("配置已修改，请重新加载后重试")
    if len(ids) != len(set(ids)) or set(ids) != {a["id"] for a in config["assets"]}:
        raise ValueError("排序必须包含全部标的且不可重复")
    by_id = {a["id"]: a for a in config["assets"]}
    config["assets"] = [by_id[aid] for aid in ids]
    new_version = save_configuration(path, config, expected_version, validate_new=False)
    return {"version": new_version}


def update_asset_monitoring(path, asset_id, payload):
    """Bind, unbind, create-local, or shared-update a monitoring strategy for one asset."""
    config = load_configuration(path)
    expected = payload["version"]
    if expected != config["version"]:
        raise ConfigurationConflict("配置已修改，请重新加载后重试")
    asset = next((a for a in config["assets"] if a["id"] == asset_id), None)
    if not asset:
        raise ValueError("标的不存在")

    action = payload["action"]
    saved_strategy_id = None
    if action == "bind":
        sid = payload["strategy_id"]
        if not any(s["id"] == sid for s in config["strategies"]):
            raise ValueError("策略配置不存在")
        if sid not in asset["strategies"]:
            asset["strategies"].append(sid)
        saved_strategy_id = sid
    elif action == "unbind":
        sid = payload["strategy_id"]
        asset["strategies"] = [s for s in asset["strategies"] if s != sid]
    elif action == "save":
        strategy = deepcopy(payload["strategy"])
        shared = bool(payload.get("shared", False))
        old = next((s for s in config["strategies"] if s["id"] == strategy.get("id")), None)
        if shared and old:
            strategy["id"] = old["id"]
            config["strategies"] = [
                strategy if s["id"] == old["id"] else s for s in config["strategies"]
            ]
        else:
            # Reuse exact match if identical signature exists, otherwise create local rule
            existing_match = next(
                (
                    s
                    for s in config["strategies"]
                    if strategy_signature(s) == strategy_signature(strategy)
                ),
                None,
            )
            if existing_match:
                strategy = existing_match
            else:
                strategy["id"] = "strategy_" + uuid4().hex[:12]
                config["strategies"].append(strategy)
            replace_id = payload.get("replace_id")
            if replace_id:
                asset["strategies"] = [
                    sid for sid in asset["strategies"] if sid != replace_id
                ]
        if strategy["id"] not in asset["strategies"]:
            asset["strategies"].append(strategy["id"])
        saved_strategy_id = strategy["id"]
    else:
        raise ValueError("未知监测操作")

    new_version = save_configuration(path, config, expected, validate_new=False)
    return {"version": new_version, "strategy_id": saved_strategy_id}


def save_strategy_rule(path, payload):
    """Create, update, merge, or delete a strategy in the strategy library."""
    config = load_configuration(path)
    expected = payload["version"]
    if expected != config["version"]:
        raise ConfigurationConflict("配置已修改，请重新加载后重试")
    op = payload.get("op", "upsert")
    lifecycle = None
    saved_id = None
    if op == "upsert":
        rule = deepcopy(payload["strategy"])
        if not rule.get("id"):
            rule["id"] = "strategy_" + uuid4().hex[:12]
        saved_id = rule["id"]
        idx = next((i for i, s in enumerate(config["strategies"]) if s["id"] == saved_id), None)
        if idx is None:
            config["strategies"].append(rule)
        else:
            config["strategies"][idx] = rule
    elif op == "delete":
        lifecycle = [payload["strategy_id"], ""]
    elif op == "merge":
        lifecycle = [payload["source_id"], payload["keep_id"]]
    else:
        raise ValueError("未知策略操作")
    new_version = save_configuration(
        path, config, expected, lifecycle=lifecycle, validate_new=False
    )
    return {"version": new_version, "strategy_id": saved_id}
