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
        (Path(__file__).resolve().parent.parent / "catalog.json").read_text()
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










def test_direct_parsers_reject_mismatched_session_profile_and_kind():
    bars = _sample_series(140, "2026-11-25")
    gold_payload = _eastmoney_payload("AU9999", "AU9999", 118, bars)
    etf_payload = _eastmoney_payload("518800", "黄金ETF国泰", 1, bars)
    now = datetime(2026, 11, 25, 14, 35, tzinfo=TZ)

    # 118.AU9999 with sge_gold + kind="etf"
    with pytest.raises(IdentityMismatch):
        parse_eastmoney(
            gold_payload,
            {
                "code": "AU9999",
                "secid": "118.AU9999",
                "expected_name": "AU9999",
                "session_profile": "sge_gold",
                "kind": "etf",
            },
            "2026-11-25",
            now=now,
        )

    # 1.518800 with cn_equity + kind="spot"
    with pytest.raises(IdentityMismatch):
        parse_eastmoney(
            etf_payload,
            {
                "code": "518800",
                "secid": "1.518800",
                "expected_name": "黄金ETF国泰",
                "session_profile": "cn_equity",
                "kind": "spot",
            },
            "2026-11-25",
            now=now,
        )

    # 118.AU9999 with us_equity
    with pytest.raises(IdentityMismatch):
        parse_eastmoney(
            gold_payload,
            {
                "code": "AU9999",
                "secid": "118.AU9999",
                "expected_name": "AU9999",
                "session_profile": "us_equity",
            },
            "2026-11-25",
            now=now,
        )

    # 118.AU9999 with omitted session_profile (defaults to cn_equity -> rejected)
    with pytest.raises(IdentityMismatch):
        parse_eastmoney(
            gold_payload,
            {
                "code": "AU9999",
                "secid": "118.AU9999",
                "expected_name": "AU9999",
            },
            "2026-11-25",
            now=now,
        )

    # 1.518800 with sge_gold
    with pytest.raises(IdentityMismatch):
        parse_eastmoney(
            etf_payload,
            {
                "code": "518800",
                "secid": "1.518800",
                "expected_name": "黄金ETF国泰",
                "session_profile": "sge_gold",
            },
            "2026-11-25",
            now=now,
        )

    # SSE / SZSE reject non-cn_equity or kind="spot"
    sse_dayk, sse_snap = _sse_payloads("518800", "黄金基金", bars)
    with pytest.raises(IdentityMismatch):
        parse_sse(
            sse_dayk,
            sse_snap,
            {
                "code": "518800",
                "expected_name": "黄金基金",
                "session_profile": "sge_gold",
            },
            "2026-11-25",
            now=now,
        )
    with pytest.raises(IdentityMismatch):
        parse_sse(
            sse_dayk,
            sse_snap,
            {
                "code": "518800",
                "expected_name": "黄金基金",
                "session_profile": "cn_equity",
                "kind": "spot",
            },
            "2026-11-25",
            now=now,
        )
    sz_hist, _ = _szse_payloads("159603", "科创创业ETF天弘", bars)
    with pytest.raises(IdentityMismatch):
        parse_szse(
            sz_hist,
            {
                "code": "159603",
                "expected_name": "科创创业ETF天弘",
                "session_profile": "us_equity",
            },
            "2026-11-25",
            now=now,
        )
    with pytest.raises(IdentityMismatch):
        data.parse_yahoo(
            {},
            {
                "code": "^GSPC",
                "label": "标普500",
                "session_profile": "cn_equity",
            },
            "2026-11-25",
            now=now,
        )
    with pytest.raises(IdentityMismatch):
        data.parse_yahoo(
            {},
            {
                "code": "^GSPC",
                "label": "标普500",
                "session_profile": "us_equity",
                "kind": "etf",
            },
            "2026-11-25",
            now=now,
        )
