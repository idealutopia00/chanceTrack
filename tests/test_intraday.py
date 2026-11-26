from copy import deepcopy
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from helpers import NOW, sample

from chance_track import storage
from chance_track.analysis import analyze
from chance_track.data import TZ, parse_eastmoney
from chance_track.runner import run
from chance_track.web import create_app

ASSET = {
    "id": "star_chinext50",
    "label": "科创创业50",
    "code": "931643",
    "provider": "eastmoney",
    "secid": "2.931643",
    "expected_name": "科创创业50",
    "session_profile": "cn_equity",
    "enabled": True,
    "strategies": ["macd"],
}
PAYLOAD = {
    "data": {
        "code": "931643",
        "market": 2,
        "name": "科创创业50",
        "klines": ["2026-11-16,100,105,110,95,200", "2026-11-17,106,107,109,104,100"],
    }
}


def test_open_market_keeps_today_candle():
    snapshot = parse_eastmoney(PAYLOAD, ASSET, "2026-11-17", now=NOW)
    assert snapshot["rows"][-1]["date"] == "2026-11-17", "开盘期间不应丢弃今天的真实K线"


def test_current_candle_state_before_open_during_lunch_and_after_close():
    before_open = parse_eastmoney(
        PAYLOAD, ASSET, "2026-11-17", now=NOW.replace(hour=9, minute=29)
    )
    assert before_open["rows"][-1]["date"] == "2026-11-16"
    lunch = parse_eastmoney(PAYLOAD, ASSET, "2026-11-17", now=NOW.replace(hour=12))
    assert lunch["provisional_date"] == "2026-11-17"
    at_close = parse_eastmoney(
        PAYLOAD, ASSET, "2026-11-17", now=NOW.replace(hour=15, minute=0)
    )
    assert at_close["provisional_date"] == "2026-11-17"
    finalized = parse_eastmoney(
        PAYLOAD, ASSET, "2026-11-17", now=NOW.replace(hour=15, minute=5)
    )
    assert finalized["rows"][-1]["date"] == "2026-11-17"
    assert finalized["provisional_date"] is None


def test_live_fetch_requests_today_instead_of_yesterday(monkeypatch):
    import httpx

    from chance_track import data

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(data, "datetime", FrozenDateTime)
    urls = []

    def handler(request):
        urls.append(request.url)
        rows = PAYLOAD["data"]["klines"]
        if request.url.params["end"] < "20261117":
            rows = rows[:1]
        return httpx.Response(200, json={"data": {**PAYLOAD["data"], "klines": rows}})

    real_client = httpx.AsyncClient

    def client(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(**kwargs)

    monkeypatch.setattr(data.httpx, "AsyncClient", client)
    snapshot = data.fetch({**ASSET, "provider": "eastmoney"}, "2026-11-17")
    assert urls[0].params["end"] >= "20261117"
    assert "_" in urls[0].params
    assert snapshot["rows"][-1]["date"] == "2026-11-17"
    assert snapshot["provisional_date"] == "2026-11-17"


def test_preview_cross_can_disappear_without_creating_confirmed_event(tmp_path):
    snapshot = sample()
    for row in snapshot["rows"]:
        row["date"] = (
            (datetime.fromisoformat(row["date"]) + timedelta(days=1)).date().isoformat()
        )
    snapshot["provisional_date"] = "2026-11-17"
    pending = analyze(snapshot, "2026-11-17")
    assert pending["is_provisional"]
    assert pending["preview_event"] == "盘中金叉 · 待收盘确认"
    assert pending["event"] is None

    path = tmp_path / "pending.sqlite3"
    run(path, [ASSET], send=False, now=NOW, fetcher=lambda *_: deepcopy(snapshot))
    assert storage.list_signals(path) == []

    # Check API reflects provisional bar in market_bars
    bars_resp = TestClient(create_app(path)).get(f"/api/v1/assets/{ASSET['id']}/bars")
    assert bars_resp.status_code == 200
    assert bars_resp.json()["result"]["is_provisional"] is True

    # After close, provisional bar is finalized in-place and confirmed signal is persisted
    finalized = deepcopy(snapshot)
    finalized["provisional_date"] = None
    run(
        path,
        [ASSET],
        send=False,
        now=NOW.replace(hour=15, minute=6),
        fetcher=lambda *_: finalized,
    )
    sigs = storage.list_signals(path, asset_id=ASSET["id"])
    assert len(sigs) == 1
    assert sigs[0]["event_date"] == "2026-11-17"
    assert sigs[0]["event_kind"] == "金叉"
    last = storage.last_bar(path, ASSET["id"])
    assert last["bar_date"] == "2026-11-17" and last["is_provisional"] == 0


def test_confirmed_event_keeps_its_date_with_today_preview(tmp_path):
    snapshot = sample()
    snapshot["rows"].append(
        {
            "date": "2026-11-17",
            "open": 1.5,
            "high": 1.51,
            "low": 0.79,
            "close": 0.8,
            "volume": 100,
        }
    )
    snapshot["provisional_date"] = "2026-11-17"
    path = tmp_path / "dates.sqlite3"
    run(path, [ASSET], send=False, now=NOW, fetcher=lambda *_: snapshot)
    sigs = storage.list_signals(path, asset_id=ASSET["id"])
    assert len(sigs) == 1
    assert sigs[0]["event_date"] == "2026-11-16"
    assert ":2026-11-16:" in sigs[0]["dedup_key"]
    assert ":2026-11-17:" not in sigs[0]["dedup_key"]
