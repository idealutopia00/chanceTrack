from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from chance_track.data import TZ, parse_eastmoney, parse_yahoo
from chance_track.market_sessions import apply_sessions, session_bounds

GOLD = {
    "id": "gold",
    "code": "AU9999",
    "secid": "118.AU9999",
    "expected_name": "黄金9999",
    "session_profile": "sge_gold",
}
PAYLOAD = {
    "data": {
        "code": "AU9999",
        "market": 118,
        "name": "黄金9999",
        "klines": [
            "2026-11-16,841,829.49,841,823,100",
            "2026-11-17,830,833.99,836,825,200",
        ],
    }
}


def test_gold_includes_day_before_equity_open_and_has_later_close():
    for hour, minute in [(9, 0), (12, 0), (14, 0), (15, 20)]:
        now = datetime(2026, 11, 17, hour, minute, tzinfo=TZ)
        snapshot = parse_eastmoney(PAYLOAD, GOLD, "2026-11-17", now)
        assert snapshot["rows"][-1]["date"] == "2026-11-17"
        assert snapshot["provisional_date"] == "2026-11-17"
        assert snapshot["session"]["phase"] == "day"
        assert snapshot["session"]["close_at"] == "2026-11-17T15:30:00+08:00"
    buffered = parse_eastmoney(
        PAYLOAD, GOLD, "2026-11-17", datetime(2026, 11, 17, 15, 32, tzinfo=TZ)
    )
    assert buffered["session"]["phase"] == "settling" and buffered["provisional_date"]
    closed = parse_eastmoney(
        PAYLOAD, GOLD, "2026-11-17", datetime(2026, 11, 17, 15, 35, tzinfo=TZ)
    )
    assert closed["provisional_date"] is None and closed["session"]["phase"] == "closed"


def test_gold_night_belongs_to_next_trading_day_and_remains_unfinished_after_midnight():
    for now in (
        datetime(2026, 11, 16, 20, 1, tzinfo=TZ),
        datetime(2026, 11, 17, 1, tzinfo=TZ),
    ):
        snapshot = parse_eastmoney(PAYLOAD, GOLD, now.date().isoformat(), now)
        assert snapshot["rows"][-1]["date"] == "2026-11-17"
        assert snapshot["provisional_date"] == "2026-11-17"
        assert snapshot["session"]["phase"] == "night"
    # The night session ended, but this trading day's day session has not closed.
    rest = parse_eastmoney(
        PAYLOAD, GOLD, "2026-11-17", datetime(2026, 11, 17, 4, tzinfo=TZ)
    )
    assert (
        rest["session"]["phase"] == "break" and rest["provisional_date"] == "2026-11-17"
    )


def test_friday_gold_night_is_monday_not_a_future_price_or_a_saturday_bar():
    rows = [{"date": "2026-11-13"}, {"date": "2026-11-16"}, {"date": "2026-11-17"}]
    friday = apply_sessions(rows, "sge_gold", datetime(2026, 11, 13, 21, tzinfo=TZ))
    assert [r["date"] for r in friday["rows"]] == ["2026-11-13", "2026-11-16"]
    assert friday["provisional_date"] == "2026-11-16"
    assert friday["session"]["open_at"] == "2026-11-13T20:00:00+08:00"
    saturday = apply_sessions(rows, "sge_gold", datetime(2026, 11, 14, 1, tzinfo=TZ))
    assert saturday["session"]["phase"] == "night"
    sunday = apply_sessions(rows, "sge_gold", datetime(2026, 11, 15, 13, tzinfo=TZ))
    assert (
        sunday["session"]["phase"] == "break"
        and sunday["provisional_date"] == "2026-11-16"
    )


def test_holiday_eve_has_no_gold_night_and_weekend_makeup_is_closed():
    bounds = session_bounds("sge_gold", "2026-02-24")
    assert bounds["open"] == datetime(2026, 2, 24, 9, tzinfo=TZ)
    assert session_bounds("sge_gold", "2026-02-28") is None
    rows = [{"date": "2026-02-13"}, {"date": "2026-02-24"}]
    eve = apply_sessions(rows, "sge_gold", datetime(2026, 2, 13, 21, tzinfo=TZ))
    assert eve["rows"][-1]["date"] == "2026-02-13" and eve["provisional_date"] is None


def test_equity_lunch_and_no_premature_future_bar():
    rows = [{"date": "2026-11-16"}, {"date": "2026-11-17"}]
    early = apply_sessions(rows, "cn_equity", datetime(2026, 11, 17, 9, 10, tzinfo=TZ))
    assert early["rows"][-1]["date"] == "2026-11-16"
    lunch = apply_sessions(rows, "cn_equity", datetime(2026, 11, 17, 12, tzinfo=TZ))
    assert (
        lunch["provisional_date"] == "2026-11-17"
        and lunch["session"]["phase"] == "break"
    )
    with pytest.raises(ValueError, match="未知交易时段"):
        apply_sessions(rows, "unknown", datetime(2026, 11, 17, 12, tzinfo=TZ))


def test_us_provider_early_close_and_dst_share_same_pipeline():
    ny = ZoneInfo("America/New_York")
    # Thanksgiving Friday: source supplies the actual 13:00 early close.
    start = datetime(2026, 11, 27, 9, 30, tzinfo=ny)
    end = datetime(2026, 11, 27, 13, tzinfo=ny)
    period = {"start": start.timestamp(), "end": end.timestamp()}
    rows = [{"date": "2026-11-25"}, {"date": "2026-11-27"}]
    pending = apply_sessions(
        rows, "us_equity", datetime(2026, 11, 27, 12, tzinfo=ny), period
    )
    assert pending["provisional_date"] == "2026-11-27"
    assert pending["session"]["schedule_source"] == "provider_regular_session"
    closed = apply_sessions(
        rows, "us_equity", datetime(2026, 11, 27, 13, 5, tzinfo=ny), period
    )
    assert closed["provisional_date"] is None
    assert (
        session_bounds("us_equity", "2026-07-06")["open"].utcoffset().total_seconds()
        == -4 * 3600
    )
    assert (
        session_bounds("us_equity", "2026-11-17")["open"].utcoffset().total_seconds()
        == -5 * 3600
    )


def test_yahoo_live_bar_uses_source_session_instead_of_china_date_cutoff():
    ny = ZoneInfo("America/New_York")
    start = datetime(2026, 11, 16, 9, 30, tzinfo=ny)
    end = datetime(2026, 11, 16, 16, tzinfo=ny)
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": "^SOX",
                        "instrumentType": "INDEX",
                        "exchangeTimezoneName": "America/New_York",
                        "currentTradingPeriod": {
                            "regular": {
                                "start": start.timestamp(),
                                "end": end.timestamp(),
                            }
                        },
                    },
                    "timestamp": [start.timestamp()],
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
            ]
        }
    }
    asset = {"code": "^SOX", "label": "费城半导体", "session_profile": "us_equity"}
    snapshot = parse_yahoo(
        payload, asset, "2026-11-17", datetime(2026, 11, 16, 15, tzinfo=ny)
    )
    assert (
        snapshot["provisional_date"] == "2026-11-16"
        and snapshot["session"]["phase"] == "day"
    )


def test_unstarted_yahoo_placeholder_does_not_break_previous_completed_bars():
    ny = ZoneInfo("America/New_York")
    days = [
        datetime(2026, 11, 16, 9, 30, tzinfo=ny),
        datetime(2026, 11, 17, 9, 30, tzinfo=ny),
    ]
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": "^NDX",
                        "instrumentType": "INDEX",
                        "exchangeTimezoneName": "America/New_York",
                    },
                    "timestamp": [day.timestamp() for day in days],
                    "indicators": {
                        "quote": [
                            {
                                "open": [100, None],
                                "high": [110, None],
                                "low": [95, None],
                                "close": [105, None],
                                "volume": [1, None],
                            }
                        ]
                    },
                }
            ]
        }
    }
    result = parse_yahoo(
        payload,
        {"code": "^NDX", "label": "纳斯达克100", "session_profile": "us_equity"},
        "2026-11-17",
        datetime(2026, 11, 17, 9, tzinfo=ny),
    )
    assert (
        result["rows"][-1]["date"] == "2026-11-16"
        and result["provisional_date"] is None
    )
