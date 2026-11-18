"""Pure daily-bar strategies. No fetching, persistence, delivery or trading."""

from datetime import datetime

import pandas as pd

from .market_sessions import session_bounds


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
        else:
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
                    reasons[i] = "触发条件成立；" + (
                        "布林中轨过滤通过" if band else "无附加过滤"
                    )
                elif event:
                    reasons[i] = "触发事件存在，但布林中轨过滤条件未满足"
                else:
                    reasons[i] = "无触发事件"
        if kind != "combination":
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
                    reasons[i] = (
                        f"收盘 {values.iloc[i]:.4f}；下轨 {lower.iloc[i]:.4f}；中轨 {mid.iloc[i]:.4f}；上轨 {upper.iloc[i]:.4f}"
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
                else ("行情过期" if stale else "历史样本不足"),
                "preview_detail": c["reasons"][-1] if provisional else "",
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
