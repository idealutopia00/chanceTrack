from copy import deepcopy
import re

import pytest
from fastapi.testclient import TestClient

from chance_track.configuration import (
    load_configuration,
    save_configuration,
    validate_configuration,
)
from chance_track.strategies import evaluate
from chance_track.runner import run
from chance_track import storage
from chance_track.notifications import build_message
from chance_track.web import create_app
from test_loop import sample, NOW, ASSET


def test_bollinger_break_return_and_combination():
    snapshot = sample()
    for r in snapshot["rows"]:
        r["close"] = 100
    snapshot["rows"][-1]["close"] = 120
    configs = load_defaults()
    configs.append(
        {
            "id": "combo",
            "name": "突破且高于中轨",
            "kind": "combination",
            "trigger": "boll_break",
            "filter": "above_mid",
            "band": "boll_break",
        }
    )
    asset = {
        **ASSET,
        "strategies": ["boll_break", "boll_return", "combo"],
        "session_profile": "cn_equity",
    }
    outputs, layers, _ = evaluate(snapshot, configs, asset, "2026-11-17")
    assert outputs[0]["event"] == "向上突破上轨"
    assert outputs[1]["event"] is None
    assert outputs[2]["event"] == "向上突破上轨"
    assert len(layers) == 6
    snapshot["rows"][-2]["close"] = 120
    snapshot["rows"][-1]["close"] = 100
    outputs, _, _ = evaluate(snapshot, configs, asset, "2026-11-17")
    assert outputs[1]["event"] == "上轨外回归轨内"
    snapshot["provisional_date"] = snapshot["rows"][-1]["date"]
    outputs, _, _ = evaluate(snapshot, configs, asset, "2026-11-17")
    assert outputs[1]["preview_event"] == "上轨外回归轨内"
    assert outputs[0]["event"] == "向上突破上轨"
    outputs, _, _ = evaluate(snapshot, configs, asset, "2026-12-17")
    assert all(s["event"] is None and s["preview_event"] is None for s in outputs)


def load_defaults():
    from chance_track.configuration import DEFAULT_STRATEGIES

    return deepcopy(DEFAULT_STRATEGIES)


def test_donchian_replays_one_entry_and_exit_without_using_current_extremes():
    snapshot = sample()
    for row in snapshot["rows"]:
        row.update(high=101, low=99, close=100)
    rows = snapshot["rows"]
    rows[-4].update(high=104, close=101)
    rows[-3].update(high=106, close=105)
    rows[-2].update(high=108, close=107)
    rows[-1].update(low=95, close=98)
    config = {
        "id": "channel",
        "name": "唐奇安",
        "kind": "donchian",
        "params": {"entry": 20, "exit": 10},
    }
    validate_configuration({"assets": [], "strategies": [config]})
    asset = {**ASSET, "strategies": ["channel"]}
    snapshot["provisional_date"] = rows[-1]["date"]
    outputs, _, calculated = evaluate(snapshot, [config], asset, "2026-11-17")
    assert calculated["channel"]["events"][-4:] == [None, "向上突破", None, "通道退出"]
    assert "预热还需 1 根" in calculated["channel"]["reasons"][19]
    assert outputs[0]["event"] is None
    assert outputs[0]["preview_event"] == "通道退出"
    del snapshot["provisional_date"]
    outputs, _, replayed = evaluate(snapshot, [config], asset, "2026-11-17")
    assert outputs[0]["event"] == "通道退出"
    assert replayed["channel"]["events"] == calculated["channel"]["events"]
    repeated_outputs, _, repeated = evaluate(
        deepcopy(snapshot), [config], asset, "2026-11-17"
    )
    assert repeated_outputs == outputs
    assert repeated["channel"]["reasons"] == replayed["channel"]["reasons"]
    assert "98.0000" in outputs[0]["detail"] and "99.0000" in outputs[0]["detail"]
    rows[-1]["close"] = 100
    recovered, _, _ = evaluate(snapshot, [config], asset, "2026-11-17")
    assert recovered[0]["event"] is None
    config["params"]["exit"] = len(rows)
    _, _, longer_exit = evaluate(snapshot, [config], asset, "2026-11-17")
    assert longer_exit["channel"]["events"][-3] == "向上突破"


def test_configuration_versions_archive_conflict_and_dependencies(tmp_path):
    path = tmp_path / "config.sqlite"
    original = load_configuration(path)
    config = deepcopy(original)
    config["strategies"].append(
        {
            "id": "combo",
            "name": "组合",
            "kind": "combination",
            "trigger": "macd",
            "filter": "above_mid",
            "band": "boll_break",
        }
    )
    config["assets"][0]["strategies"] = ["macd", "combo"]
    version = save_configuration(path, config, original["version"])
    assert version == 2
    with pytest.raises(ValueError, match="重新加载"):
        save_configuration(path, deepcopy(original), original["version"])
    config = load_configuration(path)
    config["strategies"][0]["params"]["fast"] = 10
    save_configuration(path, config, version)
    changed = load_configuration(path)
    assert changed["strategies"][0]["revision"] == 2
    assert changed["assets"][0]["activated"]["combo"]
    config = deepcopy(changed)
    config["assets"].pop()
    with pytest.raises(ValueError, match="归档"):
        save_configuration(path, config, changed["version"])
    config = deepcopy(changed)
    config["strategies"][-1]["trigger"] = "combo"
    with pytest.raises(ValueError, match="非组合"):
        validate_configuration(config)
    config = deepcopy(changed)
    config["assets"][0]["archived"] = True
    save_configuration(path, config, changed["version"])
    assert load_configuration(path)["assets"][0]["archived"]


def test_report_notification_and_management_roundtrip(tmp_path):
    path = tmp_path / "reports.sqlite"
    config = load_configuration(path)
    asset = {
        **ASSET,
        "strategies": ["macd", "ma", "boll_break"],
        "session_profile": "cn_equity",
    }
    rid = run(path, [asset], send=False, now=NOW, fetcher=lambda *_: sample())
    report = storage.report(path, rid)
    assert report["configuration"]["version"] == config["version"]
    result = report["items"][0]["result"]
    assert len(result["strategies"]) == 3
    message, keys = build_message(report, set())
    assert "MACD 默认" in message and keys
    _, repeated = build_message(report, keys)
    assert not repeated
    client = TestClient(create_app(path))
    page = client.get("/manage")
    assert page.status_code == 200 and "/static/manage.js" in page.text
    csrf = re.search('name="csrf" value="([^"]+)"', page.text)[1]
    response = client.post(
        "/manage",
        data={
            "csrf": csrf,
            "version": config["version"],
            "payload": __import__("json").dumps(config),
        },
    )
    assert response.status_code == 200
    assert client.get(f"/reports/{rid}").status_code == 200
    assert storage.report(path, rid) == report


def test_activation_suppresses_history_not_next_close():
    asset = {
        **ASSET,
        "strategies": ["macd"],
        "session_profile": "cn_equity",
        "activated": {"macd": "2026-11-16T16:00:00+08:00"},
    }
    outputs, _, _ = evaluate(sample(), load_defaults(), asset, "2026-11-17")
    assert outputs[0]["event"] == "金叉" and outputs[0]["historical_reference"]
    asset["activated"]["macd"] = "2026-11-16T14:00:00+08:00"
    outputs, _, _ = evaluate(sample(), load_defaults(), asset, "2026-11-17")
    assert not outputs[0]["historical_reference"]


def test_management_csrf_save_and_historical_snapshot(tmp_path):
    import json
    import re
    from fastapi.testclient import TestClient
    from chance_track.web import create_app
    from chance_track.configuration import configuration_version

    path = tmp_path / "web.sqlite"
    client = TestClient(create_app(path))
    page = client.get("/manage")
    assert page.status_code == 200
    assert "/static/manage.js" in page.text
    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    config = load_configuration(path)
    config["assets"][0]["strategies"] = ["macd", "boll_break"]
    data = {"csrf": token, "version": config["version"], "payload": json.dumps(config)}
    assert client.post("/manage", data={**data, "csrf": "bad"}).status_code == 403
    assert client.post("/manage", data=data).status_code == 200
    assert configuration_version(path, 1)["assets"][0]["strategies"] == ["macd"]
    assert client.get("/configuration/2").json()["assets"][0]["strategies"] == [
        "macd",
        "boll_break",
    ]
    assert client.post("/manage", data=data).status_code == 400


def test_new_binding_old_event_is_reference_not_notification(tmp_path):
    from chance_track.notifications import build_message
    from chance_track.runner import run
    from chance_track import storage
    from test_loop import sample, NOW

    asset = {
        "id": "test",
        "label": "测试",
        "code": "TEST",
        "enabled": True,
        "session_profile": "cn_equity",
        "strategies": ["macd", "boll_break"],
        "activated": {"macd": NOW.isoformat(), "boll_break": NOW.isoformat()},
    }
    path = tmp_path / "events.sqlite"
    report_id = run(path, [asset], now=NOW, send=False, fetcher=lambda *_: sample())
    report = storage.report(path, report_id)
    strategies = report["items"][0]["result"]["strategies"]
    assert strategies[0]["event"] == "金叉"
    assert all(s["historical_reference"] for s in strategies)
    message, keys = build_message(report, set())
    assert not keys and "启用前历史参考" in message
    asset["activated"] = {}
    second = run(path, [asset], now=NOW, send=False, fetcher=lambda *_: sample())
    report = storage.report(path, second)
    _, keys = build_message(report, set())
    assert len(keys) == 2
    assert not build_message(report, keys)[1]
    from fastapi.testclient import TestClient
    from chance_track.web import create_app

    page = TestClient(create_app(path)).get(f"/reports/{second}")
    assert page.status_code == 200 and "布林突破" in page.text


def test_combination_same_bar_filter_and_missing_samples():
    from test_loop import sample, NOW

    configs = load_defaults() + [
        {
            "id": "combo",
            "name": "组合",
            "kind": "combination",
            "trigger": "macd",
            "filter": "above_mid",
            "band": "boll_break",
        }
    ]
    asset = {"strategies": ["combo"], "session_profile": "cn_equity"}
    results, layers, _ = evaluate(sample(), configs, asset, NOW.date().isoformat())
    assert results[0]["event"] == "金叉" and len(layers) == 3
    assert "macd" in results[0]["config"]["dependencies"]
    configs[-1]["filter"] = "below_mid"
    results, _, _ = evaluate(sample(), configs, asset, NOW.date().isoformat())
    assert results[0]["event"] is None and "未满足" in results[0]["detail"]
    configs[2]["params"]["period"] = 250
    results, _, _ = evaluate(sample(), configs, asset, NOW.date().isoformat())
    assert results[0]["state"] == "unavailable"


def test_enabling_new_asset_validates_source_timezone_without_saving(tmp_path):
    from test_loop import sample

    path = tmp_path / "enable.sqlite"
    config = load_configuration(path)
    config["assets"].append(
        {
            "id": "new",
            "label": "新指数",
            "code": "NEW",
            "provider": "yahoo",
            "session_profile": "us_equity",
            "enabled": True,
            "strategies": [],
        }
    )
    calls = []

    def wrong_market(asset, day):
        calls.append(asset["id"])
        snapshot = sample()
        from datetime import date, timedelta

        for i, row in enumerate(snapshot["rows"]):
            row["date"] = (
                date.today() - timedelta(days=len(snapshot["rows"]) - 1 - i)
            ).isoformat()
        snapshot["exchange_timezone"] = "Asia/Tokyo"
        return snapshot

    with pytest.raises(ValueError, match="时区"):
        save_configuration(path, config, config["version"], fetcher=wrong_market)
    assert calls == ["new"] and load_configuration(path)["version"] == 1


def test_stateful_preset_replay_warmup_and_provisional_events():
    cases = [
        (
            {
                "id": "rsi",
                "name": "回调",
                "kind": "pullback",
                "params": {"trend": 20, "recovery": 3, "rsi": 2, "threshold": 10},
            },
            [1] * 20 + [25, 24, 23, 22, 21, 20, 19, 18, 17, 22],
            ["短线回调", "回调修复"],
        ),
        (
            {
                "id": "atr",
                "name": "跟踪退出",
                "kind": "trailing_exit",
                "params": {"entry": 3, "period": 3, "multiple": 1},
            },
            [10, 10, 10, 12, 14, 14, 10],
            ["向上突破", "跟踪退出"],
        ),
        (
            {
                "id": "squeeze",
                "name": "收缩释放",
                "kind": "squeeze",
                "params": {
                    "period": 3,
                    "stddev": 2,
                    "multiple": 1.5,
                    "range": "high_low",
                },
            },
            [10, 10, 10, 10, 10, 10, 10, 14],
            ["释放 · 向上"],
        ),
    ]
    for config, closes, expected in cases:
        snapshot = sample()
        snapshot["rows"] = snapshot["rows"][-len(closes) :]
        rows = snapshot["rows"]
        for row, close in zip(rows, closes):
            row.update(open=close, high=close + 0.5, low=close - 0.5, close=close)
        validate_configuration({"assets": [], "strategies": [config]})
        asset = {"strategies": [config["id"]]}
        day = rows[-1]["date"]
        snapshot["provisional_date"] = day
        preview, _, computed = evaluate(snapshot, [config], asset, day)
        signals = computed[config["id"]]
        assert [event for event in signals["events"] if event] == expected
        assert "预热还需" in signals["reasons"][0]
        assert preview[0]["event"] is None
        assert preview[0]["preview_event"] == expected[-1]
        snapshot.pop("provisional_date")
        confirmed, _, replayed = evaluate(snapshot, [config], asset, day)
        assert confirmed[0]["event"] == expected[-1]
        assert replayed[config["id"]]["events"] == signals["events"]
        assert replayed[config["id"]]["reasons"] == signals["reasons"]
