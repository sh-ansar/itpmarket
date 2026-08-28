"""One production contract for Ozon browser ownership and profiles.

The web app, collector and interactive launcher all resolve an Ozon seller
through this module.  In particular, a ready DevTools port is not sufficient
proof that a browser may be used by production automation: on Windows it must
belong to the expected profile and an interactive (non-zero) session.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from runtime_scope import SellerRuntimeScope


OZON_LEGACY_PROFILE_PATHS: dict[str, Path] = {}
OZON_LEGACY_DEBUG_PORTS = {"ozon": 9222, "ozon_kz": 9333}


def configure_legacy_profiles(root: Path) -> None:
    """Set the repository-local legacy profiles once at application startup."""
    OZON_LEGACY_PROFILE_PATHS.update({
        "ozon": Path(root) / "collectors" / "ozon" / "chrome_vpn_profile",
        "ozon_kz": Path(root) / "collectors" / "ozon" / "chrome_kz_profile",
    })


def normalize_profile_path(value: Any) -> str:
    return os.path.normcase(os.path.normpath(str(value or "").strip().strip('"')))


def seller_identity(value: Any) -> str:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return ""
    host = str(parsed.hostname or "").casefold().removeprefix("www.")
    parts = [part.casefold() for part in parsed.path.split("/") if part]
    if host not in {"ozon.ru", "ozon.kz"} or len(parts) < 2 or parts[0] != "seller":
        return ""
    return f"{host}/seller/{parts[1]}"


def marketplace_for_url(value: str) -> str:
    host = str(urlparse(str(value or "")).hostname or "").casefold().removeprefix("www.")
    if host == "ozon.ru":
        return "ozon"
    if host == "ozon.kz":
        return "ozon_kz"
    return ""


def deterministic_debug_port(tenant_id: int, marketplace_code: str, seller_id: int) -> int:
    key = f"{int(tenant_id)}:{marketplace_code}:{int(seller_id)}".encode("ascii")
    return 20000 + (zlib.crc32(key) % 30000)


def runtime_seller_id(seller: dict[str, Any], marketplace_code: str) -> int:
    """Return the persistent seller key used by browser runtime directories.

    Explicit seller rows own their database id.  Older installations can still
    have an approved integration without a seller row; such a connection gets
    the stable id prepared by SaaSService from its tenant, marketplace and URL.
    """
    value = int(seller.get("runtime_seller_id") or seller.get("id") or 0)
    if value <= 0:
        raise ValueError("Ozon seller runtime requires a stable seller id.")
    return value


def _ports_in_profile(profile: Path, default_port: int) -> list[int]:
    ports = [default_port] if 0 < int(default_port) <= 65535 else []
    for name in (".spyon_devtools_port", "DevToolsActivePort"):
        try:
            port = int(profile.joinpath(name).read_text(encoding="utf-8", errors="replace").splitlines()[0])
        except (OSError, ValueError, IndexError):
            continue
        if 0 < port <= 65535:
            ports.append(port)
    return list(dict.fromkeys(ports))


def legacy_profile_owner(legacy_profile: Path, seller_sources: Iterable[dict[str, Any]], default_debug_port: int = 0) -> int | None:
    """Identify a legacy profile only from its persisted or live seller URL."""
    try:
        if not legacy_profile.is_dir() or next(legacy_profile.iterdir(), None) is None:
            return None
    except OSError:
        return None
    candidates = {
        int(item.get("id") or 0): seller_identity(item.get("source_url"))
        for item in seller_sources
        if int(item.get("id") or 0) > 0 and seller_identity(item.get("source_url"))
    }
    marker_path = legacy_profile / ".spyon_seller_owner.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        owner_id = int(marker.get("seller_id") or 0)
        if candidates.get(owner_id) == str(marker.get("seller_identity") or ""):
            return owner_id
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    identities: set[str] = set()
    for port in _ports_in_profile(legacy_profile, default_debug_port):
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{port}/json/list", headers={"User-Agent": "Spyon-Profile-Owner/4.0"})
            with urllib.request.urlopen(request, timeout=1) as response:
                tabs = json.loads(response.read().decode("utf-8", errors="replace"))
            identities.update(seller_identity(item.get("url")) for item in tabs if isinstance(item, dict))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    matches = [(seller_id, identity) for seller_id, identity in candidates.items() if identity and identity in identities]
    if len(matches) != 1:
        return None
    owner_id, identity = matches[0]
    try:
        marker_path.write_text(json.dumps({"seller_id": owner_id, "seller_identity": identity}, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass
    return owner_id


@dataclass(frozen=True)
class OzonBrowserRuntime:
    marketplace_code: str
    tenant_id: int
    tenant_seller_id: int
    source_url: str
    profile_dir: Path
    debug_port: int
    legacy_owner: bool


def resolve_ozon_runtime(root: Path, seller: dict[str, Any], marketplace_code: str, seller_sources: Iterable[dict[str, Any]]) -> OzonBrowserRuntime:
    marketplace = str(marketplace_code or "").strip().casefold()
    if marketplace not in {"ozon", "ozon_kz"}:
        raise ValueError("Unsupported Ozon marketplace.")
    tenant_id = int(seller.get("tenant_id") or 0)
    seller_id = runtime_seller_id(seller, marketplace)
    source_url = str(seller.get("source_url") or "").strip()
    if tenant_id <= 0 or seller_id <= 0 or marketplace_for_url(source_url) != marketplace:
        raise ValueError("Ozon seller runtime requires an active seller with a matching source URL.")
    scope = SellerRuntimeScope(Path(root), tenant_id, marketplace, seller_id)
    return OzonBrowserRuntime(
        marketplace, tenant_id, seller_id, source_url,
        scope.profile_dir,
        deterministic_debug_port(tenant_id, marketplace, seller_id),
        False,
    )


def resolve_ozon_runtimes(
    root: Path, sellers: Iterable[dict[str, Any]],
) -> dict[tuple[int, str, int], OzonBrowserRuntime]:
    """Resolve all launchable Ozon sellers with collision-free DevTools ports.

    Port allocation is deterministic for the same seller set and probes within
    the dedicated 20000..49999 range only when two hashes collide.
    """
    values = [dict(item) for item in sellers]
    prepared: list[tuple[tuple[int, str, int], OzonBrowserRuntime]] = []
    for seller in values:
        marketplace = str(seller.get("marketplace_code") or "").strip().casefold()
        runtime = resolve_ozon_runtime(root, seller, marketplace, values)
        prepared.append(((runtime.tenant_id, runtime.marketplace_code, runtime.tenant_seller_id), runtime))
    used: set[int] = set()
    result: dict[tuple[int, str, int], OzonBrowserRuntime] = {}
    for key, runtime in sorted(prepared, key=lambda item: item[0]):
        port = runtime.debug_port
        while port in used:
            port = 20000 + ((port - 20000 + 1) % 30000)
        used.add(port)
        result[key] = OzonBrowserRuntime(
            runtime.marketplace_code, runtime.tenant_id,
            runtime.tenant_seller_id, runtime.source_url,
            runtime.profile_dir, port, False,
        )
    return result


def managed_ozon_session_zero_processes(
    root: Path, processes: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only Session-0 Chrome processes owned by Spyon Ozon profiles."""
    base = (Path(root) / ".runtime" / "browser_profiles").resolve()
    legacy = {normalize_profile_path(path) for path in OZON_LEGACY_PROFILE_PATHS.values()}
    result: list[dict[str, Any]] = []
    for process in processes:
        if int(process.get("session_id") or 0) != 0:
            continue
        profile = normalize_profile_path(process.get("profile_dir"))
        try:
            managed_runtime = Path(profile).resolve().is_relative_to(base)
        except (OSError, ValueError):
            managed_runtime = False
        if profile in legacy or managed_runtime:
            result.append(process)
    return result


def parse_chrome_processes(output: str) -> list[dict[str, Any]]:
    """Parse JSON-lines process probes without trusting a marker file."""
    rows: list[dict[str, Any]] = []
    for line in str(output or "").splitlines():
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            raw = {"command_line": line, "session_id": None}
        command = str(raw.get("command_line") or raw.get("CommandLine") or "")
        profile = re.search(r'--user-data-dir=(?:"([^"]+)"|(\S+))', command, re.I)
        port = re.search(r"--remote-debugging-port=(\d{1,5})", command, re.I)
        if not profile or not port:
            continue
        try:
            session_id = int(raw.get("session_id") if raw.get("session_id") is not None else raw.get("SessionId"))
            debug_port = int(port.group(1))
        except (TypeError, ValueError):
            continue
        if 0 < debug_port <= 65535:
            rows.append({"profile_dir": normalize_profile_path(profile.group(1) or profile.group(2)), "debug_port": debug_port, "session_id": session_id, "pid": raw.get("pid") or raw.get("ProcessId")})
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
    if normalize_profile_path(process.get("profile_dir")) != normalize_profile_path(runtime.profile_dir):
        return False
    if int(process.get("debug_port") or 0) != int(runtime.debug_port):
        return False
    return not production or int(process.get("session_id") or 0) != 0
