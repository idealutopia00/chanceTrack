"""Deployment validation checks for v1.0 source, catalog.json, and runtime health."""

from io import BytesIO
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_bad_catalog_is_rejected_before_stopping_running_app(tmp_path, monkeypatch):
    (tmp_path / "chance_track").mkdir(parents=True)
    (tmp_path / "catalog.json").write_text(
        '[{"id":"dup","session_profile":"us_equity"},{"id":"dup","session_profile":"us_equity"}]',
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    installer = runpy.run_path(str(ROOT / "scripts" / "install_launchd.py"))
    main = installer["main"]
    monkeypatch.setitem(main.__globals__, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["install_launchd.py"])
    calls = []
    monkeypatch.setattr(
        main.__globals__["subprocess"],
        "run",
        lambda *args, **kwargs: calls.append(args),
    )
    with pytest.raises(ValueError, match="重复 id"):
        main()
    assert calls == []


def test_other_http_listener_cannot_satisfy_candidate_readiness(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    installer = runpy.run_path(str(ROOT / "scripts" / "install_launchd.py"))
    wait_for_web = installer["wait_for_web"]

    def response(url, timeout):
        assert url.endswith("/api/runtime-health")
        body = BytesIO(b'{"revision":"old-or-unrelated"}')
        body.status = 200
        return body

    monkeypatch.setitem(wait_for_web.__globals__, "urlopen", response)
    monkeypatch.setitem(
        wait_for_web.__globals__, "time", SimpleNamespace(sleep=lambda _: None)
    )
    with pytest.raises(RuntimeError, match="未通过 HTTP 检查"):
        wait_for_web("new-revision")
