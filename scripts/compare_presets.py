"""Replay fixed ChanceTrack presets on supplied, completed market OHLC snapshots."""

import argparse
import json
from pathlib import Path
from statistics import median

import pandas as pd

from chance_track.data import validate_rows
from chance_track.strategies import (
    donchian,
    pullback,
    relative_strength,
    squeeze,
    true_range,
    wilder,
)

SPLIT = "2026-04-01"
COST = 0.001  # each side; index levels proxy trading prices in this experiment


def summarize(items):
    return f"{median(items):.2%}" if items else "—"


def percent(n, total):
    return f"{n / total:.1%}" if total else "—"


def event_days(events, label):
    return [i for i, event in enumerate(events) if event == label]


def horizon(rows, signals, allowed):
    return [
        (i, rows[i + 1 : i + 6]) for i in signals if i in allowed and i + 5 in allowed
    ]


def finite_exit(closes, start, line):
    return next((i for i in range(start + 1, len(closes)) if closes[i] < line[i]), None)


def compare(rows, market, source):
    dates = [row["date"] for row in rows]
    highs, lows, closes, opens = (
        pd.Series([row[key] for row in rows])
        for key in ("high", "low", "close", "open")
    )
    pull, _, _, _ = pullback(
        closes, {"trend": 200, "recovery": 5, "rsi": 2, "threshold": 10}
    )
    channel, _, _, bands = donchian(rows, closes, 20, 10)
    released, _, _, _ = squeeze(
        highs,
        lows,
        closes,
        {"period": 20, "stddev": 2, "multiple": 1.5, "range": "true_range"},
    )
    change = closes.diff()
    gains = wilder(change.clip(lower=0), 2)
    losses = wilder(-change.clip(upper=0), 2)
    rsi = (100 * gains / (gains + losses)).mask((gains == 0) & (losses == 0), 50)
    prior = rsi.shift()
    baseline = rsi.index[(prior >= 10) & (rsi < 10)].tolist()
    repair = closes.rolling(5).mean()
    mean = closes.rolling(20).mean()
    spread = closes.rolling(20).std(ddof=0) * 2
    upper, lower = mean + spread, mean - spread
    boll = {
        "向上": closes.index[
            (closes > upper) & (closes.shift() <= upper.shift())
        ].tolist(),
        "向下": closes.index[
            (closes < lower) & (closes.shift() >= lower.shift())
        ].tolist(),
    }
    starts = event_days(channel, "向上突破")
    atr = wilder(true_range(highs, lows, closes), 22)
    candidates = highs.rolling(22).max() - 3 * atr
    print(
        f"### {market} · {source}\n\n覆盖 {dates[0]} 至 {dates[-1]}，{len(dates)} 个已经收盘的交易日。"
    )
    for phase, allowed in (
        ("开发", {i for i, date in enumerate(dates) if date < SPLIT}),
        ("样本外", {i for i, date in enumerate(dates) if date >= SPLIT}),
    ):
        # All comparisons use the same 200-bar warmup cutoff.
        allowed = {i for i in allowed if i >= 199}
        print(f"\n**{phase} · {len(allowed)} 个可比较交易日**\n")
        for label, signals in (
            ("趋势过滤 RSI", event_days(pull, "短线回调")),
            ("单用 RSI", baseline),
        ):
            windows = horizon(rows, signals, allowed)
            returns, adverse = [], []
            repaired = 0
            for i, bars in windows:
                entry = bars[0]["open"]
                returns.append(
                    bars[-1]["close"] * (1 - COST) / (entry * (1 + COST)) - 1
                )
                adverse.append(min(0, min(bar["low"] for bar in bars) / entry - 1))
                repaired += any(closes[j] > repair[j] for j in range(i + 1, i + 6))
            print(
                f"- {label} {len(windows)} 个有效样本，窗口截断排除 {len(set(signals) & allowed) - len(windows)} 个，5 日净收益中位数 {summarize(returns)}，最大不利波动中位数 {summarize(adverse)}，5 日修复 {percent(repaired, len(windows))}。"
            )
        sample_starts = set(starts) & allowed
        boll_starts = set(boll["向上"]) & allowed
        windows = horizon(rows, starts, allowed)
        back = sum(
            any(bar["close"] < bands["突破轨"][i] for bar in bars)
            for i, bars in windows
        )
        returns, drawdowns, pairs = [], [], []
        for i in sorted(sample_starts):
            channel_end = finite_exit(closes, i, bands["退出轨"])
            if channel_end is None or channel_end + 1 not in allowed:
                continue
            returns.append(
                opens[channel_end + 1] * (1 - COST) / (opens[i + 1] * (1 + COST)) - 1
            )
            path = closes.iloc[i + 1 : channel_end + 1]
            drawdowns.append(min(0, (path / path.cummax() - 1).min()))
            trail = candidates.iloc[i:].cummax().shift()
            atr_end = next(
                (j for j in range(i + 1, len(closes)) if closes[j] < trail[j]), None
            )
            if atr_end is not None and max(channel_end, atr_end) + 5 in allowed:
                pairs.append((i, channel_end, atr_end))
        overlap = percent(
            len(sample_starts & boll_starts), len(sample_starts | boll_starts)
        )
        print(
            f"- 唐奇安突破 {len(sample_starts)} 次，布林上破 {len(boll_starts)} 次，事件日期交并比 {overlap}。5 日内跌回 {percent(back, len(windows))}（{len(windows)} 个样本）。完整退出 {len(returns)} 次，净收益中位数 {summarize(returns)}，收盘最大回撤中位数 {summarize(drawdowns)}。"
        )
        for column, label in ((1, "唐奇安退出"), (2, "ATR 退出")):
            givebacks, durations = [], []
            regained = 0
            for pair in pairs:
                start, end = pair[0], pair[column]
                peak = highs.iloc[start : end + 1].max()
                givebacks.append(1 - closes[end] / peak)
                durations.append(end - start)
                regained += highs.iloc[end + 1 : end + 6].max() >= peak
            days = f"{median(durations):g}" if durations else "—"
            print(
                f"- 同一批突破，{label} {len(pairs)} 个配对样本，峰值回吐中位数 {summarize(givebacks)}，持续 {days} 个交易日（中位数），5 日重回原高点 {percent(regained, len(pairs))}。"
            )
        for direction, sign in (("向上", 1), ("向下", -1)):
            for label, signals in (
                ("收缩释放", event_days(released, f"释放 · {direction}")),
                ("布林突破", boll[direction]),
            ):
                windows = horizon(rows, signals, allowed)
                forward, reverse = [], []
                for _, bars in windows:
                    entry = bars[0]["open"]
                    moves = [
                        sign * (bar[key] / entry - 1)
                        for bar in bars
                        for key in ("high", "low")
                    ]
                    forward.append(max(0, *moves))
                    reverse.append(min(0, *moves))
                print(
                    f"- {direction}{label} {len(windows)} 个有效样本，窗口截断排除 {len(set(signals) & allowed) - len(windows)} 个，5 日顺向波动中位数 {summarize(forward)}，逆向波动中位数 {summarize(reverse)}。"
                )
    print()


def compare_relative(snapshots, benchmark):
    reference = snapshots[benchmark]
    by_day = {row["date"]: row for row in reference["rows"]}
    params = {"benchmark": benchmark, "trend": 200, "relative_period": 60}
    print(f"### 共同交易日相对强弱 · 基准 {benchmark}\n")
    for code, snapshot in snapshots.items():
        if code == benchmark or snapshot.get("quote_currency") != "USD":
            continue
        rows = snapshot["rows"]
        closes = pd.Series([row["close"] for row in rows], dtype=float)
        signals, _, _, _ = relative_strength(
            {**snapshot, "benchmarks": {benchmark: reference}}, closes, params
        )
        common = [
            (row, by_day[row["date"]], event)
            for row, event in zip(rows, signals)
            if row["date"] in by_day
        ]
        for phase, selected in (
            ("开发", lambda date: date < SPLIT),
            ("样本外", lambda date: date >= SPLIT),
        ):
            excess = []
            excluded = 0
            for i, (row, _, event) in enumerate(common):
                if not event or not selected(row["date"]):
                    continue
                if i + 20 >= len(common) or not selected(common[i + 20][0]["date"]):
                    excluded += 1
                    continue
                entry, benchmark_entry, _ = common[i + 1]
                exit_row, benchmark_exit, _ = common[i + 20]
                asset_growth = exit_row["close"] / entry["open"]
                reference_growth = benchmark_exit["close"] / benchmark_entry["open"]
                excess.append(
                    (asset_growth - reference_growth) * (1 - COST) / (1 + COST)
                )
            print(
                f"- {code} · {phase} · {len(excess)} 个有效样本，窗口截断排除 {excluded} 个，20 个共同交易日净超额表现中位数 {summarize(excess)}。"
            )
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshots", type=Path, nargs="+")
    parser.add_argument("--benchmark", help="例如 ^GSPC，须出现在输入快照中")
    args = parser.parse_args()
    snapshots = {}
    for path in args.snapshots:
        snapshot = json.loads(path.read_text())
        if snapshot.get("basis") != "market_ohlc":
            parser.error(f"{path}: 仅支持市场 OHLC")
        rows = validate_rows(
            [
                row
                for row in snapshot["rows"]
                if row["date"] != snapshot.get("provisional_date")
            ]
        )
        code = snapshot.get("code", path.stem)
        snapshots[code] = {**snapshot, "rows": rows, "provisional_date": None}
        compare(rows, code, snapshot.get("provider", "未知来源"))
    if args.benchmark:
        if args.benchmark not in snapshots:
            parser.error("输入快照不含指定基准")
        compare_relative(snapshots, args.benchmark)


if __name__ == "__main__":
    main()
