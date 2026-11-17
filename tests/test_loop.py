from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from chance_track import storage
from chance_track.analysis import RULE, analyze
from chance_track.data import TZ, parse_eastmoney, parse_yahoo, validate_rows
from chance_track.runner import due, run
from chance_track.web import create_app

ASSET = {"id": "test", "label": "测试指数", "code": "TEST", "enabled": True}
NOW = datetime(2026, 11, 17, 14, 35, tzinfo=TZ)


def sample():
    values = (
        [1 + i * 0.001 for i in range(140)]
        + [1.14 - i * 0.002 for i in range(35)]
        + [1.5]
    )
    start = NOW.date() - timedelta(days=len(values))
    return {
        "rows": [
            {
                "date": (start + timedelta(days=i)).isoformat(),
                "open": v - 0.001,
                "high": v + 0.01,
                "low": v - 0.01,
                "close": v,
                "volume": 100,
            }
            for i, v in enumerate(values)
        ],
        "source": "fixture",
        "name": "测试指数",
        "code": "TEST",
        "basis": "market_ohlc",
    }


def fetcher(asset, day):
    return sample()


def test_macd_close_cross_chart_consistency_and_quality_gates():
    snapshot = sample()
    result = analyze(snapshot, NOW.date().isoformat())
    assert result["event"] == "金叉 · 买入观察"
    assert result["histogram"] == pytest.approx(2 * (result["dif"] - result["dea"]))
    assert result["chart"]["macd"][-1]["dif"] == result["dif"]
    assert result["chart"]["candles"][-1]["close"] == result["close"]
    assert result["chart"]["crosses"][-1] == {
        "time": result["bar_date"],
        "kind": "golden",
    }
    assert analyze(snapshot, "2026-12-01")["event"] is None
    snapshot["basis"] = "unit_nav"
    with pytest.raises(ValueError, match="不接受基金净值"):
        analyze(snapshot, "2026-11-17")
    snapshot["basis"] = "market_ohlc"
    snapshot["rows"] = snapshot["rows"][:10]
    with pytest.raises(ValueError, match="120"):
        analyze(snapshot, "2026-11-17")


def test_snapshot_dedupe_and_append_only_decisions(tmp_path):
    path = tmp_path / "history.sqlite3"
    first = run(path, [ASSET], send=False, now=NOW, fetcher=fetcher)
    second = run(path, [ASSET], send=False, now=NOW, fetcher=fetcher)
    a, b = storage.report(path, first), storage.report(path, second)
    assert a["items"][0]["result"]["new_event"] is True
    assert b["items"][0]["result"]["new_event"] is False
    assert a["items"][0]["snapshot"] == {**sample(), "fetched_at": NOW.isoformat()}
    for action in ("观望", "买入"):
        storage.add_decision(
            path, first, "test", action, "未执行", "reason", NOW.isoformat()
        )
    assert [x["action"] for x in storage.decisions(path, first)] == ["买入", "观望"]
    with pytest.raises(ValueError):
        storage.add_decision(
            path, first, "missing", "买入", "未执行", "", NOW.isoformat()
        )
    assert storage.report(path, first) == a


def test_schedule_notification_retry_and_rule_upgrade(tmp_path):
    path = tmp_path / "history.sqlite3"
    old = {
        "created_at": NOW.isoformat(),
        "day": "2026-11-17",
        "kind": "scheduled",
        "rule": {"version": "old"},
        "items": [],
    }
    storage.save_report(path, old)
    calls = []

    def broken(title, message):
        raise RuntimeError("notification denied")

    with pytest.raises(RuntimeError):
        run(path, [ASSET], scheduled=True, now=NOW, fetcher=fetcher, sender=broken)
    first = storage.reports(path)[0]["id"]
    assert first == 2  # Legacy rule must not suppress the first new index check.
    report_id = run(
        path,
        [ASSET],
        scheduled=True,
        now=NOW,
        fetcher=lambda *_: pytest.fail("must not re-fetch"),
        sender=lambda *args: calls.append(args),
    )
    assert first == report_id
    run(
        path,
        [ASSET],
        scheduled=True,
        now=NOW,
        sender=lambda *args: pytest.fail("already sent"),
    )
    assert len(calls) == 1
    assert not due(NOW.replace(hour=14, minute=34))
    assert not due(datetime(2026, 11, 21, 15, tzinfo=TZ))
    assert not due(datetime(2026, 10, 1, 15, tzinfo=TZ))
    assert due(NOW.replace(hour=16))


def test_failure_isolation_and_late_report(tmp_path):
    path = tmp_path / "history.sqlite3"
    assets = [
        ASSET,
        {**ASSET, "id": "bad"},
        {"id": "pending", "enabled": False, "reason": "待配置"},
    ]

    def mixed(asset, day):
        if asset["id"] == "bad":
            raise ValueError("invalid response")
        return sample()

    rid = run(path, assets, send=False, now=NOW.replace(hour=16), fetcher=mixed)
    report = storage.report(path, rid)
    assert report["late"]
    assert [x["status"] for x in report["items"]] == ["ok", "error", "disabled"]


def test_eastmoney_identity_real_ohlc_and_unfinished_bar_inclusion():
    asset = {"code": "931643", "secid": "2.931643", "expected_name": "科创创业50"}
    payload = {
        "data": {
            "code": "931643",
            "market": 2,
            "name": "科创创业50",
            "klines": [
                "2026-11-16,100,105,110,95,200",
                "2026-11-17,106,107,109,104,100",
            ],
        }
    }
    result = parse_eastmoney(payload, asset, "2026-11-17", now=NOW)
    assert result["rows"][0] == {
        "date": "2026-11-16",
        "open": 100,
        "close": 105,
        "high": 110,
        "low": 95,
        "volume": 200,
    }
    assert result["rows"][-1]["date"] == "2026-11-17"
    assert result["provisional_date"] == "2026-11-17"
    payload["data"]["code"] = "WRONG"
    with pytest.raises(ValueError, match="身份"):
        parse_eastmoney(payload, asset, "2026-11-17")
    with pytest.raises(ValueError, match="高低价"):
        validate_rows([{**result["rows"][0], "high": 99}])
    with pytest.raises(ValueError, match="重复"):
        validate_rows(result["rows"] * 2)


def test_yahoo_exchange_close_and_index_identity():
    from zoneinfo import ZoneInfo

    exchange = ZoneInfo("America/New_York")
    stamp = datetime(2026, 11, 16, 9, 30, tzinfo=exchange).timestamp()
    data = {
        "meta": {
            "symbol": "^SOX",
            "instrumentType": "INDEX",
            "exchangeTimezoneName": "America/New_York",
        },
        "timestamp": [stamp],
        "indicators": {
            "quote": [
                {
                    "open": [100],
                    "high": [110],
                    "low": [95],
                    "close": [105],
                    "volume": [0],
                }
            ]
        },
    }
    payload = {"chart": {"result": [data], "error": None}}
    asset = {"code": "^SOX", "label": "费城半导体"}
    assert (
        parse_yahoo(payload, asset, "2026-11-17", now=NOW)["rows"][0]["date"]
        == "2026-11-16"
    )
    intraday = parse_yahoo(
        payload, asset, "2026-11-17", now=datetime(2026, 11, 16, 15, tzinfo=exchange)
    )
    assert intraday["provisional_date"] == "2026-11-16"
    data["meta"]["instrumentType"] = "ETF"
    with pytest.raises(ValueError, match="身份"):
        parse_yahoo(payload, asset, "2026-11-17", now=NOW)


def test_web_report_chart_assets_and_decision_form(tmp_path):
    import re

    path = tmp_path / "history.sqlite3"
    rid = run(path, [ASSET], send=False, now=NOW, fetcher=fetcher)
    client = TestClient(create_app(path))
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="chart-data"' in response.text
    assert "TradingView Lightweight Charts" in response.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/vendor/lightweight-charts-5.0.9.js").status_code == 200
    token = re.search('name="csrf" value="([^"]+)"', response.text).group(1)
    form = {
        "report_id": rid,
        "asset_id": "test",
        "action": "买入",
        "execution": "已提交",
        "note": "<script>bad</script>",
        "csrf": token,
    }
    assert client.post("/decisions", data={**form, "csrf": "wrong"}).status_code == 403
    response = client.post("/decisions", data=form)
    assert response.status_code == 200
    assert "&lt;script&gt;bad&lt;/script&gt;" in response.text
    assert len(storage.decisions(path, rid)) == 1
    assert (
        client.get(f"/reports/{rid}/snapshot").json()["rule"]["version"]
        == RULE["version"]
    )
    assert client.get("/reports/99999").status_code == 404
    assert client.get("/", headers={"host": "untrusted.example"}).status_code == 400


def test_legacy_nav_report_retains_its_original_basis(tmp_path):
    path = tmp_path / "history.sqlite3"
    legacy = {
        "created_at": NOW.isoformat(),
        "day": "2026-11-17",
        "kind": "manual",
        "late": False,
        "rule": {"version": "macd-unit-nav-v1", "basis": "unit_nav"},
        "code_revision": "legacy",
        "code_sha256": "legacy",
        "items": [
            {
                "asset": {"id": "sp500", "label": "标普500", "code": "050025"},
                "status": "ok",
                "result": {
                    "nav_date": "2026-11-13",
                    "nav": 1.2,
                    "event": None,
                    "warnings": [],
                    "position": "DIF 在 DEA 下方",
                    "zero_axis": "零轴下方",
                    "momentum": "柱体缩小",
                },
            }
        ],
    }
    rid, _ = storage.save_report(path, legacy)
    response = TestClient(create_app(path)).get(f"/reports/{rid}")
    assert response.status_code == 200
    assert "历史基金净值报告" in response.text
    assert storage.report(path, rid)["rule"]["basis"] == "unit_nav"


def test_scheduled_retry_only_failed_assets_preserves_times_and_history(tmp_path):
    path = tmp_path / "retry.sqlite3"
    assets = [ASSET, {**ASSET, "id": "flaky"}]
    calls = []
    failed_once = False

    def flaky(asset, day):
        nonlocal failed_once
        calls.append(asset["id"])
        if asset["id"] == "flaky" and not failed_once:
            failed_once = True
            raise ValueError("temporary outage")
        return sample()

    first = run(path, assets, scheduled=True, send=False, now=NOW, fetcher=flaky)
    original = storage.report(path, first)
    storage.add_decision(
        path, first, "test", "持有", "未执行", "before retry", NOW.isoformat()
    )
    # No source requests during the cooldown.
    assert (
        run(
            path,
            assets,
            scheduled=True,
            send=False,
            now=NOW + timedelta(minutes=4),
            fetcher=flaky,
        )
        == first
    )
    second = run(
        path,
        assets,
        scheduled=True,
        send=False,
        now=NOW + timedelta(minutes=5),
        fetcher=flaky,
    )
    updated = storage.report(path, second)
    assert calls == ["test", "flaky", "flaky"]
    assert updated["attempt"] == 2 and updated["parent_report_id"] == first
    good, recovered = updated["items"]
    assert good["carried_forward"] and good["origin_report_id"] == first
    assert good["snapshot"]["fetched_at"] == NOW.isoformat()
    assert good["checked_at"] == NOW.isoformat()
    assert not good["result"]["new_event"]
    assert recovered["status"] == "ok" and not recovered["carried_forward"]
    assert (
        recovered["snapshot"]["fetched_at"] == (NOW + timedelta(minutes=5)).isoformat()
    )
    assert recovered["result"]["new_event"]
    assert storage.report(path, first) == original
    assert len(storage.decisions(path, first)) == 1
    assert (
        run(
            path,
            assets,
            scheduled=True,
            send=False,
            now=NOW + timedelta(minutes=10),
            fetcher=lambda *_: pytest.fail("already recovered"),
        )
        == second
    )
    page = TestClient(create_app(path)).get(f"/reports/{second}")
    assert page.status_code == 200
    assert "数据沿用" in page.text and f"来自报告 #{first}" in page.text
    assert "成功采集时间" in page.text and "第 2/3 次尝试" in page.text


def test_scheduled_retry_cap_cutoff_and_late_initial_check(tmp_path):
    def broken(*args):
        raise ValueError("offline")

    path = tmp_path / "cap.sqlite3"
    ids = [
        run(
            path,
            [ASSET],
            scheduled=True,
            send=False,
            now=NOW + timedelta(minutes=offset),
            fetcher=broken,
        )
        for offset in (0, 5, 10)
    ]
    assert len(set(ids)) == 3
    latest = storage.report(path, ids[-1])
    assert latest["attempt"] == 3 and latest["parent_report_id"] == ids[-2]
    assert (
        run(
            path,
            [ASSET],
            scheduled=True,
            send=False,
            now=NOW + timedelta(minutes=15),
            fetcher=lambda *_: pytest.fail("capped"),
        )
        == ids[-1]
    )
    cutoff_path = tmp_path / "cutoff.sqlite3"
    first = run(
        cutoff_path,
        [ASSET],
        scheduled=True,
        send=False,
        now=NOW.replace(hour=14, minute=56),
        fetcher=broken,
    )
    assert (
        run(
            cutoff_path,
            [ASSET],
            scheduled=True,
            send=False,
            now=NOW.replace(hour=15, minute=1),
            fetcher=lambda *_: pytest.fail("past cutoff"),
        )
        == first
    )
    late_path = tmp_path / "late.sqlite3"
    late = run(
        late_path,
        [ASSET],
        scheduled=True,
        send=False,
        now=NOW.replace(hour=16),
        fetcher=broken,
    )
    assert storage.report(late_path, late)["late"]
    assert (
        run(
            late_path,
            [ASSET],
            scheduled=True,
            send=False,
            now=NOW.replace(hour=16, minute=40),
            fetcher=lambda *_: pytest.fail("no late retries"),
        )
        == late
    )


def test_shared_lock_blocks_web_cli_and_releases_after_failure(tmp_path, monkeypatch):
    from chance_track.runner import CheckInProgress, check_lock, is_running

    path = tmp_path / "locked.sqlite3"
    with check_lock(path):
        assert is_running(path)
        with pytest.raises(CheckInProgress):
            run(path, [ASSET], send=False, now=NOW, fetcher=fetcher)
    assert not is_running(path)

    def fail_save(*args):
        raise RuntimeError("disk error")

    monkeypatch.setattr(storage, "save_report", fail_save)
    with pytest.raises(RuntimeError, match="disk error"):
        run(path, [ASSET], send=False, now=NOW, fetcher=fetcher)
    assert not is_running(path)


def test_refresh_endpoint_csrf_busy_status_and_empty_dashboard(tmp_path, monkeypatch):
    import re

    from chance_track import web
    from chance_track.runner import check_lock

    path = tmp_path / "web-refresh.sqlite3"
    client = TestClient(create_app(path))
    page = client.get("/")
    assert page.status_code == 200 and 'id="refresh-form"' in page.text
    assert client.get("/api/update-status").json() == {"running": False, "latest": None}
    token = re.search('name="csrf" value="([^"]+)"', page.text).group(1)
    calls = []

    def refresh_stub(db, **kwargs):
        calls.append(kwargs)
        return run(db, [ASSET], send=False, now=NOW, fetcher=fetcher)

    monkeypatch.setattr(web, "run", refresh_stub)
    assert client.post("/refresh", data={"csrf": "wrong"}).status_code == 403
    assert not calls
    response = client.post(
        "/refresh", data={"csrf": token}, headers={"accept": "application/json"}
    )
    assert response.status_code == 200 and response.json() == {
        "report_id": 1,
        "failures": 0,
    }
    assert calls == [{"send": False}]
    status = client.get("/api/update-status")
    assert status.headers["cache-control"] == "no-store"
    assert status.json()["latest"]["id"] == 1
    with check_lock(path):
        assert client.get("/api/update-status").json()["running"]
        assert client.post("/refresh", data={"csrf": token}).status_code == 409
    response = client.post("/refresh", data={"csrf": token}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/reports/2"
    assert storage.report(path, 1)["id"] == 1
