"""Pure daily-bar strategies. No fetching, persistence, delivery or trading."""

from datetime import datetime

import pandas as pd

from .market_sessions import session_bounds


def donchian(rows, closes, entry_period, exit_period):
    upper = pd.Series([row["high"] for row in rows]).shift().rolling(entry_period).max()
    lower = pd.Series([row["low"] for row in rows]).shift().rolling(exit_period).min()
    ready = pd.Series(False, index=closes.index)
    events, reasons = [None] * len(rows), [""] * len(rows)
    watching = False
    for index, close in enumerate(closes):
        period = exit_period if watching else entry_period
        ready.iloc[index] = index >= period
        if not ready.iloc[index]:
            reasons[index] = f"预热还需 {period - index} 根日线"
            continue
        high, low = upper.iloc[index], lower.iloc[index]
        threshold = f"前期最低 {low:.4f}" if watching else f"前期最高 {high:.4f}"
        reasons[index] = f"价格 {close:.4f}；{threshold}"
        if watching and close < low:
            events[index] = "通道退出"
            watching = False
        elif not watching and close > high:
            events[index] = "向上突破"
            watching = True
    return events, reasons, ready, {"突破轨": upper, "退出轨": lower}


def wilder(values, period):
    averages = values.rolling(period).mean()
    start = averages.first_valid_index()
    if start is not None:
        seeded = values.loc[start:].copy()
        seeded.iloc[0] = averages.loc[start]
        averages.loc[start:] = seeded.ewm(alpha=1 / period, adjust=False).mean()
    return averages


def pullback(closes, p):
    trend = closes.rolling(p["trend"]).mean()
    recovery = closes.rolling(p["recovery"]).mean()
    change = closes.diff()
    gain = wilder(change.clip(lower=0), p["rsi"])
    loss = wilder(-change.clip(upper=0), p["rsi"])
    rsi = (100 * gain / (gain + loss)).mask((gain == 0) & (loss == 0), 50)
    required = max(p["trend"], p["recovery"], p["rsi"] + 2)
    ready = pd.Series(range(1, len(closes) + 1)) >= required
    events, reasons = [None] * len(closes), [""] * len(closes)
    watching = False
    for i, close in enumerate(closes):
        if not ready.iloc[i]:
            reasons[i] = f"预热还需 {required - i - 1} 根日线"
            continue
        level, repair, strength = trend.iloc[i], recovery.iloc[i], rsi.iloc[i]
        reasons[i] = (
            f"价格 {close:.4f}；SMA{p['trend']} {level:.4f}；"
            f"SMA{p['recovery']} {repair:.4f}；RSI {rsi.iloc[i - 1]:.4f} → {strength:.4f}；阈值 {p['threshold']}"
        )
        if watching:
            if close < level:
                events[i] = "趋势失效"
            elif close > repair:
                events[i] = "回调修复"
            if events[i]:
                watching = False
        elif close > level and rsi.iloc[i - 1] >= p["threshold"] > strength:
            events[i] = "短线回调"
            watching = True
    return events, reasons, ready, {"趋势均线": trend, "修复均线": recovery}


def true_range(highs, lows, closes):
    previous = closes.shift()
    return pd.concat(
        [highs - lows, (highs - previous).abs(), (lows - previous).abs()], axis=1
    ).max(axis=1)


def trailing_exit(highs, lows, closes, p):
    breakout = highs.shift().rolling(p["entry"]).max()
    atr = wilder(true_range(highs, lows, closes), p["period"])
    candidate = highs.rolling(p["period"]).max() - p["multiple"] * atr
    line = pd.Series(float("nan"), index=closes.index)
    required = max(p["entry"] + 1, p["period"])
    ready = pd.Series(range(1, len(closes) + 1)) >= required
    events, reasons = [None] * len(closes), [""] * len(closes)
    trailing = None
    for i, close in enumerate(closes):
        if not ready.iloc[i]:
            reasons[i] = f"预热还需 {required - i - 1} 根日线"
            continue
        ceiling, next_line = breakout.iloc[i], candidate.iloc[i]
        if trailing is None:
            reasons[i] = (
                f"价格 {close:.4f}；突破轨 {ceiling:.4f}；ATR {atr.iloc[i]:.4f}；候选线 {next_line:.4f}"
            )
            if close > ceiling:
                trailing = next_line
                events[i] = "向上突破"
        else:
            reasons[i] = (
                f"价格 {close:.4f}；前一日跟踪线 {trailing:.4f}；ATR {atr.iloc[i]:.4f}；候选线 {next_line:.4f}"
            )
            if close < trailing:
                events[i] = "跟踪退出"
                line.iloc[i] = trailing
                trailing = None
            else:
                trailing = max(trailing, next_line)
        if trailing is not None:
            line.iloc[i] = trailing
    return events, reasons, ready, {"突破轨": breakout, "跟踪线": line}


def squeeze(highs, lows, closes, p):
    period = p["period"]
    mean = closes.rolling(period).mean()
    boll = closes.rolling(period).std(ddof=0) * p["stddev"]
    ranges = (
        true_range(highs, lows, closes) if p["range"] == "true_range" else highs - lows
    )
    channel = ranges.rolling(period).mean() * p["multiple"]
    midpoint = (highs.rolling(period).max() + lows.rolling(period).min()) / 2
    centered = closes - (midpoint + mean) / 2
    # Endpoint of the least-squares line over the rolling, equally spaced samples.
    center = (period - 1) / 2
    weights = [i - center for i in range(period)]
    denominator = sum(x * x for x in weights)
    momentum = centered.rolling(period).apply(
        lambda values: values.mean() + center * (values * weights).sum() / denominator,
        raw=True,
    )
    ready = momentum.notna()
    required = period * 2 - 1
    events, reasons = [None] * len(closes), [""] * len(closes)
    days = 0
    for i in range(len(closes)):
        if not ready.iloc[i]:
            reasons[i] = f"预热还需 {required - i - 1} 根日线"
        if i < period - 1:
            continue
        if boll.iloc[i] < channel.iloc[i]:
            days += 1
            if ready.iloc[i]:
                reasons[i] = f"已收缩 {days} 个交易日"
        elif days:
            if ready.iloc[i]:
                value = momentum.iloc[i]
                direction = "向上" if value > 0 else "向下" if value < 0 else "方向未定"
                events[i] = f"释放 · {direction}"
                reasons[i] = (
                    f"收缩 {days} 个交易日；回归动量 {value:.6f}；"
                    f"布林半宽 {boll.iloc[i]:.4f}；肯特纳半宽 {channel.iloc[i]:.4f}"
                )
            days = 0
    return (
        events,
        reasons,
        ready,
        {
            "布林上轨": mean + boll,
            "布林下轨": mean - boll,
            "肯特纳上轨": mean + channel,
            "肯特纳下轨": mean - channel,
        },
    )


STATEFUL = {"donchian", "pullback", "trailing_exit", "squeeze"}


def evaluate(snapshot, configs, asset, as_of):
    rows = snapshot["rows"]
    values = pd.Series([r["close"] for r in rows], dtype=float)
    provisional = snapshot.get("provisional_date") == rows[-1]["date"]
    ci = len(rows) - 2 if provisional else len(rows) - 1
    stale = (
        datetime.fromisoformat(as_of).date()
        - datetime.fromisoformat(rows[-1]["date"]).date()
    ).days > 7
    calculated = {}
    by_id = {s["id"]: s for s in configs}

    def compute(sid):
        if sid in calculated:
            return calculated[sid]
        s = by_id[sid]
        kind, p = s["kind"], s.get("params", {})
        n = len(rows)
        events, reasons, overlays, macd = [None] * n, [""] * n, {}, []
        ready = pd.Series([True] * n)
        if kind == "macd":
            dif = (
                values.ewm(span=p["fast"], adjust=False).mean()
                - values.ewm(span=p["slow"], adjust=False).mean()
            )
            dea = dif.ewm(span=p["signal"], adjust=False).mean()
            spread = dif - dea
            ready = pd.Series(
                [i >= max(120, p["slow"] + p["signal"]) - 1 for i in range(n)]
            )
            macd = [
                {
                    "time": r["date"],
                    "dif": float(dif.iloc[i]),
                    "dea": float(dea.iloc[i]),
                    "histogram": float(2 * spread.iloc[i]),
                }
                for i, r in enumerate(rows)
            ]
        elif kind == "ma":
            short, long = (
                values.rolling(p["fast"]).mean(),
                values.rolling(p["slow"]).mean(),
            )
            spread = short - long
            ready = long.notna() & long.shift().notna()
            overlays = {"短均线": short, "长均线": long}
        elif kind in ("boll_break", "boll_return"):
            mid = values.rolling(p["period"]).mean()
            std = values.rolling(p["period"]).std(ddof=0)
            upper, lower = mid + p["stddev"] * std, mid - p["stddev"] * std
            ready = mid.notna() & mid.shift().notna()
            overlays = {"上轨": upper, "中轨": mid, "下轨": lower}
        elif kind == "donchian":
            events, reasons, ready, overlays = donchian(
                rows, values, p["entry"], p["exit"]
            )
        elif kind in {"pullback", "trailing_exit", "squeeze"}:
            if kind == "pullback":
                events, reasons, ready, overlays = pullback(values, p)
            else:
                highs = pd.Series([row["high"] for row in rows], dtype=float)
                lows = pd.Series([row["low"] for row in rows], dtype=float)
                calculate = trailing_exit if kind == "trailing_exit" else squeeze
                events, reasons, ready, overlays = calculate(highs, lows, values, p)
        elif kind == "combination":
            trigger = compute(s["trigger"])
            ready = trigger["ready"].copy()
            band = compute(s["band"]) if s.get("filter") else None
            if band:
                ready &= band["ready"]
            for i in range(n):
                event = trigger["events"][i]
                allowed = not band or (
                    values.iloc[i] > band["overlays"]["中轨"].iloc[i]
                    if s["filter"] == "above_mid"
                    else values.iloc[i] < band["overlays"]["中轨"].iloc[i]
                )
                if event and allowed:
                    events[i] = event
                    reasons[i] = f"触发条件成立：{trigger['reasons'][i]}"
                    if band:
                        midpoint = band["overlays"]["中轨"].iloc[i]
                        reasons[i] += f"；布林中轨 {midpoint:.4f}，过滤通过"
                elif event:
                    reasons[i] = "触发事件存在，但布林中轨过滤条件未满足"
                else:
                    reasons[i] = "无触发事件"
        else:
            raise ValueError(f"未知策略类型：{kind}")
        if kind in ("macd", "ma", "boll_break", "boll_return"):
            for i in range(1, n):
                if not ready.iloc[i]:
                    continue
                if kind in ("macd", "ma"):
                    if spread.iloc[i - 1] <= 0 < spread.iloc[i]:
                        events[i] = "金叉"
                    elif spread.iloc[i - 1] >= 0 > spread.iloc[i]:
                        events[i] = "死叉"
                    reasons[i] = (
                        f"前值差 {spread.iloc[i - 1]:.6f} → 本值差 {spread.iloc[i]:.6f}"
                    )
                    if kind == "ma":
                        reasons[i] += (
                            f"；短均线 {short.iloc[i]:.4f}；长均线 {long.iloc[i]:.4f}"
                        )
                    else:
                        point = macd[i]
                        reasons[i] += (
                            f"；DIF {point['dif']:.6f}；DEA {point['dea']:.6f}；柱 {point['histogram']:.6f}"
                        )
                elif kind == "boll_break":
                    if (
                        values.iloc[i - 1] <= upper.iloc[i - 1]
                        and values.iloc[i] > upper.iloc[i]
                    ):
                        events[i] = "向上突破上轨"
                    elif (
                        values.iloc[i - 1] >= lower.iloc[i - 1]
                        and values.iloc[i] < lower.iloc[i]
                    ):
                        events[i] = "向下突破下轨"
                else:
                    if (
                        values.iloc[i - 1] > upper.iloc[i - 1]
                        and lower.iloc[i] <= values.iloc[i] <= upper.iloc[i]
                    ):
                        events[i] = "上轨外回归轨内"
                    elif (
                        values.iloc[i - 1] < lower.iloc[i - 1]
                        and lower.iloc[i] <= values.iloc[i] <= upper.iloc[i]
                    ):
                        events[i] = "下轨外回归轨内"
                if kind.startswith("boll"):
                    price_label = "盘中价格" if provisional and i == n - 1 else "收盘"
                    reasons[i] = (
                        f"{price_label} {values.iloc[i]:.4f}；下轨 {lower.iloc[i]:.4f}；中轨 {mid.iloc[i]:.4f}；上轨 {upper.iloc[i]:.4f}"
                    )
        result = {
            "events": events,
            "reasons": reasons,
            "ready": ready,
            "overlays": overlays,
            "macd": macd,
        }
        calculated[sid] = result
        return result

    outputs, layers = [], []
    for sid in asset.get("strategies", ["macd"]):
        s = by_id[sid]
        c = compute(sid)
        version = f"{sid}:v{s.get('revision', 1)}"
        if s["kind"] == "combination":
            # Dependency revisions form part of the identity even if the combination text did not change.
            dependencies = [s["trigger"]] + ([s["band"]] if s.get("filter") else [])
            version += ":" + ":".join(
                f"{d}-v{by_id[d].get('revision', 1)}" for d in dependencies
            )
        activation = asset.get("activated", {}).get(sid)
        historical = False
        if activation:
            bounds = session_bounds(asset["session_profile"], rows[ci]["date"])
            confirmed_at = bounds["confirm_after"] if bounds else None
            if not provisional and snapshot.get("session", {}).get("confirm_after"):
                confirmed_at = datetime.fromisoformat(
                    snapshot["session"]["confirm_after"]
                )
            historical = confirmed_at is None or confirmed_at <= datetime.fromisoformat(
                activation
            )
        available = bool(c["ready"].iloc[ci]) and not stale
        event = c["events"][ci] if available else None
        preview = (
            c["events"][-1]
            if provisional and c["ready"].iloc[-1] and not stale
            else None
        )
        outputs.append(
            {
                "id": sid,
                "name": s["name"],
                "group": s.get("group", "未分组"),
                "kind": s["kind"],
                "version": version,
                "config": {
                    **s,
                    "dependencies": {
                        d: by_id[d]
                        for d in (
                            [s["trigger"]] + ([s["band"]] if s.get("filter") else [])
                        )
                    },
                }
                if s["kind"] == "combination"
                else s,
                "event": event,
                "event_date": rows[ci]["date"],
                "preview_event": preview,
                "bar_date": rows[-1]["date"],
                "is_provisional": provisional,
                "state": "ready" if available else "unavailable",
                "detail": c["reasons"][ci]
                if available
                else (
                    "行情过期"
                    if stale
                    else c["reasons"][ci]
                    if s["kind"] in STATEFUL
                    else "历史样本不足"
                ),
                "preview_detail": c["reasons"][-1] if provisional else "",
                "stateful": s["kind"] in STATEFUL,
                "historical_reference": historical,
            }
        )
    for sid, c in calculated.items():
        s = by_id[sid]
        for label, series in c["overlays"].items():
            layers.append(
                {
                    "id": f"{sid}:{label}",
                    "name": f"{s['name']} · {label}",
                    "kind": s["kind"],
                    "points": [
                        {"time": rows[i]["date"], "value": float(v)}
                        for i, v in enumerate(series)
                        if pd.notna(v)
                    ],
                }
            )
    return outputs, layers, calculated


def chart_events(snapshot, strategies, calculated):
    return [
        {
            "time": row["date"],
            "name": strategy["name"],
            "version": strategy["version"],
            "config": strategy["config"],
            "event": event,
            "price": row["close"],
            "detail": detail,
            "provisional": snapshot.get("provisional_date") == row["date"],
        }
        for strategy in strategies
        for row, event, detail in zip(
            snapshot["rows"],
            calculated[strategy["id"]]["events"],
            calculated[strategy["id"]]["reasons"],
        )
        if event
    ]
