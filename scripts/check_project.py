"""Validate deployable source before touching running LaunchAgents."""

import json
import shutil
import subprocess
import tomllib
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from chance_track.market_sessions import PROFILES


def validate_source(root):
    root = Path(root)
    package = root / "chance_track"
    for path in package.rglob("*.py"):
        compile(path.read_bytes(), str(path), "exec")
    scripts = ("app.js", "manage.js", "workspace.mjs", "watchlist.mjs")
    for name in ("app.css", *scripts):
        text = (package / "static" / name).read_text()
        if "<!doctype" in text.lower() or "<html" in text.lower():
            raise ValueError(f"拒绝部署：{name} 被误写成 HTML")
    templates = Environment(loader=FileSystemLoader(package / "templates"))
    for name in templates.list_templates():
        templates.get_template(name)
    assets = json.loads((root / "watchlist.json").read_text())
    ids = [asset["id"] for asset in assets]
    if len(ids) != len(set(ids)):
        raise ValueError("观察清单存在重复 id")
    for asset in assets:
        if asset.get("enabled") and asset.get("session_profile") not in PROFILES:
            raise ValueError(f"缺少有效交易时段：{asset['id']}")
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    lock = tomllib.loads((root / "uv.lock").read_text())
    locked = next(
        item["version"] for item in lock["package"] if item["name"] == project["name"]
    )
    if project["version"] != locked:
        raise ValueError("uv.lock 与项目版本不一致，请先 uv sync")
    node = shutil.which("node")
    if node:
        for script in scripts:
            subprocess.run(
                [node, "--check", str(package / "static" / script)], check=True
            )


if __name__ == "__main__":
    validate_source(Path(__file__).resolve().parent.parent)
    print("项目检查通过：Python、模板、静态文件、观察清单、依赖锁")
