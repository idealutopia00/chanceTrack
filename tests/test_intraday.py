from datetime import datetime

from chance_track.data import TZ, parse_eastmoney

ASSET = {
    "id": "star_chinext50",
    "label": "科创创业50",
    "code": "931643",
    "secid": "2.931643",
    "expected_name": "科创创业50",
    "session_profile": "cn_equity",
    "enabled": True,
}
NOW = datetime(2026, 11, 17, 14, 35, tzinfo=TZ)
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
    from copy import deepcopy
    from datetime import timedelta

    from test_loop import sample

    from chance_track import storage
    from chance_track.analysis import analyze
    from chance_track.runner import run

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
    assert pending["chart"]["crosses"][-1]["provisional"]
    path = tmp_path / "pending.sqlite3"
    first = run(
        path, [ASSET], send=False, now=NOW, fetcher=lambda *_: deepcopy(snapshot)
    )
    from fastapi.testclient import TestClient

    from chance_track.web import create_app

    page = TestClient(create_app(path)).get(f"/reports/{first}")
    assert page.status_code == 200
    assert "盘中金叉 · 待收盘确认" in page.text and "上一收盘日参考" in page.text
    assert "临时 MACD 随当前交易日价格变化" in page.text
    with storage.connect(path) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    # Prices reverse before the close; the preview disappears, original report remains immutable.
    reversed_snapshot = deepcopy(snapshot)
    reversed_snapshot["rows"][-1].update(open=1.06, high=1.08, low=1.04, close=1.05)
    reversed_result = analyze(reversed_snapshot, "2026-11-17")
    assert reversed_result["preview_event"] is None
    assert (
        storage.report(path, first)["items"][0]["result"]["preview_event"]
        == pending["preview_event"]
    )
    finalized = deepcopy(snapshot)
    finalized["provisional_date"] = None
    final = run(
        path,
        [ASSET],
        send=False,
        now=NOW.replace(hour=15, minute=6),
        fetcher=lambda *_: finalized,
    )
    result = storage.report(path, final)["items"][0]["result"]
    assert not result["is_provisional"] and result["event"] == "金叉 · 买入观察"
    assert result["event_date"] == "2026-11-17" and result["new_event"]
    with storage.connect(path) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_confirmed_event_keeps_its_date_with_today_preview(tmp_path):
    from test_loop import sample

    from chance_track import storage
    from chance_track.runner import run

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
    report_id = run(path, [ASSET], send=False, now=NOW, fetcher=lambda *_: snapshot)
    result = storage.report(path, report_id)["items"][0]["result"]
    assert result["bar_date"] == "2026-11-17"
    assert result["event_date"] == "2026-11-16"
    assert result["event"] == "金叉 · 买入观察"
    assert result["preview_event"] == "盘中死叉 · 待收盘确认"
    with storage.connect(path) as db:
        key = db.execute("SELECT key FROM events").fetchone()[0]
        assert ":2026-11-16:" in key and ":2026-11-17:" not in key
