"""Single source of truth for verified preset instruments."""

from copy import deepcopy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "catalog.json"


def load_catalog():
    entries = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    result = []
    for entry in entries:
        item = deepcopy(entry)
        item.setdefault("group", item.get("group_name") or "未分组")
        item.setdefault("group_name", item["group"])
        item.setdefault("enabled", True)
        item.setdefault("archived", False)
        item.setdefault("watchlisted", True)
        item.setdefault("priority", False)
        item.setdefault("aliases", [])
        item.setdefault("fallbacks", [])
        result.append(item)
    return result


def default_watchlist():
    return [item for item in load_catalog() if item.get("default_in_watchlist")]


def catalog_by_id():
    return {item["id"]: item for item in load_catalog()}
