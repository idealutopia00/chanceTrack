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
    assert by_id["gold_etf_518800"]["provider"] == "sse"
    assert by_id["gold_etf_518800"]["expected_name"] == "黄金基金"
    assert by_id["gold_etf_518800"]["fallbacks"] == []
    assert by_id["star_chinext50_etf_159603"]["provider"] == "szse"
    assert by_id["star_chinext50_etf_159603"]["expected_name"] == "科创创业ETF天弘"
    assert by_id["star_chinext50_etf_159603"]["fallbacks"] == []
    assert "eastmoney" not in json.dumps(
        [by_id["gold_etf_518800"], by_id["star_chinext50_etf_159603"]]
    )
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
    snap = data.fetch(
        _etf_asset(), "2026-11-26", now=datetime(2026, 11, 26, 9, 0, tzinfo=TZ)
    )
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
    szse_hist, szse_time = _szse_payloads(
        "159603", "科创创业ETF天弘", bars, time_bar=bars[-1], market_time="15:30:00"
    )

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
    monkeypatch.setattr(data, "_asset_exchange", lambda _spec: "sh")

    seen_providers = []
    szse_hist_518800, szse_time_518800 = _szse_payloads(
        "518800", "黄金基金", bars, time_bar=bars[-1], market_time="15:30:00"
    )

    async def slow_handler(url, params, headers, kwargs):
        req = httpx.Request("GET", url, params=params)
        if "yunhq.sse.com.cn" in url:
            seen_providers.append("sse")
            await asyncio.sleep(2.0)
            raise AssertionError("should timeout")
        if "szse.cn" in url:
            seen_providers.append("szse")
            if "getHistoryData" in url:
                return httpx.Response(200, request=req, json=szse_hist_518800)
            return httpx.Response(200, request=req, json=szse_time_518800)
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
    dual = _etf_asset(asset_id="etf_dual")
    missing = _etf_asset(asset_id="no_history", code="518880", secid="1.518880", expected_name="黄金ETF")

    db_path = tmp_path / "provider-history.sqlite3"
    cfg = load_configuration(db_path)
    updated_assets = [
        {**a, "enabled": False, "strategies": []}
        for a in cfg["assets"]
    ]
    updated_assets.extend([dual, missing])
    save_configuration(
        db_path,
        {"assets": updated_assets, "strategies": cfg["strategies"]},
        cfg["version"],
        validate_new=False,
    )
    asset = next(a for a in updated_assets if a["id"] == "etf_dual")
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

    # Re-running with Eastmoney back up next morning (2026-11-25 already settled)
    # returns primary_ok without duplicating notification and preserves ETF volume_unit
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
    next_morning = datetime(2026, 11, 26, 9, 0, tzinfo=TZ)
    r3 = run(db_path, [asset], send=True, now=next_morning, sender=sender)
    rep3_item = storage.report(db_path, r3)["items"][0]
    assert rep3_item["provider"] == "eastmoney"
    assert rep3_item["snapshot"]["volume_unit"] == "手"
    assert rep3_item["result"]["chart"]["volume_unit"] == "手"
    assert rep3_item["result"]["chart"]["candles"][-1]["volume"] == 125000
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


def test_same_day_etf_paths_require_closing_evidence_and_single_observation(tmp_path, monkeypatch):
    _reset_cooldowns()
    bars = _sample_series(140, "2026-11-25", cross_on_last=True)
    at_1506 = datetime(2026, 11, 25, 15, 6, tzinfo=TZ)

    # 1) Eastmoney ETF has no per-bar closing timestamp -> stays provisional at 15:06, zero confirmed notifications
    em_asset = _etf_asset(fallbacks=[])
    em_payload = _eastmoney_payload("518800", "黄金ETF国泰", 1, bars)
    em_snap = parse_eastmoney(em_payload, em_asset, "2026-11-25", now=at_1506)
    assert em_snap["provisional_date"] == "2026-11-25"
    assert em_snap["session"]["closing_evidence_missing"] is True
    assert em_snap["volume_unit"] == "手"
    with pytest.raises(data.StaleMarketSnapshot):
        data.verify_session_freshness(em_snap, em_asset, "2026-11-25", now=at_1506)
    db_em = tmp_path / "em-unconfirmed.sqlite3"
    run(db_em, [em_asset], send=True, now=at_1506, fetcher=lambda *_: em_snap, sender=lambda *_: None)
    assert storage.notified_event_keys(db_em) == set()

    # 2) SZSE history already containing today MUST still fetch getTimeData; replayed 10:16 bar cannot confirm at 15:06
    sz_asset = {
        "id": "star_chinext50_etf_159603",
        "label": "科创创业ETF天弘",
        "ticker": "159603",
        "code": "159603",
        "provider": "szse",
        "expected_name": "科创创业ETF天弘",
        "kind": "etf",
        "enabled": True,
        "archived": False,
        "session_profile": "cn_equity",
        "strategies": ["macd"],
        "fallbacks": [],
    }
    sz_hist, sz_time_1016 = _szse_payloads(
        "159603", "科创创业ETF天弘", bars, time_bar=bars[-1], market_time="10:16:00"
    )
    # Without getTimeData at 15:06 -> stays provisional and fails freshness
    sz_no_time = parse_szse(sz_hist, sz_asset, "2026-11-25", now=at_1506, time_payload=None)
    assert sz_no_time["provisional_date"] == "2026-11-25"
    assert sz_no_time["session"]["closing_evidence_missing"] is True
    db_sz = tmp_path / "sz-unconfirmed.sqlite3"
    run(db_sz, [sz_asset], send=True, now=at_1506, fetcher=lambda *_: sz_no_time, sender=lambda *_: None)
    assert storage.notified_event_keys(db_sz) == set()

    # End-to-end SZSE fetch always calls getTimeData even when picupdata already has today
    requested = []

    def sz_handler(url, params, headers, kwargs):
        requested.append(url)
        req = httpx.Request("GET", url, params=params)
        if "getHistoryData" in url:
            return httpx.Response(200, request=req, json=sz_hist)
        if "getTimeData" in url:
            return httpx.Response(200, request=req, json=sz_time_1016)
        raise AssertionError(url)

    _patch_http(monkeypatch, sz_handler)
    with pytest.raises(MarketFetchError) as sz_exc:
        data.fetch(sz_asset, "2026-11-25", now=at_1506)
    assert sz_exc.value.fetch_status == "stale"
    assert any("getTimeData" in url for url in requested)

    # 3) SSE: dayk close 1.450 (cross) vs 15:00 snap close 1.038 (no cross) -> uses snap OHLC + snap time together
    non_cross_bar = ("2026-11-25", 1.03, 1.05, 1.02, 1.038, 125000)
    sse_dayk, sse_snap = _sse_payloads(
        "518800", "黄金基金", bars, snap_bar=non_cross_bar, snap_time="150000"
    )
    sse_asset = {
        "id": "gold_etf_518800",
        "label": "黄金ETF国泰",
        "ticker": "518800",
        "code": "518800",
        "provider": "sse",
        "expected_name": "黄金基金",
        "kind": "etf",
        "enabled": True,
        "archived": False,
        "session_profile": "cn_equity",
        "strategies": ["macd"],
        "fallbacks": [],
    }
    sse_parsed = parse_sse(sse_dayk, sse_snap, sse_asset, "2026-11-25", now=at_1506)
    assert sse_parsed["rows"][-1]["close"] == pytest.approx(1.038)
    assert sse_parsed["provisional_date"] is None
    assert analyze(sse_parsed, "2026-11-25")["event"] is None
    db_sse = tmp_path / "sse-consistent.sqlite3"
    run(db_sse, [sse_asset], send=True, now=at_1506, fetcher=lambda *_: sse_parsed, sender=lambda *_: None)
    assert storage.notified_event_keys(db_sse) == set()


def test_unknown_exchange_rejected_in_validation_and_runtime(monkeypatch):
    _reset_cooldowns()
    bad_asset = _etf_asset(
        code="518800",
        secid="2.518800",
        fallbacks=[{"provider": "sse", "code": "518800", "expected_name": "黄金基金"}],
    )
    with pytest.raises(ValueError, match="交易所"):
        validate_configuration({"assets": [bad_asset], "strategies": []})

    # Even if primary would return HTTP 200 with 140 bars, runtime must reject BEFORE any request
    bars = _sample_series(140, "2026-11-25")
    requested = []

    def handler(url, params, headers, kwargs):
        requested.append(url)
        req = httpx.Request("GET", url, params=params)
        return httpx.Response(
            200, request=req, json=_eastmoney_payload("518800", "黄金ETF国泰", 2, bars)
        )

    _patch_http(monkeypatch, handler)
    with pytest.raises(MarketFetchError) as exc_info:
        data.fetch(bad_asset, "2026-11-25", now=datetime(2026, 11, 25, 10, 16, tzinfo=TZ))
    assert requested == []
    assert [a["status"] for a in exc_info.value.attempts] == [
        "identity_mismatch",
        "identity_mismatch",
    ]


def test_migration_verifies_instrument_reconciles_fallbacks_and_preserves_priority(tmp_path, monkeypatch):
    now = datetime(2026, 11, 25, 16, 0, tzinfo=TZ)
    base_strategies = [
        {"id": "macd", "name": "MACD 默认", "kind": "macd", "group": "趋势", "params": {"fast": 12, "slow": 26, "signal": 9}}
    ]
    legacy_gold = {
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

    def seed_db(name, assets):
        db_path = tmp_path / name
        storage.initialize(db_path)
        payload = {"assets": assets, "strategies": base_strategies}
        with storage.connect(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            from chance_track import configuration_store

            cur = db.execute(
                "INSERT INTO configuration_versions(created_at, payload) VALUES(?, ?)",
                (now.isoformat(), json.dumps(payload, ensure_ascii=False)),
            )
            configuration_store.write_current(db, payload, cur.lastrowid)
        return db_path

    # Case 1: Pre-existing gold_etf_518800 holding a different instrument (515450) -> refused, legacy kept active
    db_conflict = seed_db(
        "conflict.sqlite3",
        [
            legacy_gold,
            {
                "id": "gold_etf_518800",
                "label": "误占ID",
                "code": "515450",
                "provider": "sse",
                "expected_name": "红利50",
                "kind": "etf",
                "enabled": True,
                "archived": False,
                "session_profile": "cn_equity",
                "strategies": [],
            },
        ],
    )
    res_conflict = migrate_etf_targets(db_conflict)
    assert res_conflict["changed"] is False
    assert len(res_conflict["skipped"]) == 1
    after_conflict = {a["id"]: a for a in load_configuration(db_conflict)["assets"]}
    assert after_conflict["gold"]["enabled"] is True and after_conflict["gold"]["archived"] is False
    assert after_conflict["gold_etf_518800"]["code"] == "515450"

    # Case 2: Base-version 518800 saved with kind="index" on eastmoney & empty fallbacks -> normalizes kind="etf",
    # reconciles sse fallback, and blocks timestamp-free eastmoney close at 15:06 so fetch falls back to sse
    db_reconcile = seed_db(
        "reconcile.sqlite3",
        [
            legacy_gold,
            {
                "id": "gold_etf_518800",
                "label": "黄金ETF国泰",
                "code": "518800",
                "secid": "1.518800",
                "provider": "eastmoney",
                "expected_name": "黄金ETF国泰",
                "kind": "index",
                "enabled": True,
                "archived": False,
                "session_profile": "cn_equity",
                "strategies": [],
                "fallbacks": [],
            },
        ],
    )
    res_reconcile = migrate_etf_targets(db_reconcile)
    assert res_reconcile["changed"] is True
    after_reconcile = {a["id"]: a for a in load_configuration(db_reconcile)["assets"]}
    assert after_reconcile["gold"]["archived"] is True
    reconciled_518800 = after_reconcile["gold_etf_518800"]
    assert reconciled_518800["kind"] == "etf"
    assert reconciled_518800["fallbacks"] == [
        {"provider": "sse", "code": "518800", "expected_name": "黄金基金"}
    ]
    assert "priority" not in reconciled_518800

    bars = _sample_series(140, "2026-11-25")
    sse_dayk, sse_snap = _sse_payloads("518800", "黄金基金", bars, snap_time="150000")

    def reconcile_handler(url, params, headers, kwargs):
        req = httpx.Request("GET", url, params=params)
        if "push2his.eastmoney.com" in url:
            return httpx.Response(
                200, request=req, json=_eastmoney_payload("518800", "黄金ETF国泰", 1, bars)
            )
        if "dayk" in url:
            return httpx.Response(200, request=req, json=sse_dayk)
        if "snap" in url:
            return httpx.Response(200, request=req, json=sse_snap)
        raise AssertionError(url)

    _patch_http(monkeypatch, reconcile_handler)
    fetched = data.fetch(
        reconciled_518800, "2026-11-25", now=datetime(2026, 11, 25, 15, 6, tzinfo=TZ)
    )
    assert fetched["provider"] == "sse" and fetched["fetch_status"] == "fallback_ok"
    assert [a["status"] for a in fetched["attempts"]] == ["stale", "ok"]

    # Case 3: Manually added 518800 under custom ID -> reused, no duplicate monitor, absent priority stays absent
    db_manual = seed_db(
        "manual.sqlite3",
        [
            legacy_gold,
            {
                "id": "asset_manual_518800",
                "label": "手动黄金ETF",
                "code": "518800",
                "provider": "sse",
                "expected_name": "黄金基金",
                "kind": "index",
                "enabled": True,
                "archived": False,
                "session_profile": "cn_equity",
                "strategies": [],
            },
        ],
    )
    res_manual = migrate_etf_targets(db_manual)
    assert res_manual["changed"] is True
    after_manual = load_configuration(db_manual)["assets"]
    enabled_518800 = [a for a in after_manual if a["code"] == "518800" and a["enabled"] and not a["archived"]]
    assert [a["id"] for a in enabled_518800] == ["asset_manual_518800"]
    assert enabled_518800[0]["kind"] == "etf"
    assert enabled_518800[0]["strategies"] == ["macd"]
    assert "priority" not in enabled_518800[0]

    # Case 4: Partial migration via POST /manage/migrate-etf-targets reports skip reason instead of discarding it
    db_partial = seed_db(
        "partial.sqlite3",
        [
            legacy_gold,
            {
                "id": "star_chinext50",
                "label": "科创创业50",
                "ticker": "931643",
                "code": "931643",
                "secid": "2.931643",
                "expected_name": "科创创业50",
                "provider": "eastmoney",
                "kind": "index",
                "enabled": True,
                "archived": False,
                "session_profile": "cn_equity",
                "strategies": ["macd"],
            },
            {
                "id": "gold_etf_518800",
                "label": "误占ID",
                "code": "515450",
                "provider": "sse",
                "expected_name": "红利50",
                "kind": "etf",
                "enabled": True,
                "archived": False,
                "session_profile": "cn_equity",
                "strategies": [],
            },
        ],
    )
    client = TestClient(create_app(db_partial))
    token = re.search(r'name="csrf"\s+value="([^"]+)"', client.get("/manage").text).group(1)
    partial_resp = client.post(
        "/manage/migrate-etf-targets", data={"csrf": token}, follow_redirects=False
    )
    assert partial_resp.status_code == 200
    assert "931643-&gt;159603" in partial_resp.text or "931643->159603" in partial_resp.text
    assert "515450" in partial_resp.text and "已保留原标的未迁移" in partial_resp.text


def test_unrelated_scheduled_retry_preserves_provider_probe_rotation(tmp_path, monkeypatch):
    _reset_cooldowns()
    bars = _sample_series(140, "2026-11-25")
    em_gold = _etf_asset(asset_id="gold_etf_518800", code="518800", secid="1.518800", fallbacks=[])
    em_star = _etf_asset(
        asset_id="star_chinext50_etf_159603",
        code="159603",
        secid="0.159603",
        expected_name="科创创业ETF天弘",
        fallbacks=[],
    )
    yahoo_spx = {
        "id": "sp500",
        "label": "标普500",
        "ticker": "SPX",
        "code": "^GSPC",
        "provider": "yahoo",
        "kind": "index",
        "enabled": True,
        "archived": False,
        "session_profile": "us_equity",
        "strategies": ["macd"],
    }

    fail_yahoo = [True]
    probed_em = []

    def fetcher(asset, day, now=None):
        if asset["provider"] == "yahoo":
            if fail_yahoo[0]:
                raise httpx.ConnectError("yahoo temporary failure")
            return {
                "rows": [
                    {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}
                    for d, o, h, l, c, v in bars
                ],
                "source": "yahoo",
                "name": "SP500",
                "code": "^GSPC",
                "basis": "market_ohlc",
                "provider": "yahoo",
                "provisional_date": "2026-11-25",
            }
        probed_em.append(asset["id"])
        return {
            "rows": [
                {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}
                for d, o, h, l, c, v in bars
            ],
            "source": "eastmoney",
            "name": asset["expected_name"],
            "code": asset["code"],
            "basis": "market_ohlc",
            "provider": "eastmoney",
            "provisional_date": "2026-11-25",
        }

    db_path = tmp_path / "retry-rotation.sqlite3"
    t1 = datetime(2026, 11, 25, 14, 36, tzinfo=TZ)
    run(db_path, [yahoo_spx, em_gold, em_star], scheduled=True, send=False, now=t1, fetcher=fetcher)
    assert probed_em == ["gold_etf_518800", "star_chinext50_etf_159603"]

    # Scheduled retry fetches ONLY failed yahoo asset -> zero eastmoney probes, must preserve eastmoney order
    fail_yahoo[0] = False
    probed_em.clear()
    r2 = run(
        db_path,
        [yahoo_spx, em_gold, em_star],
        scheduled=True,
        send=False,
        now=t1 + timedelta(minutes=6),
        fetcher=fetcher,
    )
    assert probed_em == []
    assert storage.report(db_path, r2)["probe_order"]["eastmoney"] == [
        "gold_etf_518800",
        "star_chinext50_etf_159603",
    ]

    # Next full check must rotate eastmoney to probe star first
    run(
        db_path,
        [yahoo_spx, em_gold, em_star],
        scheduled=False,
        send=False,
        now=t1 + timedelta(minutes=12),
        fetcher=fetcher,
    )
    assert probed_em == ["star_chinext50_etf_159603", "gold_etf_518800"]


def test_manage_form_resolves_etf_kind_before_live_validation_and_blocks_proofless_close(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "manage-etf.sqlite3"
    base_cfg = load_configuration(db_path)
    bars = _sample_series(140, "2026-11-25")
    em_payload = _eastmoney_payload("518800", "黄金ETF国泰", 1, bars)
    close_check_now = datetime(2026, 11, 25, 15, 6, tzinfo=TZ)

    # 1. Direct parse_eastmoney on an Eastmoney 518800 payload even with kind="index"
    # must enforce ETF close-time evidence and volume_unit="手".
    raw_asset = {
        "id": "custom_gold_etf",
        "label": "黄金ETF国泰",
        "code": "518800",
        "secid": "1.518800",
        "provider": "eastmoney",
        "expected_name": "黄金ETF国泰",
        "session_profile": "cn_equity",
        "kind": "index",
    }
    parsed = data.parse_eastmoney(
        em_payload, raw_asset, "2026-11-25", now=close_check_now
    )
    assert parsed["provisional_date"] == "2026-11-25"
    assert parsed["session"]["phase"] == "unconfirmed"
    assert parsed["volume_unit"] == "手"
    with pytest.raises(data.StaleMarketSnapshot):
        data.verify_session_freshness(
            parsed, raw_asset, "2026-11-25", now=close_check_now
        )

    # 2. Submitting Eastmoney 518800 through POST /manage with default kind="index"
    # must resolve kind="etf" BEFORE live validation calls fetcher (pinned clock).
    pinned_now = datetime(2026, 11, 25, 14, 35, tzinfo=TZ)

    class _PinnedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return pinned_now if tz is None else pinned_now.astimezone(tz)

    monkeypatch.setattr("chance_track.configuration.datetime", _PinnedDatetime)
    seen_kinds = []

    def validating_fetcher(asset, as_of):
        seen_kinds.append(asset.get("kind"))
        return data.parse_eastmoney(em_payload, asset, as_of, now=pinned_now)

    monkeypatch.setattr(
        "chance_track.web.save_configuration",
        lambda *a, **kw: save_configuration(*a, fetcher=validating_fetcher, **kw),
    )

    client = TestClient(create_app(db_path))
    manage_html = client.get("/manage").text
    token = re.search(r'name="csrf"\s+value="([^"]+)"', manage_html).group(1)

    # 2a. Exchange/session mismatch: Eastmoney 1.518800 with sge_gold must be rejected
    # before live fetch under both index and etf kind selections.
    for mismatched_kind in ("index", "etf"):
        bad_draft = {
            "assets": [
                *base_cfg["assets"],
                {
                    **raw_asset,
                    "id": f"bad_sge_{mismatched_kind}",
                    "session_profile": "sge_gold",
                    "kind": mismatched_kind,
                    "enabled": True,
                    "archived": False,
                    "strategies": [],
                },
            ],
            "strategies": base_cfg["strategies"],
        }
        bad_resp = client.post(
            "/manage",
            data={
                "csrf": token,
                "version": str(base_cfg["version"]),
                "payload": json.dumps(bad_draft, ensure_ascii=False),
            },
            follow_redirects=False,
        )
        assert bad_resp.status_code == 400
        assert seen_kinds == []
        assert all(
            a["id"] != f"bad_sge_{mismatched_kind}"
            for a in load_configuration(db_path)["assets"]
        )

    draft = {
        "assets": [
            *base_cfg["assets"],
            {
                **raw_asset,
                "enabled": True,
                "archived": False,
                "strategies": [],
            },
        ],
        "strategies": base_cfg["strategies"],
    }
    resp = client.post(
        "/manage",
        data={
            "csrf": token,
            "version": str(base_cfg["version"]),
            "payload": json.dumps(draft, ensure_ascii=False),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert seen_kinds == ["etf"]
    saved = load_configuration(db_path)
    added = next(a for a in saved["assets"] if a["id"] == "custom_gold_etf")
    assert added["kind"] == "etf"


def test_migrate_etf_targets_normalizes_legacy_market_and_currency_defaults(tmp_path):
    from chance_track import configuration_store

    db_path = tmp_path / "legacy-defaults.sqlite3"
    base_cfg = load_configuration(db_path)
    payload = {
        "assets": [
            {
                "id": "gold",
                "label": "黄金 Au99.99",
                "code": "AU9999",
                "secid": "118.AU9999",
                "provider": "eastmoney",
                "expected_name": "AU9999",
                "kind": "spot",
                "enabled": True,
                "archived": False,
                "session_profile": "sge_gold",
                "strategies": ["macd"],
            },
            {
                "id": "star_chinext50",
                "label": "科创创业50",
                "code": "931643",
                "secid": "2.931643",
                "provider": "eastmoney",
                "expected_name": "科创创业50",
                "kind": "index",
                "enabled": True,
                "archived": False,
                "session_profile": "cn_equity",
                "strategies": ["macd"],
            },
            {
                "id": "custom_518800",
                "label": "黄金ETF国泰",
                "code": "518800",
                "secid": "1.518800",
                "provider": "eastmoney",
                "expected_name": "黄金ETF国泰",
                "kind": "index",
                "market": "中国指数",
                "currency": "点",
                "enabled": False,
                "archived": False,
                "session_profile": "cn_equity",
                "strategies": [],
            },
            {
                "id": "custom_159603",
                "label": "科创创业ETF天弘",
                "code": "159603",
                "secid": "0.159603",
                "provider": "eastmoney",
                "expected_name": "科创创业ETF天弘",
                "kind": "index",
                "market": "自定义深市板块",
                "currency": "RMB",
                "enabled": False,
                "archived": False,
                "session_profile": "cn_equity",
                "strategies": [],
            },
        ],
        "strategies": base_cfg["strategies"],
    }
    with storage.connect(db_path) as db:
        cur = db.execute(
            "INSERT INTO configuration_versions(created_at,payload) VALUES(?,?)",
            ("2026-11-25T10:00:00+08:00", json.dumps(payload, ensure_ascii=False)),
        )
        configuration_store.write_current(db, payload, cur.lastrowid)

    result = migrate_etf_targets(db_path, validate_live=False)
    assert result["changed"] is True
    saved = {a["id"]: a for a in load_configuration(db_path)["assets"]}
    assert saved["custom_518800"]["kind"] == "etf"
    assert saved["custom_518800"]["market"] == "中国 · 上交所 ETF"
    assert saved["custom_518800"]["currency"] == "元"
    # Non-default custom market/currency are preserved
    assert saved["custom_159603"]["kind"] == "etf"
    assert saved["custom_159603"]["market"] == "自定义深市板块"
    assert saved["custom_159603"]["currency"] == "RMB"
