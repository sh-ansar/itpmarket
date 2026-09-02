#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from ozon_probe_core import article_from_url, normalize_product_url, parse_catalog_html, parse_price
from ozon_browser_runtime import (
    browser_is_eligible,
    normalize_profile_path,
    running_chrome_processes,
)


class BrowserSession:
    def __init__(
        self, debug_port: int, start_url: str, profile_dir: Path | None = None
    ) -> None:
        self.debug_port = self._available_port() if int(debug_port) <= 0 else int(debug_port)
        self.start_url = start_url
        parsed_start = urlparse(start_url)
        self.site_host = str(parsed_start.hostname or "www.ozon.ru").casefold()
        self.site_root = f"https://{self.site_host}"
        bare_host = self.site_host.removeprefix("www.")
        self.allowed_hosts = {bare_host, f"www.{bare_host}"}
        self.marketplace_label = "Ozon.kz" if bare_host == "ozon.kz" else "Ozon.ru"
        self.debug_base = f"http://127.0.0.1:{self.debug_port}"
        profile_name = "chrome_kz_profile" if bare_host == "ozon.kz" else "chrome_vpn_profile"
        self.profile_dir = (
            Path(profile_dir).resolve()
            if profile_dir else Path(__file__).resolve().parent / profile_name
        )
        self.driver = None
        self.target_id = ""
        self.original_url = ""
        self.launched_browser = False

    @staticmethod
    def _available_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def debugger_json(self, path: str, timeout: int = 8) -> Any:
        request = urllib.request.Request(
            f"{self.debug_base}{path}",
            headers={"User-Agent": "Spyon-Ozon-Collector/3.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.URLError as exc:
            if path == "/json/list":
                raise RuntimeError(
                    f"{self.marketplace_label} debug-браузер недоступен на 127.0.0.1:"
                    f"{self.debug_port}. Повторите запуск и оставьте открывшееся окно Chrome открытым."
                ) from exc
            raise

    def _is_ozon_page(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        host = parsed.netloc.lower().split(":")[0]
        return host in self.allowed_hosts and "/api/" not in parsed.path

    def _debugger_ready(self, timeout: int = 2) -> bool:
        try:
            self.debugger_json("/json/list", timeout=timeout)
            return True
        except Exception:
            return False

    def _set_debug_port(self, port: int) -> None:
        self.debug_port = int(port)
        self.debug_base = f"http://127.0.0.1:{self.debug_port}"

    @staticmethod
    def _valid_debug_port(value: Any) -> int | None:
        try:
            port = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return port if 0 < port <= 65535 else None

    @staticmethod
    def _normalized_profile_path(value: Any) -> str:
        return normalize_profile_path(value)

    def _ports_from_process_output(self, value: str) -> list[int]:
        """Extract DevTools ports only for the exact isolated profile path."""
        expected = self._normalized_profile_path(self.profile_dir)
        ports: list[int] = []
        for command_line in str(value or "").splitlines():
            profile_match = re.search(
                r'--user-data-dir=(?:"([^"]+)"|(\S+))', command_line, re.I
            )
            port_match = re.search(
                r"--remote-debugging-port=(\d{1,5})", command_line, re.I
            )
            if not profile_match or not port_match:
                continue
            profile = profile_match.group(1) or profile_match.group(2) or ""
            port = self._valid_debug_port(port_match.group(1))
            if self._normalized_profile_path(profile) == expected and port is not None:
                ports.append(port)
        return list(dict.fromkeys(ports))

    def _running_profile_debug_ports(self) -> list[int]:
        return [int(item["debug_port"]) for item in self._running_profile_processes()]

    def _running_profile_processes(self) -> list[dict[str, Any]]:
        if not sys.platform.startswith("win"):
            return []
        expected = self._normalized_profile_path(self.profile_dir)
        return [
            item for item in running_chrome_processes()
            if self._normalized_profile_path(item.get("profile_dir")) == expected
        ]

    def _profile_debug_ports(self) -> list[int]:
        """Return candidate DevTools ports owned by this isolated profile."""
        production_windows = (
            sys.platform.startswith("win")
            and os.environ.get("ITP_ENV", "").strip().casefold() == "production"
        )
        processes = self._running_profile_processes()
        if production_windows:
            expected = type("ExpectedRuntime", (), {
                "profile_dir": self.profile_dir,
                "debug_port": self.debug_port,
            })()
            return [
                int(item["debug_port"])
                for item in processes
                if browser_is_eligible(expected, item, production=True)
            ]
        ports: list[int] = []
        for name in (".spyon_devtools_port", "DevToolsActivePort"):
            try:
                value = self.profile_dir.joinpath(name).read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()[0]
            except (OSError, IndexError):
                continue
            port = self._valid_debug_port(value)
            if port is not None:
                ports.append(port)
        ports.extend(self._running_profile_debug_ports())
        return list(dict.fromkeys(ports))

    def _remember_profile_debug_port(self) -> None:
        try:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self.profile_dir.joinpath(".spyon_devtools_port").write_text(
                str(self.debug_port), encoding="ascii"
            )
        except OSError:
            pass

    def _adopt_profile_debugger(self) -> bool:
        """Reuse an already-running browser for the same isolated profile."""
        previous_port = self.debug_port
        for profile_port in self._profile_debug_ports():
            self._set_debug_port(profile_port)
            if self._debugger_ready():
                self._remember_profile_debug_port()
                if profile_port != previous_port:
                    print(
                        f"{self.marketplace_label} reuses the browser for this profile "
                        f"on port {profile_port}."
                    )
                return True
        self._set_debug_port(previous_port)
        return False

    def _hidden_profile_browser(self) -> bool:
        """Whether this exact profile is occupied by a non-interactive Chrome."""
        return any(
            int(item.get("debug_port") or 0) == int(self.debug_port)
            and int(item.get("session_id") or 0) == 0
            for item in self._running_profile_processes()
        )

    @staticmethod
    def _chrome_executable() -> str:
        env_value = os.environ.get("OZON_CHROME_PATH") or os.environ.get("CHROME_PATH") or os.environ.get("CHROME")
        candidates = [
            env_value or "",
            shutil.which("chrome.exe") or "",
            shutil.which("chrome") or "",
            shutil.which("google-chrome") or "",
        ]
        if sys.platform.startswith("win"):
            candidates.extend(
                [
                    os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                    os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
                    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
                ]
            )
        elif sys.platform == "darwin":
            candidates.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return candidate
        raise RuntimeError(
            "Google Chrome was not found. Install Chrome or set OZON_CHROME_PATH to chrome.exe."
        )

    def _launch_debug_browser(self) -> None:
        configured = os.environ.get("OZON_AUTO_OPEN_BROWSER")
        production = os.environ.get("ITP_ENV", "").strip().casefold() == "production"
        auto_open = (
            configured.strip().casefold() not in {"0", "false", "no", "off"}
            if configured is not None
            else not production
        )
        if not auto_open:
            raise RuntimeError(
                "Ozon браузер не открыт. Откройте браузер Ozon и повторите "
                "синхронизацию."
            )
        chrome = self._chrome_executable()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        args = [
            chrome,
            f"--remote-debugging-port={self.debug_port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={self.profile_dir}",
            "--profile-directory=Default",
            "--lang=ru-RU",
            "--start-maximized",
            "--no-first-run",
            "--disable-popup-blocking",
            self.start_url,
        ]
        creationflags = 0
        if sys.platform.startswith("win"):
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)
        self.launched_browser = True
        print(f"{self.marketplace_label} browser opened on port {self.debug_port}. Profile: {self.profile_dir}")

    def ensure_debug_browser(self) -> None:
        production_windows = (
            sys.platform.startswith("win")
            and os.environ.get("ITP_ENV", "").strip().casefold() == "production"
        )
        if production_windows:
            if self._adopt_profile_debugger():
                return
            if self._hidden_profile_browser():
                raise RuntimeError(
                    "Профиль Ozon запущен в фоновой сессии Windows. "
                    "Перезапустите интерактивный браузер Ozon."
                )
            raise RuntimeError(
                "Браузер Ozon не открыт. Откройте браузер Ozon и повторите синхронизацию."
            )
        if self._debugger_ready():
            self._remember_profile_debug_port()
            return
        if self._adopt_profile_debugger():
            return
        self._launch_debug_browser()
        deadline = time.monotonic() + float(os.environ.get("OZON_BROWSER_STARTUP_WAIT", "45"))
        while time.monotonic() < deadline:
            if self._debugger_ready():
                self._remember_profile_debug_port()
                return
            # Chrome redirects a second launch for the same user-data-dir to
            # the existing process and ignores the newly requested port.
            # Profile markers/process lookup are seller-scoped, so adopting the
            # port preserves the session without killing another seller's Chrome.
            if self._adopt_profile_debugger():
                return
            time.sleep(1.0)
        raise RuntimeError(
            f"{self.marketplace_label} browser was opened, but debugger port {self.debug_port} did not become ready."
        )

    def _open_debug_tab(self, url: str) -> None:
        encoded = quote(url, safe=":/?&=%")
        for method in ("PUT", "GET"):
            request = urllib.request.Request(f"{self.debug_base}/json/new?{encoded}", method=method)
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    response.read()
                return
            except Exception:
                pass

    def ensure_ozon_tab(self) -> None:
        targets = self.debugger_json("/json/list")
        for target in targets if isinstance(targets, list) else []:
            if str(target.get("type") or "") == "page" and self._is_ozon_page(str(target.get("url") or "")):
                return
        self._open_debug_tab(self.start_url)
        time.sleep(3.0)

    def _find_target(self) -> dict[str, Any]:
        targets = self.debugger_json("/json/list")
        desired = urlparse(self.start_url).path.rstrip("/")
        candidates: list[dict[str, Any]] = []
        for target in targets if isinstance(targets, list) else []:
            if str(target.get("type") or "") != "page":
                continue
            url = str(target.get("url") or "")
            if not self._is_ozon_page(url):
                continue
            path = urlparse(url).path.rstrip("/")
            score = 0
            if desired and path == desired:
                score += 100
            if path not in {"", "/"}:
                score += 20
            if str(target.get("title") or "").strip():
                score += 10
            candidates.append({**target, "_score": score})
        if not candidates:
            raise RuntimeError(
                f"Не найдена открытая вкладка {self.marketplace_label}. Повторите запуск сборщика."
            )
        candidates.sort(
            key=lambda row: (int(row.get("_score") or 0), len(str(row.get("title") or ""))),
            reverse=True,
        )
        return candidates[0]

    def connect(self) -> "BrowserSession":
        self.ensure_debug_browser()
        self.ensure_ozon_tab()
        target = self._find_target()
        self.target_id = str(target.get("id") or "")
        self.original_url = str(target.get("url") or self.start_url)

        options = Options()
        options.debugger_address = f"127.0.0.1:{self.debug_port}"
        options.page_load_strategy = "none"
        service = self._chromedriver_service()
        try:
            if service is not None:
                self.driver = webdriver.Chrome(options=options, service=service)
            else:
                self.driver = webdriver.Chrome(options=options)
        except WebDriverException as exc:
            raise RuntimeError(
                f"ChromeDriver для {self.marketplace_label} не найден или не подходит к установленному Chrome. "
                "Запустите collectors\\ozon\\0_SETUP.bat. Если сервер без доступа к Selenium/Google, "
                "положите подходящий chromedriver.exe в collectors\\ozon\\drivers\\chromedriver.exe "
                "или задайте переменную CHROMEDRIVER_PATH."
            ) from exc
        self.driver.set_script_timeout(25)
        try:
            self.driver.command_executor._client_config.timeout = 60
        except Exception:
            pass
        try:
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                },
            )
        except Exception:
            pass
        try:
            from selenium_stealth import stealth

            stealth(
                self.driver,
                languages=["ru-RU", "ru"],
                vendor="Google Inc.",
                platform="Win32",
                webgl_vendor="Intel Inc.",
                renderer="Intel Iris OpenGL Engine",
                fix_hairline=True,
            )
        except Exception:
            pass
        self._switch_to_target()
        return self

    @staticmethod
    def _chromedriver_service() -> Service | None:
        root = Path(__file__).resolve().parent
        candidates = [
            os.environ.get("CHROMEDRIVER_PATH", ""),
            os.environ.get("CHROMEDRIVER", ""),
            str(root / "drivers" / "chromedriver.exe"),
            str(root / ".drivers" / "chromedriver.exe"),
            shutil.which("chromedriver") or "",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return Service(executable_path=candidate)
        return None

    def _switch_to_target(self) -> None:
        assert self.driver is not None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if self.target_id in self.driver.window_handles:
                    self.driver.switch_to.window(self.target_id)
                    return
            except Exception:
                pass
            time.sleep(0.5)
        raise RuntimeError(f"Не удалось переключиться на рабочую вкладку {self.marketplace_label}.")

    def snapshot(self) -> tuple[str, str, str]:
        assert self.driver is not None
        probe = self.driver.execute_script(
            """
            return {
              title: document.title || '',
              text: document.body ? document.body.innerText : '',
              html: document.documentElement ? document.documentElement.outerHTML : ''
            };
            """
        )
        return (
            str((probe or {}).get("title") or ""),
            str((probe or {}).get("text") or "").strip(),
            str((probe or {}).get("html") or ""),
        )

    @staticmethod
    def _safe_dom_text(value: Any) -> str:
        text = str(value or "").strip()
        if not text or "�" in text or "??" in text:
            return ""
        return text

    def dom_catalog_products(
        self, base_url: str, seller_grid_scan: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Read visible cards only from a parser-proven seller grid.

        The structured parser supplies exact seller-grid state IDs and article
        IDs.  The state node can be hydration-only, so exact accepted articles
        first prove the narrowest DOM container that contains the whole seller
        grid; only then are visible cards read inside that container.  If the
        relationship is not provable, an empty result is safer than page-wide
        recommendation/cross-sell leakage.
        """
        assert self.driver is not None
        state_ids = [
            str(value)
            for value in seller_grid_scan.get("accepted_seller_grid_ids") or []
            if str(value)
        ]
        accepted_articles = [
            str(value)
            for value in seller_grid_scan.get("accepted_seller_articles") or []
            if str(value)
        ]
        if str(seller_grid_scan.get("selected_strategy") or "") not in {
            "seller_evidence",
            "seller_single_unscoped_fallback",
        } or not state_ids or not accepted_articles:
            return []
        try:
            rows = self.driver.execute_script(
                r"""
                const acceptedStateIds = new Set(arguments[0] || []);
                const acceptedArticles = new Set(arguments[1] || []);
                if (![...acceptedStateIds].every((stateId) => document.getElementById(stateId))) return [];
                const articleFromHref = (href) => {
                  const value = String(href || '');
                  const tail = value.match(/-(\d+)\/?(?:[?#].*)?$/);
                  const direct = value.match(/\/product\/(\d+)\/?/);
                  return (tail || direct || [])[1] || '';
                };
                const productAnchors = (node) => node
                  ? [...node.querySelectorAll('a[href*="/product/"]')]
                  : [];
                // This page-wide pass is evidence-only and is restricted to
                // exact IDs accepted by parse_catalog_html.  It never emits
                // product rows; broad /product/ collection begins only after
                // a proven container has been selected below.
                const evidenceAnchors = [...document.querySelectorAll('a[href]')]
                  .filter((anchor) => acceptedArticles.has(articleFromHref(anchor.href)));
                const evidenceArticles = new Set(
                  evidenceAnchors.map((anchor) => articleFromHref(anchor.href))
                );
                if (evidenceArticles.size !== acceptedArticles.size) return [];
                const containsEveryAcceptedArticle = (node) => {
                  const found = new Set(
                    productAnchors(node).map((anchor) => articleFromHref(anchor.href))
                  );
                  return [...acceptedArticles].every((article) => found.has(article));
                };
                const candidates = new Set();
                for (const anchor of evidenceAnchors) {
                  for (let node = anchor.parentElement; node && node !== document.body && node !== document.documentElement; node = node.parentElement) {
                    if (containsEveryAcceptedArticle(node)) candidates.add(node);
                  }
                }
                // The narrowest evidence ancestor can be a single initial
                // tile row.  For Ozon's lazy storefront, that row is nested
                // in a named paginator boundary which receives later seller
                // batches.  Prefer such a boundary only when it is already
                // in the exact-article evidence chain; this is not a global
                // paginator selector and cannot stand in for seller proof.
                const paginationCandidates = [...candidates].filter((node) =>
                  /paginator/i.test(String(node.id || ''))
                );
                const container = (paginationCandidates.length
                  ? paginationCandidates
                  : [...candidates]
                ).sort((left, right) => {
                  const count = productAnchors(left).length - productAnchors(right).length;
                  if (count) return count;
                  return right.contains(left) ? -1 : left.contains(right) ? 1 : 0;
                })[0];
                if (!container) return [];
                const anchors = productAnchors(container);
                const seen = new Set();
                const rows = [];
                for (const anchor of anchors) {
                  const href = anchor.href || anchor.getAttribute('href') || '';
                  if (!href) continue;
                  let card = anchor;
                  for (let i = 0; i < 6 && card && card.parentElement; i += 1) {
                    const text = (card.innerText || '').trim();
                    if (text && text.length > 25) break;
                    card = card.parentElement;
                  }
                  const imageFromSrcset = (value) => {
                    const parts = String(value || '').split(',').map(v => v.trim()).filter(Boolean);
                    if (!parts.length) return '';
                    return parts[parts.length - 1].split(/\s+/)[0] || '';
                  };
                  const absoluteImage = (value) => {
                    const raw = String(value || '').trim();
                    if (!raw) return '';
                    try { return new URL(raw, location.href).href; } catch (_) { return raw; }
                  };
                  const imageFromNode = (node) => {
                    if (!node) return '';
                    const img = node.querySelector('img');
                    if (img) {
                      const src = img.currentSrc || img.src || img.getAttribute('src') || img.getAttribute('data-src') || img.getAttribute('data-original') || imageFromSrcset(img.getAttribute('srcset') || img.getAttribute('data-srcset'));
                      if (src) return absoluteImage(src);
                    }
                    const lazy = node.querySelector('[data-src],[data-original],[srcset],[data-srcset]');
                    if (lazy) {
                      const src = lazy.getAttribute('data-src') || lazy.getAttribute('data-original') || imageFromSrcset(lazy.getAttribute('srcset') || lazy.getAttribute('data-srcset'));
                      if (src) return absoluteImage(src);
                    }
                    const styled = [...node.querySelectorAll('[style*="background"]')].find(el => /url\(/i.test(el.getAttribute('style') || ''));
                    if (styled) {
                      const match = String(styled.getAttribute('style') || '').match(/url\((['"]?)(.*?)\1\)/i);
                      if (match && match[2]) return absoluteImage(match[2]);
                    }
                    return '';
                  };
                  const img = (card || anchor).querySelector('img');
                  const text = ((card || anchor).innerText || anchor.textContent || '').trim();
                  const lines = text.split(/\n+/).map(v => v.trim()).filter(Boolean);
                  const title = anchor.getAttribute('aria-label') || (img && (img.alt || img.title)) || lines.find(v => !/[₽₸]/.test(v)) || '';
                  const priceLines = lines.filter(v => /[₽₸]|руб/i.test(v));
                  const priceLine = priceLines.find(v => !/(?:×|x)\s*\d+\s*(?:мес|month)/i.test(v)) || priceLines[0] || '';
                  rows.push({
                    url: href,
                    name: title,
                    image_url: imageFromNode(card || anchor),
                    price_text: priceLine
                  });
                }
                return rows;
                """,
                state_ids,
                accepted_articles,
            )
        except Exception:
            return []
        products: dict[str, dict[str, Any]] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            url = normalize_product_url(str(row.get("url") or ""), base_url)
            article = article_from_url(url)
            if not article or not url:
                continue
            products[article] = {
                "article": article,
                "name": self._safe_dom_text(row.get("name")),
                "catalog_card_price": parse_price(row.get("price_text")),
                "catalog_all_prices": [str(row.get("price_text") or "")] if row.get("price_text") else [],
                "catalog_price_style": "dom",
                "image_url": str(row.get("image_url") or ""),
                "url": url,
            }
        return list(products.values())

    @staticmethod
    def blocked_state(title: str, text: str, page_html: str) -> bool:
        value = f"{title}\n{text}\n{page_html[:30000]}".lower()
        markers = (
            "antibot challenge page",
            "fab_chlg",
            "подтвердите, что вы не робот",
            "доступ ограничен",
            "access denied",
            '"status":403',
            '"code":403',
            "forbidden",
        )
        return any(marker in value for marker in markers)

    def _extract_json(self) -> tuple[dict[str, Any] | None, str, str, str]:
        title, text, page_html = self.snapshot()
        candidates: list[str] = []
        if text.startswith("{") and text.endswith("}"):
            candidates.append(text)
        pre_match = re.search(r"<pre[^>]*>(.*?)</pre>", page_html, re.I | re.S)
        if pre_match:
            candidates.append(html.unescape(pre_match.group(1)).strip())
        first = page_html.find("{")
        last = page_html.rfind("}")
        if first >= 0 and last > first:
            candidates.append(html.unescape(page_html[first : last + 1]).strip())
        for raw in candidates:
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if isinstance(data, dict) and isinstance(data.get("widgetStates"), dict):
                return data, title, text, page_html
        return None, title, text, page_html

    def _collect_catalog_snapshot(
        self,
        base_url: str,
        wait_seconds: int,
        events: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], str, str, str, str, bool]:
        assert self.driver is not None
        parsed_base = urlparse(base_url)
        seller_root = bool(re.fullmatch(
            r"/(?:seller|продавец)/[^/]+/?",
            parsed_base.path or "",
            flags=re.IGNORECASE,
        ))
        settle_window_seconds = 3.2
        settle_poll_seconds = 0.4
        terminal_confirmation_window_seconds = 15.0
        terminal_confirmation_windows_required = 2
        # `catalog_wait_seconds` is suitable for ordinary paginated layouts,
        # but is too short to exhaust a seller root that keeps appending a
        # proven batch each bottom-scroll cycle.  Keep a finite hard deadline
        # while giving that one infinite-scroll layout enough time to reach
        # the required stable end instead of falling through to paginator URL.
        # Reserve the complete bounded terminal-confirmation phase.
        safety_seconds = (
            max(wait_seconds, 300)
            + (
                terminal_confirmation_window_seconds
                * terminal_confirmation_windows_required
            )
            if seller_root else wait_seconds
        )
        deadline = time.monotonic() + safety_seconds
        unique: dict[str, dict[str, Any]] = {}
        best_next_page = ""
        last_title = last_text = last_html = ""
        saw_block = False
        stable_cycles = 0
        seller_terminal_confirmed = not seller_root
        cycle = 0
        # A lazy seller root can add a batch only after its current bottom has
        # been reached.  This is deliberately a short polling window rather
        # than one long sleep: a new batch starts the next bottom-scroll cycle
        # immediately, while a quiet root must settle repeatedly before it is
        # considered exhausted.
        def collect_once() -> tuple[int, int, str, str, int, bool]:
            nonlocal best_next_page, last_title, last_text, last_html, saw_block
            title, text, page_html = self.snapshot()
            last_title, last_text, last_html = title, text, page_html
            grid_scan: dict[str, Any] = {}
            state_products, next_page = parse_catalog_html(
                page_html,
                base_url,
                grid_scan,
            )
            events.append({"event": "catalog_grid_scan", **grid_scan})
            # Structured state proves the seller grid.  Visible cards are then
            # read only from its proven DOM container; page-wide product links
            # remain ineligible because they can be recommendations.
            seller_dom_products = self.dom_catalog_products(base_url, grid_scan)
            unique_before = len(unique)
            for product in state_products:
                article = str(product.get("article") or "")
                if article:
                    if product.get("name"):
                        product = {
                            **product,
                            "name": self._safe_dom_text(product.get("name")),
                        }
                    unique[article] = product
            # Virtualized cards can disappear from state and the DOM; retain
            # every previously proven seller card in this accumulator.
            for product in seller_dom_products:
                article = str(product.get("article") or "")
                if article and article not in unique:
                    if product.get("name"):
                        product = {
                            **product,
                            "name": self._safe_dom_text(product.get("name")),
                        }
                    unique[article] = product
            if next_page:
                best_next_page = next_page
            blocked = self.blocked_state(title, text, page_html)
            saw_block = saw_block or blocked
            structured_signature = "|".join(sorted(
                str(product.get("article") or "")
                for product in state_products
                if str(product.get("article") or "")
            ))
            dom_signature = "|".join(sorted(
                str(product.get("article") or "")
                for product in seller_dom_products
                if str(product.get("article") or "")
            ))
            return (
                len(state_products),
                len(seller_dom_products),
                structured_signature,
                dom_signature,
                len(unique) - unique_before,
                blocked,
            )

        def confirm_seller_terminal(
            structured_signature: str,
            dom_signature: str,
            scroll_height: int,
            loading: bool,
        ) -> tuple[bool, bool]:
            """Require multiple unchanged long windows before seller exhaustion."""
            nonlocal stable_cycles
            for window in range(1, terminal_confirmation_windows_required + 1):
                if time.monotonic() >= deadline:
                    return False, False
                window_deadline = min(
                    deadline,
                    time.monotonic() + terminal_confirmation_window_seconds,
                )
                window_unique_count = len(unique)
                window_started = time.monotonic()
                try:
                    initial_scroll_state = self.driver.execute_script(
                        """
                        const root = document.scrollingElement || document.documentElement || document.body;
                        if (root) root.scrollTo(0, root.scrollHeight);
                        return {
                          top: root ? root.scrollTop : 0,
                          height: root ? root.scrollHeight : 0,
                          loading: Boolean(document.querySelector('[aria-busy="true"], [data-testid*="loading" i], [class*="loading" i]'))
                        };
                        """
                    )
                    initial_height = int(
                        (initial_scroll_state or {}).get("height") or scroll_height
                    )
                    initial_loading = bool(
                        (initial_scroll_state or {}).get("loading")
                    )
                    if any((
                        initial_height != scroll_height,
                        initial_loading != loading,
                        initial_loading,
                        len(unique) > window_unique_count,
                    )):
                        events.append(
                            {
                                "event": "terminal_confirmation_cancelled",
                                "window": window,
                                "reason": "seller_state_changed",
                                "unique_products": len(unique),
                                "scroll_height": initial_height,
                                "loading": initial_loading,
                            }
                        )
                        stable_cycles = 0
                        return False, False

                    while time.monotonic() < window_deadline:
                        time.sleep(settle_poll_seconds)
                        (
                            _structured_count,
                            _dom_count,
                            structured_signature_after,
                            dom_signature_after,
                            _new_unique,
                            blocked_after,
                        ) = collect_once()
                        if blocked_after:
                            events.append(
                                {
                                    "event": "blocked_detected",
                                    "unique_products": len(unique),
                                }
                            )
                            return False, True
                        scroll_state = self.driver.execute_script(
                            """
                            const root = document.scrollingElement || document.documentElement || document.body;
                            if (root) root.scrollTo(0, root.scrollHeight);
                            return {
                              top: root ? root.scrollTop : 0,
                              height: root ? root.scrollHeight : 0,
                              loading: Boolean(document.querySelector('[aria-busy="true"], [data-testid*="loading" i], [class*="loading" i]'))
                            };
                            """
                        )
                        current_height = int(
                            (scroll_state or {}).get("height") or scroll_height
                        )
                        current_loading = bool(
                            (scroll_state or {}).get("loading")
                        )
                        changed = any((
                            len(unique) > window_unique_count,
                            structured_signature_after != structured_signature,
                            dom_signature_after != dom_signature,
                            current_height != scroll_height,
                            current_loading != loading,
                            current_loading,
                        ))
                        if changed:
                            events.append(
                                {
                                    "event": "terminal_confirmation_cancelled",
                                    "window": window,
                                    "reason": "seller_state_changed",
                                    "unique_products": len(unique),
                                    "scroll_height": current_height,
                                    "loading": current_loading,
                                }
                            )
                            stable_cycles = 0
                            return False, False
                    events.append(
                        {
                            "event": "terminal_confirmation_window",
                            "window": window,
                            "unchanged": True,
                            "unique_products": len(unique),
                            "scroll_height": scroll_height,
                            "duration_seconds": round(
                                time.monotonic() - window_started, 3
                            ),
                        }
                    )
                except Exception as exc:
                    events.append(
                        {
                            "event": "terminal_confirmation_error",
                            "window": window,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    stable_cycles = 0
                    return False, False
            events.append(
                {
                    "event": "seller_terminal_confirmed",
                    "confirmation_windows": terminal_confirmation_windows_required,
                    "unique_products": len(unique),
                }
            )
            return True, False

        try:
            self.driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(1.0)
        except Exception as exc:
            events.append({"event": "scroll_top_error", "error": f"{type(exc).__name__}: {exc}"})

        while time.monotonic() < deadline:
            try:
                cycle += 1
                (
                    structured_before,
                    dom_before,
                    structured_signature_before,
                    dom_signature_before,
                    _initial_new_unique,
                    blocked,
                ) = collect_once()
                if blocked:
                    events.append(
                        {
                            "event": "blocked_detected",
                            "unique_products": len(unique),
                        }
                    )
                    break

                unique_count_before = len(unique)
                scroll_state_before = self.driver.execute_script(
                    """
                    const root = document.scrollingElement || document.documentElement || document.body;
                    const before = root ? root.scrollTop : 0;
                    const height = root ? root.scrollHeight : 0;
                    // Seller storefronts lazy-load only after a real trip to
                    // the current document bottom.  A viewport-sized step can
                    // leave the trigger far below the initial card batch.
                    if (root) root.scrollTo(0, height);
                    const after = root ? root.scrollTop : 0;
                    return {
                      before,
                      after,
                      heightBefore: height,
                      heightAfter: root ? root.scrollHeight : height,
                      loading: Boolean(document.querySelector('[aria-busy="true"], [data-testid*="loading" i], [class*="loading" i]'))
                    };
                    """
                )
                scroll_top_before = int((scroll_state_before or {}).get("before") or 0)
                scroll_height_before = int(
                    (scroll_state_before or {}).get("heightBefore")
                    or (scroll_state_before or {}).get("height")
                    or 0
                )
                loading_before = bool((scroll_state_before or {}).get("loading"))
                settle_deadline = min(
                    deadline, time.monotonic() + settle_window_seconds
                )
                structured_after = structured_before
                dom_after = dom_before
                structured_signature_after = structured_signature_before
                dom_signature_after = dom_signature_before
                scroll_state_after = scroll_state_before
                blocked_after = False
                observed_change = False
                while time.monotonic() < settle_deadline:
                    time.sleep(settle_poll_seconds)
                    (
                        structured_after,
                        dom_after,
                        structured_signature_after,
                        dom_signature_after,
                        _polled_new_unique,
                        blocked_after,
                    ) = collect_once()
                    scroll_state_after = self.driver.execute_script(
                        """
                        const root = document.scrollingElement || document.documentElement || document.body;
                        return {
                          top: root ? root.scrollTop : 0,
                          height: root ? root.scrollHeight : 0,
                          loading: Boolean(document.querySelector('[aria-busy="true"], [data-testid*="loading" i], [class*="loading" i]'))
                        };
                        """
                    )
                    if blocked_after:
                        break
                    height_changed = int((scroll_state_after or {}).get("height") or 0) > scroll_height_before
                    observed_change = any((
                        len(unique) > unique_count_before,
                        height_changed,
                        structured_signature_after != structured_signature_before,
                        dom_signature_after != dom_signature_before,
                        bool((scroll_state_after or {}).get("loading")) != loading_before,
                    ))
                    if observed_change:
                        break

                if blocked_after:
                    events.append(
                        {
                            "event": "blocked_detected",
                            "unique_products": len(unique),
                        }
                    )
                    break

                scroll_height_after = int(
                    (scroll_state_after or {}).get("height")
                    or (scroll_state_after or {}).get("heightAfter")
                    or scroll_height_before
                )
                new_unique = len(unique) - unique_count_before
                fully_stable = (
                    not observed_change
                    and new_unique == 0
                    and scroll_height_after == scroll_height_before
                    and dom_signature_after == dom_signature_before
                    and structured_signature_after == structured_signature_before
                )
                if fully_stable:
                    stable_cycles += 1
                else:
                    stable_cycles = 0
                events.append(
                    {
                        "event": "scroll_cycle",
                        "cycle": cycle,
                        "scroll_top_before": scroll_top_before,
                        "scroll_height_before": scroll_height_before,
                        "structured_products": structured_before,
                        "seller_dom_products": dom_before,
                        "new_unique": new_unique,
                        "total_unique": len(unique),
                        "scroll_height_after": scroll_height_after,
                        "stable_cycles": stable_cycles,
                        "visible_products": structured_after,
                        "dom_products": dom_after,
                        "unique_products": len(unique),
                        "next_page": bool(best_next_page),
                        "moved": bool((scroll_state_before or {}).get("after") != scroll_top_before),
                        "products_grew": new_unique > 0,
                        "height_grew": scroll_height_after > scroll_height_before,
                        "structured_changed": structured_signature_after != structured_signature_before,
                        "seller_dom_changed": dom_signature_after != dom_signature_before,
                        "loading_changed": bool((scroll_state_after or {}).get("loading")) != loading_before,
                        "blocked": False,
                    }
                )
                # Four normal stable cycles only identify a possible seller end.
                # Require long unchanged confirmation windows before allowing a
                # seller-root paginator URL or terminal return.
                if unique and stable_cycles >= 4:
                    if not seller_root:
                        break
                    (
                        seller_terminal_confirmed,
                        confirmation_blocked,
                    ) = confirm_seller_terminal(
                        structured_signature_after,
                        dom_signature_after,
                        scroll_height_after,
                        bool((scroll_state_after or {}).get("loading")),
                    )
                    if confirmation_blocked or seller_terminal_confirmed:
                        break
            except Exception as exc:
                events.append({"event": "scroll_cycle_error", "error": f"{type(exc).__name__}: {exc}"})
                time.sleep(settle_poll_seconds)

        if seller_root and not seller_terminal_confirmed and time.monotonic() >= deadline:
            events.append(
                {
                    "event": "scroll_safety_deadline",
                    "unique_products": len(unique),
                    "stable_cycles": stable_cycles,
                }
            )
            # Do not treat a paginator URL as a continuation while the root's
            # own lazy catalogue is still changing.
            best_next_page = ""
        elif seller_root and not seller_terminal_confirmed:
            # A blocked/error exit or any other incomplete confirmation must
            # never expose a paginator URL as if the seller root were done.
            best_next_page = ""

        return list(unique.values()), best_next_page, last_title, last_text, last_html, saw_block

    def load_catalog(
        self,
        url: str,
        wait_seconds: int,
        reloads: int,
        navigate: bool = True,
    ) -> dict[str, Any]:
        assert self.driver is not None
        started = time.monotonic()
        events: list[dict[str, Any]] = []
        last_title = last_text = last_html = ""

        if navigate:
            try:
                self.driver.get(url)
            except Exception as exc:
                events.append(
                    {
                        "event": "get_error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

        for attempt in range(reloads + 1):
            (
                products,
                next_page,
                title,
                text,
                page_html,
                saw_block,
            ) = self._collect_catalog_snapshot(
                url,
                wait_seconds,
                events,
            )

            last_title = title
            last_text = text
            last_html = page_html

            # Never retry or refresh an explicit challenge page.
            if saw_block or self.blocked_state(
                title,
                text,
                page_html,
            ):
                return {
                    "ok": False,
                    "status": "BLOCKED_CHALLENGE",
                    "title": title,
                    "html": page_html,
                    "elapsed_ms": round(
                        (time.monotonic() - started) * 1000
                    ),
                    "events": events,
                }

            if products:
                return {
                    "ok": True,
                    "status": "CATALOG_OK",
                    "products": products,
                    "next_page": next_page,
                    "html": page_html,
                    "title": title,
                    "elapsed_ms": round(
                        (time.monotonic() - started) * 1000
                    ),
                    "events": events,
                }

            if attempt < reloads:
                try:
                    self.driver.refresh()
                except Exception as exc:
                    events.append(
                        {
                            "event": "refresh_error",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

                time.sleep(8)

        status = (
            "BLOCKED_AFTER_RETRIES"
            if self.blocked_state(
                last_title,
                last_text,
                last_html,
            )
            else "NO_CATALOG"
        )

        return {
            "ok": False,
            "status": status,
            "title": last_title,
            "html": last_html,
            "elapsed_ms": round(
                (time.monotonic() - started) * 1000
            ),
            "events": events,
        }

    def load_product_api(
        self,
        article: str,
        wait_seconds: int,
        reloads: int,
    ) -> dict[str, Any]:
        assert self.driver is not None

        url = (
            f"{self.site_root}/api/composer-api.bx/page/json/v2"
            f"?url=/product/{article}&__rr=1"
        )

        started = time.monotonic()
        events: list[dict[str, Any]] = []
        last_title = last_text = last_html = ""

        try:
            self.driver.get(url)
        except Exception as exc:
            events.append(
                {
                    "event": "get_error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

        for attempt in range(reloads + 1):
            deadline = time.monotonic() + wait_seconds

            while time.monotonic() < deadline:
                try:
                    (
                        data,
                        title,
                        text,
                        page_html,
                    ) = self._extract_json()

                    last_title = title
                    last_text = text
                    last_html = page_html

                    if data is not None:
                        return {
                            "ok": True,
                            "status": "JSON_OK",
                            "json": data,
                            "url": url,
                            "title": title,
                            "elapsed_ms": round(
                                (time.monotonic() - started)
                                * 1000
                            ),
                            "events": events,
                        }

                    blocked = self.blocked_state(
                        title,
                        text,
                        page_html,
                    )

                    events.append(
                        {
                            "event": "poll",
                            "attempt": attempt + 1,
                            "blocked": blocked,
                        }
                    )

                    # Explicit challenge => fail fast.
                    if blocked:
                        return {
                            "ok": False,
                            "status": "BLOCKED_CHALLENGE",
                            "url": url,
                            "title": title,
                            "elapsed_ms": round(
                                (time.monotonic() - started)
                                * 1000
                            ),
                            "events": events,
                        }

                except Exception as exc:
                    events.append(
                        {
                            "event": "poll_error",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

                time.sleep(2.5)

            if attempt < reloads:
                try:
                    self.driver.refresh()
                except Exception as exc:
                    events.append(
                        {
                            "event": "refresh_error",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

                time.sleep(8)

        status = (
            "BLOCKED_AFTER_RETRIES"
            if self.blocked_state(
                last_title,
                last_text,
                last_html,
            )
            else "NO_JSON"
        )

        return {
            "ok": False,
            "status": status,
            "url": url,
            "title": last_title,
            "elapsed_ms": round(
                (time.monotonic() - started) * 1000
            ),
            "events": events,
        }

    def return_original(self) -> None:
        if self.driver is None or not self.original_url:
            return
        try:
            self.driver.get(self.original_url)
        except Exception:
            pass

    def close(self) -> None:
        self.return_original()
        self.driver = None
