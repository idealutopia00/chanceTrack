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
