"""One-click owner dashboard.

Binds ``127.0.0.1`` only. On Windows, copies user-environment values whose
names match ``WALLET_MCP_*_ALLOW_MAINNET`` out of ``HKCU\\Environment`` and
leaves every other variable untouched. Values are not expanded, printed, or
written to disk. The approval secret is whatever the process already
inherited; this module does not read it.

Does not create a seed and does not run init or backup. Those stay on a TTY.
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import sys
import threading
import time
from http.client import HTTPConnection, HTTPException

HOST = "127.0.0.1"
DEFAULT_PORT = 8770
CONSOLE_TITLE = "Wallet-beheer"
STOP_HINT = "Sluit dit venster om te stoppen"
AGENT_REFUSAL = "refusing: the owner dashboard does not start when WALLET_MCP_MODE=agent"
DASHBOARD_MARKER = b"<title>Wallet-beheer</title>"
_PORT_ATTEMPTS = 50

# Explicit pattern: WALLET_MCP_<COIN>_ALLOW_MAINNET.
# <COIN> is one or more uppercase segments (BTC, USDC_ETH, USDT_AVAX).
# WALLET_MCP_APPROVAL_SECRET, WALLET_MCP_MODE, and WALLET_MCP_USDT_AVAX_TOKEN
# do not match. Matching is case-sensitive and covers the whole name.
ALLOW_MAINNET_NAME = re.compile(r"WALLET_MCP_[A-Z0-9]+(?:_[A-Z0-9]+)*_ALLOW_MAINNET")


def is_allow_mainnet_name(name: object) -> bool:
    return isinstance(name, str) and ALLOW_MAINNET_NAME.fullmatch(name) is not None


def allow_mainnet_from_items(items) -> dict[str, str]:
    """Keep string values whose names match the allow-mainnet pattern.

    Other names are discarded here. They are not copied into the result.
    """
    selected: dict[str, str] = {}
    for item in items:
        if not item:
            continue
        name = item[0]
        value = item[1] if len(item) > 1 else None
        if is_allow_mainnet_name(name) and isinstance(value, str):
            selected[name] = value
    return selected


def apply_allow_mainnet(env: dict, registry) -> None:
    """Overwrite matching names in ``env``. Do not add or change any other key.

    Registry strings are stored as-is. Percent signs are not expanded, so a
    value cannot pull another variable into the environment.
    """
    pairs = registry.items() if isinstance(registry, dict) else registry
    for name, value in allow_mainnet_from_items(pairs).items():
        env[name] = value


def _enum_key(key):
    index = 0
    while True:
        try:
            yield key.EnumValue(index)
        except OSError:
            return
        index += 1


def _hkcu_environment_values(open_key=None):
    """Yield ``HKCU\\Environment`` values, retaining only allow-mainnet names.

    ``open_key`` is injectable so tests never touch the real registry.
    Importing ``winreg`` happens only when ``open_key`` is omitted, which the
    public reader does only on Windows.
    """
    if open_key is None:
        import winreg

        def open_key():
            return winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment")

    try:
        key = open_key()
    except OSError:
        return ()
    try:
        return [
            (item[0], item[1])
            for item in _enum_key(key)
            if len(item) >= 2 and is_allow_mainnet_name(item[0]) and isinstance(item[1], str)
        ]
    finally:
        close = getattr(key, "Close", None) or getattr(key, "close", None)
        if close is not None:
            close()


def read_allow_mainnet_flags(source=None) -> dict[str, str]:
    """Matching user-env vars. Empty on non-Windows unless ``source`` is passed."""
    if source is None:
        if sys.platform != "win32":
            return {}
        source = _hkcu_environment_values
    return allow_mainnet_from_items(source())


def dashboard_url(port: int) -> str:
    return f"http://{HOST}:{port}/"


def _check_port(port: int) -> int:
    if isinstance(port, bool) or not isinstance(port, int) or port < 1 or port > 65535:
        raise SystemExit("refusing: port must be between 1 and 65535")
    return port


def port_is_free(host: str, port: int) -> bool:
    """True when this process can bind ``host:port``. There is no host override."""
    if host != HOST:
        return False
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((HOST, port))
    except OSError:
        return False
    else:
        return True
    finally:
        sock.close()


def choose_port(start: int, *, is_free=port_is_free, attempts: int = _PORT_ATTEMPTS) -> int:
    """First free port at or after ``start``. Never binds a non-loopback address."""
    start = _check_port(start)
    if attempts < 1:
        raise SystemExit("refusing: no free port")
    last = min(65535, start + attempts - 1)
    for port in range(start, last + 1):
        if is_free(HOST, port):
            return port
    raise SystemExit(f"refusing: no free port from {start} through {last}")


def fetch_root(port: int, timeout: float = 3.0) -> tuple[int, bytes] | None:
    """GET ``/`` on the loopback listener. The body is not logged.

    The full response is consumed so the server can finish the write. Only
    the first 8 KiB is returned; that is enough to recognize the page title.
    """
    conn = HTTPConnection(HOST, port, timeout=timeout)
    try:
        conn.request(
            "GET",
            "/",
            headers={"Host": f"{HOST}:{port}", "Connection": "close"},
        )
        response = conn.getresponse()
        # Read the declared body. A short read closes early and the dashboard
        # then hits a broken pipe while it is still writing the page.
        declared = response.getheader("Content-Length")
        if declared and declared.isdigit():
            body = response.read(min(int(declared), 1_000_000))
        else:
            body = response.read(65_536)
        return response.status, body[:8192]
    except (OSError, HTTPException, TimeoutError):
        return None
    finally:
        conn.close()


def is_owner_dashboard(port: int, timeout: float = 0.4, fetch=fetch_root) -> bool:
    fetched = fetch(port, timeout)
    if fetched is None:
        return False
    status, body = fetched
    return status == 200 and DASHBOARD_MARKER in body


def wait_until_answers(port: int, timeout: float = 15.0, fetch=fetch_root, pause=time.sleep) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if fetch(port, 3.0) is not None:
            return
        if time.monotonic() >= deadline:
            raise SystemExit(f"refusing: the dashboard did not answer on {HOST}:{port}")
        pause(0.05)


def _mode_of(env) -> str:
    return (env.get("WALLET_MCP_MODE") or "owner").strip().lower()


def _open_browser(url: str, opener) -> None:
    if opener is not None:
        opener(url)
        return
    import webbrowser

    try:
        webbrowser.open(url)
    except (OSError, webbrowser.Error):
        print("Browser openen is mislukt. Open het adres hierboven zelf.", flush=True)


def _fit_windows_console(title: str, columns: int = 72, rows: int = 12) -> None:
    """Best-effort small visible console. Failure leaves the default console."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetConsoleTitleW.argtypes = [wintypes.LPCWSTR]
    kernel.SetConsoleTitleW.restype = wintypes.BOOL
    kernel.SetConsoleTitleW(title)

    class SMALL_RECT(ctypes.Structure):
        _fields_ = [
            ("Left", ctypes.c_short),
            ("Top", ctypes.c_short),
            ("Right", ctypes.c_short),
            ("Bottom", ctypes.c_short),
        ]

    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.SetConsoleWindowInfo.argtypes = [
        wintypes.HANDLE,
        wintypes.BOOL,
        ctypes.POINTER(SMALL_RECT),
    ]
    kernel.SetConsoleWindowInfo.restype = wintypes.BOOL
    # STD_OUTPUT_HANDLE is -11.
    handle = kernel.GetStdHandle(0xFFFFFFF5)
    rect = SMALL_RECT(0, 0, columns - 1, rows - 1)
    kernel.SetConsoleWindowInfo(handle, True, ctypes.byref(rect))


def show_running_window(url: str) -> None:
    """Title the console and print the Dutch close hint."""
    if sys.platform == "win32":
        _try_fit_console()
    print(CONSOLE_TITLE, flush=True)
    print(url, flush=True)
    print(STOP_HINT, flush=True)


def _try_fit_console() -> None:
    try:
        _fit_windows_console(CONSOLE_TITLE)
    except (OSError, AttributeError, TypeError, ValueError):
        return


def _arm_close_handler(httpd) -> None:
    """Stop the listener when the console window is closed. Ctrl+C stays with Python."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    routine_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    def _on_ctrl(event: int) -> bool:
        # 0 is Ctrl+C (KeyboardInterrupt). 1 break, 2 close, 5 logoff, 6 shutdown.
        if event in (1, 2, 5, 6):
            _stop_server(httpd)
            return True
        return False

    routine = routine_type(_on_ctrl)
    kernel.SetConsoleCtrlHandler.argtypes = [routine_type, wintypes.BOOL]
    kernel.SetConsoleCtrlHandler.restype = wintypes.BOOL
    if kernel.SetConsoleCtrlHandler(routine, True):
        httpd._wallet_ctrl_handler = routine


def _stop_server(httpd) -> None:
    lock = getattr(httpd, "_wallet_stop_lock", None)
    if lock is None:
        httpd.server_close()
        return
    with lock:
        if getattr(httpd, "_wallet_stopped", False):
            return
        httpd._wallet_stopped = True
    thread = getattr(httpd, "_wallet_thread", None)
    if thread is not None and thread.is_alive():
        httpd.shutdown()
        thread.join(timeout=3)
    try:
        httpd.server_close()
    except OSError:
        return


def _start_server(env, start: int, factory, is_free):
    port = start
    last_error: OSError | None = None
    for _ in range(_PORT_ATTEMPTS):
        port = choose_port(port, is_free=is_free)
        try:
            return factory(env, port)
        except OSError as exc:
            last_error = exc
            port += 1
            if port > 65535:
                break
    detail = f": {last_error}" if last_error else ""
    raise SystemExit("refusing: no free port for the dashboard" + detail)


def _serve_in_background(httpd):
    httpd._wallet_stop_lock = threading.Lock()
    httpd._wallet_stopped = False
    thread = threading.Thread(target=httpd.serve_forever, name="wallet-beheer", daemon=True)
    httpd._wallet_thread = thread
    thread.start()
    return thread


def _block_until_interrupt(httpd) -> None:
    thread = httpd._wallet_thread
    try:
        while thread.is_alive():
            thread.join(0.3)
    except KeyboardInterrupt:
        return


def run(
    port: int = DEFAULT_PORT,
    *,
    env: dict | None = None,
    registry=None,
    opener=None,
    probe=None,
    server_factory=None,
    is_free=None,
    hold=None,
    wait=None,
) -> str:
    """Start the dashboard, or open the browser when it is already listening.

    ``hold`` replaces the blocking wait. The console script omits it and runs
    until Ctrl+C or the window is closed. Returns the URL opened.
    """
    target = os.environ if env is None else env
    if registry is None:
        registry = read_allow_mainnet_flags()
    apply_allow_mainnet(target, registry)
    if _mode_of(target) == "agent":
        raise SystemExit(AGENT_REFUSAL)

    port = _check_port(port)
    already = is_owner_dashboard if probe is None else probe
    if already(port):
        url = dashboard_url(port)
        print(f"Wallet-beheer draait al: {url}", flush=True)
        _open_browser(url, opener)
        return url

    if server_factory is None:
        from .dashboard import make_dashboard

        def server_factory(env_snapshot, chosen):
            return make_dashboard(env_snapshot, chosen)

    if is_free is None:
        is_free = port_is_free
    httpd = _start_server(target, port, server_factory, is_free)
    try:
        _serve_in_background(httpd)
        if wait is None:
            wait_until_answers(httpd.server_address[1])
        else:
            wait(httpd.server_address[1])
        url = dashboard_url(httpd.server_address[1])
        show_running_window(url)
        _open_browser(url, opener)
        _arm_close_handler(httpd)
        if hold is not None:
            hold(httpd)
        else:
            _block_until_interrupt(httpd)
        return url
    except KeyboardInterrupt:
        return dashboard_url(httpd.server_address[1])
    finally:
        _stop_server(httpd)


def main(argv: list[str] | None = None) -> None:
    """Console-script entry for ``wallet-beheer``."""
    parser = argparse.ArgumentParser(
        prog="wallet-beheer",
        description="Start the owner dashboard on 127.0.0.1 and open it in the browser.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    run(port=args.port)


if __name__ == "__main__":
    main()
