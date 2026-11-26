"""Shared synthetic market fixtures for Chance Track v1.0 tests."""

from datetime import datetime, timedelta

from chance_track.data import TZ

NOW = datetime(2026, 11, 17, 14, 35, tzinfo=TZ)

ASSET = {
    "id": "sp500",
    "label": "标普500",
    "ticker": "SPX",
    "code": "^GSPC",
    "provider": "yahoo",
    "kind": "index",
    "market": "美国 · S&P Dow Jones",
    "currency": "点",
    "group": "美国指数",
    "session_profile": "us_equity",
    "enabled": True,
    "archived": False,
    "watchlisted": True,
    "strategies": ["macd"],
}


def sample(count=180, *, volume_unit=None, volume_multiplier=None):
    """Deterministic daily OHLCV series ending with a confirmed MACD golden cross on 2026-11-16."""
    start = datetime(2026, 11, 16, tzinfo=TZ) - timedelta(days=count - 1)
    closes = [1.0 + i * 0.001 for i in range(count - 25)]
    closes += [closes[-1] - (i + 1) * 0.004 for i in range(24)]
    closes.append(1.50)
    rows = []
    for i, c in enumerate(closes):
        day = (start + timedelta(days=i)).date().isoformat()
        row = {
            "date": day,
            "open": round(c - 0.005, 4),
            "high": round(c + 0.01, 4),
            "low": round(c - 0.01, 4),
            "close": round(c, 4),
            "volume": 10000 + i * 100,
        }
        rows.append(row)
    snap = {
        "rows": rows,
        "source": "fixture",
        "name": "测试指数",
        "code": "TEST",
        "provider": "yahoo",
        "basis": "market_ohlc",
        "provisional_date": None,
        "confirmed_date": rows[-1]["date"],
        "exchange_timezone": "America/New_York",
        "fetched_at": NOW.isoformat(),
    }
    if volume_unit:
        snap["volume_unit"] = volume_unit
    if volume_multiplier is not None:
        snap["volume_multiplier"] = volume_multiplier
    return snap
