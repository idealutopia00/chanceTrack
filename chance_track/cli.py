import argparse
from pathlib import Path

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
    args = parser.parse_args()
    if args.command == "check":
        try:
            report_id = run(args.db, scheduled=args.scheduled, send=not args.no_notify)
        except CheckInProgress as exc:
            print(exc)
            return
        print(f"报告 #{report_id}" if report_id else "当前不在检查时段")
    else:
        import uvicorn

        from .web import create_app

        uvicorn.run(create_app(args.db), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
