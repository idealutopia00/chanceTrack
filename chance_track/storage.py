"""Immutable reports and append-only user decisions in a single SQLite database."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .configuration_store import SCHEMA_VERSION


def event_key(item, rule_version):
    result = item["result"]
    event_date = (
        result.get("event_date") or result.get("bar_date") or result.get("nav_date")
    )
    return f"{item['asset']['id']}:{event_date}:{rule_version}:{result['event']}"


def migrate_notifications(db):
    columns = {
        row["name"] for row in db.execute("PRAGMA table_info(notifications)")
    }
    if "message" not in columns:
        db.execute("ALTER TABLE notifications ADD COLUMN message TEXT")
    # Old popups counted only new_event; a silent report or a zero-count popup
    # must not be backfilled as having delivered that signal.
    for row in db.execute(
        "SELECT r.id,r.payload FROM reports r JOIN notifications n ON n.report_id=r.id WHERE n.sent_at IS NOT NULL"
    ).fetchall():
        report_data = json.loads(row["payload"])
        for item in report_data["items"]:
            if item.get("result", {}).get("event") and item["result"].get(
                "new_event"
            ):
                db.execute(
                    "INSERT OR IGNORE INTO notification_events VALUES(?,?)",
                    (event_key(item, report_data["rule"]["version"]), row["id"]),
                )
    db.execute("PRAGMA user_version=1")


def migrate_investment_note_ids(db):
    db.execute("ALTER TABLE investment_notes RENAME TO legacy_investment_notes")
    db.execute("""
        CREATE TABLE investment_notes (
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
          markdown TEXT NOT NULL, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0)
    """)
    db.execute("INSERT INTO investment_notes SELECT * FROM legacy_investment_notes")
    db.execute("DROP TABLE legacy_investment_notes")
    db.execute("""
        CREATE UNIQUE INDEX investment_notes_pin
        ON investment_notes(pinned) WHERE pinned = 1
    """)
    db.execute("PRAGMA user_version=4")

@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=30)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        yield db
        db.commit()
    finally:
        db.close()


def initialize(path, *, upgrade_configuration=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError(f"数据库结构版本 {version} 高于本程序支持的 {SCHEMA_VERSION}")
        if version == SCHEMA_VERSION:
            return
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        if version > SCHEMA_VERSION:
            raise ValueError(f"数据库结构版本 {version} 高于本程序支持的 {SCHEMA_VERSION}")
        has_tables = db.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
        ).fetchone()
        if (version or has_tables) and not upgrade_configuration:
            raise ValueError("旧数据库需要使用安装器完成备份与配置升级")
        if version < 3:
            statements = """
    CREATE TABLE IF NOT EXISTS reports (
      id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, day TEXT NOT NULL,
      kind TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS events (
      key TEXT PRIMARY KEY, report_id INTEGER NOT NULL REFERENCES reports(id));
    CREATE TABLE IF NOT EXISTS decisions (
      id INTEGER PRIMARY KEY, report_id INTEGER NOT NULL REFERENCES reports(id),
      asset_id TEXT NOT NULL, created_at TEXT NOT NULL, action TEXT NOT NULL,
      execution TEXT NOT NULL, note TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS decision_contexts (
      decision_id INTEGER PRIMARY KEY REFERENCES decisions(id), payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS notification_events (
      key TEXT PRIMARY KEY, report_id INTEGER NOT NULL REFERENCES reports(id));
    CREATE TABLE IF NOT EXISTS notifications (
      report_id INTEGER PRIMARY KEY REFERENCES reports(id), sent_at TEXT, error TEXT);
    CREATE TABLE IF NOT EXISTS investment_notes (
      id INTEGER PRIMARY KEY, title TEXT NOT NULL, markdown TEXT NOT NULL,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0);
    CREATE UNIQUE INDEX IF NOT EXISTS investment_notes_pin
      ON investment_notes(pinned) WHERE pinned = 1;
    """
            for statement in statements.strip().removesuffix(";").split(";"):
                db.execute(statement)
            if version < 1:
                migrate_notifications(db)
        if version < 4:
            migrate_investment_note_ids(db)
        from .configuration import migrate_configuration

        migrate_configuration(db)


def investment_notes(path):
    with connect(path) as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT id,title,created_at,pinned FROM investment_notes ORDER BY created_at DESC, id DESC"
            )
        ]


def investment_note(path, note_id=None):
    with connect(path) as db:
        row = (
            db.execute(
                "SELECT * FROM investment_notes WHERE id=?", (note_id,)
            ).fetchone()
            if note_id is not None
            else db.execute("SELECT * FROM investment_notes WHERE pinned=1").fetchone()
        )
        return dict(row) if row else None


def save_investment_note(path, note_id, title, markdown, now):
    with connect(path) as db:
        if note_id is None:
            cursor = db.execute(
                "INSERT INTO investment_notes(title,markdown,created_at,updated_at) VALUES(?,?,?,?)",
                (title, markdown, now, now),
            )
            return cursor.lastrowid
        cursor = db.execute(
            "UPDATE investment_notes SET title=?,markdown=?,updated_at=? WHERE id=?",
            (title, markdown, now, note_id),
        )
        return note_id if cursor.rowcount else None


def delete_investment_note(path, note_id, updated_at):
    with connect(path) as db:
        return db.execute(
            "DELETE FROM investment_notes WHERE id=? AND updated_at=?",
            (note_id, updated_at),
        ).rowcount == 1


def pin_investment_note(path, note_id):
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute(
            "SELECT 1 FROM investment_notes WHERE id=?", (note_id,)
        ).fetchone():
            return False
        db.execute("UPDATE investment_notes SET pinned=0 WHERE pinned=1")
        db.execute("UPDATE investment_notes SET pinned=1 WHERE id=?", (note_id,))
        return True


def save_report(path, report):
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        # This recheck also handles two concurrent scheduled invocations.
        if report["kind"] == "scheduled":
            existing = db.execute(
                "SELECT id FROM reports WHERE day=? AND kind='scheduled' "
                "AND json_extract(payload,'$.rule.version')=? "
                "AND coalesce(json_extract(payload,'$.attempt'),1)=?",
                (report["day"], report["rule"]["version"], report.get("attempt", 1)),
            ).fetchone()
            if existing:
                return existing["id"], False
        cursor = db.execute(
            "INSERT INTO reports(created_at,day,kind,payload) VALUES(?,?,?,?)",
            (report["created_at"], report["day"], report["kind"], "{}"),
        )
        report_id = cursor.lastrowid
        for item in report["items"]:
            for strategy in item.get("result", {}).get("strategies", []):
                if strategy.get("event"):
                    key = strategy_event_key(item, strategy)
                    strategy["new_event"] = bool(
                        db.execute(
                            "INSERT OR IGNORE INTO events VALUES(?,?)", (key, report_id)
                        ).rowcount
                    )
            if item.get("result", {}).get("event"):
                key = event_key(item, report["rule"]["version"])
                inserted = db.execute(
                    "INSERT OR IGNORE INTO events VALUES(?,?)", (key, report_id)
                ).rowcount
                item["result"]["new_event"] = bool(inserted)
        db.execute(
            "UPDATE reports SET payload=? WHERE id=?",
            (json.dumps(report, ensure_ascii=False), report_id),
        )
        return report_id, True


def latest_scheduled(path, day, rule_version):
    with connect(path) as db:
        row = db.execute(
            "SELECT id,payload FROM reports WHERE day=? AND kind='scheduled' AND json_extract(payload,'$.rule.version')=? ORDER BY id DESC LIMIT 1",
            (day, rule_version),
        ).fetchone()
        return {"id": row["id"], **json.loads(row["payload"])} if row else None


def reports(path):
    with connect(path) as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT id,created_at,kind FROM reports ORDER BY id DESC LIMIT 200"
            )
        ]


def report(path, report_id=None):
    with connect(path) as db:
        row = (
            db.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
            if report_id
            else db.execute("SELECT * FROM reports ORDER BY id DESC LIMIT 1").fetchone()
        )
        if not row:
            return None
        return {"id": row["id"], **json.loads(row["payload"])}


def last_market_quote(path, asset_id):
    with connect(path) as db:
        row = db.execute(
            """
            SELECT coalesce(json_extract(item.value, '$.origin_report_id'), reports.id) AS report_id,
                   json_extract(item.value, '$.snapshot.fetched_at') AS fetched_at,
                   json_extract(item.value, '$.result.bar_date') AS bar_date,
                   json_extract(item.value, '$.result.close') AS close,
                   json_extract(item.value, '$.result.is_provisional') AS is_provisional
            FROM reports, json_each(reports.payload, '$.items') AS item
            WHERE json_extract(item.value, '$.asset.id') = ?
              AND json_extract(item.value, '$.status') IN ('ok', 'warning')
              AND json_extract(item.value, '$.snapshot.basis') = 'market_ohlc'
              AND json_extract(item.value, '$.result.bar_date') IS NOT NULL
            ORDER BY fetched_at DESC, reports.id DESC
            LIMIT 1
            """,
            (asset_id,),
        ).fetchone()
        return dict(row) if row else None


def decisions(path, report_id):
    with connect(path) as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT d.*, EXISTS(SELECT 1 FROM decision_contexts c WHERE c.decision_id=d.id) AS has_exploration FROM decisions d WHERE report_id=? ORDER BY id DESC",
                (report_id,),
            )
        ]


def add_decision(path, report_id, asset_id, action, execution, note, now, context=None):
    if action not in ("买入", "卖出", "持有", "观望") or execution not in (
        "未执行",
        "已提交",
    ):
        raise ValueError("无效决策或执行状态")
    if len(note) > 4000:
        raise ValueError("备注不能超过 4000 字")
    with connect(path) as db:
        row = db.execute(
            "SELECT payload FROM reports WHERE id=?", (report_id,)
        ).fetchone()
        if not row or asset_id not in [
            item["asset"]["id"] for item in json.loads(row["payload"])["items"]
        ]:
            raise ValueError("报告或标的不存在")
        cursor = db.execute(
            "INSERT INTO decisions(report_id,asset_id,created_at,action,execution,note) VALUES(?,?,?,?,?,?)",
            (report_id, asset_id, now, action, execution, note),
        )
        if context is not None:
            db.execute(
                "INSERT INTO decision_contexts VALUES(?,?)",
                (cursor.lastrowid, json.dumps(context, ensure_ascii=False)),
            )


def decision_context(path, decision_id):
    with connect(path) as db:
        row = db.execute(
            "SELECT payload FROM decision_contexts WHERE decision_id=?", (decision_id,)
        ).fetchone()
        return json.loads(row["payload"]) if row else None


def notification(path, report_id):
    with connect(path) as db:
        row = db.execute(
            "SELECT * FROM notifications WHERE report_id=?", (report_id,)
        ).fetchone()
        return dict(row) if row else None


def notified_event_keys(path):
    with connect(path) as db:
        return {row["key"] for row in db.execute("SELECT key FROM notification_events")}


def record_notification(
    path, report_id, message, sent_at=None, error=None, event_keys=()
):
    with connect(path) as db:
        db.execute(
            "INSERT OR REPLACE INTO notifications(report_id,sent_at,error,message) VALUES(?,?,?,?)",
            (report_id, sent_at, error, message),
        )
        if sent_at:
            db.executemany(
                "INSERT OR IGNORE INTO notification_events VALUES(?,?)",
                [(key, report_id) for key in event_keys],
            )


def strategy_event_key(item, strategy):
    return f"{item['asset']['id']}:{strategy['event_date']}:{strategy['version']}:{strategy['event']}"
