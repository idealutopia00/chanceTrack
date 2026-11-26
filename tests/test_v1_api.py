"""End-to-end API tests for Chance Track v1.0 /api/v1/* endpoints."""

from copy import deepcopy

from fastapi.testclient import TestClient
from helpers import NOW, sample

from chance_track import storage
from chance_track.configuration import load_configuration
from chance_track.runner import run
from chance_track.web import create_app


def _setup(tmp_path):
    db_path = tmp_path / "v1_api.sqlite3"
    storage.initialize(db_path)
    cfg = load_configuration(db_path)
    run(
        db_path,
        watchlist=cfg["assets"],
        send=False,
        now=NOW,
        fetcher=lambda asset, _d: {**sample(), "code": asset["code"], "provider": asset["provider"]},
    )
    client = TestClient(create_app(db_path))
    bs = client.get("/api/v1/bootstrap").json()
    return db_path, client, bs["csrf"], bs["version"]


def test_bootstrap_csrf_and_spa_routes(tmp_path):
    _, client, csrf, _ = _setup(tmp_path)
    assert client.get("/api/runtime-health").status_code == 200
    for route in ("/", "/signals", "/strategies", "/notes"):
        resp = client.get(route)
        assert resp.status_code == 200
        assert "<html" in resp.text.lower()
    # Missing CSRF rejected with 403
    bad = client.post("/api/v1/notes", json={"markdown": "# 无令牌"})
    assert bad.status_code == 403
    ok = client.post(
        "/api/v1/notes",
        headers={"x-csrf-token": csrf},
        json={"markdown": "# 有令牌\n\n正文"},
    )
    assert ok.status_code == 200


def test_catalog_search_and_add_515450_observe_only(tmp_path, monkeypatch):
    db_path, client, csrf, version = _setup(tmp_path)
    for q in ("515450", "红利低波", "低波红利", "红利50"):
        res = client.get(f"/api/v1/catalog?q={q}")
        assert res.status_code == 200
        assert any(a["id"] == "dividend_low_vol_etf_515450" for a in res.json()["assets"])

    def fake_fetch(asset, day):
        from datetime import date, timedelta
        snap = sample(volume_unit="手", volume_multiplier=1)
        end = date.fromisoformat(day)
        n = len(snap["rows"])
        for i, row in enumerate(snap["rows"]):
            row["date"] = (end - timedelta(days=n - 1 - i)).isoformat()
        snap["confirmed_date"] = day
        snap["code"] = asset["code"]
        snap["provider"] = asset["provider"]
        snap["exchange_timezone"] = "Asia/Shanghai"
        return snap

    monkeypatch.setattr("chance_track.workspace.fetch", fake_fetch)
    monkeypatch.setattr("chance_track.configuration.fetch", fake_fetch)

    added = client.post(
        "/api/v1/assets",
        headers={"x-csrf-token": csrf},
        json={"version": version, "asset": {"id": "dividend_low_vol_etf_515450"}},
    )
    assert added.status_code == 200, added.text
    assert added.json()["selected"] == "dividend_low_vol_etf_515450"

    bars = client.get("/api/v1/assets/dividend_low_vol_etf_515450/bars").json()
    assert bars["has_bars"] is True
    assert bars["asset"]["strategies"] == []
    assert len(bars["result"]["chart"]["candles"]) == 180
    assert storage.list_signals(db_path, asset_id="dividend_low_vol_etf_515450") == []


def test_preview_is_pure_and_decisions_persist_with_evidence(tmp_path):
    db_path, client, csrf, _ = _setup(tmp_path)
    before_runs = len(storage.list_check_runs(db_path))
    before_signals = len(storage.list_signals(db_path))

    exploration = [
        {"id": "exp_boll", "name": "BOLL 20/2", "kind": "boll_break", "params": {"period": 20, "stddev": 2}}
    ]
    prev = client.post(
        "/api/v1/assets/sp500/preview",
        headers={"x-csrf-token": csrf},
        json={"strategies": exploration},
    )
    assert prev.status_code == 200
    assert prev.json()["mode"] == "exploration"
    assert len(prev.json()["result"]["chart"]["overlays"]) == 3
    assert len(storage.list_check_runs(db_path)) == before_runs
    assert len(storage.list_signals(db_path)) == before_signals

    dec = client.post(
        "/api/v1/assets/sp500/decisions",
        headers={"x-csrf-token": csrf},
        json={
            "action": "买入",
            "execution": "已提交",
            "note": "回踩布林中轨加仓",
            "exploration_strategies": exploration,
        },
    )
    assert dec.status_code == 200
    did = dec.json()["id"]
    assert dec.json()["decisions"][0]["has_exploration"] is True
    ev = client.get(f"/api/v1/decisions/{did}/evidence")
    assert ev.status_code == 200
    assert ev.json()["strategies"][0]["id"] == "exp_boll"


def test_monitoring_strategies_templates_and_asset_linked_notes(tmp_path):
    _, client, csrf, version = _setup(tmp_path)
    headers = {"x-csrf-token": csrf}

    # Bind boll_break to sp500
    m = client.put(
        "/api/v1/assets/sp500/monitoring",
        headers=headers,
        json={"version": version, "action": "bind", "strategy_id": "boll_break"},
    )
    assert m.status_code == 200
    version = m.json()["version"]

    # Optimistic lock conflict returns 409
    conflict = client.put(
        "/api/v1/assets/sp500/monitoring",
        headers=headers,
        json={"version": version - 1, "action": "unbind", "strategy_id": "boll_break"},
    )
    assert conflict.status_code == 409

    # Create & delete an unused strategy in rule library
    created = client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "version": version,
            "op": "upsert",
            "strategy": {"name": "短周期 MA 5/20", "kind": "ma", "group": "短线", "params": {"fast": 5, "slow": 20}},
        },
    )
    assert created.status_code == 200
    version = created.json()["version"]
    new_sid = created.json()["strategy_id"]

    deleted = client.post(
        "/api/v1/strategies",
        headers=headers,
        json={"version": version, "op": "delete", "strategy_id": new_sid},
    )
    assert deleted.status_code == 200

    # Save versioned template
    t1 = client.post(
        "/api/v1/templates",
        headers=headers,
        json={"name": "趋势模板", "strategies": [{"id": "m1", "name": "MACD", "kind": "macd", "params": {"fast": 12, "slow": 26, "signal": 9}}]},
    )
    assert t1.status_code == 200
    assert t1.json()["templates"][0]["name"] == "趋势模板"

    # Notes: create linked to sp500, pin global note, filter by asset
    n1 = client.post(
        "/api/v1/notes",
        headers=headers,
        json={"markdown": "# 全局纪律\n\n严格收盘确认。", "asset_ids": []},
    ).json()
    client.post(f"/api/v1/notes/{n1['id']}/pin", headers=headers, json={})
    n2 = client.post(
        "/api/v1/notes",
        headers=headers,
        json={"markdown": "# 标普观察\n\n关注美股休市日。", "asset_ids": ["sp500"]},
    ).json()
    assert n2["asset_ids"] == ["sp500"]

    sp_notes = client.get("/api/v1/notes?asset_id=sp500").json()["notes"]
    assert {n["id"] for n in sp_notes} == {n1["id"], n2["id"]}
    ndx_notes = client.get("/api/v1/notes?asset_id=nasdaq100").json()["notes"]
    assert [n["id"] for n in ndx_notes] == [n1["id"]]

    # Delete linked note
    assert client.delete(f"/api/v1/notes/{n2['id']}", headers=headers).status_code == 200
