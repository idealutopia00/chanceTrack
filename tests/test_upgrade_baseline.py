import gzip
import json
from pathlib import Path

import pytest
from test_loop import NOW, fetcher

from chance_track import storage
from chance_track.configuration import load_configuration
from chance_track.notifications import build_message
from chance_track.runner import run

ACTIVATED = "2026-10-01T09:00:00+08:00"


@pytest.fixture
def v08_database(tmp_path):
    path = tmp_path / "v08.sqlite"
    fixture = Path(__file__).with_name("fixtures") / "v08-monitoring.sql.gz"
    with storage.connect(path) as db, gzip.open(fixture, "rt", encoding="utf-8") as sql:
        # SQLite dumps insert tables in name order, including child rows first.
        db.executescript("PRAGMA foreign_keys=OFF;\n" + sql.read())
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    return path


def test_v08_configuration_and_history_baseline(v08_database):
    path = v08_database
    with storage.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        row = db.execute(
            "SELECT id, payload FROM configuration_versions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        expected = {"version": row["id"], **json.loads(row["payload"])}
        event_keys = {row["key"] for row in db.execute("SELECT key FROM events")}
    report_id = 1
    assert load_configuration(path) == expected
    assets = {asset["id"]: asset for asset in expected["assets"]}
    assert assets["sp500"]["strategies"] == ["macd_sp500", "macd_combo"]
    assert sum(asset["strategies"] == ["macd"] for asset in assets.values()) == 4
    assert all(
        asset["activated"] == dict.fromkeys(asset["strategies"], ACTIVATED)
        for asset in assets.values()
    )
    rules = {rule["id"]: rule for rule in expected["strategies"]}
    assert rules["macd_combo"]["trigger"] == "macd_sp500"
    assert rules["macd_unused"]["params"] == rules["macd_sp500"]["params"]
    assert storage.investment_note(path)["markdown"] == "# 合成备忘"

    report = storage.report(path, report_id)
    assert report["configuration"] == expected
    assert rules["macd_sp500"]["params"] == {"fast": 12, "slow": 26, "signal": 9}
    keys = storage.notified_event_keys(path)
    assert keys and keys <= event_keys
    notification = storage.notification(path, report_id)
    assert notification["sent_at"] == NOW.isoformat() and notification["message"]
    assert not build_message(report, keys)[1]

    replay = run(path, now=NOW, fetcher=fetcher, send=False)
    assert storage.report(path, report_id) == report
    assert not build_message(storage.report(path, replay), keys)[1]


def test_relational_migration_is_idempotent_and_enforces_references(v08_database):
    import sqlite3

    path = v08_database
    current = load_configuration(path)
    old_report = storage.report(path, 1)
    for _ in range(2):
        storage.initialize(path)
        assert load_configuration(path) == current
    with storage.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("SELECT count(*) FROM configuration_versions").fetchone()[0] == 2
        assert db.execute(
            "SELECT activated FROM monitoring_bindings WHERE asset_id=? AND strategy_id=?",
            ("sp500", "macd_sp500"),
        ).fetchone()[0] == ACTIVATED
        assert db.execute("SELECT target_id FROM combination_references").fetchone()[0] == "macd_sp500"
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO monitoring_bindings VALUES ('sp500', 'macd_sp500', 2, NULL)")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO monitoring_bindings VALUES ('sp500', 'missing', 2, NULL)")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM current_strategies WHERE id='macd_sp500'")
    assert storage.report(path, 1) == old_report


@pytest.mark.parametrize("failure", ["interrupted", "storage_full"])
def test_migration_failure_keeps_v08_readable(v08_database, monkeypatch, failure):
    import sqlite3

    from chance_track import configuration_store

    path = v08_database
    historical_report = storage.report(path, 1)
    original = configuration_store.write_current

    def fail(db, config, version):
        original(db, config, version)
        if failure == "interrupted":
            raise RuntimeError("interrupted")
        raise sqlite3.OperationalError("database or disk is full")

    with monkeypatch.context() as patch:
        patch.setattr(configuration_store, "write_current", fail)
        with pytest.raises((RuntimeError, sqlite3.OperationalError)):
            storage.initialize(path)
    with storage.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert not db.execute(
            "SELECT name FROM sqlite_schema WHERE name='current_configuration'"
        ).fetchall()
    assert storage.report(path, 1) == historical_report
    storage.initialize(path)
    assert load_configuration(path)["version"] == 2


def test_migration_refuses_a_dangling_reference_with_location(v08_database):
    path = v08_database
    with storage.connect(path) as db:
        db.execute(
            "UPDATE configuration_versions SET payload=json_set(payload, '$.assets[0].strategies[0]', 'missing') WHERE id=2"
        )
    with pytest.raises(ValueError, match="sp500.*missing"):
        storage.initialize(path)
    with storage.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
