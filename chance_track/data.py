"""Market OHLC adapters. No fund NAV proxies and no synthetic candles."""

import asyncio
import math
from datetime import date, datetime
from random import uniform
from threading import Lock
from time import monotonic
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from .market_sessions import apply_sessions

TZ = ZoneInfo("Asia/Shanghai")


def validate_rows(rows):
    if not rows:
        raise ValueError("没有可用的日线")
    dates = []
    for row in rows:
        date.fromisoformat(row["date"])
        dates.append(row["date"])
        for key in ("open", "high", "low", "close", "volume"):
            row[key] = float(row[key])
        prices = [row[key] for key in ("open", "high", "low", "close")]
        if not all(math.isfinite(x) and x > 0 for x in prices):
            raise ValueError("OHLC 必须是有限正数")
        if (
            row["low"] > min(row["open"], row["close"])
            or row["high"] < max(row["open"], row["close"])
            or row["high"] < row["low"]
        ):
            raise ValueError("OHLC 高低价关系无效")
        if not math.isfinite(row["volume"]) or row["volume"] < 0:
            raise ValueError("成交量无效")
    if dates != sorted(set(dates)):
        raise ValueError("行情日期重复或未按时间升序")
    return rows


def parse_eastmoney(payload, asset, as_of, now=None):
    data = payload.get("data")
    if (
        not data
        or data.get("code") != asset["code"]
        or data.get("name") != asset["expected_name"]
    ):
        raise ValueError("行情标的身份不匹配")
    if str(data.get("market")) != asset["secid"].split(".")[0]:
        raise ValueError("行情市场身份不匹配")
    now = (now or datetime.now(TZ)).astimezone(TZ)
    rows = []
    for line in data["klines"]:
        parts = line.split(",")
        opening, close, high, low, volume = parts[1:6]
        rows.append(
            {
                "date": parts[0],
                "open": opening,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    session = apply_sessions(rows, asset.get("session_profile", "cn_equity"), now)
    validate_rows(session["rows"])
    return {
        **session,
        "code": asset["code"],
        "name": data["name"],
        "basis": "market_ohlc",
        "provider": "eastmoney",
    }


def parse_yahoo(payload, asset, as_of, now=None):
    chart = payload.get("chart", {})
    if chart.get("error") or not chart.get("result"):
        raise ValueError(f"行情源返回错误：{chart.get('error')}")
    data = chart["result"][0]
    meta = data["meta"]
    if meta.get("symbol") != asset["code"] or meta.get("instrumentType") != "INDEX":
        raise ValueError("指数身份或品种类型不匹配")
    exchange_tz = ZoneInfo(meta["exchangeTimezoneName"])
    quotes = data["indicators"]["quote"][0]
    timestamps = data["timestamp"]
    for key in ("open", "high", "low", "close", "volume"):
        if len(quotes[key]) != len(timestamps):
            raise ValueError("OHLC 序列长度不匹配")
    rows = []
    now = now or datetime.now(TZ)
    for i, stamp in enumerate(timestamps):
        session = datetime.fromtimestamp(stamp, exchange_tz)
        day = session.date().isoformat()
        rows.append(
            {
                "date": day,
                **{
                    key: quotes[key][i]
                    for key in ("open", "high", "low", "close", "volume")
                },
            }
        )
    period = meta.get("currentTradingPeriod", {}).get("regular")
    session = apply_sessions(
        rows, asset.get("session_profile", "us_equity"), now, period
    )
    validate_rows(session["rows"])
    return {
        **session,
        "code": asset["code"],
        "name": meta.get("longName", asset["label"]),
        "basis": "market_ohlc",
        "provider": "yahoo",
        "exchange_timezone": meta["exchangeTimezoneName"],
        "quote_currency": meta.get("currency"),
    }


REQUEST_RETRY_DELAYS = (0.25, 0.5)
SOURCE_COOLDOWN = 30
REQUEST_DEADLINE = 62
_cooldowns = {}
_cooldown_lock = Lock()


class SourceCoolingDown(ValueError):
    pass


async def get_market_response(client, provider, url, params):
    with _cooldown_lock:
        if _cooldowns.get(provider, 0) > monotonic():
            raise SourceCoolingDown(f"{provider} 连续断连，暂缓请求")
    disconnects = 0
    for delay in (*REQUEST_RETRY_DELAYS, None):
        try:
            return await client.get(url, params=params)
        except (
            httpx.RemoteProtocolError,
            httpx.ConnectError,
            httpx.ConnectTimeout,
        ) as exc:
            if isinstance(exc, httpx.RemoteProtocolError):
                disconnects += 1
            else:
                disconnects = 0
            if delay is None:
                if disconnects == len(REQUEST_RETRY_DELAYS) + 1:
                    with _cooldown_lock:
                        _cooldowns[provider] = monotonic() + SOURCE_COOLDOWN
                raise
            await asyncio.sleep(delay + uniform(0, delay))


def fetch(asset, as_of):
    try:
        return asyncio.run(fetch_market_snapshot(asset, as_of))
    except TimeoutError as exc:
        raise httpx.TimeoutException(f"行情采集超过 {REQUEST_DEADLINE} 秒") from exc


async def fetch_market_snapshot(asset, as_of):
    async with (
        asyncio.timeout(REQUEST_DEADLINE),
        httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 ChanceTrack/0.5"},
        ) as client,
    ):
        if asset["provider"] == "eastmoney":
            url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            params = {
                "secid": asset["secid"],
                "klt": "101",
                "fqt": "0",
                "lmt": "600",
                "end": "20500101",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56",
            }
            params["_"] = str(int(datetime.now(TZ).timestamp() * 1000))
            response = await get_market_response(client, asset["provider"], url, params)
            response.raise_for_status()
            snapshot = parse_eastmoney(response.json(), asset, as_of)
        elif asset["provider"] == "yahoo":
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(asset['code'], safe='')}"
            response = await get_market_response(
                client, asset["provider"], url, {"range": "2y", "interval": "1d"}
            )
            response.raise_for_status()
            snapshot = parse_yahoo(response.json(), asset, as_of)
        else:
            raise ValueError("未知行情来源")
    snapshot.update(source=str(response.url), fetched_at=datetime.now(TZ).isoformat())
    return snapshot
