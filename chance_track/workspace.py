"""Chart exploration and local edits, separate from formal checks and delivery."""

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from . import storage
from .analysis import analyze
from .configuration import (
    ConfigurationConflict,
    load_configuration,
    relative_benchmarks,
    save_configuration,
    strategy_signature,
    validate_configuration,
)
from .data import TZ, fetch
from .runner import next_regular_check
from .strategies import benchmark_context, chart_events, evaluate


def tables(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS chart_templates (
      id INTEGER PRIMARY KEY, template_id TEXT NOT NULL, name TEXT NOT NULL,
      created_at TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS workspace_quotes (
      id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, asset TEXT NOT NULL, snapshot TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS decision_contexts (
      decision_id INTEGER PRIMARY KEY REFERENCES decisions(id), payload TEXT NOT NULL);
    """)


def catalog(path, query=""):
    config = load_configuration(path)
    known = json.loads(
        (Path(__file__).resolve().parent.parent / "watchlist.json").read_text()
    )
    by_id = {a["id"]: a for a in known}
    by_id.update({a["id"]: a for a in config["assets"]})
    q = query.strip().casefold()
    return [
        a
        for a in by_id.values()
        if q
        in " ".join(
            str(a.get(k, "")) for k in ("label", "code", "ticker", "market")
        ).casefold()
    ]


def source_item(path, source, asset_id):
    if source.get("report_id"):
        report = storage.report(path, int(source["report_id"]))
        if not report:
            raise ValueError("报告不存在")
        item = next((i for i in report["items"] if i["asset"]["id"] == asset_id), None)
        if not item or not item.get("snapshot", {}).get("rows"):
            raise ValueError("该报告没有可用于探索的真实行情快照")
        return item, report["day"], report["created_at"]
    if source.get("quote_id"):
        with storage.connect(path) as db:
            tables(db)
            row = db.execute(
                "SELECT * FROM workspace_quotes WHERE id=?", (int(source["quote_id"]),)
            ).fetchone()
            if row and json.loads(row["asset"])["id"] == asset_id:
                return (
                    {
                        "asset": json.loads(row["asset"]),
                        "snapshot": json.loads(row["snapshot"]),
                    },
                    row["created_at"][:10],
                    row["created_at"],
                )
    raise ValueError("请先选择一份行情快照")


def preview(path, source, asset_id, strategies):
    if not isinstance(strategies, list) or len(strategies) > 24:
        raise ValueError("探索指标最多24个")
    validate_configuration({"assets": [], "strategies": strategies})
    item, day, checked_at = source_item(path, source, asset_id)
    asset = {
        **item["asset"],
        "strategies": [s["id"] for s in strategies],
        "activated": {},
    }
    snapshot = item["snapshot"]
    if source.get("report_id"):
        references = dict(snapshot.get("benchmarks", {}))
        for benchmark_id in relative_benchmarks(strategies, asset) - references.keys():
            try:
                peer, _, _ = source_item(path, source, benchmark_id)
            except ValueError:
                continue
            references[benchmark_id] = benchmark_context(peer["snapshot"])
        if references:
            snapshot = {**snapshot, "benchmarks": references}
    result = analyze(snapshot, day)
    results, layers, computed = evaluate(snapshot, strategies, asset, day)
    result["strategies"] = results
    result["chart"]["overlays"] = layers
    result["chart"]["macd_configs"] = [
        {"id": s["id"], "name": s["name"], "points": computed[s["id"]]["macd"]}
        for s in strategies
        if s["kind"] == "macd"
    ]
    if result["chart"]["macd_configs"]:
        result["chart"]["macd"] = result["chart"]["macd_configs"][0]["points"]
    result["chart"]["strategy_events"] = chart_events(snapshot, results, computed)
    return {
        "asset": asset,
        "result": result,
        "mode": "exploration",
        "status": item.get("status"),
        "carried_forward": item.get("carried_forward", False),
        "source": source,
        "checked_at": checked_at,
        "fetched_at": snapshot.get("fetched_at"),
        "strategies": strategies,
    }


def templates(path):
    with storage.connect(path) as db:
        tables(db)
        return [
            {**dict(r), "strategies": json.loads(r["payload"])}
            for r in db.execute(
                "SELECT * FROM chart_templates WHERE id IN (SELECT max(id) FROM chart_templates GROUP BY template_id) ORDER BY id DESC"
            )
        ]


def save_template(path, name, strategies, template_id=None, expected=None):
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise ValueError("模板名称须为1至100字")
    if len(strategies) > 24:
        raise ValueError("模板最多24个指标")
    validate_configuration({"assets": [], "strategies": strategies})
    with storage.connect(path) as db:
        tables(db)
        db.execute("BEGIN IMMEDIATE")
        if template_id:
            latest = db.execute(
                "SELECT max(id) FROM chart_templates WHERE template_id=?",
                (template_id,),
            ).fetchone()[0]
            if latest is None or latest != expected:
                raise ValueError("模板已修改，请重新加载；也可另存为新模板")
        else:
            template_id = uuid4().hex
        db.execute(
            "INSERT INTO chart_templates(template_id,name,created_at,payload) VALUES(?,?,?,?)",
            (
                template_id,
                name.strip(),
                datetime.now(TZ).isoformat(),
                json.dumps(strategies, ensure_ascii=False),
            ),
        )
    return templates(path)


def edit(path, payload, fetcher=fetch):
    config = load_configuration(path)
    if payload["version"] != config["version"]:
        raise ConfigurationConflict("配置已修改，请重新加载配置后重试；探索草稿已保留")
    action = payload["action"]
    before = (
        deepcopy(config) if action in ("monitor", "bind_monitor", "unmonitor") else None
    )
    selected = None
    captured = {}
    saved_strategy_id = None

    def capture(asset, day):
        snapshot = fetcher(asset, day)
        captured[asset["id"]] = snapshot
        return snapshot

    if action == "add_asset":
        candidate = payload["asset"]
        old = next(
            (a for a in config["assets"] if a["id"] == candidate.get("id")), None
        )
        if old:
            if old.get("watchlisted", True) is False and not old.get("archived"):
                old["watchlisted"] = True
            else:
                old.update(enabled=True, archived=False, watchlisted=True)
            selected = old["id"]
        else:
            candidate = {
                k: v
                for k, v in candidate.items()
                if k
                in (
                    "label",
                    "ticker",
                    "code",
                    "provider",
                    "session_profile",
                    "secid",
                    "expected_name",
                    "market",
                    "currency",
                )
            }
            candidate.update(
                id="asset_" + uuid4().hex[:12],
                enabled=True,
                archived=False,
                strategies=[],
            )
            config["assets"].append(candidate)
            selected = candidate["id"]
    elif action == "asset":
        a = next(a for a in config["assets"] if a["id"] == payload["asset_id"])
        for key in ("priority", "watchlisted"):
            if key in payload:
                if type(payload[key]) is not bool:
                    raise ValueError("观察列表状态须为布尔值")
                a[key] = payload[key]
        for key in ("label", "group", "enabled", "archived"):
            if key in payload:
                a[key] = payload[key]
        selected = a["id"]
    elif action == "order":
        order = payload["ids"]
        if len(order) != len(set(order)) or set(order) != {
            a["id"] for a in config["assets"]
        }:
            raise ValueError("排序必须包含全部标的且不可重复")
        by_id = {a["id"]: a for a in config["assets"]}
        config["assets"] = [by_id[sid] for sid in order]
    elif action == "monitor":
        a = next(a for a in config["assets"] if a["id"] == payload["asset_id"])
        strategy = deepcopy(payload["strategy"])
        old = next((s for s in config["strategies"] if s["id"] == strategy["id"]), None)
        shared = payload.get("shared", False)
        reuse_id = payload.get("reuse_id")
        if reuse_id:
            existing = next(
                (s for s in config["strategies"] if s["id"] == reuse_id), None
            )
            if not existing or strategy_signature(strategy) != strategy_signature(
                existing
            ):
                raise ValueError("待复用配置已变化，请重新选择")
            if strategy["kind"] == "combination":
                for dependency_id in [strategy["trigger"]] + (
                    [strategy["band"]] if strategy.get("filter") else []
                ):
                    proposed = payload.get("dependencies", {}).get(dependency_id)
                    current = next(
                        s for s in config["strategies"] if s["id"] == dependency_id
                    )
                    if proposed and any(
                        proposed.get(key) != current.get(key)
                        for key in ("kind", "params")
                    ):
                        raise ValueError("待复用配置的组合参数已变化，请重新选择")
            bindings = a.get("strategies", [])
            strategy = existing
            if reuse_id not in bindings:
                strategy = next(
                    (
                        entry
                        for entry in config["strategies"]
                        if entry["id"] in bindings
                        and strategy_signature(entry) == strategy_signature(existing)
                    ),
                    existing,
                )
            a["strategies"] = [
                sid
                for sid in a.get("strategies", [])
                if sid != payload.get("replace_id") or sid == strategy["id"]
            ]
        elif shared:
            if not old:
                raise ValueError("共享配置不存在")
            strategy["id"] = old["id"]
            config["strategies"] = [
                strategy if s["id"] == old["id"] else s for s in config["strategies"]
            ]
        else:
            strategy["id"] = "strategy_" + uuid4().hex[:12]
            if strategy["kind"] == "combination":
                dependencies = payload.get("dependencies", {})
                replacements = {}
                for key in (
                    ["trigger", "band"] if strategy.get("filter") else ["trigger"]
                ):
                    reference = strategy[key]
                    dependency = dependencies.get(reference)
                    current = next(
                        (s for s in config["strategies"] if s["id"] == reference), None
                    )
                    if dependency and (
                        not current
                        or any(
                            dependency.get(k) != current.get(k)
                            for k in ("kind", "params")
                        )
                    ):
                        if reference not in replacements:
                            copied = deepcopy(dependency)
                            copied["id"] = "strategy_" + uuid4().hex[:12]
                            config["strategies"].append(copied)
                            replacements[reference] = copied["id"]
                        strategy[key] = replacements[reference]
            strategy.pop("dependencies", None)
            config["strategies"].append(strategy)
            # Copy the current binding when editing; other assets keep their original configuration.
            a["strategies"] = [
                sid
                for sid in a.get("strategies", [])
                if sid != payload.get("replace_id")
            ]
        if strategy["id"] not in a["strategies"]:
            a["strategies"].append(strategy["id"])
        selected = a["id"]
        saved_strategy_id = strategy["id"]
    elif action in ("bind_monitor", "unmonitor"):
        a = next(a for a in config["assets"] if a["id"] == payload["asset_id"])
        sid = payload["strategy_id"]
        if action == "bind_monitor":
            if not any(rule["id"] == sid for rule in config["strategies"]):
                raise ValueError("配置已不存在，请刷新")
            if sid not in a["strategies"]:
                a["strategies"].append(sid)
        else:
            a["strategies"] = [bound for bound in a["strategies"] if bound != sid]
        selected = a["id"]
    else:
        raise ValueError("未知工作台操作")
    if config == before:
        return {
            "configuration": config,
            "selected": selected,
            "quote": None,
            "strategy_id": saved_strategy_id,
        }
    save_configuration(path, config, payload["version"], fetcher=capture)
    quote = None
    if selected in captured:
        a = next(a for a in config["assets"] if a["id"] == selected)
        quote = save_quote(path, a, captured[selected])
    return {
        "configuration": load_configuration(path),
        "selected": selected,
        "quote": quote,
        "strategy_id": saved_strategy_id,
    }


def save_quote(path, asset, snapshot):
    with storage.connect(path) as db:
        tables(db)
        created = datetime.now(TZ).isoformat()
        cursor = db.execute(
            "INSERT INTO workspace_quotes(created_at,asset,snapshot) VALUES(?,?,?)",
            (
                created,
                json.dumps(asset, ensure_ascii=False),
                json.dumps(snapshot, ensure_ascii=False),
            ),
        )
        quote_id = cursor.lastrowid
    return preview(path, {"quote_id": quote_id}, asset["id"], [])


def quote(path, asset_id, fetcher=fetch):
    a = next(a for a in load_configuration(path)["assets"] if a["id"] == asset_id)
    return save_quote(path, a, fetcher(a, datetime.now(TZ).date().isoformat()))


def register_routes(app, path, token):
    import secrets

    import httpx
    from fastapi import Body, HTTPException

    def protect(payload):
        if not isinstance(payload.get("csrf"), str) or not secrets.compare_digest(
            payload["csrf"], token
        ):
            raise HTTPException(403, "请刷新页面后重试；本地草稿仍保留")

    def guarded(call):
        try:
            return call()
        except ConfigurationConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (
            ValueError,
            KeyError,
            TypeError,
            StopIteration,
            IndexError,
            httpx.HTTPError,
        ) as exc:
            raise HTTPException(400, str(exc) or "无效的工作台参数") from exc

    @app.get("/api/workspace")
    def state():
        next_check = next_regular_check(datetime.now(TZ))
        return {
            "configuration": load_configuration(path),
            "templates": templates(path),
            "next_regular_check": next_check.isoformat() if next_check else None,
        }

    @app.get("/api/decisions/{decision_id}/context")
    def context(decision_id: int):
        result = storage.decision_context(path, decision_id)
        if result is None:
            raise HTTPException(404, "该决策依据为原始报告，无单独探索快照")
        return result

    @app.get("/api/symbols")
    def symbols(q: str = ""):
        return {"assets": catalog(path, q)}

    @app.post("/api/workspace/edit")
    def edit_state(payload: dict = Body()):
        protect(payload)
        return guarded(lambda: edit(path, payload))

    @app.post("/api/workspace/preview")
    def explore(payload: dict = Body()):
        protect(payload)
        return guarded(
            lambda: preview(
                path, payload["source"], payload["asset_id"], payload["strategies"]
            )
        )

    @app.post("/api/workspace/quote")
    def fetch_quote(payload: dict = Body()):
        protect(payload)
        return guarded(lambda: quote(path, payload["asset_id"]))

    @app.post("/api/workspace/templates")
    def store_template(payload: dict = Body()):
        protect(payload)
        return guarded(
            lambda: {
                "templates": save_template(
                    path,
                    payload["name"],
                    payload["strategies"],
                    payload.get("template_id"),
                    payload.get("expected"),
                )
            }
        )
