"""Open the two permanent interactive Ozon marketplace browsers."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import load_config, resolve_path  # noqa: E402
from ozon_browser_runtime import (  # noqa: E402
    browser_is_eligible,
    configure_marketplace_profiles,
    managed_ozon_session_zero_processes,
    resolve_ozon_runtime,
    running_chrome_processes,
)
from saas_service import SaaSService  # noqa: E402


def is_interactive_session() -> bool:
    if os.name != "nt":
        return True
    session_id = ctypes.c_uint32()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):  # type: ignore[attr-defined]
        return False
    return int(session_id.value) != 0


def chrome_executable() -> str:
    candidates = [
        os.environ.get("OZON_CHROME_PATH", ""), os.environ.get("CHROME_PATH", ""),
        shutil.which("chrome.exe") or "",
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("Google Chrome was not found. Set OZON_CHROME_PATH.")


def debugger_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/json/version", timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def start_browser(runtime: Any, *, dry_run: bool = False) -> dict[str, Any]:
    processes = running_chrome_processes()
    exact = next((item for item in processes if browser_is_eligible(runtime, item, production=True)), None)
    if exact and debugger_ready(runtime.debug_port):
        return {"status": "READY", "marketplace": runtime.marketplace_code, "profile": str(runtime.profile_dir), "port": runtime.debug_port, "session_id": int(exact["session_id"])}
    occupied = [item for item in processes if str(item.get("profile_dir") or "").casefold() == str(runtime.profile_dir).casefold()]
    if any(int(item.get("session_id") or 0) == 0 for item in occupied):
        raise RuntimeError("Managed marketplace profile is occupied by Windows Session 0; run bootstrap.")
    if occupied:
        raise RuntimeError("Managed marketplace profile is occupied without an eligible interactive DevTools browser.")
    if any(int(item.get("debug_port") or 0) == int(runtime.debug_port) for item in processes):
        raise RuntimeError("Canonical Ozon DevTools port is occupied by another Chrome profile.")
    if dry_run:
        return {"status": "PLANNED", "marketplace": runtime.marketplace_code, "profile": str(runtime.profile_dir), "port": runtime.debug_port}
    runtime.profile_dir.mkdir(parents=True, exist_ok=True)
    runtime.profile_dir.joinpath(".spyon_devtools_port").write_text(str(runtime.debug_port), encoding="ascii")
    args = [
        chrome_executable(), f"--remote-debugging-port={runtime.debug_port}", "--remote-allow-origins=*",
        f"--user-data-dir={runtime.profile_dir}", "--profile-directory=Default", "--lang=ru-RU",
        "--start-maximized", "--no-first-run", "--disable-popup-blocking", runtime.start_url,
    ]
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    for _ in range(30):
        time.sleep(0.5)
        exact = next((item for item in running_chrome_processes() if browser_is_eligible(runtime, item, production=True)), None)
        if exact and debugger_ready(runtime.debug_port):
            return {"status": "READY", "marketplace": runtime.marketplace_code, "profile": str(runtime.profile_dir), "port": runtime.debug_port, "session_id": int(exact["session_id"])}
    raise RuntimeError("Interactive Ozon browser did not become ready on its permanent profile and port.")


def active_marketplaces() -> list[str]:
    config = load_config()
    return SaaSService(resolve_path(config, "database")).active_ozon_marketplaces()


def stop_managed_session_zero_browsers() -> list[int]:
    """Close only top-level managed RU/KZ Session-0 Chrome processes."""
    closed: list[int] = []
    for process in managed_ozon_session_zero_processes(ROOT, running_chrome_processes()):
        try:
            pid = int(process.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        subprocess.run(["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force"], check=False, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        closed.append(pid)
    return closed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open permanent interactive Ozon marketplace browsers.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--bootstrap", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not is_interactive_session():
        print("Ozon browser launcher must run in an interactive Windows user session.", file=sys.stderr)
        return 2
    configure_marketplace_profiles(ROOT)
    marketplaces = active_marketplaces()
    if not marketplaces:
        print("No active Ozon marketplace integrations are available for browser launch.", file=sys.stderr)
        return 2
    if args.bootstrap and not args.dry_run:
        closed = stop_managed_session_zero_browsers()
        if closed:
            print(json.dumps({"status": "CLOSED_SESSION0", "pids": closed}, ensure_ascii=False))
    failures = 0
    for marketplace in marketplaces:
        try:
            print(json.dumps(start_browser(resolve_ozon_runtime(ROOT, marketplace), dry_run=args.dry_run), ensure_ascii=False))
        except (OSError, RuntimeError, ValueError) as exc:
            failures += 1
            print(json.dumps({"status": "FAILED", "marketplace": marketplace, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
