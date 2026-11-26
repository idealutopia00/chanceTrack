"""Validate deployable v1.0 source (Python, catalog, lockfile, TypeScript, Vite build)."""

import json
from pathlib import Path
import shutil
import subprocess
import tomllib

from chance_track.market_sessions import PROFILES


def validate_source(root, *, build_frontend=True):
    root = Path(root)
    package = root / "chance_track"
    for path in package.rglob("*.py"):
        compile(path.read_bytes(), str(path), "exec")

    catalog_path = root / "catalog.json"
    if not catalog_path.is_file():
        raise ValueError("缺少统一标的目录 catalog.json")
    assets = json.loads(catalog_path.read_text(encoding="utf-8"))
    ids = [asset["id"] for asset in assets]
    if len(ids) != len(set(ids)):
        raise ValueError("标的目录存在重复 id")
    for asset in assets:
        if asset.get("session_profile") not in PROFILES:
            raise ValueError(f"缺少有效交易时段：{asset['id']}")

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    locked = next(
        item["version"] for item in lock["package"] if item["name"] == project["name"]
    )
    if project["version"] != locked:
        raise ValueError("uv.lock 与项目版本不一致，请先 uv lock")

    frontend_dir = root / "frontend"
    if build_frontend and frontend_dir.is_dir():
        npm = shutil.which("npm")
        if not npm:
            raise ValueError("缺少 npm，无法校验和构建 v1.0 前端")
        if not (frontend_dir / "node_modules").is_dir():
            subprocess.run([npm, "--prefix", str(frontend_dir), "ci"], check=True)
        subprocess.run([npm, "--prefix", str(frontend_dir), "run", "typecheck"], check=True)
        subprocess.run([npm, "--prefix", str(frontend_dir), "run", "build"], check=True)
        dist_index = package / "static" / "dist" / "index.html"
        if not dist_index.is_file() or not dist_index.read_text(encoding="utf-8").strip():
            raise ValueError("前端构建产物缺失：chance_track/static/dist/index.html")


if __name__ == "__main__":
    validate_source(Path(__file__).resolve().parent.parent)
    print("项目检查通过：Python、catalog.json、依赖锁、TypeScript 与前端构建产物")
