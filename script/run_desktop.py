from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser
from contextlib import closing
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.runtime_paths import configure_runtime_environment, get_runtime_root, is_frozen
from app.backend.server import build_http_server
from script.run_agent_worker import main as worker_main


configure_runtime_environment()


def _find_open_port(host: str, preferred: int) -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if probe.connect_ex((host, preferred)) != 0:
            return preferred

    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as fallback:
        fallback.bind((host, 0))
        return int(fallback.getsockname()[1])


def _wait_for_server(host: str, port: int, timeout_seconds: float = 10.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"Backend did not start within {timeout_seconds:.0f} seconds.")


def _run_browser_fallback(url: str, reason: str = "") -> None:
    if reason:
        print(f"Desktop window unavailable: {reason}")
    print(f"Opening Crayotter workbench in the system browser: {url}")
    webbrowser.open(url)

    if os.name == "nt":
        import ctypes

        message = (
            "Crayotter 工作台已在默认浏览器中打开。\n\n"
            "保持此窗口运行即可继续使用；完成后点击“确定”关闭后台服务。"
        )
        ctypes.windll.user32.MessageBoxW(
            None,
            message,
            "Crayotter 创作助手",
            0x00000040 | 0x00010000,
        )
        return

    try:
        input("Press Enter to stop the Crayotter backend service...")
    except (EOFError, KeyboardInterrupt):
        pass


def _run_desktop(host: str, port: int) -> int:
    port = _find_open_port(host=host, preferred=port)
    httpd = build_http_server(host=host, port=port)
    server_thread = threading.Thread(
        target=httpd.serve_forever,
        name="crayotter-http-server",
        daemon=True,
    )
    server_thread.start()
    _wait_for_server(host=host, port=port)

    url = f"http://{host}:{port}/ui/"

    try:
        try:
            import webview

            webview.create_window(
                "Crayotter 创作助手",
                url,
                width=1440,
                height=920,
                min_size=(1120, 760),
                confirm_close=True,
            )
            webview.start(
                gui="edgechromium",
                debug=not is_frozen(),
                http_server=False,
            )
        except Exception as exc:
            _run_browser_fallback(url, reason=str(exc))
        return 0
    finally:
        httpd.shutdown()
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    raw_args = list(argv or sys.argv[1:])
    if raw_args and raw_args[0] == "--crayotter-worker":
        return worker_main(raw_args[1:])

    parser = argparse.ArgumentParser(description="Run the Crayotter desktop workbench.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(raw_args)

    runtime_root = get_runtime_root()
    print(f"Crayotter runtime root: {runtime_root}")
    return _run_desktop(host=args.host, port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())
