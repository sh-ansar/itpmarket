"""Production-safe, marketplace-owned Ozon browser runtime."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


OZON_MARKETPLACES = frozenset({"ozon", "ozon_kz"})
OZON_DEBUG_PORTS = {"ozon": 9222, "ozon_kz": 9333}
OZON_PROFILE_PATHS: dict[str, Path] = {}


def configure_marketplace_profiles(root: Path) -> None:
    """Set the two permanent, cookie-preserving marketplace profiles."""
    OZON_PROFILE_PATHS.update({
        "ozon": Path(root) / "collectors" / "ozon" / "chrome_vpn_profile",
        "ozon_kz": Path(root) / "collectors" / "ozon" / "chrome_kz_profile",
    })


def normalize_profile_path(value: Any) -> str:
    return os.path.normcase(os.path.normpath(str(value or "").strip().strip('"')))


def marketplace_for_url(value: str) -> str:
    host = str(urlparse(str(value or "")).hostname or "").casefold().removeprefix("www.")
    return "ozon" if host == "ozon.ru" else "ozon_kz" if host == "ozon.kz" else ""


def marketplace_start_url(marketplace_code: str) -> str:
    marketplace = str(marketplace_code or "").strip().casefold()
    if marketplace == "ozon":
        return "https://www.ozon.ru/продавец/"
    if marketplace == "ozon_kz":
        return "https://ozon.kz/продавец/"
    raise ValueError("Unsupported Ozon marketplace.")


@dataclass(frozen=True)
class OzonBrowserRuntime:
    marketplace_code: str
    profile_dir: Path
    debug_port: int
    start_url: str


def resolve_ozon_runtime(
    root: Path, marketplace_code: str, source_url: str = "",
) -> OzonBrowserRuntime:
    """Resolve the one permanent browser owned by a marketplace.

    ``source_url`` is validated only to keep an operation on its marketplace;
    it never affects the persistent Chrome profile or DevTools port.
    """
    marketplace = str(marketplace_code or "").strip().casefold()
    if marketplace not in OZON_MARKETPLACES:
        raise ValueError("Unsupported Ozon marketplace.")
    if source_url and marketplace_for_url(source_url) != marketplace:
        raise ValueError("Ozon seller URL does not belong to the requested marketplace.")
    configure_marketplace_profiles(Path(root))
    return OzonBrowserRuntime(
        marketplace,
        OZON_PROFILE_PATHS[marketplace].resolve(),
        OZON_DEBUG_PORTS[marketplace],
        marketplace_start_url(marketplace),
    )


def parse_chrome_processes(output: str) -> list[dict[str, Any]]:
    """Parse Chrome probes that own a profile, with DevTools port when present."""
    rows: list[dict[str, Any]] = []
    for line in str(output or "").splitlines():
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            raw = {"command_line": line, "session_id": None}
        command = str(raw.get("command_line") or raw.get("CommandLine") or "")
        profile = re.search(r'--user-data-dir=(?:"([^"]+)"|(\S+))', command, re.I)
        port = re.search(r"--remote-debugging-port=(\d{1,5})", command, re.I)
        if not profile:
            continue
        try:
            session_id = int(raw.get("session_id") if raw.get("session_id") is not None else raw.get("SessionId"))
        except (TypeError, ValueError):
            continue
        try:
            debug_port = int(port.group(1)) if port else 0
        except (TypeError, ValueError):
            debug_port = 0
        if not 0 < debug_port <= 65535:
            debug_port = 0
        rows.append({
            "profile_dir": normalize_profile_path(profile.group(1) or profile.group(2)),
            "debug_port": debug_port, "session_id": session_id,
            "pid": raw.get("pid") or raw.get("ProcessId"),
            "command_line": command,
        })
    return rows


def running_chrome_processes() -> list[dict[str, Any]]:
    if not sys.platform.startswith("win"):
        return []
    script = "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | ForEach-Object { [pscustomobject]@{ command_line=$_.CommandLine; session_id=$_.SessionId; pid=$_.ProcessId } | ConvertTo-Json -Compress }"
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_chrome_processes(result.stdout)


def browser_is_eligible(runtime: OzonBrowserRuntime, process: dict[str, Any], *, production: bool) -> bool:
    return (
        normalize_profile_path(process.get("profile_dir")) == normalize_profile_path(runtime.profile_dir)
        and int(process.get("debug_port") or 0) == int(runtime.debug_port)
        and (not production or int(process.get("session_id") or 0) != 0)
    )


def managed_ozon_session_zero_processes(
    root: Path, processes: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only top-level Session-0 Chrome for the two managed profiles.

    A historical managed browser can use an obsolete DevTools port, so port
    identity is deliberately not part of Session-0 cleanup.  The exact
    persistent profile still prevents touching ordinary user Chrome.
    """
    runtimes = [resolve_ozon_runtime(root, code) for code in sorted(OZON_MARKETPLACES)]
    managed_profiles = {normalize_profile_path(runtime.profile_dir) for runtime in runtimes}
    return [
        process for process in processes
        if int(process.get("session_id") or 0) == 0
        and normalize_profile_path(process.get("profile_dir")) in managed_profiles
        and "--type=" not in str(process.get("command_line") or "").casefold()
    ]
