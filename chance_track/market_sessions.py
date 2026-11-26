"""Trading-day/session semantics shared by all price adapters.

SGE's Friday night belongs to Monday, and a holiday eve has no night session.
Provider bars remain authoritative: this module never manufactures trading data.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from chinese_calendar import is_workday

PROFILES = {
    "cn_equity": {"timezone": "Asia/Shanghai", "open": time(9, 30), "close": time(15)},
    "sge_gold": {"timezone": "Asia/Shanghai", "open": time(9), "close": time(15, 30)},
    "us_equity": {
        "timezone": "America/New_York",
        "open": time(9, 30),
        "close": time(16),
    },
}
CONFIRMATION_BUFFER = timedelta(minutes=5)


def cn_trading_day(day):
    # Administrative weekend make-up days are not exchange sessions.
    return day.weekday() < 5 and is_workday(day)


def session_bounds(profile, day, provider_period=None):
    config = PROFILES[profile]
    tz = ZoneInfo(config["timezone"])
    day = date.fromisoformat(day) if isinstance(day, str) else day
    if (profile != "us_equity" and not cn_trading_day(day)) or (
        profile == "us_equity" and day.weekday() >= 5
    ):
        return None
    opening = datetime.combine(day, config["open"], tz)
    closing = datetime.combine(day, config["close"], tz)
    intervals = [(opening, closing, "day")]
    source = "market_profile"
    if profile == "cn_equity":
        intervals = [
            (opening, datetime.combine(day, time(11, 30), tz), "day"),
            (datetime.combine(day, time(13), tz), closing, "day"),
        ]
    elif profile == "sge_gold":
        previous = day - timedelta(days=1)
        while not cn_trading_day(previous):
            previous -= timedelta(days=1)
        gap = (day - previous).days
        # A normal overnight session, or Friday night leading into Monday.
        # A holiday gap has no eve night session, matching SGE holiday notices.
        if gap == 1 or (gap == 3 and previous.weekday() == 4 and day.weekday() == 0):
            opening = datetime.combine(previous, time(20), tz)
            night_end = datetime.combine(previous + timedelta(days=1), time(2, 30), tz)
            intervals.insert(0, (opening, night_end, "night"))
    elif provider_period:
        start = datetime.fromtimestamp(provider_period["start"], tz)
        end = datetime.fromtimestamp(provider_period["end"], tz)
        if start.date() == day and start < end and end.date() == day:
            opening, closing = start, end
            intervals = [(opening, closing, "day")]
            source = "provider_regular_session"
    return {
        "open": opening,
        "close": closing,
        "confirm_after": closing + CONFIRMATION_BUFFER,
        "intervals": intervals,
        "timezone": config["timezone"],
        "source": source,
    }


def apply_sessions(rows, profile, now, provider_period=None, latest_quote_at=None):
    if profile not in PROFILES:
        raise ValueError(f"未知交易时段配置：{profile}")
    selected = []
    latest = None
    for row in rows:
        bounds = session_bounds(profile, row["date"], provider_period)
        if bounds is None or now < bounds["open"]:
            continue
        selected.append(row)
        latest = bounds
    if not selected:
        raise ValueError("没有已开始交易的日线")
    tz = ZoneInfo(latest["timezone"])
    quote_at = (
        (
            latest_quote_at.replace(tzinfo=tz)
            if latest_quote_at.tzinfo is None
            else latest_quote_at.astimezone(tz)
        )
        if latest_quote_at is not None
        else None
    )
    if quote_at is not None and quote_at < latest["close"]:
        provisional = True
        phase = next(
            (kind for start, end, kind in latest["intervals"] if start <= quote_at < end),
            "break",
        )
        label = "盘中快照（未收盘）"
    else:
        provisional = now < latest["confirm_after"]
        if not provisional:
            phase, label = "closed", "已收盘"
        elif now >= latest["close"]:
            phase, label = "settling", "收盘确认中"
        else:
            phase = next(
                (kind for start, end, kind in latest["intervals"] if start <= now < end),
                "break",
            )
            label = {"day": "交易中", "night": "夜盘交易中", "break": "休市间隔"}[phase]
    session = {
        "profile": profile,
        "timezone": latest["timezone"],
        "phase": phase,
        "label": label,
        "trading_day": selected[-1]["date"],
        "open_at": latest["open"].isoformat(),
        "close_at": latest["close"].isoformat(),
        "close_at_beijing": latest["close"]
        .astimezone(ZoneInfo("Asia/Shanghai"))
        .isoformat(),
        "confirm_after": latest["confirm_after"].isoformat(),
        "schedule_source": latest["source"],
    }
    if quote_at is not None:
        session["quote_at"] = quote_at.isoformat()
    return {
        "rows": selected,
        "provisional_date": selected[-1]["date"] if provisional else None,
        "session_policy": "market_sessions_v1",
        "session": session,
    }
