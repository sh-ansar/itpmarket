"""Open isolated interactive Ozon Chrome sessions for active Spyon sellers."""
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
    configure_legacy_profiles,
    managed_ozon_session_zero_processes,
    resolve_ozon_runtimes,
    running_chrome_processes,
)
from saas_service import SaaSService  # noqa: E402


def is_interactive_session() -> bool:
    """Visible Chrome must run on a user desktop, never Windows Session 0."""
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
    profile_dir, port, start_url = runtime.profile_dir, runtime.debug_port, runtime.source_url
    all_processes = running_chrome_processes()
    profile_processes = [item for item in all_processes if str(item.get("profile_dir") or "").casefold() == str(profile_dir).casefold()]
    interactive = next(
        (item for item in profile_processes if browser_is_eligible(runtime, item, production=True)),
        None,
    )
    if interactive and debugger_ready(port):
        return {"status": "reused", "profile": str(profile_dir), "port": port, "session_id": int(interactive["session_id"]), "url": start_url}
    if any(int(item.get("debug_port") or 0) == int(port) and int(item.get("session_id") or 0) == 0 for item in profile_processes):
        raise RuntimeError("The managed Ozon profile is occupied by Session 0; run the interactive bootstrap.")
    if profile_processes:
        raise RuntimeError("The Ozon profile is occupied but has no eligible interactive browser.")
    if any(int(item.get("debug_port") or 0) == int(port) for item in all_processes):
        raise RuntimeError("DevTools port Ozon already belongs to another Chrome profile.")
    if dry_run:
        return {"status": "planned", "profile": str(profile_dir), "port": port, "url": start_url}
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.joinpath(".spyon_devtools_port").write_text(str(port), encoding="ascii")
    args = [
        chrome_executable(), f"--remote-debugging-port={port}", "--remote-allow-origins=*",
        f"--user-data-dir={profile_dir}", "--profile-directory=Default", "--lang=ru-RU",
        "--start-maximized", "--no-first-run", "--disable-popup-blocking", start_url,
    ]
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    for _ in range(30):
        time.sleep(0.5)
        interactive = next(
            (item for item in running_chrome_processes() if browser_is_eligible(runtime, item, production=True)),
            None,
        )
        if interactive and debugger_ready(port):
            return {"status": "opened", "profile": str(profile_dir), "port": port, "session_id": int(interactive["session_id"]), "url": start_url}
    raise RuntimeError("Interactive Ozon browser did not become available on its assigned profile and port.")


def active_sellers() -> list[dict[str, Any]]:
    config = load_config()
    return SaaSService(resolve_path(config, "database")).ozon_runtime_sellers()


def seller_plan(seller: dict[str, Any], sellers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    values = sellers if sellers is not None else active_sellers()
    key = (int(seller["tenant_id"]), str(seller["marketplace_code"]), int(seller["runtime_seller_id"]))
    runtime = resolve_ozon_runtimes(ROOT, values)[key]
    return {"runtime": runtime, "profile_dir": runtime.profile_dir, "port": runtime.debug_port, "source_url": runtime.source_url, "seller": seller}


def stop_managed_session_zero_browsers() -> list[int]:
    """Close only Session-0 Chrome processes whose profile belongs to Spyon."""
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
    parser = argparse.ArgumentParser(description="Open visible seller-scoped Ozon Chrome browsers.")
    parser.add_argument("--seller-id", type=int, action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--bootstrap", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not is_interactive_session():
        print("Ozon browser launcher must run in an interactive Windows user session.", file=sys.stderr)
        return 2
    configure_legacy_profiles(ROOT)
    sellers = active_sellers()
    if args.seller_id:
        selected = {int(item) for item in args.seller_id}
        sellers = [seller for seller in sellers if int(seller.get("id") or seller.get("runtime_seller_id") or 0) in selected]
    if not sellers:
        print("No active Ozon seller connections are available for browser launch.", file=sys.stderr)
        return 2
    if args.bootstrap and not args.dry_run:
        closed = stop_managed_session_zero_browsers()
        if closed:
            print(json.dumps({"status": "closed_session0", "pids": closed}, ensure_ascii=False))
    failures = 0
    for seller in sellers:
        try:
            result = start_browser(seller_plan(seller, sellers)["runtime"], dry_run=args.dry_run)
            print(json.dumps(result, ensure_ascii=False))
        except (OSError, RuntimeError, ValueError) as exc:
            failures += 1
            print(json.dumps({"status": "failed", "tenant_id": seller.get("tenant_id"), "marketplace": seller.get("marketplace_code"), "seller_id": seller.get("runtime_seller_id"), "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
