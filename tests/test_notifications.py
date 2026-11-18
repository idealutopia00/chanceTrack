from datetime import timedelta

import pytest
from test_loop import ASSET, NOW, sample

from chance_track import storage
from chance_track.runner import run

SOX = {**ASSET, "id": "sox", "label": "费城半导体", "code": "^SOX"}


def death_cross(asset, day):
    snapshot = sample()
    for row in snapshot["rows"]:
        row.update(
            open=3 - row["open"],
            close=3 - row["close"],
            high=3 - row["low"],
            low=3 - row["high"],
        )
    return snapshot


def test_silent_manual_detection_does_not_consume_first_notification(tmp_path):
    path = tmp_path / "signals.sqlite3"
    first = run(
        path, [SOX], send=False, now=NOW - timedelta(minutes=28), fetcher=death_cross
    )
    assert storage.report(path, first)["items"][0]["result"]["new_event"]
    calls = []
    second = run(
        path,
        [SOX],
        scheduled=True,
        send=True,
        now=NOW,
        fetcher=death_cross,
        sender=lambda *args: calls.append(args),
    )
    assert not storage.report(path, second)["items"][0]["result"]["new_event"]
    message = calls[0][1]
    assert "费城半导体" in message and "死叉" in message, message
    assert "首次通知1" in message, message


def test_notifications_count_current_events_and_dedupe_delivery_separately(tmp_path):
    from chance_track.runner import deliver

    path = tmp_path / "repeat.sqlite3"
    calls = []
    first = run(
        path,
        [SOX],
        send=True,
        now=NOW,
        fetcher=death_cross,
        sender=lambda *args: calls.append(args),
    )
    second = run(
        path,
        [SOX],
        send=True,
        now=NOW + timedelta(minutes=1),
        fetcher=death_cross,
        sender=lambda *args: calls.append(args),
    )
    assert "当前收盘交叉1（首次通知1）" in calls[0][1]
    assert "当前收盘交叉1（首次通知0）" in calls[1][1]
    assert "费城半导体｜信号日 2026-11-16｜死叉｜收盘已确认" in calls[1][1]
    deliver(
        path,
        second,
        lambda *_: (_ for _ in ()).throw(
            AssertionError("same report was already submitted")
        ),
    )
    with storage.connect(path) as db:
        assert db.execute("SELECT count(*) FROM notification_events").fetchone()[0] == 1
    assert storage.notification(path, first)["message"] == calls[0][1]
    assert storage.notification(path, second)["message"] == calls[1][1]
    from fastapi.testclient import TestClient

    from chance_track.web import create_app

    page = TestClient(create_app(path)).get(f"/reports/{second}")
    assert "最近已确认策略事件 1 项（本次新记录 0）" in page.text
    assert "交叉日期 2026-11-16" in page.text
    assert "通知已提交系统" in page.text


def test_failed_delivery_does_not_consume_signal_and_retry_preserves_message(tmp_path):
    import pytest

    from chance_track.runner import deliver

    path = tmp_path / "retry.sqlite3"
    rid = run(path, [SOX], send=False, now=NOW, fetcher=death_cross)

    def broken(*args):
        raise RuntimeError("osascript denied")

    with pytest.raises(RuntimeError, match="denied"):
        deliver(path, rid, broken)
    assert storage.notified_event_keys(path) == set()
    failed = storage.notification(path, rid)
    assert not failed["sent_at"] and failed["error"] == "osascript denied"
    assert "首次通知1" in failed["message"]
    calls = []
    deliver(path, rid, lambda *args: calls.append(args))
    assert "首次通知1" in calls[0][1]
    assert len(storage.notified_event_keys(path)) == 1
    assert storage.notification(path, rid)["sent_at"]


@pytest.mark.parametrize("entry", ["web", "cli"])
def test_legacy_migration_only_backfills_signals_actually_counted_by_old_popup(
    tmp_path, monkeypatch, entry
):
    import json
    import sqlite3

    from chance_track.analysis import RULE
    from chance_track.cli import main
    from chance_track.web import create_app

    def start(path):
        if entry == "web":
            create_app(path)
        else:
            monkeypatch.setattr(
                "sys.argv",
                ["chance-track", "--db", str(path), "check", "--scheduled", "--no-notify"],
            )
            monkeypatch.setattr("chance_track.runner.due", lambda now: False)
            main()

    fresh = tmp_path / "new" / "fresh.sqlite3"
    start(fresh)
    assert storage.reports(fresh) == []

    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""CREATE TABLE reports(id INTEGER PRIMARY KEY,created_at TEXT,day TEXT,kind TEXT,payload TEXT);
        CREATE TABLE notifications(report_id INTEGER PRIMARY KEY,sent_at TEXT,error TEXT);""")
        for rid, (name, is_new, delivered) in enumerate(
            [
                ("notified", True, True),
                ("zero_popup", False, True),
                ("silent", True, None),
                ("failed", True, False),
            ],
            1,
        ):
            payload = {
                "rule": RULE,
                "items": [
                    {
                        "asset": {**SOX, "id": name},
                        "result": {
                            "event": "死叉 · 卖出观察",
                            "event_date": "2026-11-16",
                            "new_event": is_new,
                        },
                    }
                ],
            }
            db.execute(
                "INSERT INTO reports VALUES(?,?,?,?,?)",
                (rid, NOW.isoformat(), "2026-11-17", "manual", json.dumps(payload)),
            )
            if delivered is not None:
                db.execute(
                    "INSERT INTO notifications VALUES(?,?,?)",
                    (
                        rid,
                        NOW.isoformat() if delivered else None,
                        None if delivered else "failed",
                    ),
                )
    start(path)
    assert storage.report(path, 1)["items"][0]["asset"]["id"] == "notified"
    keys = storage.notified_event_keys(path)
    assert len(keys) == 1 and next(iter(keys)).startswith("notified:")
    assert storage.notification(path, 2)["sent_at"] == NOW.isoformat()
    assert storage.notification(path, 2)["message"] is None
    with storage.connect(path) as db:
        db.execute("DELETE FROM notification_events")
    start(path)
    assert storage.notified_event_keys(path) == set()


def test_preview_is_named_but_never_counted_as_a_confirmed_notification():
    from chance_track.analysis import RULE
    from chance_track.notifications import build_message

    report = {
        "rule": RULE,
        "items": [
            {
                "asset": SOX,
                "status": "ok",
                "result": {
                    "event": None,
                    "bar_date": "2026-11-17",
                    "is_provisional": True,
                    "preview_event": "盘中死叉 · 待收盘确认",
                },
            }
        ],
    }
    message, keys = build_message(report, set())
    assert "当前收盘交叉0（首次通知0），盘中待确认1" in message
    assert "费城半导体｜信号日 2026-11-17｜死叉｜未收盘待确认" in message
    assert not keys


def test_web_dialog_replays_exact_saved_body_and_preview_is_read_only(tmp_path):
    import html
    import re

    from fastapi.testclient import TestClient

    from chance_track.notifications import NOTIFICATION_TITLE, build_message
    from chance_track.web import create_app

    path = tmp_path / "dialog.sqlite3"
    calls = []
    sent = run(
        path,
        [SOX],
        send=True,
        now=NOW,
        fetcher=death_cross,
        sender=lambda *args: calls.append(args),
    )
    client = TestClient(create_app(path))
    response = client.get(f"/reports/{sent}")
    assert response.status_code == 200
    assert (
        'id="show-notification"' in response.text
        and '<dialog id="notification-dialog"' in response.text
    )
    body = html.unescape(
        re.search(
            r'<pre id="notification-message">(.*?)</pre>', response.text, re.S
        ).group(1)
    )
    assert body == calls[0][1]
    assert NOTIFICATION_TITLE == calls[0][0] and NOTIFICATION_TITLE in response.text
    assert "系统通知原文" in response.text
    # Later delivery history must not recalculate a historical notification's first-notice count.
    assert "首次通知1" in body
    silent = run(
        path, [SOX], send=False, now=NOW + timedelta(minutes=1), fetcher=death_cross
    )
    keys_before = storage.notified_event_keys(path)
    preview = client.get(f"/reports/{silent}")
    body = html.unescape(
        re.search(
            r'<pre id="notification-message">(.*?)</pre>', preview.text, re.S
        ).group(1)
    )
    expected, _ = build_message(storage.report(path, silent), keys_before)
    assert body == expected and "通知预览" in preview.text
    assert storage.notification(path, silent) is None
    assert storage.notified_event_keys(path) == keys_before


def test_structured_rows_separate_yesterday_confirmation_from_today_preview():
    from chance_track.presentation import signal_rows

    report = {
        "items": [
            {
                "asset": SOX,
                "status": "ok",
                "result": {
                    "event": "死叉 · 卖出观察",
                    "event_date": "2026-11-16",
                    "bar_date": "2026-11-17",
                    "is_provisional": True,
                    "preview_event": "盘中金叉 · 待收盘确认",
                },
            }
        ]
    }
    rows = signal_rows(report)
    assert [
        (row["asset"], row["date"], row["signal"], row["status"]) for row in rows
    ] == [
        ("费城半导体", "2026-11-16", "死叉", "上一收盘日 · 已确认"),
        ("费城半导体", "2026-11-17", "金叉", "未收盘 · 待确认"),
    ]
    # No-cross, disabled and failure have explicit statuses, never invented signal dates.
    extra = signal_rows(
        {
            "items": [
                {
                    "asset": SOX,
                    "status": "ok",
                    "result": {"bar_date": "2026-11-17", "event": None},
                },
                {"asset": SOX, "status": "error", "error": "offline"},
                {"asset": SOX, "status": "disabled", "error": "not configured"},
            ]
        }
    )
    assert [row["status"] for row in extra] == ["已收盘 · 无交叉", "采集失败", "未启用"]
    assert extra[1]["date"] == "—"


def test_modal_current_observation_does_not_mix_prior_day_signal(tmp_path):
    import re

    from fastapi.testclient import TestClient

    from chance_track.web import create_app

    path = tmp_path / "sections.sqlite3"
    snapshot = death_cross(SOX, "2026-11-17")
    snapshot["rows"].append(
        {
            "date": "2026-11-17",
            "open": 1.9,
            "high": 2.0,
            "low": 1.8,
            "close": 1.9,
            "volume": 100,
        }
    )
    snapshot["provisional_date"] = "2026-11-17"
    rid = run(path, [SOX], send=False, now=NOW, fetcher=lambda *_: snapshot)
    page = TestClient(create_app(path)).get(f"/reports/{rid}")
    assert page.status_code == 200
    current = re.search(
        r'data-signal-section="current"[^>]*>(.*?)</section>', page.text, re.S
    ).group(1)
    reference = re.search(
        r'data-signal-section="reference"[^>]*>(.*?)</section>', page.text, re.S
    ).group(1)
    assert "2026-11-17" in current and "2026-11-16" not in current
    assert "2026-11-16" in reference and "上一收盘日" in reference
    assert current.count("费城半导体") == 1 and reference.count("费城半导体") == 1
