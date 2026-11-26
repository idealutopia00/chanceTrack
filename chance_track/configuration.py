"""Versioned configuration, shared by management and scheduled execution."""

import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from . import configuration_store, storage
from .data import TZ, _asset_exchange, fetch
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


def migrate_configuration(db):
    for statement in configuration_store.SCHEMA:
        db.execute(statement)
    row = db.execute(
        "SELECT id, payload FROM configuration_versions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row:
        try:
            payload = json.loads(row["payload"])
            known = {rule["id"] for rule in payload["strategies"]}
            for asset in payload["assets"]:
                if unknown := set(asset.get("strategies", [])) - known:
                    raise ValueError(f"标的 {asset['id']} 的策略引用不存在：{', '.join(sorted(unknown))}")
            for rule in payload["strategies"]:
                if rule["kind"] == "combination":
                    for role in ("trigger", "band"):
                        if rule.get(role) and rule[role] not in known:
                            raise ValueError(f"组合 {rule['id']} 的 {role} 引用不存在：{rule[role]}")
            validate_configuration(payload)
            configuration_store.write_current(db, payload, row["id"])
            if configuration_store.read_current(db) != {"version": row["id"], **payload}:
                raise ValueError("新旧配置内容不一致")
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"无法迁移配置版本 {row['id']}：{exc}") from exc
    db.execute(f"PRAGMA user_version={configuration_store.SCHEMA_VERSION}")


def load_configuration(path):
    storage.initialize(path)
    with storage.connect(path) as db:
        db.execute("BEGIN")
        if current := configuration_store.read_current(db):
            return current
    with storage.connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        if current := configuration_store.read_current(db):
            return current
        assets = json.loads(
            (Path(__file__).resolve().parent.parent / "watchlist.json").read_text()
        )
        for asset in assets:
            bound = list(asset.get("strategies", ["macd"]))
            now_iso = datetime.now(TZ).isoformat()
            asset.update(
                strategies=bound,
                archived=False,
                activated={sid: now_iso for sid in bound},
            )
        payload = {
            "assets": assets,
            "strategies": json.loads(json.dumps(DEFAULT_STRATEGIES)),
        }
        cursor = db.execute(
            "INSERT INTO configuration_versions(created_at,payload) VALUES(?,?)",
            (datetime.now(TZ).isoformat(), json.dumps(payload, ensure_ascii=False)),
        )
        configuration_store.write_current(db, payload, cursor.lastrowid)
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
    valid_providers = ("yahoo", "eastmoney", "sse", "szse")

    def validate_source(spec, session_profile, default_code=None):
        provider = spec.get("provider")
        if provider not in valid_providers or session_profile not in PROFILES:
            raise ValueError("未知数据源或市场时段")
        if (provider == "yahoo") != (session_profile == "us_equity"):
            raise ValueError("数据源与市场时段不匹配")
        if session_profile == "sge_gold" and provider != "eastmoney":
            raise ValueError("数据源与市场时段不匹配")
        code = spec.get("code") or default_code
        if provider == "eastmoney":
            if not spec.get("secid") or not spec.get("expected_name"):
                raise ValueError("东方财富需要secid和精确名称")
            if spec["secid"].split(".")[-1] != code:
                raise ValueError("东方财富须填写匹配的市场代码")
        elif provider in ("sse", "szse"):
            if not code or not spec.get("expected_name"):
                raise ValueError("交易所数据源需要标的代码和精确名称")
        if spec.get("mode") not in (None, "full", "close_only"):
            raise ValueError("未知备用源可用时段模式")

    for a in config["assets"]:
        if (
            not a.get("label")
            or not a.get("code")
            or a.get("session_profile") not in PROFILES
        ):
            raise ValueError("标的名称、代码、交易时段不能为空")
        if a.get("kind") not in (None, "index", "spot", "etf"):
            raise ValueError("未知标的类型")
        validate_source(a, a["session_profile"])
        fallbacks = a.get("fallbacks", [])
        if not isinstance(fallbacks, list):
            raise ValueError("备用行情源须为列表")
        seen_providers = {a["provider"]}
        primary_exchange = _asset_exchange(a)
        for entry in fallbacks:
            if not isinstance(entry, dict):
                raise ValueError("备用行情源配置无效")
            if entry.get("code") is not None and entry.get("code") != a["code"]:
                raise ValueError("备用行情源标的代码须与主标的一致")
            validate_source(entry, a["session_profile"], default_code=a["code"])
            entry_exchange = _asset_exchange(entry)
            if (
                not primary_exchange
                or not entry_exchange
                or primary_exchange != entry_exchange
            ):
                raise ValueError("备用行情源交易所须可验证且与主标的一致")
            if entry["provider"] in seen_providers:
                raise ValueError("备用行情源须使用与主源及其他备用源不同的独立来源")
            seen_providers.add(entry["provider"])
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


def save_configuration(
    path, config, expected_version, fetcher=fetch, *, lifecycle=None, validate_new=True
):
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
        identity = (
            "provider",
            "code",
            "secid",
            "expected_name",
            "session_profile",
        )
        if old and any(a.get(k) != old.get(k) for k in identity):
            raise ValueError("已有标的身份不可修改；请归档后使用新ID添加")
        if (
            validate_new
            and a.get("enabled")
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
        if a.get("kind") != "etf":
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
        configuration_store.write_current(db, config, cursor.lastrowid)
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


LEGACY_ETF_REPLACEMENTS = (
    ("gold", "AU9999", "gold_etf_518800"),
    ("star_chinext50", "931643", "star_chinext50_etf_159603"),
)


def legacy_etf_targets(config):
    assets = {asset["id"]: asset for asset in config.get("assets", [])}
    pending = []
    for legacy_id, legacy_code, replacement_id in LEGACY_ETF_REPLACEMENTS:
        old = assets.get(legacy_id)
        if (
            old
            and old.get("code") == legacy_code
            and not old.get("archived")
            and old.get("enabled", True)
        ):
            pending.append(
                {
                    "legacy_id": legacy_id,
                    "legacy_code": legacy_code,
                    "legacy_label": old.get("label", legacy_id),
                    "replacement_id": replacement_id,
                }
            )
    return pending


def _matches_verified_instrument(asset, preset):
    target_exchange = _asset_exchange(preset)
    return (
        asset.get("code") == preset["code"]
        and asset.get("session_profile") == preset["session_profile"]
        and target_exchange is not None
        and _asset_exchange(asset) == target_exchange
    )


def _reconcile_etf_fallbacks(existing, preset):
    fallbacks = list(existing.get("fallbacks") or [])
    seen = {existing.get("provider"), *(entry.get("provider") for entry in fallbacks)}
    # If an existing ETF entry still uses another primary (e.g. legacy eastmoney),
    # ensure the verified official exchange source is present in its fallback chain.
    if preset.get("provider") not in seen:
        fallbacks.append(
            {
                "provider": preset["provider"],
                "code": preset["code"],
                "expected_name": preset["expected_name"],
            }
        )
        seen.add(preset["provider"])
    for entry in preset.get("fallbacks") or []:
        if entry.get("provider") not in seen:
            fallbacks.append(deepcopy(entry))
            seen.add(entry.get("provider"))
    existing["fallbacks"] = fallbacks


def migrate_etf_targets(path, *, validate_live=False, fetcher=fetch):
    current = load_configuration(path)
    pending = legacy_etf_targets(current)
    if not pending:
        return {
            "changed": False,
            "version": current["version"],
            "migrated": [],
            "skipped": [],
        }
    presets = {
        asset["id"]: asset
        for asset in json.loads(
            (Path(__file__).resolve().parent.parent / "watchlist.json").read_text()
        )
    }
    updated = {
        "assets": deepcopy(current["assets"]),
        "strategies": deepcopy(current["strategies"]),
    }
    assets_by_id = {asset["id"]: asset for asset in updated["assets"]}
    strategy_ids = {rule["id"] for rule in updated["strategies"]}
    migrated = []
    skipped = []
    for entry in pending:
        old = assets_by_id[entry["legacy_id"]]
        replacement_id = entry["replacement_id"]
        preset = deepcopy(presets[replacement_id])
        same_id = assets_by_id.get(replacement_id)
        if same_id is not None and not _matches_verified_instrument(same_id, preset):
            skipped.append(
                {
                    "legacy_id": old["id"],
                    "legacy_code": old["code"],
                    "reason": (
                        f"已存在 ID 为 {replacement_id} 但标的代码/交易所不匹配的记录"
                        f"（当前为 {same_id.get('code')}），已保留原标的未迁移"
                    ),
                }
            )
            continue
        matches = [
            asset
            for asset in updated["assets"]
            if asset["id"] != old["id"] and _matches_verified_instrument(asset, preset)
        ]
        if len(matches) > 1:
            skipped.append(
                {
                    "legacy_id": old["id"],
                    "legacy_code": old["code"],
                    "reason": (
                        f"存在多条 {preset['code']} 同交易所记录"
                        f"（{', '.join(a['id'] for a in matches)}），身份不明确，已保留原标的未迁移"
                    ),
                }
            )
            continue
        carried = [sid for sid in old.get("strategies", []) if sid in strategy_ids]
        old["archived"] = True
        old["enabled"] = False
        if len(matches) == 1:
            existing = matches[0]
            existing["kind"] = preset["kind"]
            existing["archived"] = False
            existing["enabled"] = True
            existing["watchlisted"] = existing.get("watchlisted", True) or old.get(
                "watchlisted", True
            )
            if "priority" not in existing and "priority" in old:
                existing["priority"] = old["priority"]
            existing["strategies"] = list(
                dict.fromkeys([*(existing.get("strategies") or []), *carried])
            )
            _reconcile_etf_fallbacks(existing, preset)
            replacement = existing
        else:
            preset["strategies"] = carried
            preset["enabled"] = True
            preset["archived"] = False
            preset["watchlisted"] = old.get(
                "watchlisted", preset.get("watchlisted", True)
            )
            if "priority" in old:
                preset["priority"] = old["priority"]
            else:
                preset.pop("priority", None)
            insert_at = updated["assets"].index(old) + 1
            updated["assets"].insert(insert_at, preset)
            assets_by_id[replacement_id] = preset
            replacement = preset
        migrated.append(
            {
                "from_id": old["id"],
                "from_code": old["code"],
                "to_id": replacement["id"],
                "to_code": replacement["code"],
                "strategies": list(replacement["strategies"]),
            }
        )
    if not migrated:
        return {
            "changed": False,
            "version": current["version"],
            "migrated": [],
            "skipped": skipped,
        }
    version = save_configuration(
        path,
        updated,
        current["version"],
        fetcher=fetcher,
        validate_new=validate_live,
    )
    return {
        "changed": True,
        "version": version,
        "migrated": migrated,
        "skipped": skipped,
    }
