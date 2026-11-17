"""Deploy an explicit runtime snapshot to Application Support and install user agents."""

import argparse
import os
import plistlib
import shutil
import sqlite3
import subprocess
from pathlib import Path

from check_project import validate_source

ROOT = Path(__file__).resolve().parent.parent
LABEL = "local.chancetrack"
SUPPORT = Path.home() / "Library" / "Application Support" / "ChanceTrack"
RUNTIME = SUPPORT / "runtime"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    if not args.remove:
        validate_source(ROOT)  # Fail before stopping the healthy running version.
    folder = Path.home() / "Library" / "LaunchAgents"
    folder.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    # Stop before deployment, retaining all personal data.
    for suffix in ("check", "web"):
        target = folder / f"{LABEL}.{suffix}.plist"
        subprocess.run(
            ["launchctl", "bootout", domain, str(target)], capture_output=True
        )
        if args.remove:
            target.unlink(missing_ok=True)
    if args.remove:
        print("已移除 Chance Track 自启动任务；历史数据和运行副本未删除")
        return
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
    data = SUPPORT / "data"
    data.mkdir(parents=True, exist_ok=True)
    # Migrate the first development reports once; never overwrite an existing journal.
    old = ROOT / "data" / "chance-track.sqlite3"
    target_db = data / "chance-track.sqlite3"
    if old.exists() and not target_db.exists():
        with sqlite3.connect(old) as source, sqlite3.connect(target_db) as target:
            source.backup(target)
    logs = Path.home() / "Library" / "Logs" / "ChanceTrack"
    logs.mkdir(parents=True, exist_ok=True)
    for suffix, command, extra in [
        (
            "check",
            ["check", "--scheduled"],
            {
                "StartCalendarInterval": {"Hour": 14, "Minute": 35},
                "StartInterval": 300,
                "RunAtLoad": True,
            },
        ),
        ("web", ["serve"], {"RunAtLoad": True, "KeepAlive": True}),
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
        print(f"已安装 {target}")
    print(f"运行副本：{RUNTIME}\n历史数据：{target_db}\n日志：{logs}")


if __name__ == "__main__":
    main()
