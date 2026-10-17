"""Tiny Windows launcher. PyInstaller bundles this file alone.

It does not import wallet_mcp or any crypto library. It finds the project
venv and runs ``python.exe -m wallet_mcp launch``, which keeps the visible
console. The approval secret is inherited by that process and is never read
here.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CONSOLE_TITLE = "Wallet-beheer"
VENV_PYTHON = Path(".venv") / "Scripts" / "python.exe"
DEFAULT_REPO_NAME = "Btc-wallet-MCP"
MAX_PARENTS = 6


def resolve_python(exe: Path, *, home: Path, environ: dict, exists) -> Path:
    """Configured path, then a venv next to the exe, then the default checkout.

    ``WALLET_BEHEER_PYTHON`` is a path. When it is set and missing, detection
    stops instead of silently using another interpreter.
    """
    configured = (environ.get("WALLET_BEHEER_PYTHON") or "").strip().strip('"')
    if configured:
        path = Path(configured)
        if exists(path):
            return path
        raise SystemExit(
            "WALLET_BEHEER_PYTHON wijst niet naar een python.exe:\n" + str(path)
        )
    start = exe if exe.is_dir() else exe.parent
    directories = [start, *list(start.parents)[:MAX_PARENTS]]
    for directory in directories:
        candidate = directory / VENV_PYTHON
        if exists(candidate):
            return candidate
    fallback = home / DEFAULT_REPO_NAME / VENV_PYTHON
    if exists(fallback):
        return fallback
    raise SystemExit(
        "Python-venv niet gevonden.\n"
        "Verwacht: %USERPROFILE%\\Btc-wallet-MCP\\.venv\\Scripts\\python.exe\n"
        "Of stel WALLET_BEHEER_PYTHON in op het pad van python.exe."
    )


def launch_command(python: Path, forwarded: list[str]) -> list[str]:
    return [str(python), "-m", "wallet_mcp", "launch", *forwarded]


def executable_path(argv0: str | None = None) -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    raw = sys.argv[0] if argv0 is None else argv0
    return Path(raw).resolve()


def set_console_title(title: str = CONSOLE_TITLE) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.SetConsoleTitleW.argtypes = [wintypes.LPCWSTR]
        kernel.SetConsoleTitleW.restype = wintypes.BOOL
        kernel.SetConsoleTitleW(title)
    except (OSError, AttributeError, TypeError):
        return


def fit_console(columns: int = 72, rows: int = 12) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        class SMALL_RECT(ctypes.Structure):
            _fields_ = [
                ("Left", ctypes.c_short),
                ("Top", ctypes.c_short),
                ("Right", ctypes.c_short),
                ("Bottom", ctypes.c_short),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.SetConsoleWindowInfo.argtypes = [
            wintypes.HANDLE,
            wintypes.BOOL,
            ctypes.POINTER(SMALL_RECT),
        ]
        kernel.SetConsoleWindowInfo.restype = wintypes.BOOL
        handle = kernel.GetStdHandle(0xFFFFFFF5)
        rect = SMALL_RECT(0, 0, columns - 1, rows - 1)
        kernel.SetConsoleWindowInfo(handle, True, ctypes.byref(rect))
    except (OSError, AttributeError, TypeError, ValueError):
        return


def _pause() -> None:
    if sys.platform != "win32":
        return
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            input("Druk op Enter om te sluiten...")
    except (EOFError, OSError):
        return


def main(
    argv: list[str] | None = None,
    *,
    runner=None,
    environ: dict | None = None,
    home: Path | None = None,
    exe: Path | None = None,
    exists=None,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    set_console_title()
    fit_console()
    env = os.environ if environ is None else environ
    try:
        python = resolve_python(
            executable_path() if exe is None else exe,
            home=Path.home() if home is None else home,
            environ=env,
            exists=Path.exists if exists is None else exists,
        )
    except SystemExit as exc:
        message = exc.code
        if isinstance(message, str) and message:
            print(message, file=sys.stderr)
        _pause()
        if isinstance(message, int):
            return message or 1
        return 1
    command = launch_command(python, args)
    run = subprocess.run if runner is None else runner
    try:
        completed = run(command, check=False)
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print("Starten is mislukt.", file=sys.stderr)
        if exc.strerror:
            print(exc.strerror, file=sys.stderr)
        _pause()
        return 1
    code = int(completed.returncode or 0)
    if code != 0:
        _pause()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
