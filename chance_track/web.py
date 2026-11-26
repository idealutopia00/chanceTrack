"""FastAPI v1.0 JSON API + static SPA host for Chance Track."""

from datetime import datetime
from pathlib import Path
import re
import secrets
import threading

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import httpx
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import storage, workspace
from .configuration import ConfigurationConflict, DuplicateStrategy, load_configuration
from .data import TZ, fetch
from .runner import CheckInProgress, is_running, next_regular_check, revision, run

DIST_DIR = Path(__file__).parent / "static" / "dist"


def create_app(db_path):
    storage.initialize(db_path)
    load_configuration(db_path)

    app = FastAPI(title="Chance Track v1.0", docs_url=None, redoc_url=None)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )
    if (DIST_DIR / "assets").is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=DIST_DIR / "assets"),
            name="spa-assets",
        )

    token = secrets.token_urlsafe(32)
    runtime_revision = revision()

    def require_csrf(request: Request, payload: dict | None = None):
        supplied = request.headers.get("x-csrf-token") or (
            payload.get("csrf") if isinstance(payload, dict) else None
        )
        if not isinstance(supplied, str) or not secrets.compare_digest(supplied, token):
            raise HTTPException(403, "安全校验失效，请刷新页面后重试")

    def guarded(call):
        try:
            return call()
        except ConfigurationConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except DuplicateStrategy as exc:
            raise HTTPException(
                409,
                {
                    "message": str(exc),
                    "duplicate_id": exc.duplicate_id,
                    "match_id": exc.match_id,
                    "match_ids": exc.match_ids,
                },
            ) from exc
        except CheckInProgress as exc:
            raise HTTPException(409, str(exc)) from exc
        except (
            ValueError,
            KeyError,
            TypeError,
            StopIteration,
            IndexError,
            httpx.HTTPError,
        ) as exc:
            raise HTTPException(400, str(exc) or "请求参数无效") from exc

    # ------------------------------------------------------------------
    # Runtime health (required by install_launchd.py)
    # ------------------------------------------------------------------
    @app.get("/api/runtime-health", include_in_schema=False)
    def runtime_health():
        return {"revision": runtime_revision, "schema_version": storage.SCHEMA_VERSION}

    # ------------------------------------------------------------------
    # v1.0 JSON API
    # ------------------------------------------------------------------
    @app.get("/api/v1/bootstrap")
    def api_bootstrap():
        version, assets, strategies = workspace.asset_summaries(db_path)
        next_check = next_regular_check(datetime.now(TZ))
        latest_run = storage.get_check_run(db_path)
        return JSONResponse(
            {
                "csrf": token,
                "version": version,
                "assets": assets,
                "strategies": strategies,
                "templates": storage.list_templates(db_path),
                "pinned_note": storage.get_note(db_path),
                "next_regular_check": next_check.isoformat() if next_check else None,
                "running": is_running(db_path),
                "latest_run": (
                    {
                        "id": latest_run["id"],
                        "day": latest_run["day"],
                        "kind": latest_run["kind"],
                        "created_at": latest_run["created_at"],
                    }
                    if latest_run
                    else None
                ),
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/v1/catalog")
    def api_catalog(q: str = ""):
        return {"assets": workspace.search_catalog(db_path, q)}

    @app.post("/api/v1/assets")
    def api_add_asset(request: Request, payload: dict = Body()):
        require_csrf(request, payload)
        return guarded(lambda: workspace.add_asset(db_path, payload))

    @app.patch("/api/v1/assets/{asset_id}")
    def api_update_asset(asset_id: str, request: Request, payload: dict = Body()):
        require_csrf(request, payload)
        return guarded(lambda: workspace.update_asset(db_path, asset_id, payload))

    @app.put("/api/v1/assets/order")
    def api_reorder_assets(request: Request, payload: dict = Body()):
        require_csrf(request, payload)
        return guarded(
            lambda: workspace.reorder_assets(db_path, payload["ids"], payload["version"])
        )

    @app.get("/api/v1/assets/{asset_id}/bars")
    def api_asset_bars(asset_id: str):
        return JSONResponse(
            guarded(lambda: workspace.compute_asset_chart(db_path, asset_id)),
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/v1/assets/{asset_id}/quote")
    def api_refresh_asset_quote(asset_id: str, request: Request, payload: dict = Body(default={})):
        require_csrf(request, payload)

        def _fetch():
            cfg = load_configuration(db_path)
            asset = next((a for a in cfg["assets"] if a["id"] == asset_id), None)
            if not asset:
                raise ValueError("标的不存在")
            snap = fetch(asset, datetime.now(TZ).date().isoformat())
            storage.save_bars(db_path, asset_id, snap)
            return workspace.compute_asset_chart(db_path, asset_id)

        return guarded(_fetch)

    @app.post("/api/v1/assets/{asset_id}/preview")
    def api_preview_asset(asset_id: str, request: Request, payload: dict = Body()):
        require_csrf(request, payload)
        return guarded(
            lambda: workspace.compute_asset_chart(
                db_path, asset_id, strategies_override=payload["strategies"]
            )
        )

    @app.put("/api/v1/assets/{asset_id}/monitoring")
    def api_update_monitoring(asset_id: str, request: Request, payload: dict = Body()):
        require_csrf(request, payload)
        return guarded(lambda: workspace.update_asset_monitoring(db_path, asset_id, payload))

    @app.get("/api/v1/assets/{asset_id}/decisions")
    def api_asset_decisions(asset_id: str):
        return {"decisions": storage.list_decisions(db_path, asset_id=asset_id)}

    @app.post("/api/v1/assets/{asset_id}/decisions")
    def api_add_decision(asset_id: str, request: Request, payload: dict = Body()):
        require_csrf(request, payload)

        def _save():
            evidence = None
            if payload.get("exploration_strategies"):
                computed = workspace.compute_asset_chart(
                    db_path, asset_id, strategies_override=payload["exploration_strategies"]
                )
                evidence = {
                    "strategies": payload["exploration_strategies"],
                    "bar_date": (computed.get("result") or {}).get("bar_date"),
                    "close": (computed.get("result") or {}).get("close"),
                    "strategy_results": (computed.get("result") or {}).get("strategies", []),
                }
            latest_bar = storage.last_bar(db_path, asset_id)
            bar_date = payload.get("bar_date") or (
                latest_bar["bar_date"] if latest_bar else datetime.now(TZ).date().isoformat()
            )
            latest_run = storage.get_check_run(db_path)
            decision_id = storage.add_decision(
                db_path,
                asset_id,
                bar_date,
                payload["action"],
                payload.get("execution", "未执行"),
                payload.get("note", ""),
                datetime.now(TZ).isoformat(),
                price_at_decision=payload.get("price_at_decision"),
                run_id=latest_run["id"] if latest_run else None,
                evidence=evidence,
            )
            return {
                "id": decision_id,
                "decisions": storage.list_decisions(db_path, asset_id=asset_id),
            }

        return guarded(_save)

    @app.get("/api/v1/decisions/{decision_id}/evidence")
    def api_decision_evidence(decision_id: int):
        ev = storage.get_decision_evidence(db_path, decision_id)
        if ev is None:
            raise HTTPException(404, "该决策依据为正式行情，无独立探索快照")
        return ev

    @app.get("/api/v1/signals")
    def api_signals(asset_id: str | None = None, strategy_id: str | None = None, limit: int = 200):
        return {
            "signals": storage.list_signals(
                db_path, asset_id=asset_id, strategy_id=strategy_id, limit=min(max(limit, 1), 500)
            )
        }

    @app.get("/api/v1/runs")
    def api_runs(limit: int = 30):
        return {
            "running": is_running(db_path),
            "runs": storage.list_check_runs(db_path, limit=min(max(limit, 1), 200)),
        }

    @app.get("/api/v1/runs/{run_id}")
    def api_run_detail(run_id: int):
        rec = storage.get_check_run(db_path, run_id)
        if not rec:
            raise HTTPException(404, "检查批次不存在")
        return rec

    @app.post("/api/v1/runs")
    def api_trigger_run(request: Request, payload: dict = Body(default={})):
        require_csrf(request, payload)
        if is_running(db_path):
            raise HTTPException(409, "已有检查正在运行，请稍后查看结果")
        if payload.get("async"):
            threading.Thread(
                target=lambda: run(db_path, send=False), daemon=True
            ).start()
            return {"started": True, "running": True}
        run_id = guarded(lambda: run(db_path, send=False))
        return {"run_id": run_id, "run": storage.get_check_run(db_path, run_id)}

    @app.get("/api/v1/strategies")
    def api_strategies():
        cfg = load_configuration(db_path)
        usage = {}
        for asset in cfg["assets"]:
            if asset.get("archived"):
                continue
            for sid in asset.get("strategies", []):
                usage.setdefault(sid, []).append({"id": asset["id"], "label": asset["label"]})
        return {
            "version": cfg["version"],
            "strategies": [
                {**rule, "assets": usage.get(rule["id"], [])}
                for rule in cfg["strategies"]
            ],
        }

    @app.post("/api/v1/strategies")
    def api_save_strategy(request: Request, payload: dict = Body()):
        require_csrf(request, payload)
        return guarded(lambda: workspace.save_strategy_rule(db_path, payload))

    @app.get("/api/v1/templates")
    def api_templates():
        return {"templates": storage.list_templates(db_path)}

    @app.post("/api/v1/templates")
    def api_save_template(request: Request, payload: dict = Body()):
        require_csrf(request, payload)
        return guarded(
            lambda: {
                "templates": storage.save_template(
                    db_path,
                    payload["name"],
                    payload["strategies"],
                    payload.get("template_id"),
                    payload.get("expected"),
                )
            }
        )

    @app.get("/api/v1/notes")
    def api_list_notes(asset_id: str | None = None):
        return {"notes": storage.list_notes(db_path, asset_id=asset_id)}

    @app.get("/api/v1/notes/{note_id}")
    def api_get_note(note_id: int):
        note = storage.get_note(db_path, note_id)
        if not note:
            raise HTTPException(404, "备忘不存在")
        return note

    @app.post("/api/v1/notes")
    def api_create_note(request: Request, payload: dict = Body()):
        require_csrf(request, payload)

        def _create():
            raw = str(payload.get("markdown") or "")
            if not raw.strip() or len(raw) > 20000:
                raise ValueError("请输入 1–20,000 字的 Markdown。")
            title = str(payload.get("title") or "").strip()
            if not title:
                first_line = raw.strip().splitlines()[0].strip()
                heading = re.fullmatch(r"#\s+(.+?)(?:\s+#+)?", first_line)
                title = heading[1] if heading else first_line[:60]
            note_id = storage.save_note(
                db_path,
                None,
                title,
                raw,
                datetime.now(TZ).isoformat(),
                asset_ids=payload.get("asset_ids") or [],
            )
            return storage.get_note(db_path, note_id)

        return guarded(_create)

    @app.patch("/api/v1/notes/{note_id}")
    def api_update_note(note_id: int, request: Request, payload: dict = Body()):
        require_csrf(request, payload)

        def _update():
            existing = storage.get_note(db_path, note_id)
            if not existing:
                raise HTTPException(404, "备忘不存在")
            raw = str(payload.get("markdown", existing["markdown"]))
            if not raw.strip() or len(raw) > 20000:
                raise ValueError("请输入 1–20,000 字的 Markdown。")
            title = str(payload.get("title") or "").strip()
            if not title:
                first_line = raw.strip().splitlines()[0].strip()
                heading = re.fullmatch(r"#\s+(.+?)(?:\s+#+)?", first_line)
                title = heading[1] if heading else first_line[:60]
            saved = storage.save_note(
                db_path,
                note_id,
                title,
                raw,
                datetime.now(TZ).isoformat(),
                asset_ids=payload.get("asset_ids"),
            )
            if saved is None:
                raise HTTPException(404, "备忘不存在")
            return storage.get_note(db_path, saved)

        return guarded(_update)

    @app.post("/api/v1/notes/{note_id}/pin")
    def api_pin_note(note_id: int, request: Request, payload: dict = Body(default={})):
        require_csrf(request, payload)
        if not storage.pin_note(db_path, note_id):
            raise HTTPException(404, "备忘不存在")
        return storage.get_note(db_path, note_id)

    @app.delete("/api/v1/notes/{note_id}")
    def api_delete_note(note_id: int, request: Request):
        require_csrf(request)
        if not storage.delete_note(db_path, note_id):
            raise HTTPException(404, "备忘不存在")
        return {"deleted": True, "id": note_id}

    # ------------------------------------------------------------------
    # SPA fallback (serves compiled Vite dist/index.html)
    # ------------------------------------------------------------------
    def _serve_spa():
        index = DIST_DIR / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-store"})
        return HTMLResponse(
            "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
            "<title>Chance Track v1.0</title>"
            "<body style='font-family:system-ui;padding:32px;'>"
            "<h1>Chance Track v1.0 前端尚未构建</h1>"
            "<p>请运行 <code>npm --prefix frontend run build</code> 后刷新页面。</p>"
            "</body></html>",
            status_code=200,
        )

    @app.get("/")
    @app.get("/signals")
    @app.get("/strategies")
    @app.get("/notes")
    @app.get("/notes/{note_id}")
    @app.get("/manage")
    @app.get("/investment-notes")
    def spa_routes():
        return _serve_spa()

    return app
