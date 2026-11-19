import re

from fastapi.testclient import TestClient

from chance_track import storage
from chance_track.web import create_app


def test_independent_markdown_notes_roundtrip_and_replace_pin(tmp_path):
    path = tmp_path / "notes.sqlite"
    client = TestClient(create_app(path))
    assert "投资备忘录" in client.get("/").text
    editor = client.get("/investment-notes")
    csrf = re.search(r'name="csrf" value="([^"]+)"', editor.text)[1]
    raw = "\n# 本周提醒\n\n- **写理由**\n\n> 复盘\n\n<script>void 0</script>"
    assert (
        client.post("/investment-notes", data={"raw": raw, "csrf": "bad"}).status_code
        == 403
    )
    created = client.post("/investment-notes", data={"raw": raw, "csrf": csrf})
    assert created.url.path == "/investment-notes/1"
    assert "<h1>本周提醒</h1>" in created.text
    assert "<strong>写理由</strong>" in created.text and "<blockquote>" in created.text
    assert (
        "<script>void 0</script>" not in created.text
        and "&lt;script&gt;" in created.text
    )
    assert storage.investment_note(path, 1)["markdown"] == raw
    assert "编辑备忘" in client.get("/investment-notes/1?edit=true").text
    edited = client.post(
        "/investment-notes", data={"note_id": 1, "raw": "# 修改提醒", "csrf": csrf}
    )
    assert edited.url == created.url and "<h1>修改提醒</h1>" in edited.text
    assert (
        client.post("/investment-notes/1/pin", data={"csrf": csrf}).status_code == 200
    )
    assert "<h1>修改提醒</h1>" in client.get("/").text
    second = client.post("/investment-notes", data={"raw": "普通提醒", "csrf": csrf})
    assert second.url.path == "/investment-notes/2"
    assert (
        client.post("/investment-notes/2/pin", data={"csrf": csrf}).status_code == 200
    )
    dashboard = TestClient(create_app(path)).get("/").text
    assert "<p>普通提醒</p>" in dashboard and "修改提醒" not in dashboard
    assert [item["id"] for item in storage.investment_notes(path)] == [2, 1]
    assert "修改提醒" in client.get("/investment-notes/1").text
    assert storage.report(path) is None
