"""Open visible, seller-scoped Ozon Chrome sessions for an interactive user.

This launcher is intentionally separate from the production web service.  A
collector in Windows Session 0 may attach to these browsers over DevTools, but
must never create an invisible Session-0 browser when one is missing.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import load_config, resolve_path  # noqa: E402
from runtime_scope import SellerRuntimeScope  # noqa: E402
from saas_service import SaaSService  # noqa: E402


def is_interactive_session() -> bool:
    """Reject Session 0 on Windows; visible Chrome requires a user desktop."""
    if os.name != "nt":
        return True
    session_id = ctypes.c_uint32()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(  # type: ignore[attr-defined]
        os.getpid(), ctypes.byref(session_id)
    ):
        return False
    return int(session_id.value) != 0


def debug_port_for(tenant_id: int, marketplace: str, seller_id: int) -> int:
    key = f"{int(tenant_id)}:{marketplace}:{int(seller_id)}".encode("ascii")
    return 20000 + (zlib.crc32(key) % 30000)


def chrome_executable() -> str:
    candidates = [
        os.environ.get("OZON_CHROME_PATH", ""),
        os.environ.get("CHROME_PATH", ""),
        shutil.which("chrome.exe") or "",
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("Google Chrome не найден. Укажите OZON_CHROME_PATH.")


def debugger_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/json/version", timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def saved_debug_port(profile_dir: Path) -> int | None:
    for name in (".spyon_devtools_port", "DevToolsActivePort"):
        try:
            value = profile_dir.joinpath(name).read_text(encoding="utf-8", errors="replace").splitlines()[0]
            port = int(value.strip())
        except (OSError, IndexError, ValueError):
            continue
        if 0 < port <= 65535:
            return port
    return None


def start_browser(profile_dir: Path, port: int, start_url: str, *, dry_run: bool = False) -> dict[str, Any]:
    previous_port = saved_debug_port(profile_dir)
    if previous_port and debugger_ready(previous_port):
        return {"status": "reused", "profile": str(profile_dir), "port": previous_port, "url": start_url}
    if dry_run:
        return {"status": "planned", "profile": str(profile_dir), "port": port, "url": start_url}
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.joinpath(".spyon_devtools_port").write_text(str(port), encoding="ascii")
    args = [
        chrome_executable(),
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile_dir}",
        "--profile-directory=Default",
        "--lang=ru-RU",
        "--start-maximized",
        "--no-first-run",
        "--disable-popup-blocking",
        start_url,
    ]
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)
    return {"status": "opened", "profile": str(profile_dir), "port": port, "url": start_url}


def active_sellers() -> list[dict[str, Any]]:
    config = load_config()
    service = SaaSService(resolve_path(config, "database"))
    rows: list[dict[str, Any]] = []
    for marketplace in ("ozon", "ozon_kz"):
        for seller in service.active_seller_sources(marketplace):
            seller["marketplace_code"] = marketplace
            rows.append(seller)
    return rows


def seller_plan(seller: dict[str, Any]) -> dict[str, Any]:
    marketplace = str(seller["marketplace_code"])
    source_url = str(seller.get("source_url") or "").strip()
    host = str(urlparse(source_url).hostname or "").casefold().removeprefix("www.")
    expected_host = "ozon.kz" if marketplace == "ozon_kz" else "ozon.ru"
    if host != expected_host:
        raise ValueError(f"Некорректная ссылка продавца {marketplace}: {source_url}")
    scope = SellerRuntimeScope(ROOT, int(seller["tenant_id"]), marketplace, int(seller["id"]))
    return {
        "profile_dir": scope.profile_dir,
        "port": debug_port_for(int(seller["tenant_id"]), marketplace, int(seller["id"])),
        "source_url": source_url,
        "seller": seller,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open visible seller-scoped Ozon Chrome browsers.")
    parser.add_argument("--seller-id", type=int, action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not is_interactive_session():
        print("Ozon browser launcher must run in an interactive Windows user session.", file=sys.stderr)
        return 2
    sellers = active_sellers()
    if args.seller_id:
        selected = {int(item) for item in args.seller_id}
        sellers = [seller for seller in sellers if int(seller["id"]) in selected]
    if not sellers:
        print("Нет активных подключений Ozon для открытия браузера.", file=sys.stderr)
        return 2
    try:
        for seller in sellers:
            plan = seller_plan(seller)
            result = start_browser(plan["profile_dir"], plan["port"], plan["source_url"], dry_run=args.dry_run)
            print(json.dumps(result, ensure_ascii=False))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Не удалось открыть Ozon браузер: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
