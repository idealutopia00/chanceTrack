"""Versioned configuration, shared by management and scheduled execution."""

import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from . import storage
from .data import TZ, fetch
from .market_sessions import PROFILES

DEFAULT_STRATEGIES = [
    {
        "id": "macd",
        "name": "MACD 默认",
        "kind": "macd",
        "group": "趋势",
        "params": {"fast": 12, "slow": 26, "signal": 9},
    },
    {
        "id": "ma",
        "name": "双均线 20/60",
        "kind": "ma",
        "group": "趋势",
        "params": {"fast": 20, "slow": 60},
    },
    {
        "id": "boll_break",
        "name": "布林突破 20/2",
        "kind": "boll_break",
        "group": "布林带",
        "params": {"period": 20, "stddev": 2},
    },
    {
        "id": "boll_return",
        "name": "布林回归 20/2",
        "kind": "boll_return",
        "group": "布林带",
        "params": {"period": 20, "stddev": 2},
    },
]


def load_configuration(path):
    with storage.connect(path) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS configuration_versions (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM configuration_versions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            return {"version": row["id"], **json.loads(row["payload"])}
        assets = json.loads(
            (Path(__file__).resolve().parent.parent / "watchlist.json").read_text()
        )
        for asset in assets:
            asset.update(
                strategies=["macd"],
                archived=False,
                activated={"macd": datetime.now(TZ).isoformat()},
            )
        payload = {
            "assets": assets,
            "strategies": json.loads(json.dumps(DEFAULT_STRATEGIES)),
        }
        cursor = db.execute(
            "INSERT INTO configuration_versions(created_at,payload) VALUES(?,?)",
            (datetime.now(TZ).isoformat(), json.dumps(payload, ensure_ascii=False)),
        )
        return {"version": cursor.lastrowid, **payload}


def validate_configuration(config):
    if not isinstance(config, dict) or not all(
        isinstance(config.get(k), list) for k in ("assets", "strategies")
    ):
        raise ValueError("标的与策略须为列表")
    if any(
        not isinstance(x, dict) or not isinstance(x.get("id"), str)
        for k in ("assets", "strategies")
        for x in config[k]
    ):
        raise ValueError("每条标的与策略须包含文本ID")
    for collection in ("assets", "strategies"):
        ids = [x["id"] for x in config[collection]]
        if len(ids) != len(set(ids)) or any(
            not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", x) for x in ids
        ):
            raise ValueError("ID 必须唯一且只含字母、数字、下划线或短横线")
    strategies = {s["id"]: s for s in config["strategies"]}
    for s in strategies.values():
        if not s.get("name") or len(s["name"]) > 100:
            raise ValueError("策略名称不能为空且最多100字")
        if not isinstance(s.get("params", {}), dict):
            raise ValueError("策略参数须为对象")
        kind, p = s["kind"], s.get("params", {})
        if kind in ("macd", "ma"):
            keys = ["fast", "slow"] + (["signal"] if kind == "macd" else [])
            if (
                any(type(p.get(k)) is not int or not 2 <= p[k] <= 250 for k in keys)
                or p["fast"] >= p["slow"]
            ):
                raise ValueError("周期须为2至250整数，短周期须小于长周期")
        elif kind in ("boll_break", "boll_return"):
            if (
                type(p.get("period")) is not int
                or not 2 <= p["period"] <= 250
                or not isinstance(p.get("stddev"), (int, float))
                or not 0.1 <= p["stddev"] <= 10
            ):
                raise ValueError("布林周期须为2至250，标准差倍数须为0.1至10")
        elif kind == "donchian":
            if any(
                type(p.get(key)) is not int or not 2 <= p[key] <= 250
                for key in ("entry", "exit")
            ):
                raise ValueError("唐奇安突破和退出周期须为2至250整数")
        elif kind in ("pullback", "trailing_exit", "squeeze"):
            periods = {
                "pullback": ("trend", "recovery", "rsi"),
                "trailing_exit": ("entry", "period"),
                "squeeze": ("period",),
            }[kind]
            if any(
                type(p.get(key)) is not int or not 2 <= p[key] <= 250 for key in periods
            ):
                raise ValueError("周期须为2至250整数")
            if kind == "pullback":
                if (
                    type(p.get("threshold")) not in (int, float)
                    or not 0 < p["threshold"] < 100
                ):
                    raise ValueError("RSI 阈值须在 0 与 100 之间")
            else:
                factors = ("stddev", "multiple") if kind == "squeeze" else ("multiple",)
                if any(
                    type(p.get(key)) not in (int, float) or not 0.1 <= p[key] <= 10
                    for key in factors
                ):
                    raise ValueError("通道倍数须为0.1至10")
            if kind == "squeeze" and p.get("range") not in ("true_range", "high_low"):
                raise ValueError("未知肯特纳振幅口径")
        elif kind == "relative_strength":
            if any(
                type(p.get(key)) is not int or not 2 <= p[key] <= 250
                for key in ("trend", "relative_period")
            ):
                raise ValueError("相对强弱周期须为2至250整数")
            if not isinstance(p.get("benchmark"), str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,64}", p["benchmark"]
            ):
                raise ValueError("请选择指数基准")
        elif kind == "combination":
            trigger = strategies.get(s.get("trigger"))
            if not trigger or trigger["kind"] == "combination":
                raise ValueError("组合须引用一个非组合触发策略")
            if s.get("filter") not in (None, "", "above_mid", "below_mid"):
                raise ValueError("未知过滤条件")
            if s.get("filter"):
                band = strategies.get(s.get("band"))
                if not band or band["kind"] not in ("boll_break", "boll_return"):
                    raise ValueError("过滤器须引用布林配置")
        else:
            raise ValueError("未知策略类型")
    assets_by_id = {asset["id"]: asset for asset in config["assets"]}
    for a in config["assets"]:
        if (
            a.get("provider") not in ("yahoo", "eastmoney")
            or a.get("session_profile") not in PROFILES
        ):
            raise ValueError("未知数据源或市场时段")
        if (a["provider"] == "yahoo") != (a["session_profile"] == "us_equity"):
            raise ValueError("数据源与市场时段不匹配")
        if a["provider"] == "eastmoney" and (
            not a.get("secid") or not a.get("expected_name")
        ):
            raise ValueError("东方财富需要secid和精确名称")
        if any(sid not in strategies for sid in a.get("strategies", [])):
            raise ValueError("绑定了不存在的策略")
        for sid in a.get("strategies", []):
            rule = strategies[sid]
            if rule["kind"] == "combination":
                rule = strategies[rule["trigger"]]
            if rule["kind"] == "relative_strength":
                benchmark = assets_by_id.get(rule["params"]["benchmark"])
                if (
                    not benchmark
                    or benchmark["code"] == a["code"]
                    or any(
                        entry.get("session_profile") != "us_equity"
                        or entry.get("provider") != "yahoo"
                        for entry in (a, benchmark)
                    )
                ):
                    raise ValueError("相对强弱须绑定同市场的另一只美元指数基准")
        if (
            not a.get("label")
            or not a.get("code")
            or a.get("session_profile") not in PROFILES
        ):
            raise ValueError("标的名称、代码、交易时段不能为空")
        if a["provider"] == "eastmoney" and a["secid"].split(".")[-1] != a["code"]:
            raise ValueError("东方财富须填写匹配的市场代码")
        if len(a.get("strategies", [])) != len(set(a.get("strategies", []))):
            raise ValueError("策略不能重复绑定")
    return config


class DuplicateStrategy(ValueError):
    def __init__(self, duplicate, matches, is_new):
        match = matches[0]
        super().__init__(f"已有匹配配置：{match['name']}（{match['id']}），请复用")
        self.duplicate_id = duplicate["id"]
        self.match_id = match["id"]
        self.match_ids = [entry["id"] for entry in matches]
        self.is_new = is_new


def strategy_signature(strategy):
    kind = strategy["kind"]
    if kind == "combination":
        condition = strategy.get("filter") or None
        parameters = (
            strategy.get("trigger"),
            condition,
            strategy.get("band") if condition else None,
        )
    else:
        parameters = strategy.get("params", {})
    return strategy["name"], kind, parameters


def relative_benchmarks(configs, asset):
    by_id = {rule["id"]: rule for rule in configs}
    selected = [by_id[sid] for sid in asset.get("strategies", []) if sid in by_id]
    triggers = [
        by_id[rule["trigger"]] if rule["kind"] == "combination" else rule
        for rule in selected
    ]
    return {
        rule["params"]["benchmark"]
        for rule in triggers
        if rule["kind"] == "relative_strength"
    }


def strategy_references(config, strategy_id):
    assets = [
        asset for asset in config["assets"] if strategy_id in asset.get("strategies", [])
    ]
    combinations = [
        (rule, key)
        for rule in config["strategies"]
        if rule["kind"] == "combination"
        for key in ("trigger", "band")
        if rule.get(key) == strategy_id
    ]
    return assets, combinations


def _change_strategy(draft, source_id, keep_id):
    config = deepcopy(draft)
    rules = {rule["id"]: rule for rule in config["strategies"]}
    source = rules.get(source_id)
    if not source:
        raise ValueError("来源配置已不存在")
    assets, combinations = strategy_references(config, source_id)
    if keep_id:
        keep = rules.get(keep_id)
        if not keep or source_id == keep_id:
            raise ValueError("请选择不同的保留配置")
        if strategy_signature(source)[1:] != strategy_signature(keep)[1:]:
            raise ValueError("仅可合并规则和参数等价的配置")
        for asset in assets:
            asset["strategies"] = list(dict.fromkeys(
                keep_id if sid == source_id else sid for sid in asset["strategies"]
            ))
        for rule, key in combinations:
            rule[key] = keep_id
    elif assets or combinations:
        locations = [
            *(f"{asset['label']}（{asset['id']}）" for asset in assets),
            *(f"{rule['name']}（{rule['id']}）" for rule, _ in combinations),
        ]
        raise ValueError("仍被引用，请先替换或解除绑定：" + "、".join(locations))
    config["strategies"].remove(source)
    return config


class ConfigurationConflict(ValueError):
    pass


def save_configuration(path, config, expected_version, fetcher=fetch, *, lifecycle=None):
    previous = load_configuration(path)
    if previous["version"] != expected_version:
        raise ConfigurationConflict("配置已被修改，请重新加载后保存")
    validate_configuration(config)
    old_ids = {rule["id"] for rule in previous["strategies"]}
    if lifecycle is not None:
        source_id, keep_id = lifecycle
        if source_id not in old_ids or keep_id and keep_id not in old_ids:
            raise ValueError("请选择已保存的配置")
        config = _change_strategy(config, source_id, keep_id)
        validate_configuration(config)
        old_ids.remove(source_id)
    old_assets = {a["id"]: a for a in previous["assets"]}
    if not set(old_assets).issubset({a["id"] for a in config["assets"]}):
        raise ValueError("已有标的不能删除，请归档")
    old_strategies = {s["id"]: s for s in previous["strategies"]}
    now = datetime.now(TZ)
    for s in config["strategies"]:
        old = old_strategies.get(s["id"])
        signature = strategy_signature(s)
        if not old or strategy_signature(old) != signature:
            matches = [
                other
                for other in config["strategies"]
                if other["id"] != s["id"] and strategy_signature(other) == signature
            ]
            if matches:
                raise DuplicateStrategy(s, matches, not old)
        unchanged = old and all(
            old.get(k) == s.get(k)
            for k in ("kind", "params", "trigger", "filter", "band")
        )
        s["revision"] = (
            old.get("revision", 1)
            if unchanged
            else (old.get("revision", 1) + 1 if old else 1)
        )
    if not old_ids.issubset({s["id"] for s in config["strategies"]}):
        raise ValueError("已有策略请解除绑定，不可删除")
    for a in config["assets"]:
        old = old_assets.get(a["id"])
        identity = ("provider", "code", "secid", "expected_name", "session_profile")
        if old and any(a.get(k) != old.get(k) for k in identity):
            raise ValueError("已有标的身份不可修改；请归档后使用新ID添加")
        if (
            a.get("enabled")
            and not a.get("archived")
            and (not old or not old.get("enabled") or old.get("archived"))
        ):
            from .analysis import analyze

            snapshot = fetcher(a, now.date().isoformat())
            checked = analyze(snapshot, now.date().isoformat())
            if checked["warnings"]:
                raise ValueError("行情已过期，暂不能启用")
            if (
                snapshot.get("exchange_timezone")
                and snapshot["exchange_timezone"]
                != PROFILES[a["session_profile"]]["timezone"]
            ):
                raise ValueError("行情时区与选定市场时段不匹配")
            a["validated_at"] = now.isoformat()
        a["kind"] = "spot" if a["session_profile"] == "sge_gold" else "index"
        activations = dict(old.get("activated", {})) if old else {}
        if lifecycle is not None:
            activations.pop(lifecycle[0], None)
        for sid in a.get("strategies", []):
            s = next(s for s in config["strategies"] if s["id"] == sid)
            dependencies = (
                ([s["trigger"]] + ([s["band"]] if s.get("filter") else []))
                if s["kind"] == "combination"
                else []
            )
            dependency_changed = any(
                next(x for x in config["strategies"] if x["id"] == d).get("revision", 1)
                != old_strategies.get(d, {}).get("revision", 1)
                for d in dependencies
            )
            revision = s.get("revision", 1)
            old_s = old_strategies.get(sid, {})
            if (
                dependency_changed
                or not old
                or sid not in old.get("strategies", [])
                or revision != old_s.get("revision", 1)
                or (
                    a.get("enabled") and (not old.get("enabled") or old.get("archived"))
                )
            ):
                activations[sid] = now.isoformat()
        a["activated"] = activations
    with storage.connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        latest = db.execute("SELECT max(id) FROM configuration_versions").fetchone()[0]
        if latest != expected_version:
            raise ConfigurationConflict("配置已被修改，请重新加载后保存")
        cursor = db.execute(
            "INSERT INTO configuration_versions(created_at,payload) VALUES(?,?)",
            (
                now.isoformat(),
                json.dumps(
                    {"assets": config["assets"], "strategies": config["strategies"]},
                    ensure_ascii=False,
                ),
            ),
        )
        return cursor.lastrowid


def configuration_history(path):
    load_configuration(path)
    with storage.connect(path) as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT id,created_at FROM configuration_versions ORDER BY id DESC"
            )
        ]


def configuration_version(path, version):
    with storage.connect(path) as db:
        row = db.execute(
            "SELECT * FROM configuration_versions WHERE id=?", (version,)
        ).fetchone()
        return (
            {
                "version": row["id"],
                "created_at": row["created_at"],
                **json.loads(row["payload"]),
            }
            if row
            else None
        )
