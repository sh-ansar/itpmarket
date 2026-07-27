from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIRED = ("flask", "waitress", "playwright", "psutil", "werkzeug")


def main() -> int:
    missing = [name for name in REQUIRED if importlib.util.find_spec(name) is None]
    if missing:
        print("Не установлены библиотеки: " + ", ".join(missing))
        return 2

    browsers = ROOT / ".playwright"
    browsers.mkdir(parents=True, exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    try:
        version = metadata.version("playwright")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
    except Exception as exc:
        print(f"Не удалось проверить Playwright: {exc}")
        return 3

    sentinel = browsers / f".ready_{version}"
    if not executable.exists():
        print(f"Chromium для Playwright {version} не найден. Выполняется установка...")
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            cwd=ROOT,
            env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(browsers)},
        )
        if result.returncode != 0:
            return result.returncode or 4
        try:
            with sync_playwright() as playwright:
                executable = Path(playwright.chromium.executable_path)
        except Exception as exc:
            print(f"Повторная проверка Playwright не выполнена: {exc}")
            return 4
        if not executable.exists():
            print(f"Chromium не найден после установки: {executable}")
            return 4
    sentinel.write_text(str(executable), encoding="utf-8")
    print(f"Окружение готово. Playwright {version}; Chromium: {executable.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
