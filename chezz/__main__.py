"""Entry point: `python -m chezz [--browser | --server-only] [--port N]`."""
import argparse
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

import uvicorn

from .config import ROOT, load_config

WEB = ROOT / "web"


def build_frontend_if_stale() -> None:
    """Rebuild web/dist when frontend sources are newer than the last build (needs npm)."""
    index = WEB / "dist" / "index.html"
    sources = [WEB / "index.html", WEB / "package.json", *(WEB / "src").rglob("*")]
    newest = max((p.stat().st_mtime for p in sources if p.is_file()), default=0)
    if index.exists() and index.stat().st_mtime >= newest:
        return
    npm = shutil.which("npm")
    if not npm:
        if not index.exists():
            sys.exit("Frontend is not built and npm was not found. Install Node.js 18+ and run again.")
        print("warning: frontend sources changed but npm is not available; using the existing build")
        return
    print("Building frontend...")
    if not (WEB / "node_modules").exists():
        subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=WEB, check=True)
    subprocess.run([npm, "run", "build"], cwd=WEB, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(prog="chezz")
    ap.add_argument("--browser", action="store_true", help="open in the default browser instead of a native window")
    ap.add_argument("--server-only", action="store_true", help="run the HTTP server without opening a UI")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()

    build_frontend_if_stale()
    from .api import create_app  # after the build so web/dist exists when StaticFiles is mounted

    cfg = load_config()
    port = args.port or cfg.port
    app = create_app(cfg)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    url = f"http://127.0.0.1:{port}/"

    def request_exit() -> None:
        """Exit button: close the native window (if any) and stop the server; its shutdown
        hook stops the analysis worker and both Stockfish processes."""
        try:
            import webview

            for window in list(webview.windows):
                window.destroy()
        except Exception:
            pass
        server.should_exit = True

    app.state.request_exit = request_exit

    if args.server_only:
        print(f"Chezz server at {url}")
        server.run()
        return

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        if not thread.is_alive():
            sys.exit(f"Could not start the server on port {port}. Is Chezz already running?")
        time.sleep(0.05)

    if not args.browser:
        try:
            import webview

            if sys.platform == "win32":  # own taskbar button and icon instead of Python's
                import ctypes

                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Chezz.App")
            icon = ROOT / "assets" / ("icon.ico" if sys.platform == "win32" else "icon.png")
            webview.create_window("Chezz", url, width=1320, height=860, min_size=(960, 640))
            # Not private: keep the theme and play settings (localStorage) between launches.
            webview.start(icon=str(icon), private_mode=False, storage_path=str(cfg.data_dir / "webview"))
            server.should_exit = True
            thread.join(timeout=15)  # let a running analysis step finish and the engines quit
            return
        except Exception as exc:  # no GUI backend (e.g. missing GTK/Qt on Linux)
            print(f"Native window unavailable ({exc}); opening the browser instead.")

    webbrowser.open(url)
    print(f"Chezz running at {url}  (Ctrl+C to quit)")
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        server.should_exit = True
        thread.join(timeout=15)


if __name__ == "__main__":
    main()
