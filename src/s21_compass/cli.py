"""Entrypoints for the terminal and loopback web interfaces."""

import argparse
import ipaddress

from s21_compass import __version__


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="s21-compass", description="School21: терминал и локальный веб-кабинет"
    )
    parser.add_argument("--version", action="version", version=__version__)
    modes = parser.add_subparsers(dest="mode", required=True)
    tui = modes.add_parser("tui", help="Интерактивный терминальный интерфейс")
    tui.add_argument("--demo", action="store_true", help="Вымышленные данные без аккаунта")
    web = modes.add_parser("web", help="Локальный веб-кабинет")
    web.add_argument("--host", default="127.0.0.1", help="Локальный адрес: 127.0.0.1 или ::1")
    web.add_argument("--port", type=int, default=8210, help="Порт (8210)")
    args = parser.parse_args()
    if args.mode == "tui":
        from s21_compass.tui import CompassApp

        CompassApp(demo=args.demo).run()
        return
    try:
        address = ipaddress.ip_address(args.host)
    except ValueError:
        parser.error("Укажите числовой loopback адрес, например 127.0.0.1.")
    if str(address) not in {"127.0.0.1", "::1"}:
        parser.error("Веб-кабинет поддерживает только 127.0.0.1 или ::1.")
    args.host = str(address)
    if not 1 <= args.port <= 65535:
        parser.error("Порт должен быть от 1 до 65535.")
    import uvicorn

    from s21_compass.web import create_app

    host = f"[{args.host}]" if ":" in args.host else args.host
    print(f"S21 Compass: http://{host}:{args.port}  (Ctrl+C — остановить)")
    uvicorn.run(create_app(), host=args.host, port=args.port, access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
