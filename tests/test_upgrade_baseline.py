import json
from copy import deepcopy

import pytest
from test_loop import NOW, fetcher

from chance_track import storage
from chance_track.configuration import load_configuration, validate_configuration
from chance_track.notifications import build_message
from chance_track.runner import run

ACTIVATED = "2026-10-01T09:00:00+08:00"


@pytest.fixture
def v08_database(tmp_path):
    path = tmp_path / "v08.sqlite"
    storage.initialize(path)
    config = load_configuration(path)
    macd = next(rule for rule in config["strategies"] if rule["id"] == "macd")
    macd["revision"] = 2
    config["strategies"].extend(
        [
            {**macd, "id": "macd_sp500", "revision": 3},
            {**macd, "id": "macd_unused", "revision": 1},
            {
                "id": "macd_combo",
                "kind": "combination",
                "name": "MACD 组合",
                "trigger": "macd_sp500",
                "revision": 1,
            },
        ]
    )
    for asset in config["assets"]:
        asset["enabled"] = True
        bindings = ["macd_sp500", "macd_combo"] if asset["id"] == "sp500" else ["macd"]
        asset["strategies"] = bindings
        asset["activated"] = dict.fromkeys(bindings, ACTIVATED)
    validate_configuration(config)

    # Existing duplicates predate current write validation. Seed a v0.8 snapshot.
    with storage.connect(path) as db:
        config["version"] = db.execute(
            "INSERT INTO configuration_versions(created_at,payload) VALUES(?,?)",
            (ACTIVATED, json.dumps({k: config[k] for k in ("assets", "strategies")})),
        ).lastrowid
    note_id = storage.save_investment_note(
        path, None, "合成备忘", "# 合成备忘", ACTIVATED
    )
    storage.pin_investment_note(path, note_id)
    sent = []
    report_id = run(path, now=NOW, fetcher=fetcher, sender=lambda _, message: sent.append(message))
    return path, config, report_id, sent


def test_v08_configuration_and_history_baseline(v08_database):
    path, expected, report_id, sent = v08_database
    with storage.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        event_keys = {row["key"] for row in db.execute("SELECT key FROM events")}
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
    original = deepcopy(report)
    assert rules["macd_sp500"]["params"] == {"fast": 12, "slow": 26, "signal": 9}
    keys = storage.notified_event_keys(path)
    assert keys and keys <= event_keys
    assert len(sent) == 1 and storage.notification(path, report_id)["message"] == sent[0]
    assert not build_message(report, keys)[1]

    replay = run(path, now=NOW, fetcher=fetcher, send=False)
    assert storage.report(path, report_id) == original
    assert not build_message(storage.report(path, replay), keys)[1]
