"""SQLite representation of the current monitoring configuration."""

import json


SCHEMA_VERSION = 5

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS configuration_versions (
        id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL
    )""",
    """CREATE TABLE current_configuration (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        version INTEGER NOT NULL REFERENCES configuration_versions(id)
    )""",
    """CREATE TABLE current_assets (
        id TEXT PRIMARY KEY NOT NULL, position INTEGER NOT NULL UNIQUE,
        metadata TEXT NOT NULL
    )""",
    """CREATE TABLE current_strategies (
        id TEXT PRIMARY KEY NOT NULL, position INTEGER NOT NULL UNIQUE,
        kind TEXT NOT NULL, name TEXT NOT NULL, revision INTEGER,
        parameters TEXT, metadata TEXT NOT NULL
    )""",
    """CREATE TABLE monitoring_bindings (
        asset_id TEXT NOT NULL REFERENCES current_assets(id) ON DELETE CASCADE,
        strategy_id TEXT NOT NULL REFERENCES current_strategies(id) ON DELETE RESTRICT,
        position INTEGER NOT NULL, activated TEXT,
        PRIMARY KEY (asset_id, strategy_id), UNIQUE (asset_id, position)
    )""",
    """CREATE TABLE combination_references (
        owner_id TEXT NOT NULL REFERENCES current_strategies(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('trigger', 'band')),
        target_id TEXT NOT NULL REFERENCES current_strategies(id) ON DELETE RESTRICT,
        PRIMARY KEY (owner_id, role)
    )""",
)


def read_current(db):
    current = db.execute("SELECT version FROM current_configuration").fetchone()
    latest = db.execute("SELECT max(id) FROM configuration_versions").fetchone()[0]
    if (current["version"] if current else None) != latest:
        raise ValueError("当前配置和版本记录不匹配，请检查数据库与程序版本")
    if current is None:
        return None

    assets = {}
    for row in db.execute("SELECT * FROM current_assets ORDER BY position"):
        assets[row["id"]] = {"id": row["id"], **json.loads(row["metadata"])}
    for row in db.execute("SELECT * FROM monitoring_bindings ORDER BY position"):
        asset = assets[row["asset_id"]]
        asset.setdefault("strategies", []).append(row["strategy_id"])
        if row["activated"] is not None:
            asset.setdefault("activated", {})[row["strategy_id"]] = row["activated"]

    rules = {}
    for row in db.execute("SELECT * FROM current_strategies ORDER BY position"):
        rule = {**json.loads(row["metadata"]), "id": row["id"], "kind": row["kind"], "name": row["name"]}
        if row["revision"] is not None:
            rule["revision"] = row["revision"]
        if row["parameters"] is not None:
            rule["params"] = json.loads(row["parameters"])
        rules[row["id"]] = rule
    for row in db.execute("SELECT * FROM combination_references"):
        rules[row["owner_id"]][row["role"]] = row["target_id"]

    return {"version": latest, "assets": list(assets.values()), "strategies": list(rules.values())}


def write_current(db, config, version):
    # All callers supply a write transaction and the corresponding snapshot ID.
    db.execute("DELETE FROM combination_references")
    db.execute("DELETE FROM monitoring_bindings")
    db.execute("DELETE FROM current_assets")
    db.execute("DELETE FROM current_strategies")

    for position, asset in enumerate(config["assets"]):
        metadata = {key: value for key, value in asset.items() if key != "id"}
        bindings = metadata.get("strategies", [])
        if "strategies" in metadata:
            metadata["strategies"] = []
        activated = metadata.get("activated", {})
        if "activated" in metadata:
            metadata["activated"] = {key: value for key, value in activated.items() if key not in bindings}
        db.execute(
            "INSERT INTO current_assets VALUES (?, ?, ?)",
            (asset["id"], position, json.dumps(metadata, ensure_ascii=False)),
        )

    references = []
    fields = ("id", "kind", "name", "revision", "params")
    for position, rule in enumerate(config["strategies"]):
        metadata = {key: value for key, value in rule.items() if key not in fields}
        if rule["kind"] == "combination":
            for role in ("trigger", "band"):
                if target := metadata.get(role):
                    references.append((rule["id"], role, target))
                    del metadata[role]
        db.execute(
            "INSERT INTO current_strategies VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rule["id"], position, rule["kind"], rule["name"], rule.get("revision"),
             json.dumps(rule["params"], ensure_ascii=False) if "params" in rule else None,
             json.dumps(metadata, ensure_ascii=False)),
        )

    db.executemany("INSERT INTO combination_references VALUES (?, ?, ?)", references)
    db.executemany(
        "INSERT INTO monitoring_bindings VALUES (?, ?, ?, ?)",
        (
            (asset["id"], sid, position, asset.get("activated", {}).get(sid))
            for asset in config["assets"]
            for position, sid in enumerate(asset.get("strategies", []))
        ),
    )
    db.execute(
        """INSERT INTO current_configuration VALUES (1, ?)
        ON CONFLICT (singleton) DO UPDATE SET version = excluded.version""",
        (version,),
    )
