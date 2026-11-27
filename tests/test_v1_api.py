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


def test_chart_events_include_date_and_direction_and_relative_strength_works_from_bars(tmp_path):
    _, client, csrf, _ = _setup(tmp_path)
    headers = {"x-csrf-token": csrf}

    bars = client.get("/api/v1/assets/sp500/bars")
    assert bars.status_code == 200
    events = bars.json()["result"]["chart"]["strategy_events"]
    assert len(events) >= 1
    assert events[0]["date"] == events[0]["time"]
    assert events[0]["direction"] in ("bullish", "bearish", "neutral")

    # Unknown asset returns 404 (not 400)
    assert client.get("/api/v1/assets/does_not_exist/bars").status_code == 404

    # relative_strength evaluated from market_bars must have quote_currency='USD'
    rs_prev = client.post(
        "/api/v1/assets/nasdaq100/preview",
        headers=headers,
        json={
            "strategies": [
                {
                    "id": "exp_rs",
                    "name": "纳指相对标普",
                    "kind": "relative_strength",
                    "params": {"trend": 20, "relative_period": 10, "benchmark": "sp500"},
                }
            ]
        },
    )
    assert rs_prev.status_code == 200
    strat = rs_prev.json()["result"]["strategies"][0]
    assert strat["state"] == "ready", strat["detail"]


def test_notes_optimistic_lock_title_preservation_decision_validation_and_run_lock(tmp_path):
    db_path, client, csrf, _ = _setup(tmp_path)
    headers = {"x-csrf-token": csrf}

    # Note custom title preserved on edit without title, and stale updated_at returns 409
    created = client.post(
        "/api/v1/notes",
        headers=headers,
        json={"title": "自定义标题", "markdown": "## 副标题\n\n初始正文"},
    ).json()
    assert created["title"] == "自定义标题"

    stale_patch = client.patch(
        f"/api/v1/notes/{created['id']}",
        headers=headers,
        json={"markdown": "## 副标题\n\n冲突写入", "updated_at": "2000-01-01T00:00:00+08:00"},
    )
    assert stale_patch.status_code == 409

    ok_patch = client.patch(
        f"/api/v1/notes/{created['id']}",
        headers=headers,
        json={"markdown": "## 副标题\n\n更新正文", "updated_at": created["updated_at"]},
    )
    assert ok_patch.status_code == 200
    updated_note = ok_patch.json()
    assert updated_note["title"] == "自定义标题"

    stale_del = client.delete(
        f"/api/v1/notes/{created['id']}",
        params={"updated_at": "2000-01-01T00:00:00+08:00"},
        headers=headers,
    )
    assert stale_del.status_code == 409
    ok_del = client.delete(
        f"/api/v1/notes/{created['id']}",
        params={"updated_at": updated_note["updated_at"]},
        headers=headers,
    )
    assert ok_del.status_code == 200

    # Invalid decision bar_date rejected with 400
    bad_dec = client.post(
        "/api/v1/assets/sp500/decisions",
        headers=headers,
        json={"action": "观望", "execution": "未执行", "note": "", "bar_date": "not-a-date"},
    )
    assert bad_dec.status_code == 400

    # Signals API returns accurate total even with limit=1
    sig_res = client.get("/api/v1/signals?limit=1").json()
    assert len(sig_res["signals"]) == 1
    assert sig_res["total"] >= len(sig_res["signals"])

    # Busy check lock returns 409 on POST /api/v1/runs
    from chance_track.runner import check_lock

    with check_lock(db_path):
        busy = client.post("/api/v1/runs", headers=headers, json={"async": False})
        assert busy.status_code == 409


def test_asset_summaries_latest_signal_per_asset_past_100_and_direction(tmp_path):
    from datetime import date, timedelta

    db_path = tmp_path / "per_asset_signals.sqlite3"
    storage.initialize(db_path)
    cfg = load_configuration(db_path)
    assert len(cfg["assets"]) >= 4

    asset_a = cfg["assets"][0]["id"]  # sp500
    asset_b = cfg["assets"][1]["id"]  # nasdaq100
    asset_c = cfg["assets"][2]["id"]  # csi300
    asset_empty = cfg["assets"][3]["id"]  # gold_sge (no signal events)

    with storage.connect(db_path) as db:
        db.execute("DELETE FROM signal_events")
        # Asset A: 1 older persisted bullish event on 2026-11-13
        db.execute(
            """INSERT INTO signal_events (
                 dedup_key, asset_id, strategy_id, strategy_name, strategy_group,
                 strategy_version, event_date, event_kind, detail, is_provisional,
                 historical_reference, first_run_id, created_at
               ) VALUES (?, ?, 'macd', 'MACD 12/26/9', '趋势', 'macd:v1',
                         '2026-11-13', '金叉 · 买入观察', '旧记录', 0, 0, NULL, '2026-11-13T15:00:00+08:00')""",
            (f"{asset_a}:2026-11-13:macd:v1:金叉 · 买入观察", asset_a),
        )
        # Asset B: 101 newer events (exceeding old global LIMIT 100), latest is bearish 向下突破下轨
        base_day = date(2026, 11, 14)
        for idx in range(101):
            ev_day = (base_day + timedelta(days=idx // 10)).isoformat()
            ev_kind = "向下突破下轨" if idx == 100 else "金叉"
            db.execute(
                """INSERT INTO signal_events (
                     dedup_key, asset_id, strategy_id, strategy_name, strategy_group,
                     strategy_version, event_date, event_kind, detail, is_provisional,
                     historical_reference, first_run_id, created_at
                   ) VALUES (?, ?, 'boll_break', '布林突破', '通道', 'boll_break:v1',
                             ?, ?, ?, 0, 0, NULL, ?)""",
                (
                    f"{asset_b}:{ev_day}:idx{idx}:{ev_kind}",
                    asset_b,
                    ev_day,
                    ev_kind,
                    f"第 {idx + 1} 条",
                    f"{ev_day}T15:00:00+08:00",
                ),
            )
        # Asset C: bearish 死叉 event
        db.execute(
            """INSERT INTO signal_events (
                 dedup_key, asset_id, strategy_id, strategy_name, strategy_group,
                 strategy_version, event_date, event_kind, detail, is_provisional,
                 historical_reference, first_run_id, created_at
               ) VALUES (?, ?, 'macd', 'MACD 12/26/9', '趋势', 'macd:v1',
                         '2026-11-24', '死叉', '偏空测试', 0, 0, NULL, '2026-11-24T15:00:00+08:00')""",
            (f"{asset_c}:2026-11-24:macd:v1:死叉", asset_c),
        )

    client = TestClient(create_app(db_path))
    bs = client.get("/api/v1/bootstrap").json()
    by_id = {a["id"]: a for a in bs["assets"]}

    assert by_id[asset_a]["latest_signal"] == {
        "event_date": "2026-11-13",
        "event_kind": "金叉 · 买入观察",
        "direction": "bullish",
        "strategy_name": "MACD 12/26/9",
        "is_provisional": False,
    }
    assert by_id[asset_b]["latest_signal"]["event_kind"] == "向下突破下轨"
    assert by_id[asset_b]["latest_signal"]["direction"] == "bearish"
    assert by_id[asset_c]["latest_signal"]["event_kind"] == "死叉"
    assert by_id[asset_c]["latest_signal"]["direction"] == "bearish"
    assert by_id[asset_empty]["latest_signal"] is None

    sigs = client.get("/api/v1/signals?limit=200").json()["signals"]
    dirs_by_kind = {s["event_kind"]: s["direction"] for s in sigs}
    assert dirs_by_kind["金叉 · 买入观察"] == "bullish"
    assert dirs_by_kind["向下突破下轨"] == "bearish"
    assert dirs_by_kind["死叉"] == "bearish"
