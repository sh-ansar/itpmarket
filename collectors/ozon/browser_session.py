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

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from ozon_probe_core import article_from_url, normalize_product_url, parse_catalog_html, parse_price


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
        return os.path.normcase(
            os.path.normpath(str(value or "").strip().strip('"'))
        )

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
        if not sys.platform.startswith("win"):
            return []
        script = (
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            "ForEach-Object { [Console]::Out.WriteLine($_.CommandLine) }"
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                check=False,
                creationflags=creationflags,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        return self._ports_from_process_output(completed.stdout)

    def _profile_debug_ports(self) -> list[int]:
        """Return candidate DevTools ports owned by this isolated profile."""
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
        if os.environ.get("OZON_AUTO_OPEN_BROWSER", "1").strip().casefold() in {"0", "false", "no", "off"}:
            raise RuntimeError(
                f"{self.marketplace_label} debug browser is not open and OZON_AUTO_OPEN_BROWSER is disabled."
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

    def dom_catalog_products(self, base_url: str) -> list[dict[str, Any]]:
        assert self.driver is not None
        try:
            rows = self.driver.execute_script(
                """
                const anchors = [...document.querySelectorAll('a[href*="/product/"]')];
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
                    return parts[parts.length - 1].split(/\\s+/)[0] || '';
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
                    const styled = [...node.querySelectorAll('[style*="background"]')].find(el => /url\\(/i.test(el.getAttribute('style') || ''));
                    if (styled) {
                      const match = String(styled.getAttribute('style') || '').match(/url\\((['"]?)(.*?)\\1\\)/i);
                      if (match && match[2]) return absoluteImage(match[2]);
                    }
                    return '';
                  };
                  const img = (card || anchor).querySelector('img');
                  const text = ((card || anchor).innerText || anchor.textContent || '').trim();
                  const lines = text.split(/\\n+/).map(v => v.trim()).filter(Boolean);
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
                """
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
        deadline = time.monotonic() + wait_seconds
        unique: dict[str, dict[str, Any]] = {}
        best_next_page = ""
        last_title = last_text = last_html = ""
        saw_block = False
        last_unique = -1
        stable_ticks = 0

        try:
            self.driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(1.0)
        except Exception as exc:
            events.append({"event": "scroll_top_error", "error": f"{type(exc).__name__}: {exc}"})

        while time.monotonic() < deadline:
            try:
                title, text, page_html = self.snapshot()
                last_title, last_text, last_html = title, text, page_html
                state_products, next_page = parse_catalog_html(page_html, base_url)
                dom_products = self.dom_catalog_products(base_url)
                products = dom_products + state_products
                for product in products:
                    article = str(product.get("article") or "")
                    if article:
                        if product.get("name"):
                            product = {**product, "name": self._safe_dom_text(product.get("name"))}
                        unique[article] = product
                if next_page:
                    best_next_page = next_page
                blocked = self.blocked_state(title, text, page_html)
                saw_block = saw_block or blocked

                if blocked:
                    events.append(
                        {
                            "event": "blocked_detected",
                            "unique_products": len(unique),
                        }
                    )
                    break

                scroll_state = self.driver.execute_script(
                    """
                    const root = document.scrollingElement || document.documentElement || document.body;
                    const step = Math.max(700, Math.floor((window.innerHeight || 900) * 0.85));
                    const before = root ? root.scrollTop : 0;
                    const height = root ? root.scrollHeight : 0;
                    if (root) root.scrollTo(0, Math.min(height, before + step));
                    const after = root ? root.scrollTop : 0;
                    return {
                      before,
                      after,
                      height,
                      inner: window.innerHeight || 0,
                      nearBottom: root ? (after + (window.innerHeight || 0) >= height - 12) : true
                    };
                    """
                )
                count = len(unique)
                moved = bool((scroll_state or {}).get("after") != (scroll_state or {}).get("before"))
                if count == last_unique:
                    stable_ticks += 1
                else:
                    stable_ticks = 0
                last_unique = count
                events.append(
                    {
                        "event": "scroll_poll",
                        "visible_products": len(state_products),
                        "dom_products": len(dom_products),
                        "unique_products": count,
                        "next_page": bool(best_next_page),
                        "moved": moved,
                        "near_bottom": bool((scroll_state or {}).get("nearBottom")),
                        "blocked": blocked,
                    }
                )
                if count and bool((scroll_state or {}).get("nearBottom")) and stable_ticks >= 3:
                    break
                if count and best_next_page and stable_ticks >= 4:
                    break
            except Exception as exc:
                events.append({"event": "scroll_poll_error", "error": f"{type(exc).__name__}: {exc}"})
            time.sleep(1.4)

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
