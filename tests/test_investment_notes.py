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


def test_delete_pinned_note_from_history(tmp_path):
    path = tmp_path / "notes.sqlite"
    client = TestClient(create_app(path))
    editor = client.get("/investment-notes?new=true")
    csrf = re.search(r'name="csrf" value="([^"]+)"', editor.text)[1]
    client.post("/investment-notes", data={"raw": "# 保留备忘", "csrf": csrf})
    client.post("/investment-notes", data={"raw": "# 待删除备忘", "csrf": csrf})
    client.post("/investment-notes/2/pin", data={"csrf": csrf})

    assert "<h1>待删除备忘</h1>" in client.get("/investment-notes").text
    confirmation = client.get("/investment-notes/2/delete")
    assert "确定删除「待删除备忘」？" in confirmation.text
    old_version = storage.investment_note(path, 2)["updated_at"]
    assert client.post(
        "/investment-notes/2/delete", data={"csrf": "bad", "updated_at": old_version}
    ).status_code == 403
    client.post(
        "/investment-notes",
        data={"csrf": csrf, "note_id": 2, "raw": "# 已修改的待删除备忘"},
    )
    form = {"csrf": csrf, "updated_at": old_version}
    assert client.post("/investment-notes/2/delete", data=form).status_code == 409
    form["updated_at"] = storage.investment_note(path, 2)["updated_at"]
    deleted = client.post("/investment-notes/2/delete", data=form)
    assert deleted.url.path == "/investment-notes"
    assert "待删除备忘" not in deleted.text
    assert "待删除备忘" not in client.get("/").text
    assert client.get("/investment-notes/2").status_code == 404
    assert [note["title"] for note in storage.investment_notes(path)] == ["保留备忘"]


def test_migrate_legacy_notes_without_reusing_deleted_ids(tmp_path):
    path = tmp_path / "notes.sqlite"
    storage.initialize(path)
    with storage.connect(path) as db:
        db.executescript("""
            DROP TABLE combination_references;
            DROP TABLE monitoring_bindings;
            DROP TABLE current_assets;
            DROP TABLE current_strategies;
            DROP TABLE current_configuration;
            DROP TABLE investment_notes;
            CREATE TABLE investment_notes (
              id INTEGER PRIMARY KEY, title TEXT NOT NULL, markdown TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              pinned INTEGER NOT NULL DEFAULT 0);
            INSERT INTO investment_notes VALUES (7, '原备忘', '# 原备忘', 'day', 'day', 1);
            PRAGMA user_version=3;
        """)
    storage.initialize(path)
    assert storage.investment_note(path)["markdown"] == "# 原备忘"
    assert storage.delete_investment_note(path, 7, "day")
    assert storage.save_investment_note(path, None, "新备忘", "# 新备忘", "later") == 8
    assert not storage.delete_investment_note(path, 7, "day")
    assert storage.investment_note(path, 8)["title"] == "新备忘"


def test_saved_markdown_tables_render_in_detail_and_pinned_dialog(tmp_path):
    path = tmp_path / "notes.sqlite"
    storage.initialize(path)
    markdown = (
        "| 项目 | 说明 |\n| --- | --- |\n| 示例 | **重点** |\n\n"
        "普通段落\n\n- 列表项目\n\n![图片](/example.png)\n"
    )
    note_id = storage.save_investment_note(path, None, "表格", markdown, "day")
    storage.pin_investment_note(path, note_id)
    client = TestClient(create_app(path))

    for article in (
        client.get(f"/investment-notes/{note_id}").text,
        client.get("/").text.split('id="pinned-note-dialog"', 1)[1].split("</dialog>", 1)[0],
    ):
        assert '<div class="investment-table"' in article
        assert "<table>" in article and "<th>项目</th>" in article
        assert "<td><strong>重点</strong></td>" in article
        assert "<p>普通段落</p>" in article and "<li>列表项目</li>" in article
        assert '<img src="/example.png"' not in article
    assert storage.investment_note(path, note_id)["markdown"] == markdown
