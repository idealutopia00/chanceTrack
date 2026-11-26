"""Runner, retry carry-forward, notification dedup, and ETF migration tests on Schema v6."""

from copy import deepcopy
from datetime import timedelta

import pytest
from helpers import NOW, sample

from chance_track import storage
from chance_track.configuration import (
    load_configuration,
    migrate_etf_targets,
    save_configuration,
)
from chance_track.runner import run


def test_notification_dedup_and_silent_manual_check(tmp_path):
    db_path = tmp_path / "notif.sqlite3"
    cfg = load_configuration(db_path)
    asset = {**cfg["assets"][0], "activated": {}}
    sent = []

    # Silent manual check records signal_event but does NOT mark notified_at
    r1 = run(db_path, [asset], send=False, now=NOW, fetcher=lambda *_: sample())
    sigs = storage.list_signals(db_path, asset_id=asset["id"])
    assert len(sigs) == 1
    assert sigs[0]["notified_at"] is None
    assert storage.notified_event_keys(db_path) == set()

    # Scheduled check delivers first notification and records notified_at
    r2 = run(
        db_path,
        [asset],
        scheduled=True,
        send=True,
        now=NOW,
        fetcher=lambda *_: sample(),
        sender=lambda title, msg: sent.append((title, msg)),
    )
    assert r2 != r1
    assert len(sent) == 1
    assert "新增确认提醒 1 项" in sent[0][1]
    assert len(storage.notified_event_keys(db_path)) == 1

    # Subsequent manual send must NOT count the same signal as fresh
    run(
        db_path,
        [asset],
        scheduled=False,
        send=True,
        now=NOW + timedelta(minutes=10),
        fetcher=lambda *_: sample(),
        sender=lambda title, msg: sent.append((title, msg)),
    )
    assert len(sent) == 2
    assert "新增确认提醒 0 项" in sent[1][1]


def test_failed_delivery_does_not_consume_signal_and_retry_delivers(tmp_path):
    db_path = tmp_path / "retry_notif.sqlite3"
    cfg = load_configuration(db_path)
    with storage.connect(db_path) as db:
        db.execute("UPDATE asset_strategies SET activated_at='2026-01-01T00:00:00+08:00'")
    cfg = load_configuration(db_path)
    asset = cfg["assets"][0]

    def failing_sender(_title, _msg):
        raise RuntimeError("osascript unavailable")

    with pytest.raises(RuntimeError, match="osascript"):
        run(
            db_path,
            [asset],
            scheduled=True,
            send=True,
            now=NOW,
            fetcher=lambda *_: sample(),
            sender=failing_sender,
        )
    assert storage.notified_event_keys(db_path) == set()

    delivered = []
    run(
        db_path,
        [asset],
        scheduled=True,
        send=True,
        now=NOW + timedelta(minutes=2),
        fetcher=lambda *_: sample(),
        sender=lambda t, m: delivered.append((t, m)),
    )
    assert len(delivered) == 1
    assert "新增确认提醒 1 项" in delivered[0][1]
    assert len(storage.notified_event_keys(db_path)) == 1


def test_scheduled_retry_carries_forward_successful_assets(tmp_path):
    db_path = tmp_path / "retry.sqlite3"
    cfg = load_configuration(db_path)
    pair = [{**a, "activated": {}} for a in cfg["assets"][:2]]
    calls = []

    def flaky_fetch(asset, _day):
        calls.append(asset["id"])
        if asset["id"] == pair[1]["id"] and calls.count(pair[1]["id"]) == 1:
            raise ConnectionError("synthetic timeout")
        return {**sample(), "code": asset["code"], "provider": asset["provider"]}

    r1 = run(db_path, pair, scheduled=True, send=False, now=NOW, fetcher=flaky_fetch)
    run1 = storage.get_check_run(db_path, r1)
    assert run1["attempt"] == 1
    by_id1 = {i["asset_id"]: i for i in run1["items"]}
    assert by_id1[pair[0]["id"]]["status"] == "ok"
    assert by_id1[pair[1]["id"]]["status"] == "error"

    # Retry 6 minutes later carries forward pair[0] without re-fetching it
    r2 = run(
        db_path,
        pair,
        scheduled=True,
        send=False,
        now=NOW + timedelta(minutes=6),
        fetcher=flaky_fetch,
    )
    assert r2 > r1
    run2 = storage.get_check_run(db_path, r2)
    assert run2["attempt"] == 2
    by_id2 = {i["asset_id"]: i for i in run2["items"]}
    assert by_id2[pair[0]["id"]]["carried_forward"] is True
    assert by_id2[pair[1]["id"]]["status"] == "ok"
    assert calls.count(pair[0]["id"]) == 1
    assert calls.count(pair[1]["id"]) == 2


def test_new_binding_activation_boundary_suppresses_old_signal_notification(tmp_path):
    db_path = tmp_path / "activation.sqlite3"
    cfg = load_configuration(db_path)
    # Default assets have activated_at = current wall clock (2026-11-26 > 2026-11-16 sample event)
    asset = cfg["assets"][0]
    sent = []
    run(
        db_path,
        [asset],
        scheduled=True,
        send=True,
        now=NOW,
        fetcher=lambda *_: sample(),
        sender=lambda t, m: sent.append((t, m)),
    )
    assert "新增确认提醒 0 项" in sent[0][1]
    assert "启用前历史参考" in sent[0][1]
    assert storage.notified_event_keys(db_path) == set()


def test_migrate_etf_targets_on_schema_v6(tmp_path):
    db_path = tmp_path / "etf_mig.sqlite3"
    cfg = load_configuration(db_path)
    legacy = deepcopy(cfg)
    legacy["assets"] = [
        a for a in legacy["assets"] if a["id"] not in ("gold_etf_518800", "star_chinext50_etf_159603")
    ]
    legacy["assets"].append(
        {
            "id": "gold",
            "label": "黄金 Au99.99",
            "ticker": "AU9999",
            "code": "AU9999",
            "provider": "eastmoney",
            "secid": "118.AU9999",
            "expected_name": "黄金9999",
            "kind": "spot",
            "group": "黄金",
            "market": "上金所",
            "currency": "元/克",
            "session_profile": "sge_gold",
            "enabled": True,
            "archived": False,
            "watchlisted": True,
            "priority": True,
            "strategies": ["macd"],
        }
    )
    with storage.connect(db_path) as db:
        db.execute("DELETE FROM assets WHERE id IN ('gold_etf_518800', 'star_chinext50_etf_159603')")
        storage.write_current_configuration(db, legacy, cfg["version"])

    res = migrate_etf_targets(db_path, validate_live=False)
    assert res["changed"] is True
    assert res["migrated"][0]["to_id"] == "gold_etf_518800"
    updated = load_configuration(db_path)
    by_id = {a["id"]: a for a in updated["assets"]}
    assert by_id["gold"]["archived"] is True
    assert by_id["gold_etf_518800"]["enabled"] is True
    assert by_id["gold_etf_518800"]["priority"] is True
    assert "macd" in by_id["gold_etf_518800"]["strategies"]
    # Second run is a clean no-op
    assert migrate_etf_targets(db_path, validate_live=False)["changed"] is False


def test_retry_cap_cutoff_late_flag_and_shared_lock(tmp_path):
    """Ported from v0.9 test_loop: retry cap, 15:00 cutoff, late check flag, and CheckInProgress lock."""
    from datetime import datetime
    from chance_track.data import TZ
    from chance_track.runner import CheckInProgress, check_lock, retry_due

    db_path = tmp_path / "loop.sqlite3"
    cfg = load_configuration(db_path)
    pair = [{**a, "activated": {}} for a in cfg["assets"][:2]]

    def fail_second(asset, _day):
        if asset["id"] == pair[1]["id"]:
            raise ConnectionError("down")
        return {**sample(), "code": asset["code"], "provider": asset["provider"]}

    # Attempt 1 at 14:35 -> 1 ok, 1 error
    r1 = run(db_path, pair, scheduled=True, send=False, now=NOW, fetcher=fail_second)
    assert storage.get_check_run(db_path, r1)["attempt"] == 1
    # Too soon (3m later): no retry
    assert run(db_path, pair, scheduled=True, send=False, now=NOW + timedelta(minutes=3), fetcher=fail_second) == r1
    # Attempt 2 at 14:41
    r2 = run(db_path, pair, scheduled=True, send=False, now=NOW + timedelta(minutes=6), fetcher=fail_second)
    assert storage.get_check_run(db_path, r2)["attempt"] == 2
    # Attempt 3 at 14:47 (hits MAX_ATTEMPTS=3)
    r3 = run(db_path, pair, scheduled=True, send=False, now=NOW + timedelta(minutes=12), fetcher=fail_second)
    assert storage.get_check_run(db_path, r3)["attempt"] == 3
    # Attempt 4 at 14:53 must NOT run (cap reached)
    assert run(db_path, pair, scheduled=True, send=False, now=NOW + timedelta(minutes=18), fetcher=fail_second) == r3

    # Cutoff at 15:00 blocks further retries
    run2 = storage.get_check_run(db_path, r2)
    assert retry_due(run2, datetime(2026, 11, 17, 15, 0, tzinfo=TZ)) is False

    # Late first scheduled check at 15:05 marks late=True
    db_late = tmp_path / "late.sqlite3"
    load_configuration(db_late)
    r_late = run(
        db_late,
        [pair[0]],
        scheduled=True,
        send=False,
        now=datetime(2026, 11, 17, 15, 5, tzinfo=TZ),
        fetcher=lambda *_: sample(),
    )
    assert storage.get_check_run(db_late, r_late)["late"] is True

    # Shared lock prevents concurrent run
    with check_lock(db_path):
        with pytest.raises(CheckInProgress):
            run(db_path, [pair[0]], send=False, now=NOW, fetcher=lambda *_: sample())


def test_failed_benchmark_triggers_retry_and_recovers(tmp_path):
    """Regression for H1 + Gemini High #2: failed relative_strength benchmark triggers retry."""
    db_path = tmp_path / "bench_retry.sqlite3"
    cfg = load_configuration(db_path)
    rs_rule = {
        "id": "rs_ndx",
        "name": "纳指相对标普",
        "kind": "relative_strength",
        "group": "相对强弱",
        "params": {"trend": 20, "relative_period": 10, "benchmark": "sp500"},
    }
    cfg["strategies"].append(rs_rule)
    for a in cfg["assets"]:
        if a["id"] == "nasdaq100":
            a["strategies"] = ["rs_ndx"]
            a["activated"] = {}
        elif a["id"] == "sp500":
            a["activated"] = {}
    save_configuration(db_path, cfg, cfg["version"], validate_new=False)
    cfg = load_configuration(db_path)
    pair = [a for a in cfg["assets"] if a["id"] in ("sp500", "nasdaq100")]

    calls = []

    def flaky_bench(asset, _day):
        calls.append(asset["id"])
        if asset["id"] == "sp500" and calls.count("sp500") == 1:
            raise ConnectionError("benchmark down")
        snap = sample()
        snap["code"] = asset["code"]
        snap["provider"] = "yahoo"
        snap["quote_currency"] = "USD"
        return snap

    r1 = run(db_path, pair, scheduled=True, send=False, now=NOW, fetcher=flaky_bench)
    run1 = storage.get_check_run(db_path, r1)
    by_id1 = {i["asset_id"]: i for i in run1["items"]}
    assert by_id1["nasdaq100"]["status"] == "ok"
    assert by_id1["nasdaq100"].get("failed_benchmark") is True

    # Retry 6 minutes later must recover sp500 and re-evaluate nasdaq100 without failed_benchmark
    r2 = run(
        db_path,
        pair,
        scheduled=True,
        send=False,
        now=NOW + timedelta(minutes=6),
        fetcher=flaky_bench,
    )
    assert r2 > r1
    run2 = storage.get_check_run(db_path, r2)
    by_id2 = {i["asset_id"]: i for i in run2["items"]}
    assert by_id2["sp500"]["status"] == "ok"
    assert not by_id2["nasdaq100"].get("failed_benchmark")
