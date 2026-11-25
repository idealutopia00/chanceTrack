from copy import deepcopy
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from test_loop import NOW

from chance_track import data, storage
from chance_track.configuration import load_configuration, save_configuration, validate_configuration
from chance_track.data import (
    TZ,
    IdentityMismatch,
    MarketFetchError,
    parse_sina,
    parse_sse,
    parse_szse,
    parse_tencent,
)
from chance_track.notifications import build_message
from chance_track.presentation import fetch_state_summary, signal_rows
from chance_track.runner import run
from chance_track.web import create_app

DAY = "2026-11-26"
INTRADAY_NOW = datetime(2026, 11, 26, 14, 35, tzinfo=TZ)
CLOSED_NOW = datetime(2026, 11, 26, 15, 6, tzinfo=TZ)


def _dates(count=150, end_day=DAY):
    end = datetime.fromisoformat(end_day).date()
    days = []
    cur = end
    while len(days) < count:
        if cur.weekday() < 5:
            days.append(cur.isoformat())
        cur -= timedelta(days=1)
    return list(reversed(days))


def _closes(count=150):
    return [1.0 + i * 0.002 for i in range(count - 20)] + [
        1.26 - i * 0.01 for i in range(18)
    ] + [1.22, 1.35]


def _tencent_payload(symbol, code, name, *, market="1", dates=None, include_last=True):
    days = dates or _dates()
    prices = _closes(len(days))
    if not include_last:
        days, prices = days[:-1], prices[:-1]
    qt_day = days[-1].replace("-", "") + "143500"
    qt = [""] * 35
    qt[0], qt[1], qt[2] = market, name, code
    qt[3], qt[5], qt[6] = f"{prices[-1]:.3f}", f"{prices[-1] - 0.01:.3f}", "12000"
    qt[30], qt[33], qt[34] = qt_day, f"{prices[-1] + 0.02:.3f}", f"{prices[-1] - 0.02:.3f}"
    return {
        "data": {
            symbol: {
                "day": [
                    [
                        day,
                        f"{p - 0.005:.3f}",
                        f"{p:.3f}",
                        f"{p + 0.01:.3f}",
                        f"{p - 0.01:.3f}",
                        str(10000 + i * 10),
                    ]
                    for i, (day, p) in enumerate(zip(days, prices))
                ],
                "qt": {symbol: qt},
            }
        }
    }


def _sse_payloads(code, name, *, dates=None, include_last=True):
    days = dates or _dates()
    prices = _closes(len(days))
    if not include_last:
        days, prices = days[:-1], prices[:-1]
    last_p = prices[-1]
    dayk = {
        "code": code,
        "kline": [
            [
                int(day.replace("-", "")),
                round(p - 0.005, 3),
                round(p + 0.01, 3),
                round(p - 0.01, 3),
                round(p, 3),
                (10000 + i * 10) * 100,
                1000000,
            ]
            for i, (day, p) in enumerate(zip(days, prices))
        ],
    }
    snap = {
        "code": code,
        "date": int(days[-1].replace("-", "")),
        "time": 143500,
        "snap": [
            name,
            round(prices[-2], 3),
            round(last_p - 0.005, 3),
            round(last_p + 0.01, 3),
            round(last_p - 0.01, 3),
            round(last_p, 3),
            0.01,
            1.0,
            1200000,
        ],
    }
    return dayk, snap


def _szse_payloads(code, name, *, dates=None, include_last=True, split_intraday=False):
    days = dates or _dates()
    prices = _closes(len(days))
    if not include_last:
        days, prices = days[:-1], prices[:-1]
    hist_days = days[:-1] if split_intraday else days
    hist_prices = prices[:-1] if split_intraday else prices
    hist = {
        "data": {
            "code": code,
            "name": name,
            "picupdata": [
                [
                    day,
                    f"{p - 0.005:.3f}",
                    f"{p:.3f}",
                    f"{p - 0.01:.3f}",
                    f"{p + 0.01:.3f}",
                    "0.010",
                    "1.00",
                    10000 + i * 10,
                    1000000.0,
                ]
                for i, (day, p) in enumerate(zip(hist_days, hist_prices))
            ],
        }
    }
    time_data = {
        "data": {
            "code": code,
            "name": name,
            "open": f"{prices[-1] - 0.005:.3f}",
            "now": f"{prices[-1]:.3f}",
            "high": f"{prices[-1] + 0.01:.3f}",
            "low": f"{prices[-1] - 0.01:.3f}",
            "volume": 10000 + (len(days) - 1) * 10,
            "marketTime": f"{days[-1]} 14:35:00",
        }
    }
    return hist, time_data


def _sina_payloads(symbol, name, *, dates=None, include_last=True):
    days = dates or _dates()
    prices = _closes(len(days))
    if not include_last:
        days, prices = days[:-1], prices[:-1]
    kline = [
        {
            "day": day,
            "open": f"{p - 0.005:.3f}",
            "high": f"{p + 0.01:.3f}",
            "low": f"{p - 0.01:.3f}",
            "close": f"{p:.3f}",
            "volume": str((10000 + i * 10) * 100),
        }
        for i, (day, p) in enumerate(zip(days, prices))
    ]
    last = prices[-1]
    fields = [""] * 32
    fields[0] = name
    fields[1] = f"{last - 0.005:.3f}"
    fields[2] = f"{prices[-2]:.3f}"
    fields[3] = f"{last:.3f}"
    fields[4] = f"{last + 0.01:.3f}"
    fields[5] = f"{last - 0.01:.3f}"
    fields[8] = str((10000 + (len(days) - 1) * 10) * 100)
    fields[30] = days[-1]
    fields[31] = "14:35:00"
    hq = f'var hq_str_{symbol}="{",".join(fields)}";'
    return kline, hq


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _patch_http(monkeypatch, handler):
    monkeypatch.setattr(
        data.httpx,
        "AsyncClient",
        lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kw),
    )


def _etf_assets(tmp_path):
    config = load_configuration(tmp_path / "etfs.sqlite")
    gold = next(a for a in config["assets"] if a["id"] == "gold_etf_518800")
    star = next(a for a in config["assets"] if a["id"] == "star_chinext50_etf_159603")
    return gold, star


def test_parsers_verify_identity_volume_units_and_intraday_merge(tmp_path):
    gold, star = _etf_assets(tmp_path)

    tx = parse_tencent(
        _tencent_payload("sh518800", "518800", "黄金ETF国泰"),
        gold,
        DAY,
        now=INTRADAY_NOW,
    )
    assert tx["provider"] == "tencent"
    assert tx["volume_unit"] == "手" and tx["volume_multiplier"] == 1
    assert tx["provisional_date"] == DAY

    # Tencent qt appends intraday bar if day array has not added today yet.
    tx_lag = _tencent_payload("sh518800", "518800", "黄金ETF国泰", include_last=False)
    tx_lag["data"]["sh518800"]["qt"]["sh518800"][30] = "20261126143500"
    merged_tx = parse_tencent(tx_lag, gold, DAY, now=INTRADAY_NOW)
    assert merged_tx["rows"][-1]["date"] == DAY and merged_tx["provisional_date"] == DAY

    bad_tx = _tencent_payload("sh518800", "518800", "错误名称")
    with pytest.raises(IdentityMismatch):
        parse_tencent(bad_tx, gold, DAY, now=INTRADAY_NOW)

    dayk, snap = _sse_payloads("518800", "黄金基金")
    sse = parse_sse(dayk, snap, {**gold, **gold["fallbacks"][0]}, DAY, now=INTRADAY_NOW)
    assert sse["provider"] == "sse"
    assert sse["volume_unit"] == "手" and sse["volume_multiplier"] == 0.01
    assert sse["rows"][-1]["date"] == DAY
    with pytest.raises(IdentityMismatch):
        parse_sse(dayk, {**snap, "snap": ["错误名称", *snap["snap"][1:]]}, {**gold, **gold["fallbacks"][0]}, DAY)

    hist, time_data = _szse_payloads("159603", "科创创业ETF天弘", split_intraday=True)
    szse = parse_szse(
        hist,
        {**star, **star["fallbacks"][0]},
        DAY,
        now=INTRADAY_NOW,
        time_payload=time_data,
    )
    assert szse["provider"] == "szse"
    assert szse["volume_unit"] == "手" and szse["volume_multiplier"] == 1
    assert szse["rows"][-1]["date"] == DAY and szse["provisional_date"] == DAY
    with pytest.raises(IdentityMismatch):
        parse_szse({"data": {"code": "159603", "name": "不对"}}, {**star, **star["fallbacks"][0]}, DAY)

    kline, hq = _sina_payloads("sz159603", "双创天弘")
    sina = parse_sina(kline, hq, {**star, **star["fallbacks"][1]}, DAY, now=INTRADAY_NOW)
    assert sina["provider"] == "sina"
    assert sina["volume_unit"] == "手" and sina["volume_multiplier"] == 0.01
    with pytest.raises(IdentityMismatch):
        parse_sina(kline, 'var hq_str_sz159603="错误名字,1,1,1,1,1";', {**star, **star["fallbacks"][1]}, DAY)


def test_primary_disconnect_triggers_isolated_per_asset_fallbacks_and_status(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(data, "_cooldowns", {})
    monkeypatch.setattr(data, "REQUEST_RETRY_DELAYS", (0, 0))
    monkeypatch.setattr(data, "uniform", lambda *_: 0)

    gold, star = _etf_assets(tmp_path)
    dayk_518800, snap_518800 = _sse_payloads("518800", "黄金基金")
    hist_159603, time_159603 = _szse_payloads("159603", "科创创业ETF天弘")
    tencent_calls = []

    def handler(request):
        host = request.url.host
        if host == "proxy.finance.qq.com":
            tencent_calls.append(request.url.params["param"])
            raise httpx.RemoteProtocolError("Server disconnected")
        if host == "yunhq.sse.com.cn":
            if "/dayk/" in request.url.path:
                return httpx.Response(200, json=dayk_518800)
            return httpx.Response(200, json=snap_518800)
        if host == "www.szse.cn":
            if "getTimeData" in request.url.path:
                return httpx.Response(200, json=time_159603)
            return httpx.Response(200, json=hist_159603)
        raise AssertionError(f"Unexpected host: {request.url}")

    _patch_http(monkeypatch, handler)
    db_path = tmp_path / "fallbacks.sqlite"
    rid = run(db_path, [gold, star], send=False, now=INTRADAY_NOW, fetcher=data.fetch)
    report = storage.report(db_path, rid)
    g_item, s_item = report["items"]

    # Primary failed with 3 attempts on 518800, then 159603 skipped tencent due to cooldown.
    assert len(tencent_calls) == 3
    assert all("sh518800" in call for call in tencent_calls)
    assert g_item["status"] == s_item["status"] == "ok"
    assert g_item["provider"] == "sse" and g_item["fetch_status"] == "fallback_ok"
    assert s_item["provider"] == "szse" and s_item["fetch_status"] == "fallback_ok"
    assert [a["status"] for a in g_item["attempts"]] == ["disconnected", "ok"]
    assert [a["status"] for a in s_item["attempts"]] == ["cooling_down", "ok"]

    g_summary = fetch_state_summary(g_item)
    s_summary = fetch_state_summary(s_item)
    assert g_summary["label"] == "备用源成功" and "请求断连" in g_summary["detail"]
    assert s_summary["label"] == "备用源成功" and "同源冷却未请求" in s_summary["detail"]

    client = TestClient(create_app(db_path))
    page = client.get(f"/reports/{rid}")
    html = page.text
    assert "备用源成功" in html
    assert "上海证券交易所" in html and "深圳证券交易所" in html
    assert "请求断连" in html and "同源冷却未请求" in html

    import re

    csrf = re.search(r'name="csrf" value="([^"]+)"', html)[1]
    preview_res = client.post(
        "/api/workspace/preview",
        json={
            "csrf": csrf,
            "source": {"report_id": rid},
            "asset_id": "gold_etf_518800",
            "strategies": [],
        },
    )
    assert preview_res.status_code == 200
    preview_json = preview_res.json()
    assert preview_json["fetch_summary"]["code"] == "fallback_ok"
    assert preview_json["fetch_summary"]["label"] == "备用源成功"


def test_stale_1435_and_bad_identity_fallbacks_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_cooldowns", {})
    monkeypatch.setattr(data, "REQUEST_RETRY_DELAYS", (0, 0))
    monkeypatch.setattr(data, "uniform", lambda *_: 0)

    gold, _ = _etf_assets(tmp_path)
    stale_dayk, stale_snap = _sse_payloads("518800", "黄金基金", include_last=False)
    fresh_kline, fresh_hq = _sina_payloads("sh518800", "黄金ETF国泰")

    def handler_fallback_to_sina(request):
        if request.url.host == "proxy.finance.qq.com":
            # Tencent returns only yesterday's close at 14:35 -> must NOT be counted as today's success.
            return httpx.Response(
                200,
                json=_tencent_payload("sh518800", "518800", "黄金ETF国泰", include_last=False),
            )
        if request.url.host == "yunhq.sse.com.cn":
            # SSE returns yesterday's close too -> rejected as stale at 14:35.
            return httpx.Response(200, json=stale_dayk if "/dayk/" in request.url.path else stale_snap)
        if request.url.host == "money.finance.sina.com.cn":
            return httpx.Response(200, json=fresh_kline)
        if request.url.host == "hq.sinajs.cn":
            return httpx.Response(200, content=fresh_hq.encode("gbk"))
        raise AssertionError(f"Unexpected URL: {request.url}")

    _patch_http(monkeypatch, handler_fallback_to_sina)
    snapshot = data.fetch(gold, DAY, now=INTRADAY_NOW)
    assert snapshot["provider"] == "sina" and snapshot["fetch_status"] == "fallback_ok"
    assert [a["status"] for a in snapshot["attempts"]] == ["stale", "stale", "ok"]

    # close_only fallback is rejected before confirmation time (14:35), accepted after close (15:06).
    gold_close_only = deepcopy(gold)
    gold_close_only["fallbacks"] = [
        {"provider": "sse", "code": "518800", "expected_name": "黄金基金", "mode": "close_only"}
    ]
    fresh_dayk, fresh_snap = _sse_payloads("518800", "黄金基金")

    def handler_close_only(request):
        if request.url.host == "proxy.finance.qq.com":
            return httpx.Response(
                200,
                json=_tencent_payload("sh518800", "518800", "WRONG_NAME"),
            )
        if request.url.host == "yunhq.sse.com.cn":
            return httpx.Response(200, json=fresh_dayk if "/dayk/" in request.url.path else fresh_snap)
        raise AssertionError(f"Unexpected URL: {request.url}")

    _patch_http(monkeypatch, handler_close_only)
    with pytest.raises(MarketFetchError) as exc_info:
        data.fetch(gold_close_only, DAY, now=INTRADAY_NOW)
    assert [a["status"] for a in exc_info.value.attempts] == ["identity_mismatch", "stale"]

    closed_snap = data.fetch(gold_close_only, DAY, now=CLOSED_NOW)
    assert closed_snap["provider"] == "sse" and closed_snap["provisional_date"] is None


def test_total_failure_distinguishes_historical_snapshot_vs_no_quote(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_cooldowns", {})
    monkeypatch.setattr(data, "REQUEST_RETRY_DELAYS", (0, 0))
    monkeypatch.setattr(data, "uniform", lambda *_: 0)

    gold, star = _etf_assets(tmp_path)
    db_path = tmp_path / "total_fail.sqlite"

    def ok_only_gold(request):
        if request.url.host == "proxy.finance.qq.com" and "sh518800" in request.url.params["param"]:
            return httpx.Response(200, json=_tencent_payload("sh518800", "518800", "黄金ETF国泰"))
        raise httpx.RemoteProtocolError("offline")

    _patch_http(monkeypatch, ok_only_gold)
    first_id = run(db_path, [gold, star], send=False, now=INTRADAY_NOW, fetcher=data.fetch)
    first_report = storage.report(db_path, first_id)
    assert first_report["items"][0]["status"] == "ok"
    assert first_report["items"][1]["status"] == "error"
    assert first_report["items"][1]["has_historical_snapshot"] is False

    # Now both fail; gold has prior snapshot, star has none.
    monkeypatch.setattr(data, "_cooldowns", {})

    def all_fail(request):
        raise httpx.RemoteProtocolError("offline")

    _patch_http(monkeypatch, all_fail)
    second_id = run(
        db_path,
        [gold, star],
        send=False,
        now=INTRADAY_NOW + timedelta(minutes=1),
        fetcher=data.fetch,
    )
    second_report = storage.report(db_path, second_id)
    g_err, s_err = second_report["items"]
    assert g_err["status"] == s_err["status"] == "error"
    assert g_err["has_historical_snapshot"] is True and g_err["last_quote"]["bar_date"] == DAY
    assert s_err["has_historical_snapshot"] is False and s_err["last_quote"] is None

    g_summary = fetch_state_summary(g_err)
    s_summary = fetch_state_summary(s_err)
    assert g_err["attempts"][0]["status"] == "cooling_down"
    assert s_err["attempts"][0]["status"] == "disconnected"
    assert f"仅有历史快照（{DAY}）" in g_summary["label"]
    assert "同源冷却未请求" in g_summary["detail"]
    assert "暂无行情" in s_summary["label"]
    assert "请求断连" in s_summary["label"]

    # When an asset has no non-cooled fallback available, top-level reason is 同源冷却未请求.
    single = {**star, "id": "single_tx", "fallbacks": []}
    r_cool = run(
        db_path,
        [single],
        send=False,
        now=INTRADAY_NOW + timedelta(minutes=2),
        fetcher=data.fetch,
    )
    cool_item = storage.report(db_path, r_cool)["items"][0]
    cool_summary = fetch_state_summary(cool_item)
    assert cool_item["fetch_status"] == "cooling_down"
    assert cool_summary["label"] == "同源冷却未请求 · 暂无行情"

    assert not build_message(second_report, set())[1]
    rows = signal_rows(second_report)
    assert f"仅有历史快照（{DAY}）" in rows[0]["detail"]
    assert "暂无行情" in rows[1]["detail"]

    html = TestClient(create_app(db_path)).get(f"/reports/{second_id}").text
    assert f"仅有历史快照（{DAY}）" in html
    assert "暂无行情" in html
    assert "同源冷却未请求" in html


def test_post_cooldown_recovery_rotates_probe_order_without_duplicate_notifications(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(data, "_cooldowns", {})
    monkeypatch.setattr(data, "REQUEST_RETRY_DELAYS", (0, 0))
    monkeypatch.setattr(data, "uniform", lambda *_: 0)

    gold, star = _etf_assets(tmp_path)
    clock = [100.0]
    monkeypatch.setattr(data, "monotonic", lambda: clock[0])

    dayk_518800, snap_518800 = _sse_payloads("518800", "黄金基金")
    hist_159603, time_159603 = _szse_payloads("159603", "科创创业ETF天弘")
    tencent_online = [False]
    probe_sequence = []

    def handler(request):
        host = request.url.host
        if host == "proxy.finance.qq.com":
            probe_sequence.append(request.url.params["param"].split(",")[0])
            if not tencent_online[0]:
                raise httpx.RemoteProtocolError("down")
            if "sh518800" in request.url.params["param"]:
                return httpx.Response(200, json=_tencent_payload("sh518800", "518800", "黄金ETF国泰"))
            return httpx.Response(200, json=_tencent_payload("sz159603", "159603", "科创创业ETF天弘", market="51"))
        if host == "yunhq.sse.com.cn":
            return httpx.Response(200, json=dayk_518800 if "/dayk/" in request.url.path else snap_518800)
        if host == "www.szse.cn":
            return httpx.Response(200, json=time_159603 if "getTimeData" in request.url.path else hist_159603)
        raise AssertionError(f"Unexpected URL: {request.url}")

    _patch_http(monkeypatch, handler)
    db_path = tmp_path / "rotate.sqlite"
    notifications = []

    # Run 1 (closed session): gold probes tencent first -> disconnects -> SSE succeeds; star skips -> SZSE succeeds.
    r1 = run(
        db_path,
        [gold, star],
        send=True,
        now=CLOSED_NOW,
        fetcher=data.fetch,
        sender=lambda *args: notifications.append(args),
    )
    assert probe_sequence[0] == "sh518800"
    assert storage.report(db_path, r1)["probe_order"]["tencent"] == [
        "gold_etf_518800",
        "star_chinext50_etf_159603",
    ]
    assert len(notifications) == 1

    # Advance past cooldown and recover tencent. Run 2 must probe star (159603) FIRST.
    clock[0] += data.SOURCE_COOLDOWN + 1
    tencent_online[0] = True
    probe_sequence.clear()
    r2 = run(
        db_path,
        [gold, star],
        send=True,
        now=CLOSED_NOW + timedelta(minutes=1),
        fetcher=data.fetch,
        sender=lambda *args: notifications.append(args),
    )
    assert probe_sequence == ["sz159603", "sh518800"]
    report2 = storage.report(db_path, r2)
    assert [item["asset"]["id"] for item in report2["items"]] == [
        "gold_etf_518800",
        "star_chinext50_etf_159603",
    ]
    assert all(item["fetch_status"] == "primary_ok" for item in report2["items"])
    # Source switch from SSE/SZSE to Tencent on the same confirmed bar must NOT create duplicate notification keys.
    assert "新增确认提醒 0 项" in notifications[1][1]

    # Run 3 rotates back to gold first.
    probe_sequence.clear()
    run(
        db_path,
        [gold, star],
        send=False,
        now=CLOSED_NOW + timedelta(minutes=2),
        fetcher=data.fetch,
    )
    assert probe_sequence == ["sh518800", "sz159603"]


def test_configuration_validates_independent_etf_fallbacks_and_preserves_kind(tmp_path):
    config = load_configuration(tmp_path / "cfg.sqlite")
    assert [a["id"] for a in config["assets"]] == [
        "sp500",
        "nasdaq100",
        "gold_etf_518800",
        "star_chinext50_etf_159603",
        "sox",
    ]
    bad = deepcopy(config)
    bad["assets"][2]["fallbacks"].append(
        {"provider": "tencent", "code": "518800", "symbol": "sh518800", "expected_name": "黄金ETF国泰"}
    )
    with pytest.raises(ValueError, match="独立来源"):
        validate_configuration(bad)

    bad_yahoo = deepcopy(config)
    bad_yahoo["assets"][2]["fallbacks"] = [{"provider": "yahoo", "code": "518800.SS"}]
    with pytest.raises(ValueError, match="不匹配"):
        validate_configuration(bad_yahoo)

    # Saving preserves kind == "etf"
    updated = deepcopy(config)
    updated["assets"][2]["label"] = "黄金ETF国泰（已更名）"
    new_ver = save_configuration(tmp_path / "cfg.sqlite", updated, config["version"])
    assert new_ver == 2
    saved_gold = next(
        a for a in load_configuration(tmp_path / "cfg.sqlite")["assets"] if a["id"] == "gold_etf_518800"
    )
    assert saved_gold["kind"] == "etf"
