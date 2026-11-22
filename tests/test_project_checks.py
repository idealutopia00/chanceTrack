"""Deployment must reject the original HTML-in-CSS failure before launchctl is touched."""

import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_bad_static_source_is_rejected_before_stopping_running_app(
    tmp_path, monkeypatch
):
    package = tmp_path / "chance_track" / "static"
    package.mkdir(parents=True)
    (package / "app.css").write_text("<!doctype html><html>wrong file</html>")
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
    with pytest.raises(ValueError, match="HTML"):
        main()
    assert calls == []


def test_other_http_listener_cannot_satisfy_candidate_readiness(monkeypatch):
    from io import BytesIO
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    installer = runpy.run_path(str(ROOT / "scripts" / "install_launchd.py"))
    wait_for_web = installer["wait_for_web"]

    def response(url, timeout):
        assert url.endswith("/api/runtime-health")
        body = BytesIO(b'{"revision":"old-or-unrelated"}')
        body.status = 200
        return body

    monkeypatch.setitem(wait_for_web.__globals__, "urlopen", response)
    monkeypatch.setitem(wait_for_web.__globals__, "time", SimpleNamespace(sleep=lambda _: None))
    with pytest.raises(RuntimeError, match="未通过 HTTP 检查"):
        wait_for_web("new-revision")
