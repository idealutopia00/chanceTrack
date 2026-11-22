import json
import re
from copy import deepcopy

from fastapi.testclient import TestClient
from test_loop import NOW, sample

from chance_track import configuration_store, storage, workspace
from chance_track.configuration import load_configuration
from chance_track.runner import run
from chance_track.web import create_app


def setup_workspace(tmp_path):
    path = tmp_path / "workspace.sqlite"
    config = load_configuration(path)
    asset = {**config["assets"][0], "activated": {}}
    rid = run(path, [asset], send=False, now=NOW, fetcher=lambda *_: sample())
    client = TestClient(create_app(path))
    html = client.get("/").text
    csrf = re.search(r'name="csrf" value="([^"]+)"', html)[1]
    return path, config, rid, client, csrf


def seed_configuration(path, config, timestamp):
    with storage.connect(path) as db:
        version = db.execute(
            "INSERT INTO configuration_versions(created_at,payload) VALUES (?,?)",
            (timestamp, json.dumps({key: config[key] for key in ("assets", "strategies")})),
        ).lastrowid
        configuration_store.write_current(db, config, version)
    return version


def test_preview_does_not_write_report_event_configuration_or_notification(tmp_path):
    path, config, rid, client, csrf = setup_workspace(tmp_path)
    original = deepcopy(storage.report(path, rid))
    payload = {
        "csrf": csrf,
        "source": {"report_id": rid},
        "asset_id": "sp500",
        "strategies": config["strategies"],
    }
    response = client.post("/api/workspace/preview", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["mode"] == "exploration" and len(result["result"]["strategies"]) == 4
    assert len(result["result"]["chart"]["overlays"]) == 8
    assert len(storage.reports(path)) == 1
    assert storage.report(path, rid) == original
    assert load_configuration(path) == config
    assert storage.notification(path, rid) is None
    payload["strategies"][0]["params"]["fast"] = 100
    assert client.post("/api/workspace/preview", json=payload).status_code == 400
    payload["csrf"] = "wrong"
    assert client.post("/api/workspace/preview", json=payload).status_code == 403


def test_preview_retains_report_quote_status(tmp_path):
    path, _, rid, client, csrf = setup_workspace(tmp_path)
    report = storage.report(path, rid)
    item = report["items"][0]
    item.update(carried_forward=True, status="error")
    report.pop("id")
    replay_id, _ = storage.save_report(path, report)

    response = client.post(
        "/api/workspace/preview",
        json={
            "csrf": csrf,
            "source": {"report_id": replay_id},
            "asset_id": item["asset"]["id"],
            "strategies": [],
        },
    )
    assert response.status_code == 200
    assert response.json()["carried_forward"] is True
    assert response.json()["status"] == "error"


def test_template_versions_and_configuration_are_separate(tmp_path):
    path, config, _, client, csrf = setup_workspace(tmp_path)
    data = {"csrf": csrf, "name": "趋势", "strategies": config["strategies"][:2]}
    first = client.post("/api/workspace/templates", json=data).json()["templates"][0]
    data.update(template_id=first["template_id"], expected=first["id"], name="趋势 v2")
    assert client.post("/api/workspace/templates", json=data).status_code == 200
    assert client.post("/api/workspace/templates", json=data).status_code == 400
    assert load_configuration(path) == config
    with storage.connect(path) as db:
        assert db.execute("SELECT count(*) FROM chart_templates").fetchone()[0] == 2


def test_monitor_private_copy_and_explicit_shared_update(tmp_path):
    path, config, _, client, csrf = setup_workspace(tmp_path)
    strategy = deepcopy(config["strategies"][0])
    strategy["params"]["fast"] = 10
    result = client.post(
        "/api/workspace/edit",
        json={
            "csrf": csrf,
            "version": 1,
            "action": "monitor",
            "asset_id": "sp500",
            "strategy": strategy,
            "replace_id": "macd",
        },
    ).json()
    changed = result["configuration"]
    sid = result["strategy_id"]
    assert changed["version"] == 2
    assert next(a for a in changed["assets"] if a["id"] == "sp500")["strategies"] == [
        sid
    ]
    assert next(a for a in changed["assets"] if a["id"] == "sox")["strategies"] == [
        "macd"
    ]
    assert (
        next(s for s in changed["strategies"] if s["id"] == "macd")["params"]["fast"]
        == 12
    )
    # A distinct shared update still affects the original rule.
    strategy["params"]["fast"] = 11
    response = client.post(
        "/api/workspace/edit",
        json={
            "csrf": csrf,
            "version": 2,
            "action": "monitor",
            "asset_id": "sox",
            "strategy": strategy,
            "shared": True,
        },
    )
    assert response.status_code == 200, response.text
    assert (
        next(
            s
            for s in response.json()["configuration"]["strategies"]
            if s["id"] == "macd"
        )["revision"]
        == 2
    )
    assert (
        client.post(
            "/api/workspace/edit",
            json={
                "csrf": csrf,
                "version": 1,
                "action": "asset",
                "asset_id": "sox",
                "archived": True,
            },
        ).status_code
        == 409
    )


def test_watchlist_catalog_order_group_and_no_monitor_on_add(tmp_path):
    path, config, _, client, csrf = setup_workspace(tmp_path)
    assert client.get("/api/symbols?q=SOX").json()["assets"][0]["id"] == "sox"
    ids = list(reversed([a["id"] for a in config["assets"]]))
    result = workspace.edit(path, {"version": 1, "action": "order", "ids": ids})
    assert [a["id"] for a in result["configuration"]["assets"]] == ids
    result = workspace.edit(
        path,
        {
            "version": 2,
            "action": "asset",
            "asset_id": "sox",
            "group": "科技",
            "archived": True,
        },
    )
    assert result["configuration"]["assets"][0]["group"] == "科技"
    assert result["configuration"]["assets"][0]["archived"]
    from datetime import date, timedelta

    def fresh(*_):
        snapshot = sample()
        for i, r in enumerate(snapshot["rows"]):
            r["date"] = (
                date.today() - timedelta(days=len(snapshot["rows"]) - i - 1)
            ).isoformat()
        return snapshot

    result = workspace.edit(
        path,
        {
            "version": 3,
            "action": "add_asset",
            "asset": {
                "label": "测试",
                "code": "^TEST",
                "provider": "yahoo",
                "session_profile": "us_equity",
            },
        },
        fetcher=fresh,
    )
    a = result["configuration"]["assets"][-1]
    assert a["strategies"] == [] and result["quote"]["source"]["quote_id"]
    assert result["quote"]["mode"] == "exploration"
    assert len(storage.reports(path)) == 1


def test_decision_can_save_exploration_with_original_report_unchanged(tmp_path):
    path, config, rid, client, csrf = setup_workspace(tmp_path)
    original = storage.report(path, rid)
    basis = {"source": {"report_id": rid}, "strategies": config["strategies"][:2]}
    response = client.post(
        "/decisions",
        data={
            "csrf": csrf,
            "report_id": rid,
            "asset_id": "sp500",
            "action": "观望",
            "execution": "未执行",
            "note": "根据探索判断",
            "exploration": json.dumps(basis),
        },
    )
    assert response.status_code == 200, response.text
    decision = storage.decisions(path, rid)[0]
    context = client.get(f"/api/decisions/{decision['id']}/context").json()
    assert context["mode"] == "exploration" and len(context["strategies"]) == 2
    assert storage.report(path, rid) == original
    basis["source"]["report_id"] = rid + 1
    assert (
        client.post(
            "/decisions",
            data={
                "csrf": csrf,
                "report_id": rid,
                "asset_id": "sp500",
                "action": "观望",
                "execution": "未执行",
                "exploration": json.dumps(basis),
            },
        ).status_code
        == 400
    )


def test_private_historical_combination_preserves_dependency_parameters(tmp_path):
    path, config, _, _, _ = setup_workspace(tmp_path)
    previous_macd = deepcopy(config["strategies"][0])
    current = deepcopy(previous_macd)
    current["params"]["fast"] = 10
    workspace.edit(
        path,
        {
            "action": "monitor",
            "version": 1,
            "asset_id": "sox",
            "strategy": current,
            "shared": True,
        },
    )
    combination = {
        "id": "new_combo",
        "name": "历史组合",
        "kind": "combination",
        "trigger": "macd",
        "filter": "above_mid",
        "band": "boll_break",
    }
    result = workspace.edit(
        path,
        {
            "action": "monitor",
            "version": 2,
            "asset_id": "sp500",
            "strategy": combination,
            "dependencies": {
                "macd": previous_macd,
                "boll_break": config["strategies"][2],
            },
        },
    )
    strategies = {s["id"]: s for s in result["configuration"]["strategies"]}
    saved = strategies[result["strategy_id"]]
    assert strategies[saved["trigger"]]["params"]["fast"] == 12
    assert strategies["macd"]["params"]["fast"] == 10
    assert saved["trigger"] != "macd"


def test_reuse_duplicate_monitor_preserves_existing_ids(tmp_path):
    path, config, _, client, csrf = setup_workspace(tmp_path)
    legacy = deepcopy(config)
    legacy["strategies"].append({**config["strategies"][0], "id": "legacy"})
    legacy["assets"][0]["strategies"] = ["legacy"]
    legacy["assets"][1]["strategies"] = ["macd", "legacy"]
    seed_configuration(path, legacy, NOW.isoformat())
    data = {
        "csrf": csrf,
        "version": 2,
        "action": "monitor",
        "asset_id": "sox",
        "strategy": config["strategies"][0],
    }
    response = client.post("/api/workspace/edit", json=data)
    assert response.status_code == 400
    assert "已有匹配配置" in response.json()["detail"]
    assert load_configuration(path)["version"] == 2
    response = client.post("/api/workspace/edit", json={**data, "reuse_id": "macd"})
    assert response.status_code == 200, response.text
    assert response.json()["strategy_id"] == "macd"
    saved = load_configuration(path)
    assert saved["strategies"][-1]["id"] == "legacy"
    assert saved["assets"][0]["strategies"] == ["legacy"]

    data.update(version=saved["version"], asset_id=saved["assets"][0]["id"])
    response = client.post(
        "/api/workspace/edit",
        json={**data, "reuse_id": "macd", "replace_id": "macd"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["strategy_id"] == "legacy"
    assert load_configuration(path)["assets"][0]["strategies"] == ["legacy"]

    unchanged = load_configuration(path)
    response = client.post("/api/workspace/edit", json={
        **data, "version": unchanged["version"], "asset_id": legacy["assets"][1]["id"],
        "reuse_id": "macd", "replace_id": "legacy",
    })
    assert response.status_code == 200, response.text
    assert response.json()["strategy_id"] == "legacy"
    assert load_configuration(path) == unchanged


def test_watchlist_membership_is_independent_of_monitoring(tmp_path):
    path, config, _, client, csrf = setup_workspace(tmp_path)
    data = {"csrf": csrf, "version": 1, "action": "asset", "asset_id": "sp500"}
    response = client.post(
        "/api/workspace/edit", json={**data, "watchlisted": False, "priority": True}
    )
    assert response.status_code == 200, response.text
    result = response.json()["configuration"]
    item = next(a for a in result["assets"] if a["id"] == "sp500")
    old = next(a for a in config["assets"] if a["id"] == "sp500")
    assert item["enabled"] == old["enabled"] and item["strategies"] == old["strategies"]
    assert not item["watchlisted"] and item["priority"]
    assert item["activated"] == old["activated"]

    response = client.post(
        "/api/workspace/edit", json={**data, "version": 2, "enabled": False}
    )
    assert response.status_code == 200, response.text
    response = client.post(
        "/api/workspace/edit",
        json={**data, "version": 3, "action": "add_asset", "asset": {"id": "sp500"}},
    )
    assert response.status_code == 200, response.text
    item = next(a for a in load_configuration(path)["assets"] if a["id"] == "sp500")
    assert (
        item["watchlisted"]
        and not item["enabled"]
        and item["strategies"] == old["strategies"]
    )


def test_strategy_lifecycle_migrates_references_atomically(tmp_path):
    path, config, report_id, client, csrf = setup_workspace(tmp_path)
    retained = config["strategies"][0]
    source = {**retained, "id": "macd_sp500", "revision": 3}
    unused = {**retained, "id": "macd_unused", "revision": 2}
    combination = {
        "id": "combo",
        "kind": "combination",
        "name": "组合",
        "trigger": source["id"],
    }
    different = {
        **retained, "id": "macd_different",
        "params": {**retained["params"], "fast": 10},
    }
    config["strategies"].extend([source, unused, combination, different])
    config["assets"][0]["strategies"] = [source["id"], "combo"]
    config["assets"][1]["strategies"].append(source["id"])
    source_activation = "2026-10-01T00:00:00+08:00"
    config["assets"][0]["activated"] = {source["id"]: source_activation}
    retained_activation = config["assets"][1]["activated"][retained["id"]]
    version = seed_configuration(path, config, source_activation)

    current = load_configuration(path)
    report = storage.report(path, report_id)
    def submit(draft, source_id, keep_id=""):
        return client.post(
            "/manage", data={
                "csrf": csrf,
                "version": draft["version"],
                "payload": json.dumps({**draft, "lifecycle": [source_id, keep_id]}),
            },
        )

    blocked = submit(current, source["id"])
    assert blocked.status_code == 400
    assert "sp500" in blocked.text and "combo" in blocked.text
    assert submit(current, source["id"], different["id"]).status_code == 400
    assert load_configuration(path) == current

    draft = deepcopy(current)
    draft["strategies"][0]["name"] = "保留并重命名"
    saved = submit(draft, source["id"], retained["id"])
    assert saved.status_code == 200
    merged = load_configuration(path)
    assert merged["version"] == version + 1
    assert merged["strategies"][0]["name"] == "保留并重命名"
    assert all(a["strategies"].count(retained["id"]) == 1 for a in merged["assets"])
    assert all(source["id"] not in a["strategies"] for a in merged["assets"])
    assert source["id"] not in merged["assets"][0]["activated"]
    assert merged["assets"][0]["activated"][retained["id"]] > source_activation
    assert merged["assets"][1]["activated"][retained["id"]] == retained_activation
    assert next(s for s in merged["strategies"] if s["id"] == "combo")["trigger"] == retained["id"]
    assert storage.report(path, report_id) == report
    assert client.get(f"/configuration/{version}").json() == {
        **current, "created_at": source_activation
    }
    assert storage.notification(path, report_id) is None

    assert submit(current, unused["id"]).status_code == 409
    assert submit(merged, unused["id"]).status_code == 200
    assert unused["id"] not in {s["id"] for s in load_configuration(path)["strategies"]}


def test_immediate_monitor_binding_is_idempotent_and_versioned(tmp_path):
    path, config, report_id, client, csrf = setup_workspace(tmp_path)
    original = storage.report(path, report_id)
    payload = {
        "csrf": csrf,
        "version": config["version"],
        "asset_id": "sp500",
        "strategy_id": "ma",
        "action": "bind_monitor",
    }
    url = "/api/workspace/edit"
    added = client.post(url, json=payload).json()["configuration"]
    assert added["version"] == config["version"] + 1
    assert added["assets"][0]["strategies"] == ["macd", "ma"]
    assert added["assets"][0]["activated"]["ma"]
    assert client.post(url, json=payload).status_code == 409
    payload["version"] = added["version"]
    assert client.post(url, json=payload).json()["configuration"] == added

    payload["action"] = "unmonitor"
    removed = client.post(url, json=payload).json()["configuration"]
    assert removed["assets"][0]["strategies"] == ["macd"]
    assert removed["assets"][1:] == added["assets"][1:]
    assert removed["strategies"] == added["strategies"]
    assert storage.report(path, report_id) == original
    assert storage.notification(path, report_id) is None


def test_check_uses_one_current_configuration_snapshot(tmp_path, monkeypatch):
    from chance_track import runner

    path, config, _, _, _ = setup_workspace(tmp_path)
    reads = []

    def load(path):
        reads.append(path)
        return deepcopy(config)

    monkeypatch.setattr(runner, "load_configuration", load)
    report_id = run(path, send=False, now=NOW, fetcher=lambda *_: sample())
    assert reads == [path]
    assert storage.report(path, report_id)["configuration"] == config
