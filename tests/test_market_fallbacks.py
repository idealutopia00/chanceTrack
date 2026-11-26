import asyncio
import json
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from chance_track import cli, data, storage
from chance_track.analysis import analyze
from chance_track.configuration import (
    legacy_etf_targets,
    load_configuration,
    migrate_etf_targets,
    save_configuration,
    validate_configuration,
)
from chance_track.data import (
    IdentityMismatch,
    InsufficientMarketHistory,
    MarketFetchError,
    TZ,
    parse_eastmoney,
    parse_sse,
    parse_szse,
)
from chance_track.market_sessions import cn_trading_day
from chance_track.presentation import fetch_state_summary
from chance_track.runner import run
from chance_track.strategies import evaluate as evaluate_strategies
from chance_track.web import create_app

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _reset_cooldowns():
    with data._cooldown_lock:
        data._cooldowns.clear()


def _trading_days(count=140, end_day="2026-11-25"):
    days = []
    cursor = date.fromisoformat(end_day)
    while len(days) < count:
        if cn_trading_day(cursor):
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    days.reverse()
    return days


def _sample_series(count=140, end_day="2026-11-25", cross_on_last=False):
    days = _trading_days(count, end_day)
    bars = []
    for index, day in enumerate(days):
        if cross_on_last:
            if index < count - 36:
                close = round(1.0 + index * 0.002, 4)
            elif index < count - 1:
                close = round(1.0 + (count - 36) * 0.002 - (index - (count - 36)) * 0.005, 4)
            else:
                close = round(1.45, 4)
        else:
            close = round(7.4 + (index % 7) * 0.03 + index * 0.001, 4)
        opening = round(close - 0.01, 4)
        high = round(close + 0.03, 4)
        low = round(close - 0.03, 4)
        bars.append((day, opening, high, low, close, 125000))
    return bars


def _etf_asset(
    asset_id="gold_etf_518800",
    *,
    code="518800",
    secid="1.518800",
    expected_name="黄金ETF国泰",
    fallback_provider="sse",
    fallback_name="黄金基金",
    fallbacks=None,
    strategies=None,
):
    return {
        "id": asset_id,
        "label": expected_name,
        "ticker": code,
        "code": code,
        "secid": secid,
        "provider": "eastmoney",
        "expected_name": expected_name,
        "kind": "etf",
        "group": "黄金",
        "market": "中国 ETF",
        "currency": "元",
        "enabled": True,
        "archived": False,
        "session_profile": "cn_equity",
        "strategies": list(strategies if strategies is not None else ["macd"]),
        "fallbacks": (
            list(fallbacks)
            if fallbacks is not None
            else [
                {
                    "provider": fallback_provider,
                    "code": code,
                    "expected_name": fallback_name,
                }
            ]
        ),
    }


def _eastmoney_payload(code, name, market, bars):
    return {
        "data": {
            "code": code,
            "market": market,
            "name": name,
            "klines": [
                f"{day},{op},{close},{high},{low},{vol}"
                for day, op, high, low, close, vol in bars
            ],
        }
    }


def _sse_payloads(code, name, dayk_bars, snap_bar=None, snap_time="162900"):
    dayk = {
        "code": code,
        "kline": [
            [int(day.replace("-", "")), op, high, low, close, vol * 100]
            for day, op, high, low, close, vol in dayk_bars
        ],
    }
    ref = snap_bar or dayk_bars[-1]
    snap = {
        "code": code,
        "date": int(ref[0].replace("-", "")),
        "time": snap_time,
        "snap": [name, 0, ref[1], ref[2], ref[3], ref[4], 0, 0, ref[5] * 100],
    }
    return dayk, snap


def _szse_payloads(code, name, hist_bars, time_bar=None, market_time="15:30:00"):
    hist = {
        "data": {
            "code": code,
            "name": name,
            "picupdata": [
                [day, str(op), str(close), str(low), str(high), "0", "0", vol, 0]
                for day, op, high, low, close, vol in hist_bars
            ],
        }
    }
    time_payload = None
    if time_bar is not None:
        time_payload = {
            "data": {
                "code": code,
                "name": name,
                "marketTime": f"{time_bar[0]} {market_time}",
                "open": time_bar[1],
                "high": time_bar[2],
                "low": time_bar[3],
                "now": time_bar[4],
                "volume": time_bar[5],
            }
        }
    return hist, time_payload


def _patch_http(monkeypatch, handler):
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, headers=None):
            result = handler(url, params or {}, headers or {}, self.kwargs)
            if asyncio.iscoroutine(result):
                return await result
            return result

    monkeypatch.setattr(data.httpx, "AsyncClient", FakeAsyncClient)


def test_watchlist_etf_targets_and_validation_rules():
    watchlist = json.loads(
        (Path(__file__).resolve().parent.parent / "watchlist.json").read_text()
    )
    by_id = {entry["id"]: entry for entry in watchlist}
    assert {"gold_etf_518800", "star_chinext50_etf_159603"}.issubset(by_id)
    assert by_id["gold_etf_518800"]["provider"] == "eastmoney"
    assert by_id["gold_etf_518800"]["fallbacks"] == [
        {"provider": "sse", "code": "518800", "expected_name": "黄金基金"}
    ]
    assert by_id["star_chinext50_etf_159603"]["provider"] == "eastmoney"
    assert by_id["star_chinext50_etf_159603"]["fallbacks"] == [
        {"provider": "szse", "code": "159603", "expected_name": "科创创业ETF天弘"}
    ]
    assert "symbol" not in json.dumps(watchlist)
    for bad_provider, extra in (
        ("tencent", {"symbol": "sh518800"}),
        ("sina", {"symbol": "sh518800"}),
    ):
        with pytest.raises(ValueError, match="未知数据源"):
            validate_configuration(
                {
                    "assets": [
                        _etf_asset(
                            fallbacks=[
                                {
                                    "provider": bad_provider,
                                    "code": "518800",
                                    "expected_name": "黄金ETF国泰",
                                    **extra,
                                }
                            ]
                        )
                    ],
                    "strategies": [],
                }
            )


def test_b1_fallback_cannot_override_code_or_exchange(monkeypatch):
    _reset_cooldowns()
    with pytest.raises(ValueError, match="标的代码"):
        validate_configuration(
            {
                "assets": [
                    _etf_asset(
                        fallbacks=[
                            {
                                "provider": "sse",
                                "code": "515450",
                                "expected_name": "红利50",
                            }
                        ]
                    )
                ],
                "strategies": [],
            }
        )
    with pytest.raises(ValueError, match="交易所"):
        validate_configuration(
            {
                "assets": [
                    _etf_asset(
                        fallbacks=[
                            {
                                "provider": "szse",
                                "code": "518800",
                                "expected_name": "黄金基金",
                            }
                        ]
                    )
                ],
                "strategies": [],
            }
        )

    asset = _etf_asset(
        fallbacks=[
            {"provider": "sse", "code": "515450", "expected_name": "红利50"}
        ]
    )
    requested_urls = []

    def handler(url, params, headers, kwargs):
        requested_urls.append(url)
        return httpx.Response(503, request=httpx.Request("GET", url))

    _patch_http(monkeypatch, handler)
    now = datetime(2026, 11, 25, 16, 0, tzinfo=TZ)
    with pytest.raises(MarketFetchError) as exc_info:
        data.fetch(asset, "2026-11-25", now=now)
    assert len(requested_urls) == 1 and "push2his.eastmoney.com" in requested_urls[0]
    assert [attempt["status"] for attempt in exc_info.value.attempts] == [
        "error",
        "identity_mismatch",
    ]


def test_b2_intraday_snapshot_quote_time_blocks_false_close_event(monkeypatch):
    _reset_cooldowns()
    bars = _sample_series(140, "2026-11-25", cross_on_last=True)
    hist_bars = bars[:-1]
    today_bar = bars[-1]

    sse_dayk, sse_snap_1435 = _sse_payloads(
        "518800", "黄金基金", hist_bars, snap_bar=today_bar, snap_time="143500"
    )
    asset_518800 = _etf_asset()
    sse_spec = {**asset_518800, "provider": "sse", "expected_name": "黄金基金"}
    at_1436 = datetime(2026, 11, 25, 14, 36, tzinfo=TZ)
    at_1506 = datetime(2026, 11, 25, 15, 6, tzinfo=TZ)

    direct_1436 = parse_sse(sse_dayk, sse_snap_1435, sse_spec, "2026-11-25", now=at_1436)
    assert direct_1436["provisional_date"] == "2026-11-25"

    direct_at_1506 = parse_sse(sse_dayk, sse_snap_1435, sse_spec, "2026-11-25", now=at_1506)
    assert direct_at_1506["provisional_date"] == "2026-11-25"
    analyzed = analyze(direct_at_1506, "2026-11-25")
    assert analyzed["is_provisional"] is True
    assert analyzed["event"] is None

    szse_hist, szse_time_1435 = _szse_payloads(
        "159603",
        "科创创业ETF天弘",
        hist_bars,
        time_bar=today_bar,
        market_time="14:35:00",
    )
    asset_159603 = _etf_asset(
        asset_id="star_chinext50_etf_159603",
        code="159603",
        secid="0.159603",
        expected_name="科创创业ETF天弘",
        fallback_provider="szse",
        fallback_name="科创创业ETF天弘",
    )
    direct_szse_1506 = parse_szse(
        szse_hist, asset_159603, "2026-11-25", now=at_1506, time_payload=szse_time_1435
    )
    assert direct_szse_1506["provisional_date"] == "2026-11-25"

    # Missing/date-only timestamp must not append snap bar for today
    _, szse_time_notime = _szse_payloads(
        "159603", "科创创业ETF天弘", hist_bars, time_bar=today_bar
    )
    szse_time_notime["data"]["marketTime"] = "2026-11-25"
    no_time_parsed = parse_szse(
        szse_hist, asset_159603, "2026-11-25", now=at_1506, time_payload=szse_time_notime
    )
    assert no_time_parsed["rows"][-1]["date"] == hist_bars[-1][0]

    # End-to-end fetch at 15:06 rejects 14:35 fallback snap as stale; at 14:36 allows provisional
    def handler(url, params, headers, kwargs):
        req = httpx.Request("GET", url, params=params)
        if "push2his.eastmoney.com" in url:
            return httpx.Response(503, request=req)
        if "dayk" in url:
            return httpx.Response(200, request=req, json=sse_dayk)
        if "snap" in url:
            return httpx.Response(200, request=req, json=sse_snap_1435)
        raise AssertionError(url)

    _patch_http(monkeypatch, handler)
    provisional_snap = data.fetch(asset_518800, "2026-11-25", now=at_1436)
    assert provisional_snap["provisional_date"] == "2026-11-25"

    with pytest.raises(MarketFetchError) as stale_exc:
        data.fetch(asset_518800, "2026-11-25", now=at_1506)
    assert stale_exc.value.fetch_status == "stale"


def test_b3_existing_install_migration_is_repeatable_and_preserves_history(tmp_path, monkeypatch):
    db_path = tmp_path / "existing.sqlite3"
    storage.initialize(db_path)
    now = datetime(2026, 11, 25, 15, 6, tzinfo=TZ)
    legacy_config = {
        "assets": [
            {
                "id": "sp500",
                "label": "标普500",
                "ticker": "SPX",
                "code": "^GSPC",
                "provider": "yahoo",
                "kind": "index",
                "market": "美国",
                "currency": "点",
                "enabled": True,
                "archived": False,
                "session_profile": "us_equity",
                "strategies": ["macd"],
            },
            {
                "id": "gold",
                "label": "黄金",
                "ticker": "Au99.99",
                "code": "AU9999",
                "secid": "118.AU9999",
                "expected_name": "Au9999",
                "provider": "eastmoney",
                "kind": "spot",
                "market": "上金所",
                "currency": "元/克",
                "enabled": True,
                "archived": False,
                "watchlisted": True,
                "priority": 3,
                "session_profile": "sge_gold",
                "strategies": ["macd", "ma"],
            },
            {
                "id": "star_chinext50",
                "label": "科创创业50",
                "ticker": "931643",
                "code": "931643",
                "secid": "2.931643",
                "expected_name": "科创创业50",
                "provider": "eastmoney",
                "kind": "index",
                "market": "中证",
                "currency": "点",
                "enabled": True,
                "archived": False,
                "watchlisted": False,
                "priority": 7,
                "session_profile": "cn_equity",
                "strategies": ["boll_break"],
            },
        ],
        "strategies": [
            {"id": "macd", "name": "MACD 默认", "kind": "macd", "group": "趋势", "params": {"fast": 12, "slow": 26, "signal": 9}},
            {"id": "ma", "name": "双均线 20/60", "kind": "ma", "group": "趋势", "params": {"fast": 20, "slow": 60}},
            {"id": "boll_break", "name": "布林突破", "kind": "boll_break", "group": "布林带", "params": {"period": 20, "stddev": 2}},
        ],
    }
    with storage.connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        from chance_track import configuration_store

        cursor = db.execute(
            "INSERT INTO configuration_versions(created_at, payload) VALUES(?, ?)",
            (now.isoformat(), json.dumps(legacy_config, ensure_ascii=False)),
        )
        configuration_store.write_current(db, legacy_config, cursor.lastrowid)

    legacy_bars = _sample_series(140, "2026-11-25", cross_on_last=True)

    def legacy_fetcher(asset, day):
        return {
            "rows": [
                {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}
                for d, o, h, l, c, v in legacy_bars
            ],
            "source": "fixture",
            "name": asset["label"],
            "code": asset["code"],
            "basis": "market_ohlc",
            "provider": asset["provider"],
        }

    report_id = run(
        db_path,
        [legacy_config["assets"][1]],
        send=False,
        now=now,
        fetcher=legacy_fetcher,
    )
    storage.add_decision(
        db_path,
        report_id,
        "gold",
        "买入",
        "未执行",
        "历史黄金记录",
        now.isoformat(),
    )

    loaded = load_configuration(db_path)
    assert [a["code"] for a in loaded["assets"]] == ["^GSPC", "AU9999", "931643"]
    assert len(legacy_etf_targets(loaded)) == 2

    client = TestClient(create_app(db_path))
    manage_html = client.get("/manage").text
    assert "migrate-etf-targets-button" in manage_html
    assert "AU9999" in manage_html

    result = migrate_etf_targets(db_path)
    assert result["changed"] is True
    assert [(m["from_code"], m["to_code"]) for m in result["migrated"]] == [
        ("AU9999", "518800"),
        ("931643", "159603"),
    ]
    after = load_configuration(db_path)
    by_id = {a["id"]: a for a in after["assets"]}
    assert by_id["gold"]["archived"] is True and by_id["gold"]["enabled"] is False
    assert by_id["star_chinext50"]["archived"] is True
    assert by_id["gold_etf_518800"]["strategies"] == ["macd", "ma"]
    assert by_id["gold_etf_518800"]["priority"] == 3
    assert set(by_id["gold_etf_518800"]["activated"]) == {"macd", "ma"}
    assert by_id["star_chinext50_etf_159603"]["strategies"] == ["boll_break"]
    assert by_id["star_chinext50_etf_159603"]["watchlisted"] is False

    # Backfill safety + preserved legacy reports & decisions
    etf_snapshot = {
        "rows": [
            {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}
            for d, o, h, l, c, v in legacy_bars
        ],
        "source": "sse",
        "name": "黄金基金",
        "code": "518800",
        "basis": "market_ohlc",
        "provider": "sse",
        "fetched_at": now.isoformat(),
    }
    evaluated, _, _ = evaluate_strategies(
        etf_snapshot,
        [rule for rule in after["strategies"] if rule["id"] in ("macd", "ma")],
        by_id["gold_etf_518800"],
        "2026-11-25",
    )
    assert all(
        entry["historical_reference"] is True or not entry.get("event")
        for entry in evaluated
    )
    migrated_run_id = run(
        db_path,
        [by_id["gold_etf_518800"]],
        send=False,
        now=now,
        fetcher=lambda a, d: etf_snapshot,
    )
    from chance_track.notifications import build_message

    msg_text, pending_keys = build_message(storage.report(db_path, migrated_run_id), set())
    assert not pending_keys and "新增确认提醒 0 项" in msg_text
    assert client.get(f"/reports/{report_id}").status_code == 200
    assert storage.report(db_path, report_id)["items"][0]["asset"]["code"] == "AU9999"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT note FROM decisions").fetchone()[0] == "历史黄金记录"

    # Idempotent second run & CLI/POST checks
    assert migrate_etf_targets(db_path)["changed"] is False
    monkeypatch.setattr(sys, "argv", ["chance-track", "--db", str(db_path), "migrate-etf-targets"])
    cli.main()
    assert "migrate-etf-targets-button" not in client.get("/manage").text


def test_manage_migrate_etf_targets_post_route(tmp_path):
    db_path = tmp_path / "web-migrate.sqlite3"
    storage.initialize(db_path)
    now = datetime(2026, 11, 25, 16, 0, tzinfo=TZ)
    legacy = {
        "assets": [
            {
                "id": "gold",
                "label": "黄金",
                "ticker": "Au99.99",
                "code": "AU9999",
                "secid": "118.AU9999",
                "expected_name": "Au9999",
                "provider": "eastmoney",
                "kind": "spot",
                "market": "上金所",
                "currency": "元/克",
                "enabled": True,
                "archived": False,
                "session_profile": "sge_gold",
                "strategies": ["macd"],
            }
        ],
        "strategies": [
            {"id": "macd", "name": "MACD 默认", "kind": "macd", "group": "趋势", "params": {"fast": 12, "slow": 26, "signal": 9}}
        ],
    }
    with storage.connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        from chance_track import configuration_store

        cur = db.execute(
            "INSERT INTO configuration_versions(created_at, payload) VALUES(?, ?)",
            (now.isoformat(), json.dumps(legacy, ensure_ascii=False)),
        )
        configuration_store.write_current(db, legacy, cur.lastrowid)
    client = TestClient(create_app(db_path))
    manage_html = client.get("/manage").text
    csrf_match = re.search(r'name="csrf"\s+value="([^"]+)"', manage_html)
    assert csrf_match is not None
    token = csrf_match.group(1)
    resp = client.post("/manage/migrate-etf-targets", data={"csrf": token}, follow_redirects=False)
    assert resp.status_code == 303
    by_id = {a["id"]: a for a in load_configuration(db_path)["assets"]}
    assert by_id["gold"]["archived"] is True
    assert by_id["gold_etf_518800"]["enabled"] is True


def test_b4_short_primary_and_empty_szse_picupdata_do_not_abort_chain(monkeypatch):
    _reset_cooldowns()
    short_bars = _sample_series(2, "2026-11-25")
    full_bars = _sample_series(140, "2026-11-25")
    sse_dayk, sse_snap = _sse_payloads("518800", "黄金基金", full_bars)

    def handler_short(url, params, headers, kwargs):
        req = httpx.Request("GET", url, params=params)
        if "push2his.eastmoney.com" in url:
            return httpx.Response(
                200, request=req, json=_eastmoney_payload("518800", "黄金ETF国泰", 1, short_bars)
            )
        if "dayk" in url:
            return httpx.Response(200, request=req, json=sse_dayk)
        if "snap" in url:
            return httpx.Response(200, request=req, json=sse_snap)
        raise AssertionError(url)

    _patch_http(monkeypatch, handler_short)
    snap = data.fetch(_etf_asset(), "2026-11-25", now=datetime(2026, 11, 25, 16, 0, tzinfo=TZ))
    assert snap["provider"] == "sse" and snap["fetch_status"] == "fallback_ok"
    assert [a["status"] for a in snap["attempts"]] == ["insufficient_history", "ok"]

    # SZSE malformed picupdata=[[]] must not crash with IndexError
    def handler_empty_szse(url, params, headers, kwargs):
        req = httpx.Request("GET", url, params=params)
        if "push2his.eastmoney.com" in url:
            return httpx.Response(503, request=req)
        if "getHistoryData" in url:
            return httpx.Response(
                200,
                request=req,
                json={"data": {"code": "159603", "name": "科创创业ETF天弘", "picupdata": [[]]}},
            )
        if "getTimeData" in url:
            return httpx.Response(200, request=req, json={"data": {}})
        raise AssertionError(url)

    _patch_http(monkeypatch, handler_empty_szse)
    asset_159603 = _etf_asset(
        asset_id="star_chinext50_etf_159603",
        code="159603",
        secid="0.159603",
        expected_name="科创创业ETF天弘",
        fallback_provider="szse",
        fallback_name="科创创业ETF天弘",
    )
    with pytest.raises(MarketFetchError) as exc_info:
        data.fetch(asset_159603, "2026-11-25", now=datetime(2026, 11, 25, 16, 0, tzinfo=TZ))
    assert len(exc_info.value.attempts) == 2


def test_b5_cooldown_isolation_and_rotation_tracks_real_requests(tmp_path, monkeypatch):
    _reset_cooldowns()
    clock = [100.0]
    monkeypatch.setattr(data, "monotonic", lambda: clock[0])
    monkeypatch.setattr(data, "REQUEST_RETRY_DELAYS", (0.0, 0.0))

    bars = _sample_series(140, "2026-11-25")
    sse_dayk, sse_snap = _sse_payloads("518800", "黄金基金", bars)
    szse_hist, szse_time = _szse_payloads("159603", "科创创业ETF天弘", bars)

    a1 = _etf_asset(asset_id="gold_etf_518800", code="518800", secid="1.518800")
    a2 = _etf_asset(
        asset_id="star_chinext50_etf_159603",
        code="159603",
        secid="0.159603",
        expected_name="科创创业ETF天弘",
        fallback_provider="szse",
        fallback_name="科创创业ETF天弘",
    )

    eastmoney_probes = []

    def handler(url, params, headers, kwargs):
        req = httpx.Request("GET", url, params=params)
        if "push2his.eastmoney.com" in url:
            eastmoney_probes.append((clock[0], params.get("secid")))
            raise httpx.RemoteProtocolError("Server disconnected", request=req)
        if "dayk" in url:
            return httpx.Response(200, request=req, json=sse_dayk)
        if "snap" in url:
            return httpx.Response(200, request=req, json=sse_snap)
        if "getHistoryData" in url:
            return httpx.Response(200, request=req, json=szse_hist)
        if "getTimeData" in url:
            return httpx.Response(200, request=req, json=szse_time)
        raise AssertionError(url)

    _patch_http(monkeypatch, handler)
    db_path = tmp_path / "rotation.sqlite3"
    now = datetime(2026, 11, 25, 16, 0, tzinfo=TZ)

    r1 = run(db_path, [a1, a2], send=False, now=now)
    r2 = None
    clock[0] = 115.0
    r2 = run(db_path, [a1, a2], send=False, now=now + timedelta(seconds=15))
    clock[0] = 131.0
    r3 = run(db_path, [a1, a2], send=False, now=now + timedelta(seconds=31))

    probes_by_run = {}
    for t, secid in eastmoney_probes:
        probes_by_run.setdefault(t, []).append(secid)
    assert probes_by_run[100.0] == ["1.518800", "1.518800", "1.518800"]
    assert 115.0 not in probes_by_run  # both assets skipped Eastmoney via cooldown
    assert probes_by_run[131.0] == ["0.159603", "0.159603", "0.159603"]

    rep2 = storage.report(db_path, r2)
    assert rep2["items"][0]["attempts"][0]["status"] == "cooling_down"
    assert rep2["probe_order"]["eastmoney"] == ["gold_etf_518800", "star_chinext50_etf_159603"]
    assert storage.report(db_path, r3)["probe_order"]["eastmoney"] == [
        "star_chinext50_etf_159603",
        "gold_etf_518800",
    ]


def test_b6_per_candidate_timeout_preserves_attempts_and_lets_next_run(monkeypatch):
    _reset_cooldowns()
    monkeypatch.setattr(data, "REQUEST_DEADLINE", 0.3)
    with data._cooldown_lock:
        data._cooldowns["eastmoney"] = data.monotonic() + 30

    bars = _sample_series(140, "2026-11-25")
    asset = _etf_asset(
        fallbacks=[
            {"provider": "sse", "code": "518800", "expected_name": "黄金基金"},
            {"provider": "szse", "code": "518800", "expected_name": "黄金基金"},
        ]
    )
    # Bypass exchange validation to test a 3-source multi-candidate budget cleanly
    monkeypatch.setattr(data, "_asset_exchange", lambda _spec: None)

    seen_providers = []

    async def slow_handler(url, params, headers, kwargs):
        req = httpx.Request("GET", url, params=params)
        if "yunhq.sse.com.cn" in url:
            seen_providers.append("sse")
            await asyncio.sleep(2.0)
            raise AssertionError("should timeout")
        if "szse.cn" in url:
            seen_providers.append("szse")
            if "getHistoryData" in url:
                return httpx.Response(200, request=req, json=_szse_payloads("518800", "黄金基金", bars)[0])
            return httpx.Response(200, request=req, json={"data": {}})
        raise AssertionError(url)

    _patch_http(monkeypatch, slow_handler)
    snap = data.fetch(asset, "2026-11-25", now=datetime(2026, 11, 25, 16, 0, tzinfo=TZ))
    assert snap["provider"] == "szse"
    assert [a["provider"] for a in snap["attempts"]] == ["eastmoney", "sse", "szse"]
    assert [a["status"] for a in snap["attempts"]] == ["cooling_down", "disconnected", "ok"]

    # If all fail under short budget, MarketFetchError keeps every step attempted
    with data._cooldown_lock:
        data._cooldowns["eastmoney"] = data.monotonic() + 30

    async def hang_all(url, params, headers, kwargs):
        await asyncio.sleep(2.0)
        raise AssertionError("should timeout")

    _patch_http(monkeypatch, hang_all)
    with pytest.raises(MarketFetchError) as exc_info:
        data.fetch(asset, "2026-11-25", now=datetime(2026, 11, 25, 16, 0, tzinfo=TZ))
    attempts = exc_info.value.attempts
    assert [a["provider"] for a in attempts] == ["eastmoney", "sse", "szse"]
    assert attempts[0]["status"] == "cooling_down"
    assert attempts[1]["status"] == "disconnected"
    assert "上海证券交易所" in str(exc_info.value)


def test_b7_historical_quote_provider_and_no_backfill_notifications(tmp_path, monkeypatch):
    _reset_cooldowns()
    bars = _sample_series(140, "2026-11-25", cross_on_last=True)
    sse_dayk, sse_snap = _sse_payloads("518800", "黄金基金", bars)
    asset = _etf_asset()
    missing = _etf_asset(asset_id="no_history", code="518880", secid="1.518880", expected_name="黄金ETF")

    db_path = tmp_path / "provider-history.sqlite3"
    cfg = load_configuration(db_path)
    updated_assets = [
        {**a, "enabled": False, "strategies": []}
        if a["id"] != "gold_etf_518800"
        else {**a, "enabled": True, "strategies": ["macd"]}
        for a in cfg["assets"]
    ]
    updated_assets.append(missing)
    save_configuration(
        db_path,
        {"assets": updated_assets, "strategies": cfg["strategies"]},
        cfg["version"],
        validate_new=False,
    )
    asset = next(a for a in updated_assets if a["id"] == "gold_etf_518800")
    asset["activated"] = {"macd": "2026-11-24T00:00:00+08:00"}

    fail_all = [False]

    def handler(url, params, headers, kwargs):
        req = httpx.Request("GET", url, params=params)
        if fail_all[0] or "push2his.eastmoney.com" in url:
            return httpx.Response(503, request=req)
        if params.get("code") == "518880" or "518880" in url:
            return httpx.Response(503, request=req)
        if "dayk" in url:
            return httpx.Response(200, request=req, json=sse_dayk)
        if "snap" in url:
            return httpx.Response(200, request=req, json=sse_snap)
        raise AssertionError(url)

    _patch_http(monkeypatch, handler)
    now = datetime(2026, 11, 25, 16, 0, tzinfo=TZ)
    sent = []

    def sender(title, body):
        sent.append((title, body))

    r1 = run(db_path, [asset], send=True, now=now, sender=sender)
    rep1 = storage.report(db_path, r1)
    assert rep1["items"][0]["provider"] == "sse"
    # fetched_at reflects receipt wall clock rather than explicit historical `now`
    assert date.fromisoformat(rep1["items"][0]["snapshot"]["fetched_at"][:10]) >= date(
        2026, 11, 25
    )
    assert "新增确认提醒 1 项" in sent[0][1]
    assert "首次提醒" in sent[0][1]
    notified_after_r1 = storage.notified_event_keys(db_path)
    assert len(notified_after_r1) == 1

    # Re-running with Eastmoney back up returns primary_ok without duplicating notification
    def recover_handler(url, params, headers, kwargs):
        req = httpx.Request("GET", url, params=params)
        if "push2his.eastmoney.com" in url:
            return httpx.Response(
                200, request=req, json=_eastmoney_payload("518800", "黄金ETF国泰", 1, bars)
            )
        raise AssertionError(url)

    # First check error state while sse was last successful quote
    fail_all[0] = True
    r2 = run(db_path, [asset, missing], send=True, now=now + timedelta(minutes=2), sender=sender)
    rep2 = storage.report(db_path, r2)
    err_asset, err_missing = rep2["items"]
    assert err_asset["last_quote"]["provider"] == "sse"
    summary = fetch_state_summary(err_asset)
    assert summary["provider_label"] == "上海证券交易所"
    assert fetch_state_summary(err_missing)["provider_label"] == "暂无行情"

    client = TestClient(create_app(db_path))
    html = client.get(f"/reports/{r2}").text
    assert "上海证券交易所" in html

    # Recover via eastmoney after sse -> same event key is already notified, zero duplicate alert
    fail_all[0] = False
    _patch_http(monkeypatch, recover_handler)
    r3 = run(db_path, [asset], send=True, now=now + timedelta(minutes=5), sender=sender)
    assert storage.report(db_path, r3)["items"][0]["provider"] == "eastmoney"
    assert "新增确认提醒 0 项" in sent[-1][1]
    assert "已确认 · 已提醒" in sent[-1][1]
    assert storage.notified_event_keys(db_path) == notified_after_r1


def test_close_only_fallback_skipped_intraday(monkeypatch):
    _reset_cooldowns()
    bars = _sample_series(140, "2026-11-25")
    sse_dayk, sse_snap = _sse_payloads("518800", "黄金基金", bars, snap_time="143500")
    asset = _etf_asset(
        fallbacks=[
            {
                "provider": "sse",
                "code": "518800",
                "expected_name": "黄金基金",
                "mode": "close_only",
            }
        ]
    )

    def handler(url, params, headers, kwargs):
        req = httpx.Request("GET", url, params=params)
        if "push2his.eastmoney.com" in url:
            return httpx.Response(503, request=req)
        if "dayk" in url:
            return httpx.Response(200, request=req, json=sse_dayk)
        if "snap" in url:
            return httpx.Response(200, request=req, json=sse_snap)
        raise AssertionError(url)

    _patch_http(monkeypatch, handler)
    with pytest.raises(MarketFetchError) as exc_info:
        data.fetch(asset, "2026-11-25", now=datetime(2026, 11, 25, 14, 36, tzinfo=TZ))
    assert exc_info.value.fetch_status == "stale"
    assert [a["status"] for a in exc_info.value.attempts] == ["error", "stale"]
