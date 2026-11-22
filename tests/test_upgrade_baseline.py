import gzip
import json
from pathlib import Path

import pytest
from test_loop import NOW, fetcher

from chance_track import storage
from chance_track.configuration import load_configuration
from chance_track.notifications import build_message
from chance_track.runner import run

ACTIVATED = "2026-10-01T09:00:00+08:00"


@pytest.fixture
def v08_database(tmp_path):
    path = tmp_path / "v08.sqlite"
    fixture = Path(__file__).with_name("fixtures") / "v08-monitoring.sql.gz"
    with storage.connect(path) as db, gzip.open(fixture, "rt", encoding="utf-8") as sql:
        # SQLite dumps insert tables in name order, including child rows first.
        db.executescript("PRAGMA foreign_keys=OFF;\n" + sql.read())
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    return path


def test_v08_configuration_and_history_baseline(v08_database):
    path = v08_database
    with storage.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        row = db.execute(
            "SELECT id, payload FROM configuration_versions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        expected = {"version": row["id"], **json.loads(row["payload"])}
        event_keys = {row["key"] for row in db.execute("SELECT key FROM events")}
    report_id = 1
    storage.initialize(path, upgrade_configuration=True)
    assert load_configuration(path) == expected
    assets = {asset["id"]: asset for asset in expected["assets"]}
    assert assets["sp500"]["strategies"] == ["macd_sp500", "macd_combo"]
    assert sum(asset["strategies"] == ["macd"] for asset in assets.values()) == 4
    assert all(
        asset["activated"] == dict.fromkeys(asset["strategies"], ACTIVATED)
        for asset in assets.values()
    )
    rules = {rule["id"]: rule for rule in expected["strategies"]}
    assert rules["macd_combo"]["trigger"] == "macd_sp500"
    assert rules["macd_unused"]["params"] == rules["macd_sp500"]["params"]
    assert storage.investment_note(path)["markdown"] == "# 合成备忘"

    report = storage.report(path, report_id)
    assert report["configuration"] == expected
    assert rules["macd_sp500"]["params"] == {"fast": 12, "slow": 26, "signal": 9}
    keys = storage.notified_event_keys(path)
    assert keys and keys <= event_keys
    notification = storage.notification(path, report_id)
    assert notification["sent_at"] == NOW.isoformat() and notification["message"]
    assert not build_message(report, keys)[1]

    replay = run(path, now=NOW, fetcher=fetcher, send=False)
    assert storage.report(path, report_id) == report
    assert not build_message(storage.report(path, replay), keys)[1]


def test_relational_migration_is_idempotent_and_enforces_references(v08_database):
    import sqlite3

    path = v08_database
    storage.initialize(path, upgrade_configuration=True)
    current = load_configuration(path)
    old_report = storage.report(path, 1)
    for _ in range(2):
        storage.initialize(path)
        assert load_configuration(path) == current
    with storage.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("SELECT count(*) FROM configuration_versions").fetchone()[0] == 2
        assert db.execute(
            "SELECT activated FROM monitoring_bindings WHERE asset_id=? AND strategy_id=?",
            ("sp500", "macd_sp500"),
        ).fetchone()[0] == ACTIVATED
        assert db.execute("SELECT target_id FROM combination_references").fetchone()[0] == "macd_sp500"
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO monitoring_bindings VALUES ('sp500', 'macd_sp500', 2, NULL)")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO monitoring_bindings VALUES ('sp500', 'missing', 2, NULL)")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM current_strategies WHERE id='macd_sp500'")
    assert storage.report(path, 1) == old_report


@pytest.mark.parametrize("failure", ["interrupted", "storage_full"])
def test_migration_failure_keeps_v08_readable(v08_database, monkeypatch, failure):
    import sqlite3

    from chance_track import configuration_store

    path = v08_database
    historical_report = storage.report(path, 1)
    original = configuration_store.write_current

    def fail(db, config, version):
        original(db, config, version)
        if failure == "interrupted":
            raise RuntimeError("interrupted")
        raise sqlite3.OperationalError("database or disk is full")

    with monkeypatch.context() as patch:
        patch.setattr(configuration_store, "write_current", fail)
        with pytest.raises((RuntimeError, sqlite3.OperationalError)):
            storage.initialize(path, upgrade_configuration=True)
    with storage.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert not db.execute(
            "SELECT name FROM sqlite_schema WHERE name='current_configuration'"
        ).fetchall()
    assert storage.report(path, 1) == historical_report
    storage.initialize(path, upgrade_configuration=True)
    assert load_configuration(path)["version"] == 2


def test_migration_refuses_a_dangling_reference_with_location(v08_database):
    path = v08_database
    with storage.connect(path) as db:
        db.execute(
            "UPDATE configuration_versions SET payload=json_set(payload, '$.assets[0].strategies[0]', 'missing') WHERE id=2"
        )
    with pytest.raises(ValueError, match="sp500.*missing"):
        storage.initialize(path, upgrade_configuration=True)
    with storage.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4


def test_concurrent_relational_writes_only_save_one_version(v08_database, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from chance_track import configuration

    path = v08_database
    storage.initialize(path, upgrade_configuration=True)
    expected = load_configuration(path)["version"]
    ready = Barrier(2)

    def synchronized_load(db_path):
        current = load_configuration(db_path)
        ready.wait(timeout=5)
        return current

    def save(label):
        current = load_configuration(path)
        current["assets"][0]["label"] = label
        try:
            return configuration.save_configuration(path, current, expected)
        except configuration.ConfigurationConflict:
            return None

    monkeypatch.setattr(configuration, "load_configuration", synchronized_load)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["并发修改甲", "并发修改乙"]))
    assert results.count(expected + 1) == results.count(None) == 1
    current = load_configuration(path)
    assert current["version"] == expected + 1
    assert current["assets"][0]["label"] in {"并发修改甲", "并发修改乙"}
    assert configuration.configuration_version(path, expected + 1)["assets"] == current["assets"]


@pytest.mark.parametrize("mode", ["fresh", "adopt", "upgrade", "startup_failure"])
def test_installer_rehearsal_uses_isolated_runtime(v08_database, monkeypatch, tmp_path, mode):
    import runpy
    import shutil
    import subprocess
    import sys
    import tarfile

    from fastapi.testclient import TestClient

    from chance_track.web import create_app

    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    installer = runpy.run_path(str(root / "scripts" / "install_launchd.py"))
    main = installer["main"]
    source_root = tmp_path / "candidate-source"
    shutil.copytree(
        root / "chance_track", source_root / "chance_track",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("watchlist.json", "pyproject.toml", "uv.lock", "README.md"):
        shutil.copy2(root / name, source_root / name)
    legacy_path = source_root / "data" / "chance-track.sqlite3"
    if mode == "adopt":
        legacy_path.parent.mkdir()
        shutil.copy2(v08_database, legacy_path)
    support = tmp_path / "support"
    runtime = support / "runtime"
    database = support / "data" / "chance-track.sqlite3"
    old_revision = installer["V08_RECOVERY_REVISION"]
    run_process = subprocess.run
    candidate_revision = "b" * 40
    if mode in ("upgrade", "startup_failure"):
        runtime.mkdir(parents=True)
        (runtime / "deployment-revision.txt").write_text(old_revision)
        database.parent.mkdir()
        shutil.copy2(v08_database, database)

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setitem(main.__globals__, "ROOT", source_root)
    monkeypatch.setitem(main.__globals__, "SUPPORT", support)
    monkeypatch.setitem(main.__globals__, "RUNTIME", runtime)
    monkeypatch.setattr(sys, "argv", ["install_launchd.py"])
    actions = []

    def command(args, **kwargs):
        if args[0] == "git":
            return subprocess.CompletedProcess(args, 0, stdout=candidate_revision)
        if args[0] != "launchctl":
            return subprocess.CompletedProcess(args, 0)
        actions.append(tuple(args))
        return subprocess.CompletedProcess(args, int(args[1] == "print" and mode in ("fresh", "adopt")))

    def ready(expected_revision):
        assert expected_revision == candidate_revision
        if mode == "startup_failure":
            raise RuntimeError("synthetic web startup failure")
        assert TestClient(create_app(database)).get("/").status_code == 200

    monkeypatch.setattr(subprocess, "run", command)
    monkeypatch.setitem(main.__globals__, "wait_for_web", ready)
    if mode == "startup_failure":
        with pytest.raises(RuntimeError, match="synthetic web startup failure"):
            main()
        assert not any(action[1] == "bootstrap" and action[-1].endswith("check.plist")
                       for action in actions)
    else:
        main()
        assert actions[-1][-1].endswith("check.plist")
    assert (runtime / "deployment-revision.txt").read_text() == candidate_revision
    assert load_configuration(database)["version"] == (1 if mode == "fresh" else 2)

    if mode != "fresh":
        backup, = (support / "backups").glob("configuration-v4-*")
        assert (backup / "runtime-revision.txt").read_text().strip() == old_revision
        with storage.connect(backup / "chance-track.sqlite3") as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 4
            assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert storage.report(backup / "chance-track.sqlite3", 1) == storage.report(database, 1)

        if mode == "startup_failure" and (root / ".git").exists():
            old_runtime = tmp_path / "restored-runtime"
            old_runtime.mkdir()
            archive = tmp_path / "v08-runtime.tar"
            with archive.open("wb") as stream:
                run_process(["git", "archive", old_revision], cwd=root, stdout=stream, check=True)
            with tarfile.open(archive) as source:
                source.extractall(old_runtime, filter="data")
            (old_runtime / "deployment-revision.txt").write_text(old_revision)
            recovered_db = tmp_path / "restored-v08.sqlite"
            shutil.copy2(backup / "chance-track.sqlite3", recovered_db)
            storage.save_investment_note(database, None, "新版写入", "# 新版写入", ACTIVATED)
            run_process(
                [sys.executable, "-c", """
import sys
from pathlib import Path
sys.path.insert(0, 'tests')
from fastapi.testclient import TestClient
from test_loop import NOW, fetcher
from chance_track import storage
from chance_track.runner import revision, run
from chance_track.web import create_app

path = Path(sys.argv[1])
assert revision() == sys.argv[2]
assert TestClient(create_app(path)).get('/').status_code == 200
assert storage.investment_note(path)['title'] == '合成备忘'
with storage.connect(path) as db:
    assert db.execute('PRAGMA user_version').fetchone()[0] == 4
for scheduled in (False, True):
    report = run(path, now=NOW, scheduled=scheduled, fetcher=fetcher, send=False)
    assert storage.report(path, report)['kind'] == ('scheduled' if scheduled else 'manual')
""", str(recovered_db), old_revision],
                cwd=old_runtime,
                check=True,
            )
            assert any(note["title"] == "新版写入" for note in storage.investment_notes(database))
    if mode == "adopt":
        with storage.connect(legacy_path) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 4
    if mode != "startup_failure":
        for scheduled in (False, True):
            report = run(database, scheduled=scheduled, now=NOW, fetcher=fetcher, send=False)
            assert storage.report(database, report)["kind"] == ("scheduled" if scheduled else "manual")


def test_upgraded_user_can_merge_and_customize_without_rewriting_history(v08_database):
    import re
    from copy import deepcopy

    from fastapi.testclient import TestClient

    from chance_track.web import create_app

    path = v08_database
    old = storage.report(path, 1)
    notified = storage.notified_event_keys(path)
    storage.initialize(path, upgrade_configuration=True)
    client = TestClient(create_app(path))
    current = load_configuration(path)
    csrf = re.search(r'name="csrf" value="([^"]+)"', client.get("/manage").text)[1]
    assert client.post("/manage", data={
        "csrf": csrf,
        "version": current["version"],
        "payload": json.dumps({**current, "lifecycle": ["macd_sp500", "macd"]}),
    }).status_code == 200
    merged = load_configuration(path)
    assert all(asset["strategies"].count("macd") == 1 for asset in merged["assets"])

    private = deepcopy(next(rule for rule in merged["strategies"] if rule["id"] == "macd"))
    private["params"]["fast"] = 10
    response = client.post("/api/workspace/edit", json={
        "csrf": csrf,
        "version": merged["version"],
        "action": "monitor",
        "asset_id": "sp500",
        "replace_id": "macd",
        "strategy": private,
    })
    assert response.status_code == 200, response.text
    config = response.json()["configuration"]
    assert config["assets"][1:] == merged["assets"][1:]
    assert "macd" not in config["assets"][0]["strategies"]
    assert storage.notified_event_keys(path) == notified
    assert storage.report(path, 1) == old
    assert "macd_sp500" in client.get("/reports/1").text
    replay = run(path, now=NOW, fetcher=fetcher, send=False)
    assert not build_message(storage.report(path, replay), notified)[1]


def test_runtime_cannot_bypass_v08_upgrade_gate(v08_database):
    from chance_track.web import create_app

    path = v08_database
    for start in (create_app, load_configuration, lambda db: run(db, now=NOW, send=False)):
        with pytest.raises(ValueError, match="使用安装器"):
            start(path)
    with storage.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.execute("SELECT count(*) FROM configuration_versions").fetchone()[0] == 2


def test_installer_refuses_an_unrecoverable_old_revision(v08_database, monkeypatch, tmp_path):
    import runpy

    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    installer = runpy.run_path(str(root / "scripts" / "install_launchd.py"))
    prepare = installer["prepare_configuration_upgrade"]
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setitem(prepare.__globals__, "RUNTIME", runtime)
    monkeypatch.setitem(prepare.__globals__, "SUPPORT", tmp_path)
    for revision in ("uncommitted", "0" * 40):
        (runtime / "deployment-revision.txt").write_text(revision)
        with pytest.raises(RuntimeError, match="可恢复的旧程序提交"):
            prepare(v08_database)
    assert not (tmp_path / "backups").exists()
    with storage.connect(v08_database) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
