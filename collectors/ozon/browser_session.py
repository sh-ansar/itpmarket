#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from ozon_probe_core import parse_catalog_html


class BrowserSession:
    def __init__(self, debug_port: int, start_url: str) -> None:
        self.debug_port = debug_port
        self.start_url = start_url
        self.debug_base = f"http://127.0.0.1:{debug_port}"
        self.driver = None
        self.target_id = ""
        self.original_url = ""

    def debugger_json(self, path: str, timeout: int = 8) -> Any:
        request = urllib.request.Request(
            f"{self.debug_base}{path}",
            headers={"User-Agent": "Unityre-Ozon-Collector/3.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.URLError as exc:
            if path == "/json/list":
                raise RuntimeError(
                    "Ozon debug-браузер недоступен на 127.0.0.1:"
                    f"{self.debug_port}. Запустите collectors\\ozon\\1_OPEN_VPN_BROWSER.bat, "
                    "откройте Ozon через VPN и оставьте это окно Chrome открытым."
                ) from exc
            raise

    @staticmethod
    def _is_ozon_page(url: str) -> bool:
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        host = parsed.netloc.lower().split(":")[0]
        return host in {"ozon.ru", "www.ozon.ru"} and "/api/" not in parsed.path

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
                "Не найдена открытая вкладка Ozon. Запустите 1_OPEN_VPN_BROWSER.bat."
            )
        candidates.sort(
            key=lambda row: (int(row.get("_score") or 0), len(str(row.get("title") or ""))),
            reverse=True,
        )
        return candidates[0]

    def connect(self) -> "BrowserSession":
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
                "ChromeDriver для Ozon не найден или не подходит к установленному Chrome. "
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
        raise RuntimeError("Не удалось переключиться на рабочую вкладку Ozon.")

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
                events.append({"event": "get_error", "error": f"{type(exc).__name__}: {exc}"})
        for attempt in range(reloads + 1):
            deadline = time.monotonic() + wait_seconds
            saw_block = False
            while time.monotonic() < deadline:
                try:
                    title, text, page_html = self.snapshot()
                    last_title, last_text, last_html = title, text, page_html
                    products, next_page = parse_catalog_html(page_html, url)
                    if products:
                        return {
                            "ok": True,
                            "status": "CATALOG_OK",
                            "products": products,
                            "next_page": next_page,
                            "html": page_html,
                            "title": title,
                            "elapsed_ms": round((time.monotonic() - started) * 1000),
                            "events": events,
                        }
                    blocked = self.blocked_state(title, text, page_html)
                    saw_block = saw_block or blocked
                    events.append({"event": "poll", "attempt": attempt + 1, "blocked": blocked})
                except Exception as exc:
                    events.append({"event": "poll_error", "error": f"{type(exc).__name__}: {exc}"})
                time.sleep(2.5)
            if attempt < reloads:
                try:
                    self.driver.refresh()
                except Exception as exc:
                    events.append({"event": "refresh_error", "error": f"{type(exc).__name__}: {exc}"})
                time.sleep(15 if saw_block else 8)
        status = "BLOCKED_AFTER_RETRIES" if self.blocked_state(last_title, last_text, last_html) else "NO_CATALOG"
        return {
            "ok": False,
            "status": status,
            "title": last_title,
            "html": last_html,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "events": events,
        }

    def load_product_api(self, article: str, wait_seconds: int, reloads: int) -> dict[str, Any]:
        assert self.driver is not None
        url = (
            "https://www.ozon.ru/api/composer-api.bx/page/json/v2"
            f"?url=/product/{article}&__rr=1"
        )
        started = time.monotonic()
        events: list[dict[str, Any]] = []
        last_title = last_text = last_html = ""
        try:
            self.driver.get(url)
        except Exception as exc:
            events.append({"event": "get_error", "error": f"{type(exc).__name__}: {exc}"})
        for attempt in range(reloads + 1):
            deadline = time.monotonic() + wait_seconds
            saw_block = False
            while time.monotonic() < deadline:
                try:
                    data, title, text, page_html = self._extract_json()
                    last_title, last_text, last_html = title, text, page_html
                    if data is not None:
                        return {
                            "ok": True,
                            "status": "JSON_OK",
                            "json": data,
                            "url": url,
                            "title": title,
                            "elapsed_ms": round((time.monotonic() - started) * 1000),
                            "events": events,
                        }
                    blocked = self.blocked_state(title, text, page_html)
                    saw_block = saw_block or blocked
                    events.append({"event": "poll", "attempt": attempt + 1, "blocked": blocked})
                except Exception as exc:
                    events.append({"event": "poll_error", "error": f"{type(exc).__name__}: {exc}"})
                time.sleep(2.5)
            if attempt < reloads:
                try:
                    self.driver.refresh()
                except Exception as exc:
                    events.append({"event": "refresh_error", "error": f"{type(exc).__name__}: {exc}"})
                time.sleep(15 if saw_block else 8)
        status = "BLOCKED_AFTER_RETRIES" if self.blocked_state(last_title, last_text, last_html) else "NO_JSON"
        return {
            "ok": False,
            "status": status,
            "url": url,
            "title": last_title,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
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
