"""Pure indicator computation: one result supplies signals and chart series."""

from datetime import date

import pandas as pd

RULE = {
    "version": "macd-market-sessions-v4",
    "fast": 12,
    "slow": 26,
    "signal": 9,
    "adjust": False,
    "histogram_multiplier": 2,
    "minimum_samples": 120,
    "max_age_calendar_days": 7,
    "basis": "market_ohlc",
    "intraday_preview": True,
    "session_policy": "market_sessions_v1",
}


def analyze(snapshot, as_of):
    if snapshot.get("basis") != "market_ohlc":
        raise ValueError("新分析仅接受真实行情 OHLC，不接受基金净值")
    rows = snapshot["rows"]
    if not rows:
        raise ValueError("至少需要 120 个已完成日线样本")
    is_provisional = snapshot.get("provisional_date") == rows[-1]["date"]
    confirmed_index = len(rows) - 2 if is_provisional else len(rows) - 1
    if confirmed_index + 1 < RULE["minimum_samples"]:
        raise ValueError("至少需要 120 个已完成日线样本")
    values = pd.Series([row["close"] for row in rows], dtype=float)
    dif = (
        values.ewm(span=12, adjust=False).mean()
        - values.ewm(span=26, adjust=False).mean()
    )
    dea = dif.ewm(span=9, adjust=False).mean()
    spread = dif - dea
    age = (date.fromisoformat(as_of) - date.fromisoformat(rows[-1]["date"])).days
    warnings = (
        ["行情超过 7 个自然日未更新，停止生成新事件"]
        if age > RULE["max_age_calendar_days"]
        else []
    )
    points, crosses = [], []
    for i, row in enumerate(rows):
        points.append(
            {
                "time": row["date"],
                "dif": float(dif.iloc[i]),
                "dea": float(dea.iloc[i]),
                "histogram": float(2 * spread.iloc[i]),
            }
        )
        if i < RULE["minimum_samples"] - 1:
            continue
        kind = None
        if spread.iloc[i - 1] <= 0 < spread.iloc[i]:
            kind = "golden"
        elif spread.iloc[i - 1] >= 0 > spread.iloc[i]:
            kind = "death"
        if kind:
            cross = {"time": row["date"], "kind": kind}
            if is_provisional and row["date"] == rows[-1]["date"]:
                cross["provisional"] = True
            crosses.append(cross)
    confirmed_date = rows[confirmed_index]["date"]
    confirmed_cross = next(
        (x for x in reversed(crosses) if x["time"] == confirmed_date), None
    )
    event = (
        (
            "金叉 · 买入观察"
            if confirmed_cross["kind"] == "golden"
            else "死叉 · 卖出观察"
        )
        if confirmed_cross and not warnings
        else None
    )
    preview_cross = next((x for x in reversed(crosses) if x.get("provisional")), None)
    preview_event = (
        (
            "盘中金叉 · 待收盘确认"
            if preview_cross["kind"] == "golden"
            else "盘中死叉 · 待收盘确认"
        )
        if preview_cross and not warnings
        else None
    )
    change = rows[-1]["close"] - rows[-2]["close"]
    return {
        "is_provisional": is_provisional,
        "confirmed_date": confirmed_date,
        "event_date": confirmed_date,
        "preview_event": preview_event,
        "bar_date": rows[-1]["date"],
        "close": rows[-1]["close"],
        "change": change,
        "change_pct": change / rows[-2]["close"] * 100,
        "dif": float(dif.iloc[-1]),
        "dea": float(dea.iloc[-1]),
        "histogram": float(2 * spread.iloc[-1]),
        "position": "DIF 在 DEA 上方"
        if spread.iloc[-1] > 0
        else "DIF 在 DEA 下方或重合",
        "zero_axis": "DIF 在零轴上方" if dif.iloc[-1] > 0 else "DIF 在零轴下方或重合",
        "momentum": "柱体绝对值扩大"
        if abs(spread.iloc[-1]) > abs(spread.iloc[-2])
        else "柱体绝对值缩小或持平",
        "event": event,
        "warnings": warnings,
        "age_days": max(0, age),
        "session": snapshot.get("session"),
        "chart": {
            "candles": [
                {
                    "time": r["date"],
                    **{k: r[k] for k in ("open", "high", "low", "close")},
                }
                for r in rows
            ],
            "macd": points,
            "crosses": crosses,
        },
    }
