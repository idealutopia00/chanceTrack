"""Comprehensive tests for Schema v6 initialization and lossless v4/v5 -> v6 migration."""

import gzip
import json
from pathlib import Path
import sqlite3

import pytest

from chance_track import storage
from chance_track.configuration import load_configuration
from chance_track.schema import SCHEMA_VERSION

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "v08-monitoring.sql.gz"


def test_empty_db_initializes_directly_to_schema_v6(tmp_path):
    db_path = tmp_path / "fresh.sqlite3"
    storage.initialize(db_path)
    with storage.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        tables = {
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        expected = {
            "config_meta",
            "assets",
            "strategies",
            "asset_strategies",
            "strategy_dependencies",
            "check_runs",
            "check_run_items",
            "market_bars",
            "signal_events",
            "notifications",
            "decisions",
            "notes",
            "note_assets",
            "chart_templates",
        }
        assert expected.issubset(tables)

    cfg = load_configuration(db_path)
    assert cfg["version"] == 1
    assert len(cfg["assets"]) == 5
    assert len(cfg["strategies"]) == 4


def test_unupgraded_legacy_db_is_refused_without_explicit_flag(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    sql = gzip.decompress(FIXTURE.read_bytes()).decode("utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(sql)
    with pytest.raises(ValueError, match="Schema v6"):
        storage.initialize(db_path, upgrade_configuration=False)


def test_lossless_migration_from_v08_fixture_preserves_all_entities_and_dedup_keys(tmp_path):
    db_path = tmp_path / "v08.sqlite3"
    sql = gzip.decompress(FIXTURE.read_bytes()).decode("utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(sql)
        old_notes = conn.execute("SELECT id, title, markdown, pinned FROM investment_notes").fetchall()
        old_notified = {
            r[0] for r in conn.execute("SELECT key FROM notification_events").fetchall()
        }
        old_events = {r[0] for r in conn.execute("SELECT key FROM events").fetchall()}

    storage.initialize(db_path, upgrade_configuration=True)
    # Second initialize call must be an idempotent no-op
    storage.initialize(db_path)

    with storage.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        # Legacy bloated tables removed
        remaining = {
            r["name"]
            for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")
        }
        assert "reports" not in remaining
        assert "current_assets" not in remaining
        assert "workspace_quotes" not in remaining

        new_notes = db.execute("SELECT id, title, markdown, pinned FROM notes").fetchall()
        assert [tuple(r) for r in new_notes] == old_notes
        new_events = {
            r["dedup_key"] for r in db.execute("SELECT dedup_key FROM signal_events")
        }
        assert old_events.issubset(new_events)
        assert old_notified.issubset(storage.notified_event_keys(db_path))
        bar_count = db.execute("SELECT count(*) FROM market_bars").fetchone()[0]
        assert bar_count > 500


def test_v5_migration_deduplicates_bars_and_hydrates_decisions_with_evidence(tmp_path):
    db_path = tmp_path / "v5.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE configuration_versions (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE current_configuration (singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL);
            CREATE TABLE current_assets (id TEXT PRIMARY KEY, position INTEGER NOT NULL, metadata TEXT NOT NULL);
            CREATE TABLE current_strategies (id TEXT PRIMARY KEY, position INTEGER NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, revision INTEGER, parameters TEXT, metadata TEXT NOT NULL);
            CREATE TABLE monitoring_bindings (asset_id TEXT NOT NULL, strategy_id TEXT NOT NULL, position INTEGER NOT NULL, activated TEXT);
            CREATE TABLE combination_references (owner_id TEXT NOT NULL, role TEXT NOT NULL, target_id TEXT NOT NULL);
            CREATE TABLE reports (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE events (key TEXT PRIMARY KEY, report_id INTEGER NOT NULL);
            CREATE TABLE notification_events (key TEXT PRIMARY KEY, report_id INTEGER NOT NULL);
            CREATE TABLE notifications (report_id INTEGER PRIMARY KEY, sent_at TEXT, error TEXT, message TEXT);
            CREATE TABLE decisions (id INTEGER PRIMARY KEY, report_id INTEGER NOT NULL, asset_id TEXT NOT NULL, created_at TEXT NOT NULL, action TEXT NOT NULL, execution TEXT NOT NULL, note TEXT NOT NULL);
            CREATE TABLE decision_contexts (decision_id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE investment_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, markdown TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE chart_templates (id INTEGER PRIMARY KEY, template_id TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);
            """
        )
        asset_meta = {
            "label": "标普500",
            "ticker": "SPX",
            "code": "^GSPC",
            "provider": "yahoo",
            "kind": "index",
            "session_profile": "us_equity",
            "market": "美国 · S&P",
            "currency": "点",
            "group": "美国指数",
            "enabled": True,
            "archived": False,
            "watchlisted": True,
            "priority": True,
        }
        conn.execute(
            "INSERT INTO configuration_versions VALUES (3, '2026-11-20T10:00:00+08:00', ?)",
            (json.dumps({"assets": [{"id": "sp500", **asset_meta}], "strategies": []}),),
        )
        conn.execute("INSERT INTO current_configuration VALUES (1, 3)")
        conn.execute(
            "INSERT INTO current_assets VALUES ('sp500', 0, ?)",
            (json.dumps(asset_meta),),
        )
        # Report 1: provisional bar on 2026-11-20; Report 2: confirmed bar on 2026-11-20
        for rid, close, prov in [(1, 5900.0, "2026-11-20"), (2, 5950.5, None)]:
            payload = {
                "rule": {"version": "macd-v3"},
                "configuration": {"version": 3},
                "items": [
                    {
                        "asset": {"id": "sp500", **asset_meta},
                        "status": "ok",
                        "snapshot": {
                            "basis": "market_ohlc",
                            "provider": "yahoo",
                            "fetched_at": f"2026-11-20T1{rid}:00:00+08:00",
                            "provisional_date": prov,
                            "rows": [
                                {"date": "2026-11-19", "open": 5880, "high": 5910, "low": 5870, "close": 5895, "volume": 1000},
                                {"date": "2026-11-20", "open": 5895, "high": 5960, "low": 5890, "close": close, "volume": 1200},
                            ],
                        },
                        "result": {"bar_date": "2026-11-20", "close": close, "is_provisional": bool(prov)},
                    }
                ],
            }
            conn.execute(
                "INSERT INTO reports VALUES (?, ?, '2026-11-20', 'manual', ?)",
                (rid, f"2026-11-20T1{rid}:00:00+08:00", json.dumps(payload)),
            )
        conn.execute(
            "INSERT INTO decisions VALUES (7, 2, 'sp500', '2026-11-20T15:00:00+08:00', '买入', '已提交', '突破加仓')"
        )
        conn.execute(
            "INSERT INTO decision_contexts VALUES (7, ?)",
            (json.dumps({"strategies": [{"id": "b1", "kind": "boll_break"}]}),),
        )
        conn.execute(
            "INSERT INTO chart_templates VALUES (1, 'tpl_1', '趋势模板', '2026-11-20T10:00:00+08:00', '[]')"
        )
        conn.execute("PRAGMA user_version=5")

    storage.initialize(db_path, upgrade_configuration=True)

    bars = storage.get_bars(db_path, "sp500")
    assert len(bars) == 2
    assert bars[-1]["bar_date"] == "2026-11-20"
    assert bars[-1]["close"] == 5950.5
    assert bars[-1]["is_provisional"] == 0

    decs = storage.list_decisions(db_path, asset_id="sp500")
    assert len(decs) == 1
    assert decs[0]["bar_date"] == "2026-11-20"
    assert decs[0]["price_at_decision"] == 5950.5
    assert decs[0]["has_exploration"] is True
    assert storage.get_decision_evidence(db_path, 7)["strategies"][0]["id"] == "b1"

    tpls = storage.list_templates(db_path)
    assert len(tpls) == 1 and tpls[0]["name"] == "趋势模板"
    assert load_configuration(db_path)["version"] == 3
