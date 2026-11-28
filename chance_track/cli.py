import argparse
from pathlib import Path

from . import storage, workspace
from .configuration import migrate_etf_targets
from .runner import CheckInProgress, run


def main():
    parser = argparse.ArgumentParser(description="Chance Track 本地指数观察")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path.home()
        / "Library"
        / "Application Support"
        / "ChanceTrack"
        / "data"
        / "chance-track.sqlite3",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("--scheduled", action="store_true")
    check.add_argument("--no-notify", action="store_true")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    migrate_etf = commands.add_parser("migrate-etf-targets")
    migrate_etf.add_argument("--validate-live", action="store_true")
    refresh_sum = commands.add_parser("refresh-summaries")
    refresh_sum.add_argument("--asset", action="append", default=[])
    args = parser.parse_args()
    if args.command == "check":
        try:
            report_id = run(args.db, scheduled=args.scheduled, send=not args.no_notify)
        except CheckInProgress as exc:
            print(exc)
            return
        print(f"报告 #{report_id}" if report_id else "当前不在检查时段")
    elif args.command == "refresh-summaries":
        storage.initialize(args.db)
        res = workspace.refresh_signal_summaries(args.db, asset_ids=args.asset or None)
        print(
            f"已重算 {res['refreshed_assets']} 个标的、{res['summary_rows']} 条策略事件摘要（不补发通知）"
        )
    elif args.command == "migrate-etf-targets":
        result = migrate_etf_targets(args.db, validate_live=args.validate_live)
        if not result["changed"]:
            if result.get("skipped"):
                reasons = "；".join(item["reason"] for item in result["skipped"])
                print(f"未切换（版本 {result['version']}）：{reasons}")
            else:
                print(f"无需切换：未发现待替换的旧标的（版本 {result['version']}）")
        else:
            workspace.refresh_signal_summaries(args.db)
            pairs = "，".join(
                f"{item['from_code']}->{item['to_code']}" for item in result["migrated"]
            )
            suffix = (
                "；已跳过：" + "；".join(item["reason"] for item in result["skipped"])
                if result.get("skipped")
                else ""
            )
            print(f"已切换标的：{pairs}（版本 {result['version']}）{suffix}")
    else:
        import uvicorn

        from .web import create_app

        uvicorn.run(create_app(args.db), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
