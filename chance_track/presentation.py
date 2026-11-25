"""Pure report presentation: current observation versus prior closed-bar references.

This module does not fetch prices, compute indicators, write history or send notifications.
"""

from .data import provider_label

ATTEMPT_STATUS_LABELS = {
    "ok": "成功",
    "cooling_down": "同源冷却未请求",
    "disconnected": "请求断连",
    "stale": "行情未更新",
    "identity_mismatch": "身份不符",
    "error": "失败",
}


def fetch_state_summary(item):
    asset = item["asset"]
    snapshot = item.get("snapshot") or {}
    result = item.get("result") or {}
    last_quote = item.get("last_quote")
    attempts = item.get("attempts") or snapshot.get("attempts") or []
    provider = item.get("provider") or snapshot.get("provider") or asset.get("provider")
    source_label = (
        item.get("provider_label")
        or snapshot.get("provider_label")
        or provider_label(provider)
    )
    attempt_notes = [
        f"{entry.get('provider_label') or provider_label(entry.get('provider'))}（{ATTEMPT_STATUS_LABELS.get(entry.get('status'), '失败')}）"
        for entry in attempts
    ]
    if item.get("status") == "disabled":
        return {
            "code": "disabled",
            "label": "未启用",
            "reason_label": "未启用",
            "snapshot_label": "未启用",
            "provider_label": source_label,
            "bar_date": "—",
            "confirmation": "未启用",
            "fetched_at": None,
            "detail": item.get("error", ""),
        }
    if item.get("status") == "error":
        code = item.get("fetch_status")
        if code not in ("cooling_down", "disconnected", "stale", "error"):
            err = item.get("error", "")
            if "SourceCoolingDown" in err:
                code = "cooling_down"
            elif any(token in err for token in ("RemoteProtocolError", "Connect", "Timeout")):
                code = "disconnected"
            elif "StaleMarketSnapshot" in err:
                code = "stale"
            else:
                code = "error"
        reason_label = {
            "cooling_down": "同源冷却未请求",
            "disconnected": "请求断连",
            "stale": "行情未更新",
            "error": "采集失败",
        }[code]
        snapshot_label = (
            f"仅有历史快照（{last_quote['bar_date']}）" if last_quote else "暂无行情"
        )
        confirmation = (
            "盘中待确认快照"
            if last_quote and last_quote.get("is_provisional") == 1
            else ("已收盘快照" if last_quote else "暂无行情")
        )
        return {
            "code": code,
            "label": f"{reason_label} · {snapshot_label}",
            "reason_label": reason_label,
            "snapshot_label": snapshot_label,
            "provider_label": source_label,
            "bar_date": last_quote["bar_date"] if last_quote else "—",
            "confirmation": confirmation,
            "fetched_at": last_quote.get("fetched_at") if last_quote else None,
            "detail": "；".join(attempt_notes) if attempt_notes else item.get("error", ""),
        }
    fallback_used = (
        item.get("fetch_status") == "fallback_ok"
        or snapshot.get("fetch_status") == "fallback_ok"
        or snapshot.get("fallback_used")
    )
    code = "fallback_ok" if fallback_used else "primary_ok"
    label = "备用源成功" if fallback_used else "主源正常"
    confirmation = (
        "未收盘 · 待确认"
        if result.get("is_provisional")
        else ("已收盘 · 已确认" if result.get("bar_date") or result.get("nav_date") else "—")
    )
    return {
        "code": code,
        "label": label,
        "reason_label": label,
        "snapshot_label": label,
        "provider_label": source_label,
        "bar_date": result.get("bar_date") or result.get("nav_date") or "—",
        "confirmation": confirmation,
        "fetched_at": snapshot.get("fetched_at"),
        "detail": "；".join(attempt_notes),
    }


def signal_rows(report):
    """Keep market confirmation separate from report time and delivery history."""
    rows = []
    for item in report["items"]:
        asset, result = item["asset"], item.get("result", {})
        summary = fetch_state_summary(item)
        base = {
            "asset_id": asset["id"],
            "asset": asset["label"],
            "section": "current",
            "ticker": asset.get("ticker") or asset.get("code") or "",
            "fetch_status": summary["code"],
            "fetch_label": summary["label"],
            "provider_label": summary["provider_label"],
        }
        day = result.get("bar_date") or result.get("nav_date") or "—"
        if item["status"] in ("error", "disabled"):
            detail = (
                f"{summary['label']} · {summary['detail']}"
                if item["status"] == "error" and summary["detail"]
                else (summary["label"] if item["status"] == "error" else item.get("error", ""))
            )
            rows.append(
                {
                    **base,
                    "date": "—",
                    "signal": "无法判断",
                    "state": item["status"],
                    "status": "采集失败" if item["status"] == "error" else "未启用",
                    "detail": detail,
                }
            )
            continue
        if result.get("warnings"):
            rows.append(
                {
                    **base,
                    "date": day,
                    "signal": "暂停判断",
                    "state": "warning",
                    "status": "数据需检查",
                    "detail": "；".join(result["warnings"]),
                }
            )
            continue
        if "strategies" in result:
            if not result["strategies"]:
                rows.append(
                    {
                        **base,
                        "date": day,
                        "signal": "未绑定策略",
                        "state": "idle",
                        "status": "仅观察行情",
                        "detail": "请在管理页绑定策略",
                    }
                )
            for strategy in result["strategies"]:
                label = f"{strategy['group']} / {strategy['name']}"
                common = {
                    **base,
                    "ticker": f"{base['ticker']} · {label}",
                    "strategy": strategy["name"],
                }
                available = strategy["state"] == "ready"
                pending = strategy.get("preview_event")
                event = strategy.get("event")
                rows.append(
                    {
                        **common,
                        "date": day,
                        "signal": (pending or "无盘中事件")
                        if result.get("is_provisional")
                        else (event or ("无事件" if available else "无法判断")),
                        "state": ("pending_cross" if pending else "pending")
                        if result.get("is_provisional")
                        else (
                            "confirmed"
                            if event
                            else ("idle" if available else "warning")
                        ),
                        "status": "未收盘 · 待确认"
                        if result.get("is_provisional")
                        else ("收盘已确认" if available else "数据不足"),
                        "detail": strategy.get("preview_detail")
                        if result.get("is_provisional")
                        else strategy["detail"],
                    }
                )
                if result.get("is_provisional") and event:
                    rows.append(
                        {
                            **common,
                            "section": "reference",
                            "date": strategy["event_date"],
                            "signal": event,
                            "state": "confirmed",
                            "status": "上一收盘日 · 已确认",
                            "detail": strategy["detail"],
                        }
                    )
            continue
        if result.get("event"):
            rows.append(
                {
                    **base,
                    "date": result.get("event_date")
                    or result.get("confirmed_date")
                    or day,
                    "signal": result["event"].split(" · ")[0],
                    "state": "confirmed",
                    "section": "reference"
                    if result.get("is_provisional")
                    else "current",
                    "status": "上一收盘日 · 已确认"
                    if result.get("is_provisional")
                    else "收盘已确认",
                    "detail": "不是当前未收盘K线的新信号"
                    if result.get("is_provisional")
                    else "",
                }
            )
        if result.get("is_provisional"):
            preview = result.get("preview_event")
            rows.append(
                {
                    **base,
                    "date": day,
                    "signal": preview.split(" · ")[0].removeprefix("盘中")
                    if preview
                    else "暂未交叉",
                    "state": "pending_cross" if preview else "pending",
                    "status": "未收盘 · 待确认",
                    "detail": "临时指标可能随价格改变",
                }
            )
        elif not result.get("event"):
            rows.append(
                {
                    **base,
                    "date": day,
                    "signal": "未形成交叉",
                    "state": "none",
                    "status": "已收盘 · 无交叉",
                    "detail": "",
                }
            )
    return rows
