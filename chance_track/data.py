"""Market OHLC adapters. No fund NAV proxies and no synthetic candles."""

import asyncio
import math
from datetime import date, datetime, timedelta
from random import uniform
from threading import Lock
from time import monotonic
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from .market_sessions import apply_sessions, cn_trading_day, session_bounds

TZ = ZoneInfo("Asia/Shanghai")

PROVIDER_LABELS = {
    "yahoo": "Yahoo Finance",
    "eastmoney": "东方财富",
    "sse": "上海证券交易所",
    "szse": "深圳证券交易所",
}


def provider_label(provider):
    return PROVIDER_LABELS.get(provider, provider or "未知来源")


def validate_rows(rows):
    if not rows:
        raise ValueError("没有可用的日线")
    dates = []
    for row in rows:
        date.fromisoformat(row["date"])
        dates.append(row["date"])
        for key in ("open", "high", "low", "close"):
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
        volume = row.get("volume")
        row["volume"] = None if volume in (None, "", "-") else float(volume)
        if row["volume"] is not None and (not math.isfinite(row["volume"]) or row["volume"] < 0):
            raise ValueError("成交量无效")
    if dates != sorted(set(dates)):
        raise ValueError("行情日期重复或未按时间升序")
    return rows


def _valid_positive_ohlc(opening, high, low, close):
    try:
        values = [float(opening), float(high), float(low), float(close)]
    except (TypeError, ValueError):
        return False
    return all(math.isfinite(value) and value > 0 for value in values)


def _etf_volume_meta(asset, multiplier):
    if asset.get("kind") == "etf":
        return {"volume_unit": "手", "volume_multiplier": multiplier}
    return {}


class SourceCoolingDown(ValueError):
    pass


class StaleMarketSnapshot(ValueError):
    pass


class IdentityMismatch(ValueError):
    pass


class InsufficientMarketHistory(ValueError):
    pass


class MarketFetchError(ValueError):
    def __init__(self, message, *, fetch_status="error", attempts=()):
        super().__init__(message)
        self.fetch_status = fetch_status
        self.attempts = list(attempts)


def _asset_exchange(spec):
    provider = spec.get("provider")
    if provider == "sse":
        return "sh"
    if provider == "szse":
        return "sz"
    if provider == "eastmoney":
        secid = str(spec.get("secid") or "")
        if secid.startswith("1."):
            return "sh"
        if secid.startswith("0."):
            return "sz"
    return None


def parse_eastmoney(payload, asset, as_of, now=None):
    data = payload.get("data")
    if (
        not data
        or data.get("code") != asset["code"]
        or data.get("name") != asset["expected_name"]
    ):
        raise IdentityMismatch("行情标的身份不匹配")
    if str(data.get("market")) != asset["secid"].split(".")[0]:
        raise IdentityMismatch("行情市场身份不匹配")
    now = (now or datetime.now(TZ)).astimezone(TZ)
    rows = []
    for line in data["klines"]:
        parts = line.split(",")
        opening, close, high, low = parts[1:5]
        volume = parts[5] if len(parts) > 5 else None
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
        **(
            {"volume_unit": "千克", "volume_multiplier": 0.01}
            if asset["secid"] == "118.AU9999"
            and asset.get("session_profile") == "sge_gold"
            else {}
        ),
    }


def parse_yahoo(payload, asset, as_of, now=None):
    chart = payload.get("chart", {})
    if chart.get("error") or not chart.get("result"):
        raise ValueError(f"行情源返回错误：{chart.get('error')}")
    data = chart["result"][0]
    meta = data["meta"]
    if meta.get("symbol") != asset["code"] or meta.get("instrumentType") != "INDEX":
        raise IdentityMismatch("指数身份或品种类型不匹配")
    exchange_tz = ZoneInfo(meta["exchangeTimezoneName"])
    quotes = data["indicators"]["quote"][0]
    timestamps = data["timestamp"]
    volumes = quotes.get("volume")
    for key in ("open", "high", "low", "close"):
        if len(quotes[key]) != len(timestamps):
            raise ValueError("OHLC 序列长度不匹配")
    if volumes is not None and len(volumes) != len(timestamps):
        raise ValueError("成交量序列长度不匹配")
    rows = []
    now = now or datetime.now(TZ)
    for i, stamp in enumerate(timestamps):
        session = datetime.fromtimestamp(stamp, exchange_tz)
        day = session.date().isoformat()
        rows.append(
            {
                "date": day,
                **{key: quotes[key][i] for key in ("open", "high", "low", "close")},
                "volume": volumes[i] if volumes is not None else None,
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


def _yyyymmdd_to_iso(value):
    digits = str(value)
    if len(digits) != 8 or not digits.isdigit():
        raise ValueError("上交所行情日期格式无效")
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def _parse_sse_quote_at(day_iso, raw_time):
    if raw_time is None:
        return None
    digits = str(raw_time).strip()
    if not digits.isdigit() or not (5 <= len(digits) <= 6):
        return None
    digits = digits.zfill(6)
    hh, mm, ss = int(digits[:2]), int(digits[2:4]), int(digits[4:6])
    if hh > 23 or mm > 59 or ss > 59:
        return None
    day = date.fromisoformat(day_iso)
    return datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=TZ)


def parse_sse(dayk_payload, snap_payload, asset, as_of, now=None):
    if (
        not isinstance(dayk_payload, dict)
        or not isinstance(snap_payload, dict)
        or str(dayk_payload.get("code")) != asset.get("code")
        or str(snap_payload.get("code")) != asset.get("code")
    ):
        raise IdentityMismatch("上交所行情标的代码不匹配")
    snap = snap_payload.get("snap")
    if (
        not isinstance(snap, list)
        or len(snap) < 9
        or snap[0] != asset.get("expected_name")
    ):
        raise IdentityMismatch("上交所行情标的身份不匹配")
    rows = []
    for entry in dayk_payload.get("kline") or []:
        if len(entry) < 5:
            continue
        rows.append(
            {
                "date": _yyyymmdd_to_iso(entry[0]),
                "open": entry[1],
                "high": entry[2],
                "low": entry[3],
                "close": entry[4],
                "volume": entry[5] if len(entry) > 5 else None,
            }
        )
    latest_quote_at = None
    if snap_payload.get("date") is not None:
        snap_day = _yyyymmdd_to_iso(snap_payload["date"])
        snap_quote_at = _parse_sse_quote_at(snap_day, snap_payload.get("time"))
        if (
            (not rows or snap_day > rows[-1]["date"])
            and snap_quote_at is not None
            and _valid_positive_ohlc(snap[2], snap[3], snap[4], snap[5])
        ):
            rows.append(
                {
                    "date": snap_day,
                    "open": snap[2],
                    "high": snap[3],
                    "low": snap[4],
                    "close": snap[5],
                    "volume": snap[8],
                }
            )
            latest_quote_at = snap_quote_at
        elif rows and snap_day == rows[-1]["date"] and snap_quote_at is not None:
            latest_quote_at = snap_quote_at
    now = (now or datetime.now(TZ)).astimezone(TZ)
    session = apply_sessions(
        rows,
        asset.get("session_profile", "cn_equity"),
        now,
        latest_quote_at=latest_quote_at,
    )
    validate_rows(session["rows"])
    return {
        **session,
        "code": asset["code"],
        "name": snap[0],
        "basis": "market_ohlc",
        "provider": "sse",
        **_etf_volume_meta(asset, 0.01),
    }


def parse_szse(hist_payload, asset, as_of, now=None, time_payload=None):
    data = hist_payload.get("data") if isinstance(hist_payload, dict) else None
    if (
        not isinstance(data, dict)
        or str(data.get("code")) != asset.get("code")
        or data.get("name") != asset.get("expected_name")
    ):
        raise IdentityMismatch("深交所行情标的身份不匹配")
    rows = []
    for entry in data.get("picupdata") or []:
        if not isinstance(entry, list) or len(entry) < 5:
            continue
        rows.append(
            {
                "date": entry[0],
                "open": entry[1],
                "high": entry[4],
                "low": entry[3],
                "close": entry[2],
                "volume": entry[7] if len(entry) > 7 else None,
            }
        )
    latest_quote_at = None
    if time_payload is not None:
        tdata = time_payload.get("data") if isinstance(time_payload, dict) else None
        if (
            not isinstance(tdata, dict)
            or str(tdata.get("code")) != asset.get("code")
            or tdata.get("name") != asset.get("expected_name")
        ):
            raise IdentityMismatch("深交所盘中行情标的身份不匹配")
        market_time = str(tdata.get("marketTime") or "").strip()
        try:
            quote_dt = datetime.strptime(market_time, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=TZ
            )
        except ValueError:
            quote_dt = None
        if quote_dt is not None:
            tday = quote_dt.date().isoformat()
            if (not rows or tday > rows[-1]["date"]) and _valid_positive_ohlc(
                tdata.get("open"), tdata.get("high"), tdata.get("low"), tdata.get("now")
            ):
                rows.append(
                    {
                        "date": tday,
                        "open": tdata["open"],
                        "high": tdata["high"],
                        "low": tdata["low"],
                        "close": tdata["now"],
                        "volume": tdata.get("volume"),
                    }
                )
                latest_quote_at = quote_dt
            elif rows and tday == rows[-1]["date"]:
                latest_quote_at = quote_dt
    now = (now or datetime.now(TZ)).astimezone(TZ)
    session = apply_sessions(
        rows,
        asset.get("session_profile", "cn_equity"),
        now,
        latest_quote_at=latest_quote_at,
    )
    validate_rows(session["rows"])
    return {
        **session,
        "code": asset["code"],
        "name": data["name"],
        "basis": "market_ohlc",
        "provider": "szse",
        **_etf_volume_meta(asset, 1),
    }


def active_trading_day(profile, now):
    if profile not in ("cn_equity", "sge_gold"):
        return None
    local = now.astimezone(TZ)
    try:
        candidates = [local.date()]
        if profile == "sge_gold":
            next_day = local.date() + timedelta(days=1)
            while not cn_trading_day(next_day):
                next_day += timedelta(days=1)
            candidates.append(next_day)
        for day in reversed(candidates):
            bounds = session_bounds(profile, day)
            if bounds and bounds["open"] <= local:
                if day == local.date() or local <= bounds["confirm_after"]:
                    return day.isoformat()
    except NotImplementedError:
        return None
    return None


def verify_session_freshness(
    snapshot,
    asset,
    as_of,
    now=None,
    *,
    source_mode=None,
    is_fallback=False,
    has_fallbacks=False,
):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    latest_bar = snapshot["rows"][-1]["date"]
    profile = asset.get("session_profile", "cn_equity")
    active_day = active_trading_day(profile, now)
    if active_day is not None:
        if latest_bar < active_day:
            raise StaleMarketSnapshot(
                f"当前交易日 {active_day} 行情尚未更新（仅返回至 {latest_bar}）"
            )
        if source_mode == "close_only":
            bounds = session_bounds(profile, active_day)
            if bounds and now < bounds["confirm_after"]:
                raise StaleMarketSnapshot(
                    f"该备用源仅支持收盘后核对，{active_day} 尚未收盘确认"
                )
    quote_at_raw = (snapshot.get("session") or {}).get("quote_at")
    if quote_at_raw:
        quote_at = datetime.fromisoformat(quote_at_raw)
        latest_bounds = session_bounds(profile, latest_bar)
        if (
            latest_bounds
            and now >= latest_bounds["confirm_after"]
            and quote_at < latest_bounds["close"]
        ):
            raise StaleMarketSnapshot(
                f"快照时间 {quote_at.astimezone(TZ).strftime('%H:%M:%S')} 早于收盘时间，尚非收盘确认行情"
            )
    if (is_fallback or has_fallbacks) and (
        date.fromisoformat(as_of) - date.fromisoformat(latest_bar)
    ).days > 7:
        raise StaleMarketSnapshot(f"行情已过期（最新交易日 {latest_bar}）")


REQUEST_RETRY_DELAYS = (0.25, 0.5)
SOURCE_COOLDOWN = 30
REQUEST_DEADLINE = 62
_cooldowns = {}
_cooldown_lock = Lock()


async def get_market_response(client, provider, url, params, headers=None):
    with _cooldown_lock:
        if _cooldowns.get(provider, 0) > monotonic():
            raise SourceCoolingDown(f"{provider} 连续断连，暂缓请求")
    disconnects = 0
    for delay in (*REQUEST_RETRY_DELAYS, None):
        try:
            kwargs = {"params": params}
            if headers:
                kwargs["headers"] = headers
            return await client.get(url, **kwargs)
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


def source_specs(asset):
    primary = {
        key: asset[key]
        for key in ("provider", "code", "secid", "expected_name")
        if asset.get(key)
    }
    primary["role"] = "primary"
    specs = [primary]
    seen_providers = {primary.get("provider")}
    primary_code = asset.get("code")
    primary_exchange = _asset_exchange(primary)
    for entry in asset.get("fallbacks", []):
        provider = entry.get("provider")
        if not provider or provider in seen_providers:
            continue
        seen_providers.add(provider)
        spec = {
            "provider": provider,
            "code": primary_code,
            "expected_name": entry.get("expected_name") or asset.get("expected_name"),
            "mode": entry.get("mode"),
            "role": "fallback",
        }
        if entry.get("secid") is not None:
            spec["secid"] = entry["secid"]
        mismatch = None
        if entry.get("code") is not None and entry.get("code") != primary_code:
            mismatch = "备用源标的代码与主标的不一致"
        else:
            entry_exchange = _asset_exchange(spec)
            if (
                primary_exchange
                and entry_exchange
                and primary_exchange != entry_exchange
            ):
                mismatch = "备用源交易所与主标的不一致"
        if mismatch:
            spec["identity_error"] = mismatch
        specs.append(spec)
    return specs


async def _fetch_single_source(client, asset, as_of, now):
    provider = asset.get("provider")
    if provider == "eastmoney":
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": asset["secid"],
            "klt": "101",
            "fqt": "0",
            "lmt": "600",
            "end": "20500101",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56",
            "_": str(int(now.timestamp() * 1000)),
        }
        response = await get_market_response(client, provider, url, params)
        response.raise_for_status()
        return parse_eastmoney(response.json(), asset, as_of, now=now), str(response.url)
    if provider == "yahoo":
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(asset['code'], safe='')}"
        response = await get_market_response(
            client, provider, url, {"range": "2y", "interval": "1d"}
        )
        response.raise_for_status()
        return parse_yahoo(response.json(), asset, as_of, now=now), str(response.url)
    if provider == "sse":
        headers = {"Referer": "https://www.sse.com.cn/"}
        dayk_url = f"https://yunhq.sse.com.cn:32042/v1/sh1/dayk/{quote(asset['code'], safe='')}"
        snap_url = f"https://yunhq.sse.com.cn:32042/v1/sh1/snap/{quote(asset['code'], safe='')}"
        dayk_resp = await get_market_response(
            client, provider, dayk_url, {"begin": "-600", "end": "-1"}, headers=headers
        )
        dayk_resp.raise_for_status()
        snap_resp = await get_market_response(
            client, provider, snap_url, {}, headers=headers
        )
        snap_resp.raise_for_status()
        return (
            parse_sse(dayk_resp.json(), snap_resp.json(), asset, as_of, now=now),
            str(dayk_resp.url),
        )
    if provider == "szse":
        headers = {"Referer": "https://www.szse.cn/"}
        hist_url = "https://www.szse.cn/api/market/ssjjhq/getHistoryData"
        hist_resp = await get_market_response(
            client,
            provider,
            hist_url,
            {"cycleType": "32", "marketId": "1", "code": asset["code"]},
            headers=headers,
        )
        hist_resp.raise_for_status()
        hist_json = hist_resp.json()
        time_json = None
        active_day = active_trading_day(asset.get("session_profile", "cn_equity"), now)
        pic = (hist_json.get("data") or {}).get("picupdata") or []
        last_hist_date = (
            pic[-1][0] if pic and isinstance(pic[-1], list) and pic[-1] else None
        )
        if active_day and (not last_hist_date or str(last_hist_date) < active_day):
            time_url = "https://www.szse.cn/api/market/ssjjhq/getTimeData"
            time_resp = await get_market_response(
                client,
                provider,
                time_url,
                {"marketId": "1", "code": asset["code"]},
                headers=headers,
            )
            time_resp.raise_for_status()
            time_json = time_resp.json()
        return (
            parse_szse(hist_json, asset, as_of, now=now, time_payload=time_json),
            str(hist_resp.url),
        )
    raise ValueError("未知行情来源")


def _classify_attempt_error(exc):
    if isinstance(exc, SourceCoolingDown):
        return "cooling_down"
    if isinstance(
        exc,
        (
            TimeoutError,
            httpx.RemoteProtocolError,
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.TimeoutException,
        ),
    ):
        return "disconnected"
    if isinstance(exc, StaleMarketSnapshot):
        return "stale"
    if isinstance(exc, InsufficientMarketHistory):
        return "insufficient_history"
    if isinstance(exc, IdentityMismatch) or "身份" in str(exc):
        return "identity_mismatch"
    return "error"


def fetch(asset, as_of, now=None):
    try:
        return asyncio.run(fetch_market_snapshot(asset, as_of, now=now))
    except TimeoutError as exc:
        raise httpx.TimeoutException(f"行情采集超过 {REQUEST_DEADLINE} 秒") from exc


async def fetch_market_snapshot(asset, as_of, now=None):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    specs = source_specs(asset)
    multi_source = len(specs) > 1
    per_source_timeout = min(12.0, float(REQUEST_DEADLINE)) if multi_source else 20
    client_timeout = (
        httpx.Timeout(per_source_timeout, connect=min(5.0, per_source_timeout))
        if multi_source
        else per_source_timeout
    )
    attempts = []
    primary_provider = asset.get("provider")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + REQUEST_DEADLINE
    catch_types = (
        (TimeoutError, httpx.HTTPError, ValueError, KeyError, TypeError, IndexError)
        if multi_source
        else (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError)
    )

    async def try_candidates(client):
        for index, spec in enumerate(specs):
            provider = spec.get("provider")
            target = {
                **asset,
                **{
                    k: v
                    for k, v in spec.items()
                    if k not in ("role", "mode", "identity_error")
                },
            }
            try:
                if spec.get("identity_error"):
                    raise IdentityMismatch(spec["identity_error"])
                if multi_source:
                    remaining_candidates = len(specs) - index
                    remaining_budget = deadline - loop.time()
                    if remaining_budget <= 0:
                        raise TimeoutError(f"超出采集总预算 {REQUEST_DEADLINE} 秒")
                    candidate_budget = min(
                        per_source_timeout,
                        max(0.01, remaining_budget / remaining_candidates),
                    )
                    async with asyncio.timeout(candidate_budget):
                        snapshot, url_str = await _fetch_single_source(
                            client, target, as_of, now
                        )
                else:
                    snapshot, url_str = await _fetch_single_source(
                        client, target, as_of, now
                    )
                if snapshot.get("code") != asset.get("code"):
                    raise IdentityMismatch("返回标的代码与主标的不一致")
                verify_session_freshness(
                    snapshot,
                    target,
                    as_of,
                    now,
                    source_mode=spec.get("mode"),
                    is_fallback=(spec["role"] == "fallback"),
                    has_fallbacks=multi_source,
                )
                confirmed_rows = [
                    row
                    for row in snapshot["rows"]
                    if row["date"] != snapshot.get("provisional_date")
                ]
                if multi_source and len(confirmed_rows) < 120:
                    raise InsufficientMarketHistory(
                        f"已收盘日线仅 {len(confirmed_rows)} 根，不足 120 根分析基线"
                    )
                attempts.append(
                    {
                        "provider": provider,
                        "provider_label": provider_label(provider),
                        "role": spec["role"],
                        "status": "ok",
                        "bar_date": snapshot["rows"][-1]["date"],
                    }
                )
                snapshot.update(
                    source=url_str,
                    fetched_at=datetime.now(TZ).isoformat(),
                    primary_provider=primary_provider,
                    fallback_used=spec["role"] == "fallback",
                    fetch_status="fallback_ok"
                    if spec["role"] == "fallback"
                    else "primary_ok",
                    attempts=attempts,
                )
                return snapshot
            except catch_types as exc:
                attempts.append(
                    {
                        "provider": provider,
                        "provider_label": provider_label(provider),
                        "role": spec["role"],
                        "status": _classify_attempt_error(exc),
                        "detail": f"{type(exc).__name__}: {str(exc) or '采集超过候选时限'}",
                    }
                )
        return None

    async with httpx.AsyncClient(
        timeout=client_timeout,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 ChanceTrack/0.9"},
    ) as client:
        if multi_source:
            snapshot = await try_candidates(client)
        else:
            async with asyncio.timeout(REQUEST_DEADLINE):
                snapshot = await try_candidates(client)
    if snapshot is not None:
        return snapshot

    statuses = [entry["status"] for entry in attempts]
    if statuses and all(status == "cooling_down" for status in statuses):
        aggregate_status = "cooling_down"
    elif "cooling_down" in statuses and "disconnected" not in statuses:
        aggregate_status = "cooling_down"
    elif "disconnected" in statuses:
        aggregate_status = "disconnected"
    elif "stale" in statuses:
        aggregate_status = "stale"
    elif "insufficient_history" in statuses:
        aggregate_status = "insufficient_history"
    else:
        aggregate_status = "error"
    summary = "；".join(
        f"{entry['provider_label']}（{'主源' if entry['role'] == 'primary' else '备用源'}）：{entry['detail']}"
        for entry in attempts
    )
    raise MarketFetchError(
        summary or "未知行情来源",
        fetch_status=aggregate_status,
        attempts=attempts,
    )
