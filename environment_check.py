from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SUPPORTED_PYTHON = {(3, 10), (3, 11)}
REQUIRED = (
    "certifi",
    "cryptography",
    "flask",
    "playwright",
    "psutil",
    "psycopg",
    "selenium",
    "selenium_stealth",
    "waitress",
    "werkzeug",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the Spyon Python/browser runtime.")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="verify prerequisites without installing browsers or writing a sentinel",
    )
    args = parser.parse_args(argv)

    if sys.version_info[:2] not in SUPPORTED_PYTHON:
        supported = ", ".join(
            f"{major}.{minor}" for major, minor in sorted(SUPPORTED_PYTHON)
        )
        print(
            f"Неподдерживаемая версия Python {sys.version_info.major}.{sys.version_info.minor}. "
            f"Используйте Python {supported}."
        )
        return 1

    missing = [name for name in REQUIRED if importlib.util.find_spec(name) is None]
    if missing:
        print("Не установлены библиотеки: " + ", ".join(missing))
        return 2

    browsers = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or ROOT / ".playwright")
    if not args.check_only:
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
        if args.check_only:
            print(
                f"Chromium для Playwright {version} не найден: {executable}. "
                "Запустите python -m playwright install chromium."
            )
            return 4
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
    if not args.check_only:
        sentinel.write_text(str(executable), encoding="utf-8")
    print(f"Окружение готово. Playwright {version}; Chromium: {executable.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
