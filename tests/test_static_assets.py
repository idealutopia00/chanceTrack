import re

from fastapi.testclient import TestClient

from chance_track.web import create_app


def test_stylesheet_is_css_not_an_html_page(tmp_path):
    client = TestClient(create_app(tmp_path / "assets.sqlite3"))
    response = client.get("/static/app.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert "<!doctype" not in response.text.lower(), (
        "HTML was written into the CSS file"
    )
    assert "<html" not in response.text.lower()
    assert re.search(r":root\s*{", response.text) and re.search(
        r"#chart\s*{", response.text
    )
