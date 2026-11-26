"""SQLite Schema v6 storage access for Chance Track."""

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .data import TZ, resolve_asset_kind
from .market_sessions import PROFILES
from .schema import SCHEMA_VERSION, _upsert_snapshot_bars, create_v6_schema, migrate_to_v6


def event_key(item, rule_version):
    result = item["result"]
    event_date = (
        result.get("event_date") or result.get("bar_date") or result.get("nav_date")
    )
    return f"{item['asset']['id']}:{event_date}:{rule_version}:{result['event']}"


def strategy_event_key(item, strategy):
    return f"{item['asset']['id']}:{strategy['event_date']}:{strategy['version']}:{strategy['event']}"


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=30)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        yield db
        db.commit()
    finally:
        db.close()


def initialize(path, *, upgrade_configuration=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError(f"数据库结构版本 {version} 高于本程序支持的 {SCHEMA_VERSION}")
        if version == SCHEMA_VERSION:
            return
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        if version > SCHEMA_VERSION:
            raise ValueError(f"数据库结构版本 {version} 高于本程序支持的 {SCHEMA_VERSION}")
        has_tables = db.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
        ).fetchone()
        if not version and not has_tables:
            create_v6_schema(db)
            return
        if not upgrade_configuration:
            raise ValueError("旧数据库需要使用安装器完成备份与 Schema v6 升级")
        migrate_to_v6(db)


# ---------------------------------------------------------------------------
# Configuration (assets, strategies, bindings, dependencies, config_meta)
# ---------------------------------------------------------------------------


def read_current_configuration(db):
    meta = db.execute("SELECT version FROM config_meta WHERE singleton=1").fetchone()
    if meta is None:
        return None
    assets = {}
    for row in db.execute("SELECT * FROM assets ORDER BY position"):
        asset = {
            "id": row["id"],
            "label": row["label"],
            "ticker": row["ticker"],
            "code": row["code"],
            "provider": row["provider"],
            "kind": row["kind"],
            "session_profile": row["session_profile"],
            "market": row["market"],
            "currency": row["currency"],
            "group": row["group_name"],
            "group_name": row["group_name"],
            "enabled": bool(row["enabled"]),
            "archived": bool(row["archived"]),
            "watchlisted": bool(row["watchlisted"]),
            "priority": bool(row["priority"]),
            "aliases": json.loads(row["aliases_json"] or "[]"),
            "fallbacks": json.loads(row["fallbacks_json"] or "[]"),
            "strategies": [],
            "activated": {},
        }
        if row["secid"]:
            asset["secid"] = row["secid"]
        if row["expected_name"]:
            asset["expected_name"] = row["expected_name"]
        if row["validated_at"]:
            asset["validated_at"] = row["validated_at"]
        if row["reason"]:
            asset["reason"] = row["reason"]
        assets[row["id"]] = asset

    for row in db.execute("SELECT * FROM asset_strategies ORDER BY position"):
        if row["asset_id"] in assets:
            assets[row["asset_id"]]["strategies"].append(row["strategy_id"])
            assets[row["asset_id"]]["activated"][row["strategy_id"]] = row["activated_at"]

    rules = {}
    for row in db.execute("SELECT * FROM strategies ORDER BY position"):
        rule = {
            "id": row["id"],
            "kind": row["kind"],
            "name": row["name"],
            "group": row["group_name"],
            "group_name": row["group_name"],
            "revision": row["revision"],
        }
        params = json.loads(row["params_json"] or "{}")
        if rule["kind"] != "combination" or params:
            rule["params"] = params
        if row["filter_mode"]:
            rule["filter"] = row["filter_mode"]
        rules[row["id"]] = rule

    for row in db.execute("SELECT * FROM strategy_dependencies"):
        if row["owner_id"] in rules:
            rules[row["owner_id"]][row["role"]] = row["target_id"]

    return {
        "version": int(meta["version"]),
        "assets": list(assets.values()),
        "strategies": list(rules.values()),
    }


def write_current_configuration(db, config, version, now_iso=None):
    now_iso = now_iso or datetime.now(TZ).isoformat()
    existing_assets = {
        row["id"]: row["created_at"] for row in db.execute("SELECT id, created_at FROM assets")
    }
    existing_strats = {
        row["id"]: row["created_at"] for row in db.execute("SELECT id, created_at FROM strategies")
    }

    # Clear join tables first so foreign keys stay satisfied while syncing
    db.execute("DELETE FROM strategy_dependencies")
    db.execute("DELETE FROM asset_strategies")

    # Avoid unique position collisions during updates by shifting positions temporarily
    db.execute("UPDATE assets SET position = -(position + 1)")
    db.execute("UPDATE strategies SET position = -(position + 1)")

    new_asset_ids = set()
    for pos, asset in enumerate(config["assets"]):
        aid = asset["id"]
        new_asset_ids.add(aid)
        kind = resolve_asset_kind(asset)
        if kind not in ("index", "etf", "spot"):
            kind = "index"
        group_name = asset.get("group") or asset.get("group_name") or "未分组"
        created_at = existing_assets.get(aid, now_iso)
        db.execute(
            """INSERT INTO assets (
                 id, label, ticker, code, provider, kind, session_profile,
                 market, currency, group_name, secid, expected_name,
                 enabled, archived, watchlisted, priority, position,
                 aliases_json, fallbacks_json, validated_at, reason, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 label=excluded.label, ticker=excluded.ticker, code=excluded.code,
                 provider=excluded.provider, kind=excluded.kind,
                 session_profile=excluded.session_profile, market=excluded.market,
                 currency=excluded.currency, group_name=excluded.group_name,
                 secid=excluded.secid, expected_name=excluded.expected_name,
                 enabled=excluded.enabled, archived=excluded.archived,
                 watchlisted=excluded.watchlisted, priority=excluded.priority,
                 position=excluded.position, aliases_json=excluded.aliases_json,
                 fallbacks_json=excluded.fallbacks_json, validated_at=excluded.validated_at,
                 reason=excluded.reason, updated_at=excluded.updated_at""",
            (
                aid,
                asset["label"],
                asset.get("ticker") or asset.get("code") or "",
                asset["code"],
                asset["provider"],
                kind,
                asset["session_profile"],
                asset.get("market") or "",
                asset.get("currency") or "",
                group_name,
                asset.get("secid"),
                asset.get("expected_name"),
                1 if asset.get("enabled", True) else 0,
                1 if asset.get("archived", False) else 0,
                1 if asset.get("watchlisted", True) else 0,
                1 if asset.get("priority", False) else 0,
                pos,
                json.dumps(list(asset.get("aliases") or []), ensure_ascii=False),
                json.dumps(list(asset.get("fallbacks") or []), ensure_ascii=False),
                asset.get("validated_at"),
                asset.get("reason"),
                created_at,
                now_iso,
            ),
        )

    new_strat_ids = set()
    references = []
    for pos, rule in enumerate(config["strategies"]):
        sid = rule["id"]
        new_strat_ids.add(sid)
        group_name = rule.get("group") or rule.get("group_name") or "未分组"
        created_at = existing_strats.get(sid, now_iso)
        if rule["kind"] == "combination":
            for role in ("trigger", "band"):
                target = rule.get(role)
                if target:
                    references.append((sid, role, target))
        db.execute(
            """INSERT INTO strategies (
                 id, kind, name, group_name, revision, params_json,
                 filter_mode, position, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 kind=excluded.kind, name=excluded.name, group_name=excluded.group_name,
                 revision=excluded.revision, params_json=excluded.params_json,
                 filter_mode=excluded.filter_mode, position=excluded.position,
                 updated_at=excluded.updated_at""",
            (
                sid,
                rule["kind"],
                rule["name"],
                group_name,
                int(rule.get("revision") or 1),
                json.dumps(rule.get("params") or {}, ensure_ascii=False),
                rule.get("filter") or rule.get("filter_mode"),
                pos,
                created_at,
                now_iso,
            ),
        )

    # Remove deleted strategies (already verified unreferenced by configuration.py)
    for old_sid in set(existing_strats) - new_strat_ids:
        db.execute("DELETE FROM strategies WHERE id=?", (old_sid,))

    if references:
        db.executemany(
            "INSERT INTO strategy_dependencies(owner_id, role, target_id) VALUES (?, ?, ?)",
            references,
        )

    bindings = []
    for asset in config["assets"]:
        activated = asset.get("activated") or {}
        for pos, sid in enumerate(asset.get("strategies") or []):
            bindings.append((asset["id"], sid, pos, activated.get(sid) or now_iso))
    if bindings:
        db.executemany(
            "INSERT INTO asset_strategies(asset_id, strategy_id, position, activated_at) VALUES (?, ?, ?, ?)",
            bindings,
        )

    db.execute(
        """INSERT INTO config_meta(singleton, version, updated_at) VALUES (1, ?, ?)
           ON CONFLICT(singleton) DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at""",
        (int(version), now_iso),
    )


# ---------------------------------------------------------------------------
# Normalized Market Bars
# ---------------------------------------------------------------------------


def save_bars(path, asset_id, snapshot, *, run_id=None):
    with connect(path) as db:
        _upsert_snapshot_bars(db, asset_id, snapshot, run_id=run_id)


def get_bars(path, asset_id, limit=None):
    sql = "SELECT * FROM market_bars WHERE asset_id=? ORDER BY bar_date ASC"
    params = [asset_id]
    if limit is not None:
        sql = (
            "SELECT * FROM ("
            "  SELECT * FROM market_bars WHERE asset_id=? ORDER BY bar_date DESC LIMIT ?"
            ") ORDER BY bar_date ASC"
        )
        params.append(int(limit))
    with connect(path) as db:
        return [dict(row) for row in db.execute(sql, params)]


def last_bar(path, asset_id):
    with connect(path) as db:
        row = db.execute(
            "SELECT * FROM market_bars WHERE asset_id=? ORDER BY bar_date DESC LIMIT 1",
            (asset_id,),
        ).fetchone()
        return dict(row) if row else None


def last_market_quote(path, asset_id):
    bar = last_bar(path, asset_id)
    if not bar:
        return None
    return {
        "report_id": bar.get("source_run_id"),
        "run_id": bar.get("source_run_id"),
        "fetched_at": bar["fetched_at"],
        "bar_date": bar["bar_date"],
        "close": bar["close"],
        "is_provisional": bar["is_provisional"],
        "provider": bar["provider"],
    }


def snapshot_from_bars(path, asset):
    """Reconstruct the exact market_ohlc snapshot dict consumed by analysis & strategies."""
    bars = get_bars(path, asset["id"])
    if not bars:
        return None
    latest = bars[-1]
    rows = []
    volume_unit = None
    volume_multiplier = None
    for b in bars:
        row = {
            "date": b["bar_date"],
            "open": b["open"],
            "high": b["high"],
            "low": b["low"],
            "close": b["close"],
        }
        if b["volume"] is not None:
            row["volume"] = b["volume"]
        if b["volume_unit"]:
            volume_unit = b["volume_unit"]
        if b["volume_multiplier"] is not None:
            volume_multiplier = b["volume_multiplier"]
        rows.append(row)
    provisional_date = latest["bar_date"] if latest["is_provisional"] else None
    confirmed_bars = [b["bar_date"] for b in bars if not b["is_provisional"]]
    profile = PROFILES.get(asset.get("session_profile") or "", {})
    snapshot = {
        "basis": "market_ohlc",
        "code": asset.get("code") or "",
        "provider": latest["provider"],
        "fetched_at": latest["fetched_at"],
        "rows": rows,
        "provisional_date": provisional_date,
        "confirmed_date": confirmed_bars[-1] if confirmed_bars else None,
        "exchange_timezone": profile.get("timezone"),
        "session": {
            "profile": asset.get("session_profile"),
            "label": profile.get("label", ""),
            "phase": "intraday" if provisional_date else "closed",
        },
    }
    if volume_unit:
        snapshot["volume_unit"] = volume_unit
    if volume_multiplier is not None:
        snapshot["volume_multiplier"] = volume_multiplier
    return snapshot


# ---------------------------------------------------------------------------
# Check Runs, Signal Events & Notifications
# ---------------------------------------------------------------------------


def save_check_run(path, run_record, items):
    """Persist a lightweight check run, upsert fresh market_bars, and record structured signal_events."""
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        if run_record["kind"] == "scheduled":
            existing = db.execute(
                "SELECT id FROM check_runs WHERE day=? AND kind='scheduled' AND rule_version=? AND attempt=?",
                (run_record["day"], run_record["rule_version"], int(run_record.get("attempt") or 1)),
            ).fetchone()
            if existing:
                return int(existing["id"]), False

        cursor = db.execute(
            """INSERT INTO check_runs (
                 day, kind, attempt, parent_run_id, rule_version, config_version,
                 code_revision, late, probe_order_json, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_record["day"],
                run_record["kind"],
                int(run_record.get("attempt") or 1),
                run_record.get("parent_run_id"),
                run_record["rule_version"],
                int(run_record.get("config_version") or 1),
                run_record.get("code_revision") or "",
                1 if run_record.get("late") else 0,
                json.dumps(run_record.get("probe_order") or {}, ensure_ascii=False),
                run_record["created_at"],
            ),
        )
        run_id = int(cursor.lastrowid)

        for item in items:
            asset = item["asset"]
            aid = asset["id"]
            if not db.execute("SELECT 1 FROM assets WHERE id=?", (aid,)).fetchone():
                next_pos = int(
                    db.execute("SELECT coalesce(max(position), -1) + 1 FROM assets").fetchone()[0]
                )
                kind = resolve_asset_kind(asset)
                if kind not in ("index", "etf", "spot"):
                    kind = "index"
                now_iso = run_record["created_at"]
                db.execute(
                    """INSERT INTO assets (
                         id, label, ticker, code, provider, kind, session_profile,
                         market, currency, group_name, secid, expected_name,
                         enabled, archived, watchlisted, priority, position,
                         aliases_json, fallbacks_json, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        aid,
                        asset.get("label") or aid,
                        asset.get("ticker") or asset.get("code") or "",
                        asset.get("code") or asset.get("ticker") or aid,
                        asset.get("provider") or "yahoo",
                        kind,
                        asset.get("session_profile") or "us_equity",
                        asset.get("market") or "",
                        asset.get("currency") or "",
                        asset.get("group") or asset.get("group_name") or "未分组",
                        asset.get("secid"),
                        asset.get("expected_name"),
                        1 if asset.get("enabled", True) else 0,
                        1 if asset.get("archived", False) else 0,
                        1 if asset.get("watchlisted", True) else 0,
                        1 if asset.get("priority", False) else 0,
                        next_pos,
                        json.dumps(list(asset.get("aliases") or []), ensure_ascii=False),
                        json.dumps(list(asset.get("fallbacks") or []), ensure_ascii=False),
                        now_iso,
                        now_iso,
                    ),
                )
            snapshot = item.get("snapshot")
            if snapshot and item.get("status") in ("ok", "warning") and not item.get("carried_forward"):
                _upsert_snapshot_bars(
                    db,
                    aid,
                    snapshot,
                    run_id=run_id,
                    default_provider=item.get("provider") or asset.get("provider") or "unknown",
                )

            result = item.get("result") or {}
            last_q = item.get("last_quote") or {}
            bar_date = result.get("bar_date") or last_q.get("bar_date")
            close_val = result.get("close") if result.get("close") is not None else last_q.get("close")
            fetch_meta = {
                k: item[k]
                for k in ("fetch_status", "attempts", "provider_label", "last_quote", "warnings")
                if k in item and item[k] is not None
            }
            if result.get("warnings"):
                fetch_meta["warnings"] = result["warnings"]
            db.execute(
                """INSERT INTO check_run_items (
                     run_id, asset_id, status, bar_date, close, is_provisional,
                     provider, fetched_at, carried_forward, origin_run_id, error, fetch_meta_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    aid,
                    item["status"],
                    bar_date,
                    float(close_val) if close_val is not None else None,
                    1 if result.get("is_provisional") else 0,
                    item.get("provider") or (snapshot or {}).get("provider") or asset.get("provider"),
                    (snapshot or {}).get("fetched_at") or last_q.get("fetched_at"),
                    1 if item.get("carried_forward") else 0,
                    item.get("origin_run_id") or item.get("origin_report_id"),
                    item.get("error"),
                    json.dumps(fetch_meta, ensure_ascii=False),
                ),
            )

            # Record structured confirmed strategy events (deduplicated by dedup_key)
            for strat in result.get("strategies") or []:
                if strat.get("event") and strat.get("event_date"):
                    key = strategy_event_key(item, strat)
                    inserted = db.execute(
                        """INSERT OR IGNORE INTO signal_events (
                             dedup_key, asset_id, strategy_id, strategy_name, strategy_group,
                             strategy_version, event_date, event_kind, detail, is_provisional,
                             historical_reference, first_run_id, created_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                        (
                            key,
                            aid,
                            strat.get("id"),
                            strat.get("name") or "",
                            strat.get("group") or "",
                            strat.get("version") or "",
                            strat["event_date"],
                            strat["event"],
                            strat.get("detail") or "",
                            1 if strat.get("historical_reference") else 0,
                            run_id,
                            run_record["created_at"],
                        ),
                    ).rowcount
                    strat["new_event"] = bool(inserted)

            # Fallback single-MACD event key when legacy/default mode is used
            if result.get("event") and "strategies" not in result:
                key = event_key(item, run_record["rule_version"])
                edate = result.get("event_date") or result.get("bar_date") or run_record["day"]
                inserted = db.execute(
                    """INSERT OR IGNORE INTO signal_events (
                         dedup_key, asset_id, strategy_id, strategy_name, strategy_group,
                         strategy_version, event_date, event_kind, detail, is_provisional,
                         historical_reference, first_run_id, created_at
                       ) VALUES (?, ?, 'macd', 'MACD 默认', '趋势', ?, ?, ?, '', 0, 0, ?, ?)""",
                    (
                        key,
                        aid,
                        run_record["rule_version"],
                        edate,
                        result["event"],
                        run_id,
                        run_record["created_at"],
                    ),
                ).rowcount
                result["new_event"] = bool(inserted)

        return run_id, True


def list_check_runs(path, limit=50):
    with connect(path) as db:
        rows = db.execute(
            """SELECT r.*, n.sent_at AS notification_sent_at, n.error AS notification_error,
                      n.message AS notification_message,
                      (SELECT count(*) FROM check_run_items i WHERE i.run_id=r.id) AS total_assets,
                      (SELECT count(*) FROM check_run_items i WHERE i.run_id=r.id AND i.status='ok') AS ok_assets,
                      (SELECT count(*) FROM check_run_items i WHERE i.run_id=r.id AND i.status='error') AS error_assets,
                      (SELECT count(*) FROM signal_events e WHERE e.first_run_id=r.id) AS new_events
               FROM check_runs r
               LEFT JOIN notifications n ON n.run_id = r.id
               ORDER BY r.id DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["probe_order"] = json.loads(d.pop("probe_order_json", "{}") or "{}")
            d["late"] = bool(d["late"])
            result.append(d)
        return result


def get_check_run(path, run_id=None):
    with connect(path) as db:
        row = (
            db.execute("SELECT * FROM check_runs WHERE id=?", (int(run_id),)).fetchone()
            if run_id is not None
            else db.execute("SELECT * FROM check_runs ORDER BY id DESC LIMIT 1").fetchone()
        )
        if not row:
            return None
        run_dict = dict(row)
        run_dict["probe_order"] = json.loads(run_dict.pop("probe_order_json", "{}") or "{}")
        run_dict["late"] = bool(run_dict["late"])
        items = []
        for irow in db.execute(
            "SELECT * FROM check_run_items WHERE run_id=? ORDER BY asset_id", (run_dict["id"],)
        ):
            item = dict(irow)
            meta = json.loads(item.pop("fetch_meta_json", "{}") or "{}")
            item.update(meta)
            item["is_provisional"] = bool(item["is_provisional"])
            item["carried_forward"] = bool(item["carried_forward"])
            items.append(item)
        run_dict["items"] = items
        notif = db.execute("SELECT * FROM notifications WHERE run_id=?", (run_dict["id"],)).fetchone()
        run_dict["notification"] = dict(notif) if notif else None
        return run_dict


def latest_scheduled_run(path, day, rule_version):
    with connect(path) as db:
        row = db.execute(
            "SELECT id FROM check_runs WHERE day=? AND kind='scheduled' AND rule_version=? ORDER BY id DESC LIMIT 1",
            (day, rule_version),
        ).fetchone()
    return get_check_run(path, row["id"]) if row else None


def latest_run_items_by_asset(path):
    """Return the most recent check_run_item per asset for status/error badges."""
    with connect(path) as db:
        rows = db.execute(
            """SELECT i.*, r.created_at AS checked_at, r.day AS run_day
               FROM check_run_items i
               JOIN check_runs r ON r.id = i.run_id
               WHERE (i.asset_id, i.run_id) IN (
                 SELECT asset_id, max(run_id) FROM check_run_items GROUP BY asset_id
               )"""
        ).fetchall()
        out = {}
        for r in rows:
            item = dict(r)
            meta = json.loads(item.pop("fetch_meta_json", "{}") or "{}")
            item.update(meta)
            item["is_provisional"] = bool(item["is_provisional"])
            item["carried_forward"] = bool(item["carried_forward"])
            out[item["asset_id"]] = item
        return out


def list_signals(path, *, asset_id=None, strategy_id=None, limit=200):
    clauses = []
    params = []
    if asset_id:
        clauses.append("e.asset_id = ?")
        params.append(asset_id)
    if strategy_id:
        clauses.append("e.strategy_id = ?")
        params.append(strategy_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(int(limit))
    with connect(path) as db:
        rows = db.execute(
            f"""SELECT e.*, a.label AS asset_label, a.code AS asset_code, a.ticker AS asset_ticker
                FROM signal_events e
                JOIN assets a ON a.id = e.asset_id
                {where}
                ORDER BY e.event_date DESC, e.id DESC
                LIMIT ?""",
            params,
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["is_provisional"] = bool(d["is_provisional"])
            d["historical_reference"] = bool(d["historical_reference"])
            out.append(d)
        return out


def get_notification(path, run_id):
    with connect(path) as db:
        row = db.execute("SELECT * FROM notifications WHERE run_id=?", (int(run_id),)).fetchone()
        return dict(row) if row else None


notification = get_notification


def notified_event_keys(path):
    with connect(path) as db:
        return {
            row["dedup_key"]
            for row in db.execute(
                "SELECT dedup_key FROM signal_events WHERE notified_at IS NOT NULL"
            )
        }


def record_notification(path, run_id, message, sent_at=None, error=None, event_keys=()):
    with connect(path) as db:
        db.execute(
            "INSERT OR REPLACE INTO notifications(run_id, sent_at, error, message) VALUES (?, ?, ?, ?)",
            (int(run_id), sent_at, error, message),
        )
        if sent_at and event_keys:
            db.executemany(
                "UPDATE signal_events SET notified_at=?, notified_run_id=? WHERE dedup_key=? AND notified_at IS NULL",
                [(sent_at, int(run_id), key) for key in event_keys],
            )


# ---------------------------------------------------------------------------
# Decisions (Asset-Centric Review Timeline)
# ---------------------------------------------------------------------------


def list_decisions(path, *, asset_id=None, limit=200):
    clauses = []
    params = []
    if asset_id:
        clauses.append("d.asset_id = ?")
        params.append(asset_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(int(limit))
    with connect(path) as db:
        rows = db.execute(
            f"""SELECT d.id, d.asset_id, d.run_id, d.bar_date, d.price_at_decision,
                       d.action, d.execution, d.note,
                       (d.evidence_json IS NOT NULL) AS has_exploration,
                       d.created_at,
                       a.label AS asset_label, a.code AS asset_code
                FROM decisions d
                JOIN assets a ON a.id = d.asset_id
                {where}
                ORDER BY d.bar_date DESC, d.id DESC
                LIMIT ?""",
            params,
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["has_exploration"] = bool(d["has_exploration"])
            out.append(d)
        return out


def add_decision(
    path,
    asset_id,
    bar_date,
    action,
    execution,
    note,
    now,
    *,
    price_at_decision=None,
    run_id=None,
    evidence=None,
):
    if action not in ("买入", "卖出", "持有", "观望") or execution not in ("未执行", "已提交"):
        raise ValueError("无效决策或执行状态")
    if len(note) > 4000:
        raise ValueError("备注不能超过 4000 字")
    with connect(path) as db:
        asset_row = db.execute("SELECT id FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not asset_row:
            raise ValueError("标的不存在")
        if price_at_decision is None:
            bar = db.execute(
                "SELECT close FROM market_bars WHERE asset_id=? AND bar_date<=? ORDER BY bar_date DESC LIMIT 1",
                (asset_id, bar_date),
            ).fetchone()
            if bar:
                price_at_decision = float(bar["close"])
        cursor = db.execute(
            """INSERT INTO decisions (
                 asset_id, run_id, bar_date, price_at_decision,
                 action, execution, note, evidence_json, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                asset_id,
                run_id,
                bar_date,
                float(price_at_decision) if price_at_decision is not None else None,
                action,
                execution,
                note,
                json.dumps(evidence, ensure_ascii=False) if evidence is not None else None,
                now,
            ),
        )
        return int(cursor.lastrowid)


def get_decision_evidence(path, decision_id):
    with connect(path) as db:
        row = db.execute(
            "SELECT evidence_json FROM decisions WHERE id=?", (int(decision_id),)
        ).fetchone()
        return json.loads(row["evidence_json"]) if row and row["evidence_json"] else None


# ---------------------------------------------------------------------------
# Investment Notes (Global Pin + Optional Asset Links)
# ---------------------------------------------------------------------------


def _attach_note_assets(db, note_dicts):
    if not note_dicts:
        return note_dicts
    ids = [n["id"] for n in note_dicts]
    placeholders = ",".join("?" for _ in ids)
    links = {}
    for row in db.execute(
        f"""SELECT na.note_id, na.asset_id, a.label
            FROM note_assets na
            JOIN assets a ON a.id = na.asset_id
            WHERE na.note_id IN ({placeholders})
            ORDER BY a.position""",
        ids,
    ):
        links.setdefault(row["note_id"], []).append(
            {"id": row["asset_id"], "label": row["label"]}
        )
    for n in note_dicts:
        linked = links.get(n["id"], [])
        n["asset_ids"] = [x["id"] for x in linked]
        n["assets"] = linked
        n["pinned"] = bool(n["pinned"])
    return note_dicts


def list_notes(path, *, asset_id=None):
    with connect(path) as db:
        if asset_id:
            rows = db.execute(
                """SELECT DISTINCT n.*
                   FROM notes n
                   LEFT JOIN note_assets na ON na.note_id = n.id
                   WHERE na.asset_id = ? OR n.pinned = 1
                   ORDER BY n.pinned DESC, n.updated_at DESC, n.id DESC""",
                (asset_id,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM notes ORDER BY pinned DESC, updated_at DESC, id DESC"
            ).fetchall()
        return _attach_note_assets(db, [dict(r) for r in rows])


def get_note(path, note_id=None):
    with connect(path) as db:
        row = (
            db.execute("SELECT * FROM notes WHERE id=?", (int(note_id),)).fetchone()
            if note_id is not None
            else db.execute("SELECT * FROM notes WHERE pinned=1").fetchone()
        )
        if not row:
            return None
        return _attach_note_assets(db, [dict(row)])[0]


def save_note(path, note_id, title, markdown, now, *, asset_ids=None):
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        if note_id is None:
            cursor = db.execute(
                "INSERT INTO notes(title, markdown, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (title, markdown, now, now),
            )
            saved_id = int(cursor.lastrowid)
        else:
            cursor = db.execute(
                "UPDATE notes SET title=?, markdown=?, updated_at=? WHERE id=?",
                (title, markdown, now, int(note_id)),
            )
            if not cursor.rowcount:
                return None
            saved_id = int(note_id)
        if asset_ids is not None:
            db.execute("DELETE FROM note_assets WHERE note_id=?", (saved_id,))
            valid_ids = [
                r["id"]
                for r in db.execute(
                    f"SELECT id FROM assets WHERE id IN ({','.join('?' for _ in asset_ids)})",
                    list(asset_ids),
                )
            ] if asset_ids else []
            if valid_ids:
                db.executemany(
                    "INSERT OR IGNORE INTO note_assets(note_id, asset_id) VALUES (?, ?)",
                    [(saved_id, aid) for aid in valid_ids],
                )
        return saved_id


def pin_note(path, note_id):
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM notes WHERE id=?", (int(note_id),)).fetchone():
            return False
        db.execute("UPDATE notes SET pinned=0 WHERE pinned=1")
        db.execute("UPDATE notes SET pinned=1 WHERE id=?", (int(note_id),))
        return True


def delete_note(path, note_id, updated_at=None):
    with connect(path) as db:
        if updated_at is not None:
            return (
                db.execute(
                    "DELETE FROM notes WHERE id=? AND updated_at=?",
                    (int(note_id), updated_at),
                ).rowcount
                == 1
            )
        return db.execute("DELETE FROM notes WHERE id=?", (int(note_id),)).rowcount == 1


# ---------------------------------------------------------------------------
# Chart Templates
# ---------------------------------------------------------------------------


def list_templates(path):
    with connect(path) as db:
        return [
            {
                "id": r["id"],
                "template_id": r["template_id"],
                "name": r["name"],
                "created_at": r["created_at"],
                "strategies": json.loads(r["strategies_json"]),
            }
            for r in db.execute(
                """SELECT * FROM chart_templates
                   WHERE id IN (SELECT max(id) FROM chart_templates GROUP BY template_id)
                   ORDER BY id DESC"""
            )
        ]


def save_template(path, name, strategies, template_id=None, expected=None):
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise ValueError("模板名称须为1至100字")
    if len(strategies) > 24:
        raise ValueError("模板最多24个指标")
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        if template_id:
            latest = db.execute(
                "SELECT max(id) FROM chart_templates WHERE template_id=?",
                (template_id,),
            ).fetchone()[0]
            if latest is None or latest != expected:
                raise ValueError("模板已修改，请重新加载；也可另存为新模板")
        else:
            template_id = uuid4().hex
        db.execute(
            "INSERT INTO chart_templates(template_id, name, strategies_json, created_at) VALUES (?, ?, ?, ?)",
            (
                template_id,
                name.strip(),
                json.dumps(strategies, ensure_ascii=False),
                datetime.now(TZ).isoformat(),
            ),
        )
    return list_templates(path)
