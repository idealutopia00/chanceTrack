"""SQLite Schema v6 definition and lossless v5 -> v6 migration."""

from datetime import datetime
import json
import math

from .catalog import catalog_by_id
from .data import TZ, resolve_asset_kind

SCHEMA_VERSION = 6

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS config_meta (
      singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
      version INTEGER NOT NULL DEFAULT 1,
      updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS assets (
      id TEXT PRIMARY KEY NOT NULL,
      label TEXT NOT NULL,
      ticker TEXT NOT NULL DEFAULT '',
      code TEXT NOT NULL,
      provider TEXT NOT NULL,
      kind TEXT NOT NULL CHECK (kind IN ('index', 'etf', 'spot')),
      session_profile TEXT NOT NULL,
      market TEXT NOT NULL DEFAULT '',
      currency TEXT NOT NULL DEFAULT '',
      group_name TEXT NOT NULL DEFAULT '未分组',
      secid TEXT,
      expected_name TEXT,
      enabled INTEGER NOT NULL DEFAULT 1,
      archived INTEGER NOT NULL DEFAULT 0,
      watchlisted INTEGER NOT NULL DEFAULT 1,
      priority INTEGER NOT NULL DEFAULT 0,
      position INTEGER NOT NULL UNIQUE,
      aliases_json TEXT NOT NULL DEFAULT '[]',
      fallbacks_json TEXT NOT NULL DEFAULT '[]',
      validated_at TEXT,
      reason TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS strategies (
      id TEXT PRIMARY KEY NOT NULL,
      kind TEXT NOT NULL,
      name TEXT NOT NULL,
      group_name TEXT NOT NULL DEFAULT '未分组',
      revision INTEGER NOT NULL DEFAULT 1,
      params_json TEXT NOT NULL DEFAULT '{}',
      filter_mode TEXT,
      position INTEGER NOT NULL UNIQUE,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS asset_strategies (
      asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
      strategy_id TEXT NOT NULL REFERENCES strategies(id) ON DELETE RESTRICT,
      position INTEGER NOT NULL,
      activated_at TEXT,
      PRIMARY KEY (asset_id, strategy_id),
      UNIQUE (asset_id, position)
    )""",
    """CREATE TABLE IF NOT EXISTS strategy_dependencies (
      owner_id TEXT NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
      role TEXT NOT NULL CHECK (role IN ('trigger', 'band')),
      target_id TEXT NOT NULL REFERENCES strategies(id) ON DELETE RESTRICT,
      PRIMARY KEY (owner_id, role)
    )""",
    """CREATE TABLE IF NOT EXISTS check_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      day TEXT NOT NULL,
      kind TEXT NOT NULL CHECK (kind IN ('scheduled', 'manual')),
      attempt INTEGER NOT NULL DEFAULT 1,
      parent_run_id INTEGER REFERENCES check_runs(id) ON DELETE SET NULL,
      rule_version TEXT NOT NULL,
      config_version INTEGER NOT NULL DEFAULT 1,
      code_revision TEXT NOT NULL DEFAULT '',
      late INTEGER NOT NULL DEFAULT 0,
      probe_order_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_check_runs_day_kind ON check_runs(day, kind, id DESC)",
    """CREATE TABLE IF NOT EXISTS check_run_items (
      run_id INTEGER NOT NULL REFERENCES check_runs(id) ON DELETE CASCADE,
      asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
      status TEXT NOT NULL CHECK (status IN ('ok', 'warning', 'error', 'disabled')),
      bar_date TEXT,
      close REAL,
      is_provisional INTEGER NOT NULL DEFAULT 0,
      provider TEXT,
      fetched_at TEXT,
      carried_forward INTEGER NOT NULL DEFAULT 0,
      origin_run_id INTEGER REFERENCES check_runs(id) ON DELETE SET NULL,
      error TEXT,
      fetch_meta_json TEXT NOT NULL DEFAULT '{}',
      PRIMARY KEY (run_id, asset_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_check_run_items_asset ON check_run_items(asset_id, run_id DESC)",
    """CREATE TABLE IF NOT EXISTS market_bars (
      asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
      bar_date TEXT NOT NULL,
      open REAL NOT NULL,
      high REAL NOT NULL,
      low REAL NOT NULL,
      close REAL NOT NULL,
      volume REAL,
      volume_unit TEXT,
      volume_multiplier REAL,
      quote_currency TEXT,
      is_provisional INTEGER NOT NULL DEFAULT 0,
      provider TEXT NOT NULL,
      fetched_at TEXT NOT NULL,
      source_run_id INTEGER REFERENCES check_runs(id) ON DELETE SET NULL,
      PRIMARY KEY (asset_id, bar_date)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_market_bars_asset_date ON market_bars(asset_id, bar_date DESC)",
    """CREATE TABLE IF NOT EXISTS signal_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      dedup_key TEXT NOT NULL UNIQUE,
      asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
      strategy_id TEXT,
      strategy_name TEXT NOT NULL DEFAULT '',
      strategy_group TEXT NOT NULL DEFAULT '',
      strategy_version TEXT NOT NULL DEFAULT '',
      event_date TEXT NOT NULL,
      event_kind TEXT NOT NULL,
      detail TEXT NOT NULL DEFAULT '',
      is_provisional INTEGER NOT NULL DEFAULT 0,
      historical_reference INTEGER NOT NULL DEFAULT 0,
      first_run_id INTEGER REFERENCES check_runs(id) ON DELETE SET NULL,
      notified_at TEXT,
      notified_run_id INTEGER REFERENCES check_runs(id) ON DELETE SET NULL,
      created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_signal_events_asset_date ON signal_events(asset_id, event_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_signal_events_date ON signal_events(event_date DESC, id DESC)",
    """CREATE TABLE IF NOT EXISTS notifications (
      run_id INTEGER PRIMARY KEY REFERENCES check_runs(id) ON DELETE CASCADE,
      sent_at TEXT,
      error TEXT,
      message TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS decisions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
      run_id INTEGER REFERENCES check_runs(id) ON DELETE SET NULL,
      bar_date TEXT NOT NULL,
      price_at_decision REAL,
      action TEXT NOT NULL CHECK (action IN ('买入', '卖出', '持有', '观望')),
      execution TEXT NOT NULL CHECK (execution IN ('未执行', '已提交')),
      note TEXT NOT NULL DEFAULT '',
      evidence_json TEXT,
      created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_decisions_asset_date ON decisions(asset_id, bar_date DESC, id DESC)",
    """CREATE TABLE IF NOT EXISTS notes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      markdown TEXT NOT NULL,
      pinned INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_notes_pin ON notes(pinned) WHERE pinned = 1",
    """CREATE TABLE IF NOT EXISTS note_assets (
      note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
      asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
      PRIMARY KEY (note_id, asset_id)
    )""",
    """CREATE TABLE IF NOT EXISTS chart_templates (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      template_id TEXT NOT NULL,
      name TEXT NOT NULL,
      strategies_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_chart_templates_tid ON chart_templates(template_id, id DESC)",
    """CREATE TABLE IF NOT EXISTS asset_strategy_summaries (
      asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
      strategy_id TEXT NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
      strategy_name TEXT NOT NULL DEFAULT '',
      strategy_kind TEXT NOT NULL DEFAULT '',
      strategy_version TEXT NOT NULL DEFAULT '',
      bar_date TEXT,
      bar_is_provisional INTEGER NOT NULL DEFAULT 0,
      event_date TEXT,
      event_kind TEXT,
      direction TEXT,
      detail TEXT NOT NULL DEFAULT '',
      event_is_provisional INTEGER NOT NULL DEFAULT 0,
      state TEXT NOT NULL DEFAULT 'idle',
      checked_event_kind TEXT,
      checked_event_date TEXT,
      checked_is_provisional INTEGER NOT NULL DEFAULT 0,
      source_fingerprint TEXT NOT NULL DEFAULT '',
      refreshed_at TEXT NOT NULL,
      PRIMARY KEY (asset_id, strategy_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_asset_strategy_summaries_asset ON asset_strategy_summaries(asset_id)",
)

V6_EXTENSION_STATEMENTS = SCHEMA_STATEMENTS[-2:]


def ensure_v6_extensions(db):
    """Idempotently ensure additive v6 tables/indexes exist on already-upgraded v6 databases."""
    for stmt in V6_EXTENSION_STATEMENTS:
        db.execute(stmt)


def create_v6_schema(db):
    for stmt in SCHEMA_STATEMENTS:
        db.execute(stmt)
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _table_exists(db, name):
    return bool(
        db.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (name,)
        ).fetchone()
    )


def _upsert_snapshot_bars(db, asset_id, snapshot, *, run_id=None, default_provider="unknown"):
    if not isinstance(snapshot, dict) or snapshot.get("basis") != "market_ohlc":
        return
    rows = snapshot.get("rows") or []
    if not rows:
        return
    provider = str(snapshot.get("provider") or default_provider or "unknown")
    fetched_at = str(snapshot.get("fetched_at") or datetime.now(TZ).isoformat())
    provisional_date = snapshot.get("provisional_date")
    volume_unit = snapshot.get("volume_unit")
    volume_multiplier = snapshot.get("volume_multiplier")
    quote_currency = snapshot.get("quote_currency")
    values = []
    for row in rows:
        bar_date = row.get("date")
        if not bar_date:
            continue
        try:
            o = float(row["open"])
            h = float(row["high"])
            lo = float(row["low"])
            c = float(row["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (o, h, lo, c)):
            continue
        vol = row.get("volume")
        vol_val = None
        if vol is not None:
            try:
                parsed_vol = float(vol)
                if math.isfinite(parsed_vol):
                    vol_val = parsed_vol
            except (TypeError, ValueError):
                vol_val = None
        vm_val = None
        if volume_multiplier is not None:
            try:
                parsed_vm = float(volume_multiplier)
                if math.isfinite(parsed_vm):
                    vm_val = parsed_vm
            except (TypeError, ValueError):
                vm_val = None
        is_prov = 1 if (provisional_date and bar_date == provisional_date) else 0
        values.append(
            (
                asset_id,
                str(bar_date),
                o,
                h,
                lo,
                c,
                vol_val,
                volume_unit if vol_val is not None or volume_unit else None,
                vm_val,
                str(quote_currency) if quote_currency else None,
                is_prov,
                provider,
                fetched_at,
                run_id,
            )
        )
    if not values:
        return
    db.executemany(
        """INSERT INTO market_bars (
             asset_id, bar_date, open, high, low, close,
             volume, volume_unit, volume_multiplier, quote_currency,
             is_provisional, provider, fetched_at, source_run_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(asset_id, bar_date) DO UPDATE SET
             open = CASE
               WHEN market_bars.is_provisional = 0 AND excluded.is_provisional = 1 THEN market_bars.open
               ELSE excluded.open
             END,
             high = CASE
               WHEN market_bars.is_provisional = 0 AND excluded.is_provisional = 1 THEN market_bars.high
               ELSE excluded.high
             END,
             low = CASE
               WHEN market_bars.is_provisional = 0 AND excluded.is_provisional = 1 THEN market_bars.low
               ELSE excluded.low
             END,
             close = CASE
               WHEN market_bars.is_provisional = 0 AND excluded.is_provisional = 1 THEN market_bars.close
               ELSE excluded.close
             END,
             volume = CASE
               WHEN market_bars.is_provisional = 0 AND excluded.is_provisional = 1 THEN market_bars.volume
               ELSE COALESCE(excluded.volume, market_bars.volume)
             END,
             volume_unit = CASE
               WHEN market_bars.is_provisional = 0 AND excluded.is_provisional = 1 THEN market_bars.volume_unit
               ELSE COALESCE(excluded.volume_unit, market_bars.volume_unit)
             END,
             volume_multiplier = CASE
               WHEN market_bars.is_provisional = 0 AND excluded.is_provisional = 1 THEN market_bars.volume_multiplier
               ELSE COALESCE(excluded.volume_multiplier, market_bars.volume_multiplier)
             END,
             quote_currency = CASE
               WHEN market_bars.is_provisional = 0 AND excluded.is_provisional = 1 THEN COALESCE(market_bars.quote_currency, excluded.quote_currency)
               ELSE COALESCE(excluded.quote_currency, market_bars.quote_currency)
             END,
             is_provisional = CASE
               WHEN market_bars.is_provisional = 0 AND excluded.is_provisional = 1 THEN 0
               ELSE excluded.is_provisional
             END,
             provider = CASE
               WHEN market_bars.is_provisional = 0 AND excluded.is_provisional = 1 THEN market_bars.provider
               ELSE excluded.provider
             END,
             fetched_at = CASE
               WHEN market_bars.is_provisional = 0 AND excluded.is_provisional = 1 THEN market_bars.fetched_at
               ELSE excluded.fetched_at
             END,
             source_run_id = CASE
               WHEN market_bars.is_provisional = 0 AND excluded.is_provisional = 1 THEN market_bars.source_run_id
               ELSE COALESCE(excluded.source_run_id, market_bars.source_run_id)
             END
        """,
        values,
    )


def _read_legacy_config(db):
    if _table_exists(db, "current_configuration") and _table_exists(db, "current_assets"):
        cur = db.execute("SELECT version FROM current_configuration").fetchone()
        if cur:
            version = cur["version"]
            assets = {}
            for row in db.execute("SELECT * FROM current_assets ORDER BY position"):
                assets[row["id"]] = {"id": row["id"], **json.loads(row["metadata"])}
            if _table_exists(db, "monitoring_bindings"):
                for row in db.execute("SELECT * FROM monitoring_bindings ORDER BY position"):
                    if row["asset_id"] in assets:
                        asset = assets[row["asset_id"]]
                        asset.setdefault("strategies", []).append(row["strategy_id"])
                        if row["activated"] is not None:
                            asset.setdefault("activated", {})[row["strategy_id"]] = row["activated"]
            rules = {}
            for row in db.execute("SELECT * FROM current_strategies ORDER BY position"):
                rule = {
                    **json.loads(row["metadata"]),
                    "id": row["id"],
                    "kind": row["kind"],
                    "name": row["name"],
                }
                if row["revision"] is not None:
                    rule["revision"] = row["revision"]
                if row["parameters"] is not None:
                    rule["params"] = json.loads(row["parameters"])
                rules[row["id"]] = rule
            if _table_exists(db, "combination_references"):
                for row in db.execute("SELECT * FROM combination_references"):
                    if row["owner_id"] in rules:
                        rules[row["owner_id"]][row["role"]] = row["target_id"]
            return {
                "version": version,
                "assets": list(assets.values()),
                "strategies": list(rules.values()),
            }
    if _table_exists(db, "configuration_versions"):
        row = db.execute(
            "SELECT id, payload FROM configuration_versions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            payload = json.loads(row["payload"])
            return {"version": row["id"], **payload}
    return None


def migrate_to_v6(db):
    """Losslessly migrate a v1..v5 ChanceTrack SQLite database to Schema v6 inside an open transaction."""
    now_iso = datetime.now(TZ).isoformat()
    legacy_config = _read_legacy_config(db)

    # Preserve legacy tables under temp names where names overlap with v6
    renamed = {}
    for overlapping in ("decisions", "notifications", "chart_templates"):
        if _table_exists(db, overlapping):
            temp_name = f"_legacy_{overlapping}"
            db.execute(f"ALTER TABLE {overlapping} RENAME TO {temp_name}")
            renamed[overlapping] = temp_name

    # Drop conflicting unique index name if any
    for stmt in SCHEMA_STATEMENTS:
        db.execute(stmt)

    known_catalog = catalog_by_id()
    asset_ids = set()
    next_asset_pos = 0

    # 1. Migrate assets, strategies, bindings, combination deps, config_meta
    if legacy_config:
        db.execute(
            "INSERT OR REPLACE INTO config_meta(singleton, version, updated_at) VALUES (1, ?, ?)",
            (int(legacy_config.get("version") or 1), now_iso),
        )
        for pos, asset in enumerate(legacy_config.get("assets") or []):
            preset = known_catalog.get(asset["id"], {})
            kind = resolve_asset_kind(asset)
            if kind not in ("index", "etf", "spot"):
                kind = "index"
            aliases = asset.get("aliases") or preset.get("aliases") or []
            fallbacks = asset.get("fallbacks") or []
            group_name = asset.get("group") or asset.get("group_name") or preset.get("group_name") or "未分组"
            db.execute(
                """INSERT INTO assets (
                     id, label, ticker, code, provider, kind, session_profile,
                     market, currency, group_name, secid, expected_name,
                     enabled, archived, watchlisted, priority, position,
                     aliases_json, fallbacks_json, validated_at, reason, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    asset["id"],
                    asset.get("label") or asset["id"],
                    asset.get("ticker") or asset.get("code") or "",
                    asset.get("code") or asset.get("ticker") or "",
                    asset.get("provider") or "yahoo",
                    kind,
                    asset.get("session_profile") or "us_equity",
                    asset.get("market") or preset.get("market") or "",
                    asset.get("currency") or preset.get("currency") or "",
                    group_name,
                    asset.get("secid"),
                    asset.get("expected_name"),
                    1 if asset.get("enabled", True) else 0,
                    1 if asset.get("archived", False) else 0,
                    1 if asset.get("watchlisted", True) else 0,
                    1 if asset.get("priority", False) else 0,
                    pos,
                    json.dumps(list(aliases), ensure_ascii=False),
                    json.dumps(list(fallbacks), ensure_ascii=False),
                    asset.get("validated_at"),
                    asset.get("reason"),
                    now_iso,
                    now_iso,
                ),
            )
            asset_ids.add(asset["id"])
            next_asset_pos = pos + 1

        strategy_ids = set()
        for pos, rule in enumerate(legacy_config.get("strategies") or []):
            db.execute(
                """INSERT INTO strategies (
                     id, kind, name, group_name, revision, params_json,
                     filter_mode, position, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rule["id"],
                    rule["kind"],
                    rule["name"],
                    rule.get("group") or rule.get("group_name") or "未分组",
                    int(rule.get("revision") or 1),
                    json.dumps(rule.get("params") or {}, ensure_ascii=False),
                    rule.get("filter") or rule.get("filter_mode"),
                    pos,
                    now_iso,
                    now_iso,
                ),
            )
            strategy_ids.add(rule["id"])

        for rule in legacy_config.get("strategies") or []:
            if rule["kind"] == "combination":
                for role in ("trigger", "band"):
                    target = rule.get(role)
                    if target and target in strategy_ids:
                        db.execute(
                            "INSERT OR IGNORE INTO strategy_dependencies(owner_id, role, target_id) VALUES (?, ?, ?)",
                            (rule["id"], role, target),
                        )

        for asset in legacy_config.get("assets") or []:
            activated = asset.get("activated") or {}
            seen_sids = set()
            pos = 0
            for sid in asset.get("strategies") or []:
                if sid in strategy_ids and sid not in seen_sids:
                    seen_sids.add(sid)
                    db.execute(
                        "INSERT INTO asset_strategies(asset_id, strategy_id, position, activated_at) VALUES (?, ?, ?, ?)",
                        (asset["id"], sid, pos, activated.get(sid)),
                    )
                    pos += 1

    def ensure_asset_exists(asset_id, asset_blob=None):
        nonlocal next_asset_pos
        if asset_id in asset_ids:
            return
        blob = asset_blob or {}
        preset = known_catalog.get(asset_id, {})
        kind = resolve_asset_kind(blob) if blob else preset.get("kind") or "index"
        if kind not in ("index", "etf", "spot"):
            kind = "index"
        aliases = blob.get("aliases") or preset.get("aliases") or []
        fallbacks = blob.get("fallbacks") or []
        db.execute(
            """INSERT INTO assets (
                 id, label, ticker, code, provider, kind, session_profile,
                 market, currency, group_name, secid, expected_name,
                 enabled, archived, watchlisted, priority, position,
                 aliases_json, fallbacks_json, validated_at, reason, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, 0, 0, ?, ?, ?, ?, ?, ?, ?)""",
            (
                asset_id,
                blob.get("label") or preset.get("label") or asset_id,
                blob.get("ticker") or blob.get("code") or preset.get("ticker") or "",
                blob.get("code") or blob.get("ticker") or preset.get("code") or asset_id,
                blob.get("provider") or preset.get("provider") or "yahoo",
                kind,
                blob.get("session_profile") or preset.get("session_profile") or "us_equity",
                blob.get("market") or preset.get("market") or "",
                blob.get("currency") or preset.get("currency") or "",
                blob.get("group") or blob.get("group_name") or "已归档",
                blob.get("secid") or preset.get("secid"),
                blob.get("expected_name") or preset.get("expected_name"),
                next_asset_pos,
                json.dumps(list(aliases), ensure_ascii=False),
                json.dumps(list(fallbacks), ensure_ascii=False),
                blob.get("validated_at"),
                blob.get("reason"),
                now_iso,
                now_iso,
            ),
        )
        asset_ids.add(asset_id)
        next_asset_pos += 1

    # 2a. Fold workspace_quotes BEFORE reports so newer confirmed bars from reports win
    if _table_exists(db, "workspace_quotes"):
        for qrow in db.execute("SELECT id, asset, snapshot FROM workspace_quotes ORDER BY id ASC"):
            try:
                ablob = json.loads(qrow["asset"])
                snap = json.loads(qrow["snapshot"])
            except Exception:
                continue
            aid = ablob.get("id")
            if aid:
                ensure_asset_exists(aid, ablob)
                _upsert_snapshot_bars(
                    db, aid, snap, run_id=None, default_provider=ablob.get("provider") or "unknown"
                )

    # 2b. Replay legacy reports (id ASC) -> market_bars + check_runs + check_run_items + signal metadata map
    report_item_lookup = {}  # (report_id, asset_id) -> {bar_date, close}
    report_created_at = {}
    event_detail_map = {}  # dedup_key -> dict
    migrated_run_ids = set()

    if _table_exists(db, "reports"):
        legacy_reports = db.execute(
            "SELECT id, created_at, day, kind, payload FROM reports ORDER BY id ASC"
        ).fetchall()
        for row in legacy_reports:
            rid = int(row["id"])
            report_created_at[rid] = row["created_at"]
            try:
                payload = json.loads(row["payload"])
            except Exception:
                continue
            kind = row["kind"] if row["kind"] in ("scheduled", "manual") else "manual"
            parent_id = payload.get("parent_report_id")
            if parent_id not in migrated_run_ids:
                parent_id = None
            rule_version = (payload.get("rule") or {}).get("version") or "migrated"
            cfg_ver = int((payload.get("configuration") or {}).get("version") or 1)
            db.execute(
                """INSERT INTO check_runs (
                     id, day, kind, attempt, parent_run_id, rule_version,
                     config_version, code_revision, late, probe_order_json, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rid,
                    row["day"],
                    kind,
                    int(payload.get("attempt") or 1),
                    parent_id,
                    rule_version,
                    cfg_ver,
                    str(payload.get("code_revision") or ""),
                    1 if payload.get("late") else 0,
                    json.dumps(payload.get("probe_order") or {}, ensure_ascii=False),
                    row["created_at"],
                ),
            )
            migrated_run_ids.add(rid)

            for item in payload.get("items") or []:
                asset_blob = item.get("asset") or {}
                aid = asset_blob.get("id")
                if not aid:
                    continue
                ensure_asset_exists(aid, asset_blob)
                snapshot = item.get("snapshot") or {}
                result = item.get("result") or {}
                _upsert_snapshot_bars(
                    db,
                    aid,
                    snapshot,
                    run_id=rid,
                    default_provider=item.get("provider") or asset_blob.get("provider") or "unknown",
                )
                bar_date = result.get("bar_date") or result.get("nav_date") or (item.get("last_quote") or {}).get("bar_date")
                close_val = result.get("close")
                if close_val is None and item.get("last_quote"):
                    close_val = item["last_quote"].get("close")
                report_item_lookup[(rid, aid)] = {
                    "bar_date": bar_date or row["day"],
                    "close": float(close_val) if close_val is not None else None,
                }
                status = item.get("status") if item.get("status") in ("ok", "warning", "error", "disabled") else "error"
                origin_rid = item.get("origin_report_id")
                if origin_rid not in migrated_run_ids:
                    origin_rid = None
                fetch_meta = {
                    k: item[k]
                    for k in ("fetch_status", "attempts", "provider_label", "last_quote")
                    if k in item and item[k] is not None
                }
                db.execute(
                    """INSERT OR REPLACE INTO check_run_items (
                         run_id, asset_id, status, bar_date, close, is_provisional,
                         provider, fetched_at, carried_forward, origin_run_id, error, fetch_meta_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        rid,
                        aid,
                        status,
                        bar_date,
                        float(close_val) if close_val is not None else None,
                        1 if result.get("is_provisional") else 0,
                        item.get("provider") or snapshot.get("provider") or asset_blob.get("provider"),
                        snapshot.get("fetched_at") or (item.get("last_quote") or {}).get("fetched_at"),
                        1 if item.get("carried_forward") else 0,
                        origin_rid,
                        item.get("error"),
                        json.dumps(fetch_meta, ensure_ascii=False),
                    ),
                )
                # Index strategy & legacy event details for signal_events hydration
                for strat in result.get("strategies") or []:
                    if strat.get("event") and strat.get("event_date"):
                        ek = f"{aid}:{strat['event_date']}:{strat['version']}:{strat['event']}"
                        event_detail_map.setdefault(
                            ek,
                            {
                                "asset_id": aid,
                                "strategy_id": strat.get("id"),
                                "strategy_name": strat.get("name") or strat.get("id") or "",
                                "strategy_group": strat.get("group") or "",
                                "strategy_version": strat.get("version") or "",
                                "event_date": strat["event_date"],
                                "event_kind": strat["event"],
                                "detail": strat.get("detail") or "",
                                "historical_reference": 1 if strat.get("historical_reference") else 0,
                                "first_run_id": rid,
                                "created_at": row["created_at"],
                            },
                        )
                if result.get("event"):
                    edate = result.get("event_date") or result.get("bar_date") or result.get("nav_date") or row["day"]
                    ek = f"{aid}:{edate}:{rule_version}:{result['event']}"
                    event_detail_map.setdefault(
                        ek,
                        {
                            "asset_id": aid,
                            "strategy_id": "macd",
                            "strategy_name": "MACD 默认",
                            "strategy_group": "趋势",
                            "strategy_version": rule_version,
                            "event_date": edate,
                            "event_kind": result["event"],
                            "detail": "",
                            "historical_reference": 0,
                            "first_run_id": rid,
                            "created_at": row["created_at"],
                        },
                    )

    # 3. Migrate notifications
    notif_sent_by_run = {}
    if "notifications" in renamed:
        cols = {r["name"] for r in db.execute(f"PRAGMA table_info({renamed['notifications']})")}
        msg_col = "message" if "message" in cols else "NULL AS message"
        for nrow in db.execute(
            f"SELECT report_id, sent_at, error, {msg_col} FROM {renamed['notifications']}"
        ):
            rid = int(nrow["report_id"])
            if rid in migrated_run_ids:
                db.execute(
                    "INSERT OR REPLACE INTO notifications(run_id, sent_at, error, message) VALUES (?, ?, ?, ?)",
                    (rid, nrow["sent_at"], nrow["error"], nrow["message"]),
                )
                if nrow["sent_at"]:
                    notif_sent_by_run[rid] = nrow["sent_at"]

    # 4. Migrate events + notification_events -> signal_events (exact dedup_key preservation!)
    notified_map = {}  # dedup_key -> (run_id, sent_at)
    if _table_exists(db, "notification_events"):
        for nerow in db.execute("SELECT key, report_id FROM notification_events"):
            rid = int(nerow["report_id"])
            sent_at = notif_sent_by_run.get(rid) or report_created_at.get(rid) or now_iso
            notified_map[nerow["key"]] = (rid if rid in migrated_run_ids else None, sent_at)

    all_event_keys = {}
    if _table_exists(db, "events"):
        for erow in db.execute("SELECT key, report_id FROM events"):
            rid = int(erow["report_id"])
            all_event_keys[erow["key"]] = rid if rid in migrated_run_ids else None
    for k, (rid, _) in notified_map.items():
        all_event_keys.setdefault(k, rid)

    for key, rid in all_event_keys.items():
        meta = event_detail_map.get(key)
        if not meta:
            head = key.split(":", 2)
            if len(head) == 3 and ":" in head[2]:
                aid, edate, rest = head
                sver, ekind = rest.rsplit(":", 1)
            else:
                continue
            ensure_asset_exists(aid)
            meta = {
                "asset_id": aid,
                "strategy_id": None,
                "strategy_name": sver,
                "strategy_group": "",
                "strategy_version": sver,
                "event_date": edate,
                "event_kind": ekind,
                "detail": "",
                "historical_reference": 0,
                "first_run_id": rid,
                "created_at": report_created_at.get(rid, now_iso) if rid else now_iso,
            }
        else:
            ensure_asset_exists(meta["asset_id"])
        notif_run_id, notif_at = notified_map.get(key, (None, None))
        db.execute(
            """INSERT OR IGNORE INTO signal_events (
                 dedup_key, asset_id, strategy_id, strategy_name, strategy_group,
                 strategy_version, event_date, event_kind, detail, is_provisional,
                 historical_reference, first_run_id, notified_at, notified_run_id, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)""",
            (
                key,
                meta["asset_id"],
                meta["strategy_id"],
                meta["strategy_name"],
                meta["strategy_group"],
                meta["strategy_version"],
                meta["event_date"],
                meta["event_kind"],
                meta["detail"],
                meta["historical_reference"],
                meta["first_run_id"] if meta["first_run_id"] in migrated_run_ids else None,
                notif_at,
                notif_run_id,
                meta["created_at"],
            ),
        )

    # 5. Migrate decisions + decision_contexts -> decisions
    if "decisions" in renamed:
        contexts = {}
        if _table_exists(db, "decision_contexts"):
            for crow in db.execute("SELECT decision_id, payload FROM decision_contexts"):
                contexts[int(crow["decision_id"])] = crow["payload"]
        for drow in db.execute(f"SELECT * FROM {renamed['decisions']} ORDER BY id ASC"):
            did = int(drow["id"])
            rid = int(drow["report_id"])
            aid = drow["asset_id"]
            ensure_asset_exists(aid)
            info = report_item_lookup.get((rid, aid)) or {}
            bar_date = info.get("bar_date") or str(drow["created_at"])[:10]
            price = info.get("close")
            if price is None:
                bar_row = db.execute(
                    "SELECT close FROM market_bars WHERE asset_id=? AND bar_date<=? ORDER BY bar_date DESC LIMIT 1",
                    (aid, bar_date),
                ).fetchone()
                if bar_row:
                    price = float(bar_row["close"])
            db.execute(
                """INSERT INTO decisions (
                     id, asset_id, run_id, bar_date, price_at_decision,
                     action, execution, note, evidence_json, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    did,
                    aid,
                    rid if rid in migrated_run_ids else None,
                    bar_date,
                    price,
                    drow["action"],
                    drow["execution"],
                    drow["note"] or "",
                    contexts.get(did),
                    drow["created_at"],
                ),
            )

    # 6. Migrate investment_notes -> notes
    if _table_exists(db, "investment_notes"):
        for n in db.execute("SELECT * FROM investment_notes ORDER BY id ASC"):
            db.execute(
                """INSERT INTO notes (id, title, markdown, pinned, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (n["id"], n["title"], n["markdown"], int(n["pinned"] or 0), n["created_at"], n["updated_at"]),
            )

    # 7. Migrate chart_templates
    if "chart_templates" in renamed:
        for t in db.execute(f"SELECT * FROM {renamed['chart_templates']} ORDER BY id ASC"):
            db.execute(
                """INSERT INTO chart_templates (id, template_id, name, strategies_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (t["id"], t["template_id"], t["name"], t["payload"], t["created_at"]),
            )

    # 8. Drop legacy tables cleanly
    legacy_tables = (
        "combination_references",
        "monitoring_bindings",
        "current_configuration",
        "current_assets",
        "current_strategies",
        "configuration_versions",
        "notification_events",
        "events",
        "decision_contexts",
        "workspace_quotes",
        "investment_notes",
        "_legacy_decisions",
        "_legacy_notifications",
        "_legacy_chart_templates",
        "reports",
    )
    for tbl in legacy_tables:
        if _table_exists(db, tbl):
            db.execute(f"DROP TABLE {tbl}")

    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
