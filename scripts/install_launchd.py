"""Deploy an explicit runtime snapshot to Application Support and install user agents."""

import argparse
import json
import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from contextlib import closing
from pathlib import Path
from urllib.request import urlopen

from check_project import validate_source

from chance_track import storage
from chance_track.configuration_store import SCHEMA_VERSION
from chance_track.runner import check_lock

ROOT = Path(__file__).resolve().parent.parent
LABEL = "local.chancetrack"
SUPPORT = Path.home() / "Library" / "Application Support" / "ChanceTrack"
RUNTIME = SUPPORT / "runtime"
V08_RECOVERY_REVISION = "e67e3bca51b4d0b5332673ee773b97ffe36a9c3e"


def prepare_configuration_upgrade(target_db, *, adopting=False):
    if not target_db.is_file():
        return
    with closing(sqlite3.connect(target_db.as_uri() + "?mode=ro", uri=True)) as source:
        version = source.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        if version != 4:
            raise RuntimeError(f"配置升级需要 SQLite 结构版本 4，当前为 {version}")
        old_revision = (
            V08_RECOVERY_REVISION if adopting
            else (RUNTIME / "deployment-revision.txt").read_text().strip()
        )
        if not re.fullmatch(r"[0-9a-f]{40}", old_revision) or subprocess.run(
            ["git", "cat-file", "-e", f"{old_revision}^{{commit}}"],
            cwd=ROOT,
            capture_output=True,
        ).returncode != 0:
            raise RuntimeError("缺少可恢复的旧程序提交，升级已停止")
        backups = SUPPORT / "backups"
        backups.mkdir(mode=0o700, exist_ok=True)
        bundle = Path(tempfile.mkdtemp(dir=backups, prefix="configuration-v4-"))
        (bundle / "runtime-revision.txt").write_text(old_revision + "\n")
        backup = bundle / "chance-track.sqlite3"
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise RuntimeError(f"配置升级备份校验失败：{bundle}")
    with tempfile.TemporaryDirectory() as directory:
        trial = Path(directory) / "configuration-upgrade.sqlite3"
        shutil.copy2(backup, trial)
        storage.initialize(trial, upgrade_configuration=True)
        with storage.connect(trial) as db:
            if (
                db.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                or db.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise RuntimeError(f"配置升级预演校验失败：{bundle}")
    print(f"已验证配置升级和旧版恢复备份：{bundle}")
    return backup


def deploy_runtime():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        ROOT / "chance_track",
        RUNTIME / "chance_track",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("pyproject.toml", "uv.lock", "README.md", "watchlist.json"):
        shutil.copy2(ROOT / name, RUNTIME / name)
    (RUNTIME / "deployment-revision.txt").write_text(
        subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
        ).stdout.strip()
        or "uncommitted"
    )
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("安装需要 uv")
    subprocess.run(
        [uv, "sync", "--frozen", "--no-dev", "--python", "3.12"],
        cwd=RUNTIME,
        check=True,
    )


def wait_for_web(expected_revision):
    for _ in range(10):
        try:
            with urlopen("http://127.0.0.1:8765/api/runtime-health", timeout=1) as response:
                if response.status == 200 and json.load(response).get("revision") == expected_revision:
                    return
        except (OSError, ValueError, AttributeError):
            pass
        time.sleep(1)
    raise RuntimeError("新运行副本未通过 HTTP 检查，定时检查保持停止；请参阅升级恢复说明")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    if not args.remove:
        validate_source(ROOT)  # Fail before stopping the healthy running version.
    folder = Path.home() / "Library" / "LaunchAgents"
    folder.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    jobs = [
        (f"{domain}/{LABEL}.{suffix}", folder / f"{LABEL}.{suffix}.plist")
        for suffix in ("check", "web")
    ]
    active = []
    data = SUPPORT / "data"
    target_db = data / "chance-track.sqlite3"
    # Preserve the old jobs only while the old code and data are still paired.
    recover_old_jobs = True
    try:
        for job, plist in jobs:
            if subprocess.run(["launchctl", "print", job], capture_output=True).returncode == 0:
                subprocess.run(["launchctl", "bootout", job], check=True, capture_output=True)
                active.append(plist)
        if args.remove:
            for _, plist in jobs:
                plist.unlink(missing_ok=True)
            print("已移除 Chance Track 自启动任务；历史数据和运行副本未删除")
            return
        with check_lock(target_db):
            legacy_path = ROOT / "data" / "chance-track.sqlite3"
            adopt = not target_db.exists() and legacy_path.is_file()
            if adopt:
                with check_lock(legacy_path):
                    backup = prepare_configuration_upgrade(legacy_path, adopting=True)
                    source_path = backup or legacy_path
                    with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(target_db)) as target:
                        source.backup(target)
            else:
                prepare_configuration_upgrade(target_db)
            recover_old_jobs = False
            deploy_runtime()
            storage.initialize(target_db, upgrade_configuration=True)
    except Exception:
        if recover_old_jobs:
            for plist in reversed(active):
                subprocess.run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True)
        raise

    logs = Path.home() / "Library" / "Logs" / "ChanceTrack"
    logs.mkdir(parents=True, exist_ok=True)
    for suffix, command, extra in [
        ("web", ["serve"], {"RunAtLoad": True, "KeepAlive": True}),
        (
            "check",
            ["check", "--scheduled"],
            {
                "StartCalendarInterval": {"Hour": 14, "Minute": 35},
                "StartInterval": 300,
                "RunAtLoad": True,
            },
        ),
    ]:
        target = folder / f"{LABEL}.{suffix}.plist"
        config = {
            "Label": f"{LABEL}.{suffix}",
            "ProgramArguments": [
                str(RUNTIME / ".venv" / "bin" / "python"),
                "-m",
                "chance_track.cli",
                *command,
            ],
            "WorkingDirectory": str(RUNTIME),
            "StandardOutPath": str(logs / f"{suffix}.out.log"),
            "StandardErrorPath": str(logs / f"{suffix}.err.log"),
            **extra,
        }
        with target.open("wb") as stream:
            plistlib.dump(config, stream)
        subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
        if suffix == "web":
            expected_revision = (RUNTIME / "deployment-revision.txt").read_text().strip()
            wait_for_web(expected_revision)
        print(f"已安装 {target}")
    print(f"运行副本：{RUNTIME}\n历史数据：{target_db}\n日志：{logs}")


if __name__ == "__main__":
    main()
