"""v1.0 Asset-centric chart computation, live exploration preview, and config commands."""

from copy import deepcopy
from datetime import datetime
import hashlib
import json
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
from .strategies import benchmark_context, chart_events, evaluate, event_direction


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
        raise LookupError("标的不存在")

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


def _strategy_fingerprint(asset, strategy, strategies_by_id, assets_by_id, bar_fps):
    """Deterministic fingerprint of an asset's bars + one strategy (including deps & benchmarks)."""
    deps = {}
    benchmark_ids = set()
    if strategy.get("kind") == "combination":
        for role in ("trigger", "band"):
            tid = strategy.get(role)
            if tid and tid in strategies_by_id:
                dep = strategies_by_id[tid]
                deps[role] = {
                    "id": dep["id"],
                    "kind": dep["kind"],
                    "name": dep["name"],
                    "revision": dep.get("revision", 1),
                    "params": dep.get("params") or {},
                }
                if dep["kind"] == "relative_strength" and (dep.get("params") or {}).get("benchmark"):
                    benchmark_ids.add(dep["params"]["benchmark"])
    elif strategy.get("kind") == "relative_strength":
        bid = (strategy.get("params") or {}).get("benchmark")
        if bid:
            benchmark_ids.add(bid)

    benchmarks = {}
    for bid in sorted(benchmark_ids):
        peer = assets_by_id.get(bid)
        benchmarks[bid] = {
            "code": peer.get("code") if peer else None,
            "provider": peer.get("provider") if peer else None,
            "session_profile": peer.get("session_profile") if peer else None,
            "bars": bar_fps.get(bid),
        }

    payload = {
        "asset": {
            "id": asset["id"],
            "code": asset.get("code"),
            "provider": asset.get("provider"),
            "session_profile": asset.get("session_profile"),
        },
        "bars": bar_fps.get(asset["id"]),
        "strategy": {
            "id": strategy["id"],
            "kind": strategy["kind"],
            "name": strategy["name"],
            "revision": strategy.get("revision", 1),
            "params": strategy.get("params") or {},
            "filter": strategy.get("filter"),
            "trigger": strategy.get("trigger"),
            "band": strategy.get("band"),
        },
        "deps": deps,
        "benchmarks": benchmarks,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def refresh_signal_summaries(path, *, asset_ids=None, now=None):
    """Recompute per-enabled-strategy summaries from persisted market_bars (zero network/notify/signal_events writes)."""
    config = load_configuration(path)
    assets_by_id = {a["id"]: a for a in config["assets"]}
    strategies_by_id = {s["id"]: s for s in config["strategies"]}
    bar_fps = storage.asset_bar_fingerprints(path)
    now_iso = (
        now.isoformat()
        if isinstance(now, datetime)
        else (str(now) if now else datetime.now(TZ).isoformat())
    )

    if asset_ids is None:
        target_ids = [a["id"] for a in config["assets"] if not a.get("archived")]
        prune_ids = [a["id"] for a in config["assets"] if a.get("archived")]
    else:
        expanded = set(asset_ids)
        for a in config["assets"]:
            if a.get("archived"):
                continue
            if relative_benchmarks(config["strategies"], a) & expanded:
                expanded.add(a["id"])
        target_ids = [
            a["id"]
            for a in config["assets"]
            if a["id"] in expanded and not a.get("archived")
        ]
        prune_ids = [
            aid
            for aid in expanded
            if aid not in assets_by_id or assets_by_id[aid].get("archived")
        ]

    summaries_by_asset = {}
    total_rows = 0

    for aid in target_ids:
        asset = assets_by_id[aid]
        enabled_sids = [sid for sid in asset.get("strategies") or [] if sid in strategies_by_id]
        if not enabled_sids:
            summaries_by_asset[aid] = []
            continue

        snapshot = storage.snapshot_from_bars(path, asset)
        if not snapshot:
            rows = []
            for sid in enabled_sids:
                rule = strategies_by_id[sid]
                rows.append(
                    {
                        "asset_id": aid,
                        "strategy_id": sid,
                        "strategy_name": rule["name"],
                        "strategy_kind": rule["kind"],
                        "strategy_version": f"{sid}:v{rule.get('revision', 1)}",
                        "bar_date": None,
                        "bar_is_provisional": False,
                        "event_date": None,
                        "event_kind": None,
                        "direction": None,
                        "detail": "暂无已保存日线",
                        "event_is_provisional": False,
                        "state": "idle",
                        "checked_event_kind": None,
                        "checked_event_date": None,
                        "checked_is_provisional": False,
                        "source_fingerprint": _strategy_fingerprint(
                            asset, rule, strategies_by_id, assets_by_id, bar_fps
                        ),
                        "refreshed_at": now_iso,
                    }
                )
            summaries_by_asset[aid] = rows
            total_rows += len(rows)
            continue

        day = snapshot["rows"][-1]["date"]
        bar_is_provisional = bool(snapshot.get("provisional_date") == day)
        enriched = _enrich_snapshot_benchmarks(
            path, snapshot, config["strategies"], asset, assets_by_id
        )
        strat_results, _, computed = evaluate(enriched, config["strategies"], asset, day)
        ev_list = chart_events(enriched, strat_results, computed)

        latest_ev_by_sid = {}
        for ev in ev_list:
            sid = ev.get("strategy_id")
            if sid:
                latest_ev_by_sid[sid] = ev

        strat_by_id = {s["id"]: s for s in strat_results}
        rows = []
        for sid in enabled_sids:
            rule = strategies_by_id[sid]
            s = strat_by_id.get(sid) or {}
            latest_ev = latest_ev_by_sid.get(sid)
            if s.get("preview_event"):
                checked_kind = s["preview_event"]
                checked_date = s.get("bar_date") or day
                checked_prov = True
            elif s.get("event"):
                checked_kind = s["event"]
                checked_date = s.get("event_date") or day
                checked_prov = False
            else:
                checked_kind = None
                checked_date = None
                checked_prov = False

            rows.append(
                {
                    "asset_id": aid,
                    "strategy_id": sid,
                    "strategy_name": s.get("name") or rule["name"],
                    "strategy_kind": s.get("kind") or rule["kind"],
                    "strategy_version": s.get("version") or f"{sid}:v{rule.get('revision', 1)}",
                    "bar_date": day,
                    "bar_is_provisional": bar_is_provisional,
                    "event_date": latest_ev["date"] if latest_ev else None,
                    "event_kind": latest_ev["event"] if latest_ev else None,
                    "direction": latest_ev["direction"] if latest_ev else None,
                    "detail": (
                        latest_ev.get("detail")
                        if latest_ev
                        else (s.get("detail") or "")
                    )
                    or "",
                    "event_is_provisional": bool(latest_ev.get("provisional")) if latest_ev else False,
                    "state": s.get("state") or "ready",
                    "checked_event_kind": checked_kind,
                    "checked_event_date": checked_date,
                    "checked_is_provisional": checked_prov,
                    "source_fingerprint": _strategy_fingerprint(
                        asset, rule, strategies_by_id, assets_by_id, bar_fps
                    ),
                    "refreshed_at": now_iso,
                }
            )
        summaries_by_asset[aid] = rows
        total_rows += len(rows)

    storage.replace_asset_strategy_summaries(
        path, summaries_by_asset, prune_asset_ids=prune_ids
    )
    return {
        "refreshed_assets": len(summaries_by_asset),
        "summary_rows": total_rows,
        "refreshed_at": now_iso,
    }


def asset_summaries(path):
    """Build lightweight summary cards for every configured asset (used in /api/v1/bootstrap)."""
    config = load_configuration(path)
    assets_by_id = {a["id"]: a for a in config["assets"]}
    strategies_by_id = {s["id"]: s for s in config["strategies"]}
    bar_fps = storage.asset_bar_fingerprints(path)
    stored_summaries = storage.list_asset_strategy_summaries(path)

    stale_ids = []
    for asset in config["assets"]:
        if asset.get("archived"):
            if asset["id"] in stored_summaries:
                stale_ids.append(asset["id"])
            continue
        aid = asset["id"]
        expected_sids = [sid for sid in asset.get("strategies") or [] if sid in strategies_by_id]
        existing = stored_summaries.get(aid, {})
        if set(existing.keys()) != set(expected_sids):
            stale_ids.append(aid)
            continue
        for sid in expected_sids:
            fp = _strategy_fingerprint(asset, strategies_by_id[sid], strategies_by_id, assets_by_id, bar_fps)
            if existing[sid].get("source_fingerprint") != fp:
                stale_ids.append(aid)
                break

    if stale_ids:
        refresh_signal_summaries(path, asset_ids=stale_ids)
        stored_summaries = storage.list_asset_strategy_summaries(path)

    latest_items = storage.latest_run_items_by_asset(path)
    latest_sig_by_asset = storage.latest_signals_by_asset(path)

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
            per_strat_map = stored_summaries.get(aid, {})
            strategy_summaries = [
                {k: v for k, v in per_strat_map[sid].items() if k != "source_fingerprint"}
                for sid in asset.get("strategies") or []
                if sid in per_strat_map
            ]
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
                    "strategy_summaries": strategy_summaries,
                    "latest_signal": (
                        {
                            "event_date": sig["event_date"],
                            "event_kind": sig["event_kind"],
                            "direction": event_direction(sig["event_kind"]),
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
    refresh_signal_summaries(path, asset_ids=[selected])
    return {"version": new_version, "selected": selected}


def update_asset(path, asset_id, payload, *, fetcher=None):
    fetcher = fetcher or fetch
    config = load_configuration(path)
    expected = payload["version"]
    if expected != config["version"]:
        raise ConfigurationConflict("配置已修改，请重新加载后重试")
    asset = next((a for a in config["assets"] if a["id"] == asset_id), None)
    if not asset:
        raise LookupError("标的不存在")
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
    refresh_signal_summaries(path, asset_ids=[asset_id])
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
        raise LookupError("标的不存在")

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
    refresh_signal_summaries(
        path, asset_ids=None if (action == "save" and payload.get("shared")) else [asset_id]
    )
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
    refresh_signal_summaries(path)
    return {"version": new_version, "strategy_id": saved_id}
