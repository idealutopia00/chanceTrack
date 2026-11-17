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
