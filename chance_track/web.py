import json
import secrets
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import storage
from .configuration import (
    configuration_history,
    configuration_version,
    load_configuration,
    save_configuration,
)
from .data import TZ
from .notifications import NOTIFICATION_TITLE, build_message
from .presentation import signal_rows
from .runner import CheckInProgress, is_running, run
from .workspace import preview, register_routes


def create_app(db_path):
    storage.initialize(db_path)
    app = FastAPI(title="Chance Track", docs_url=None, redoc_url=None)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "static"),
        name="static",
    )

    def static_version():
        static = Path(__file__).parent / "static"
        return max(
            (static / name).stat().st_mtime_ns
            for name in ("app.css", "app.js", "workspace.mjs", "watchlist.mjs", "manage.js")
        )

    token = secrets.token_urlsafe(32)
    register_routes(app, db_path, token)

    @app.get("/")
    @app.get("/reports/{report_id}")
    def dashboard(request: Request, report_id: int | None = None):
        selected = storage.report(db_path, report_id)
        if report_id and not selected:
            raise HTTPException(404, "报告不存在")
        items = selected["items"] if selected else []
        chart_items = [
            {
                "asset": item["asset"],
                "result": item.get("result"),
                "fetched_at": (item.get("snapshot") or {}).get("fetched_at"),
            }
            for item in items
        ]
        summary = {
            "total": len(items),
            "ready": sum(item["status"] == "ok" for item in items),
            "events": sum(
                sum(
                    bool(s.get("new_event"))
                    for s in item.get("result", {}).get("strategies", [])
                )
                if "strategies" in item.get("result", {})
                else bool(item.get("result", {}).get("new_event"))
                for item in items
            ),
            "confirmed": sum(
                sum(
                    bool(s.get("event"))
                    for s in item.get("result", {}).get("strategies", [])
                )
                if "strategies" in item.get("result", {})
                else bool(item.get("result", {}).get("event"))
                for item in items
            ),
            "previews": sum(
                sum(
                    bool(s.get("preview_event"))
                    for s in item.get("result", {}).get("strategies", [])
                )
                if "strategies" in item.get("result", {})
                else bool(item.get("result", {}).get("preview_event"))
                for item in items
            ),
            "issues": sum(item["status"] != "ok" for item in items),
        }
        notification = (
            storage.notification(db_path, selected["id"]) if selected else None
        )
        notification_view = None
        if selected:
            if notification and notification.get("message"):
                message = notification["message"]
                note = (
                    f"系统通知已提交 · {datetime.fromisoformat(notification['sent_at']).astimezone(TZ).strftime('%Y-%m-%d %H:%M:%S')}（北京时间）"
                    if notification["sent_at"]
                    else f"系统通知提交失败 · {notification['error']}"
                )
            else:
                message, _ = build_message(
                    selected, storage.notified_event_keys(db_path)
                )
                note = (
                    "历史通知未保存原文，以下为预览"
                    if notification and notification["sent_at"]
                    else "通知预览 · 当前报告尚未发送系统通知"
                )
            rows = signal_rows(selected)
            notification_view = {
                "title": NOTIFICATION_TITLE,
                "message": message,
                "note": note,
                "rows": rows,
                "groups": [
                    {
                        "key": "current",
                        "title": "本次观察快照",
                        "description": "按本报告检查时的行情展示，按标的与策略排列；不是实时行情。",
                        "rows": [row for row in rows if row["section"] == "current"],
                    },
                    {
                        "key": "reference",
                        "title": "上一收盘日参考",
                        "description": "以下属于盘中标的的上一根已收盘K线，不是当前交易日新产生的信号。",
                        "rows": [row for row in rows if row["section"] == "reference"],
                    },
                ],
                "confirmed": sum(row["state"] == "confirmed" for row in rows),
                "pending": sum(row["state"] == "pending_cross" for row in rows),
                "issues": sum(row["state"] in ("error", "warning") for row in rows),
                "checked_at": datetime.fromisoformat(selected["created_at"])
                .astimezone(TZ)
                .strftime("%Y-%m-%d %H:%M:%S"),
            }
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "asset_version": static_version(),
                "report": selected,
                "reports": storage.reports(db_path),
                "chart_items": chart_items,
                "summary": summary,
                "decisions": storage.decisions(db_path, selected["id"])
                if selected
                else [],
                "token": token,
                "historical": report_id is not None
                and report_id != (storage.report(db_path) or {}).get("id"),
                "notification": notification,
                "notification_view": notification_view,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/manage")
    def manage(request: Request):
        return management_page(request)

    def management_page(request, error=None, status=200, draft=None):
        return templates.TemplateResponse(
            request=request,
            name="manage.html",
            context={
                "config": draft or load_configuration(db_path),
                "versions": configuration_history(db_path),
                "token": token,
                "error": error,
                "asset_version": static_version(),
            },
            status_code=status,
        )

    @app.get("/configuration/{version}")
    def historical_configuration(version: int):
        config = configuration_version(db_path, version)
        if not config:
            raise HTTPException(404, "配置版本不存在")
        return config

    @app.post("/manage")
    def save_management(
        request: Request,
        csrf: str = Form(),
        version: int = Form(),
        payload: str = Form(),
    ):
        if not secrets.compare_digest(csrf, token):
            raise HTTPException(403, "请刷新页面后重试")
        draft = None
        try:
            draft = json.loads(payload)
            save_configuration(db_path, draft, version)
        except (ValueError, KeyError, TypeError, httpx.HTTPError) as exc:
            return management_page(
                request,
                str(exc),
                400,
                draft
                if isinstance(draft, dict)
                and "assets" in draft
                and "strategies" in draft
                else None,
            )
        return RedirectResponse("/manage", status_code=303)

    @app.get("/api/update-status")
    def update_status():
        latest = storage.report(db_path)
        return JSONResponse(
            {
                "running": is_running(db_path),
                "latest": {
                    "id": latest["id"],
                    "created_at": latest["created_at"],
                }
                if latest
                else None,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/refresh")
    def refresh(request: Request, csrf: str = Form()):
        if not secrets.compare_digest(csrf, token):
            raise HTTPException(403, "请刷新页面后重试")
        try:
            report_id = run(db_path, send=False)
        except CheckInProgress as exc:
            raise HTTPException(409, str(exc)) from exc
        if "application/json" in request.headers.get("accept", ""):
            result = storage.report(db_path, report_id)
            failures = sum(item["status"] == "error" for item in result["items"])
            return {"report_id": report_id, "failures": failures}
        return RedirectResponse(f"/reports/{report_id}", status_code=303)

    @app.get("/reports/{report_id}/snapshot")
    def snapshot(report_id: int):
        result = storage.report(db_path, report_id)
        if not result:
            raise HTTPException(404, "报告不存在")
        return result

    @app.post("/decisions")
    def decide(
        report_id: int = Form(),
        asset_id: str = Form(),
        action: str = Form(),
        execution: str = Form(),
        note: str = Form(""),
        exploration: str = Form(""),
        csrf: str = Form(),
    ):
        if not secrets.compare_digest(csrf, token):
            raise HTTPException(403, "请刷新页面后重试")
        try:
            context = None
            if exploration:
                basis = json.loads(exploration)
                if basis["source"].get("report_id") != report_id:
                    raise ValueError("探索依据必须属于当前报告")
                context = preview(
                    db_path, basis["source"], asset_id, basis["strategies"]
                )
            storage.add_decision(
                db_path,
                report_id,
                asset_id,
                action,
                execution,
                note,
                datetime.now(TZ).isoformat(),
                context=context,
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return RedirectResponse(f"/reports/{report_id}", status_code=303)

    return app
