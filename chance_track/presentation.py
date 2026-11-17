"""Pure report presentation: current observation versus prior closed-bar references.

This module does not fetch prices, compute indicators, write history or send notifications.
"""


def signal_rows(report):
    """Keep market confirmation separate from report time and delivery history."""
    rows = []
    for item in report["items"]:
        asset, result = item["asset"], item.get("result", {})
        base = {
            "asset_id": asset["id"],
            "asset": asset["label"],
            "section": "current",
            "ticker": asset.get("ticker") or asset.get("code") or "",
        }
        day = result.get("bar_date") or result.get("nav_date") or "—"
        if item["status"] in ("error", "disabled"):
            rows.append(
                {
                    **base,
                    "date": "—",
                    "signal": "无法判断",
                    "state": item["status"],
                    "status": "采集失败" if item["status"] == "error" else "未启用",
                    "detail": item.get("error", ""),
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
