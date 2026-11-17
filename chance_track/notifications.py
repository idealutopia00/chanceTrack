"""Native macOS delivery; success means submitted to osascript, not read by user."""

import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

from .presentation import signal_rows
from .storage import event_key, strategy_event_key

NOTIFICATION_TITLE = "Chance Track · 指数信号"


def notify(title, message):
    script = "on run argv\n display notification (item 2 of argv) with title (item 1 of argv)\nend run"
    subprocess.run(
        ["/usr/bin/osascript", "-e", script, title, message],
        check=True,
        capture_output=True,
        timeout=15,
    )


def build_message(report, notified_keys):
    if any("strategies" in item.get("result", {}) for item in report["items"]):
        return build_strategy_message(report, notified_keys)
    rows = signal_rows(report)
    by_id = {item["asset"]["id"]: item for item in report["items"]}
    confirmed = [row for row in rows if row["state"] == "confirmed"]
    pending_keys = {
        event_key(by_id[row["asset_id"]], report["rule"]["version"])
        for row in confirmed
    } - notified_keys
    confirmed.sort(
        key=lambda row: (
            event_key(by_id[row["asset_id"]], report["rule"]["version"])
            not in pending_keys
        )
    )
    previews = [row for row in rows if row["state"] == "pending_cross"]
    errors = sum(item["status"] == "error" for item in report["items"])
    lines = []
    for row in confirmed:
        status = "上一收盘日参考" if row["section"] == "reference" else row["status"]
        lines.append(f"{row['asset']}｜信号日 {row['date']}｜{row['signal']}｜{status}")
    for row in previews:
        lines.append(
            f"{row['asset']}｜信号日 {row['date']}｜{row['signal']}｜未收盘待确认"
        )
    if not lines:
        lines.append("本次没有确认或临时交叉信号")
    lines.append(
        f"当前收盘交叉{len(confirmed)}（首次通知{len(pending_keys)}），盘中待确认{len(previews)}，采集异常{errors}。"
    )
    if report.get("attempt", 1) > 1:
        lines.append(f"自动重试第{report['attempt']}次。")
    if report.get("late"):
        lines.append("补发：已过15:00决策时间。")
    if report.get("created_at"):
        checked_at = datetime.fromisoformat(report["created_at"]).astimezone(
            ZoneInfo("Asia/Shanghai")
        )
        lines.append(f"检查时间 {checked_at:%Y-%m-%d %H:%M}（北京时间）")
    lines.append("查看 http://127.0.0.1:8765")
    return "\n".join(lines), pending_keys


def build_strategy_message(report, notified_keys):
    pending, lines = set(), []
    for item in report["items"]:
        result = item.get("result", {})
        lines.append(item["asset"]["label"])
        if not result or result.get("warnings"):
            lines.append(
                "  无法判断 · "
                + (item.get("error") or "；".join(result.get("warnings", [])))
            )
            continue
        if not result.get("strategies"):
            lines.append("  未绑定策略 · 仅观察行情")
        for s in result.get("strategies", []):
            prefix = f"  {s['group']} / {s['name']}"
            if result.get("is_provisional"):
                lines.append(
                    f"{prefix} | {s['bar_date']} | {s.get('preview_event') or '无盘中事件'} | 未收盘待确认"
                )
            event = s.get("event")
            if event:
                key = strategy_event_key(item, s)
                fresh = key not in notified_keys and not s.get("historical_reference")
                if fresh:
                    pending.add(key)
                state = (
                    "首次提醒"
                    if fresh
                    else (
                        "启用前历史参考" if s.get("historical_reference") else "已提醒"
                    )
                )
                lines.append(
                    f"{prefix} | {s['event_date']} | {event} | 已确认 · {state}"
                )
            elif not result.get("is_provisional"):
                lines.append(
                    f"{prefix} | {s['event_date']} | {'无事件' if s['state'] == 'ready' else '无法判断'} | {s['detail']}"
                )
    return "\n".join(
        [
            f"新增确认提醒 {len(pending)} 项 · {report['created_at'][:16]}",
            *lines,
            "查看详情 http://127.0.0.1:8765",
        ]
    ), pending
