from __future__ import annotations

import json
import py_compile
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from collectors.ozon.registry import Registry
from config import load_config, resolve_path
from data_service import DataService
from engine.simple_xlsx import write_xlsx

ROOT = Path(__file__).resolve().parent
CFG = load_config()
DB_PATH = resolve_path(CFG, "database")
OZON_DB_PATH = ROOT / "collectors" / "ozon" / "data" / "ozon_registry.db"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def service(db_path: Path = DB_PATH, ozon_path: Path | None = OZON_DB_PATH) -> DataService:
    return DataService(
        db_path,
        str(CFG["kaspi"]["seller_name"]),
        ozon_path if ozon_path and ozon_path.exists() else None,
        seller_id=str(CFG["kaspi"]["seller_id"]),
        halyk_seller_name=str(CFG["halyk"]["seller_name"]),
    )


def test_sources() -> None:
    files = [
        "app.py", "data_service.py", "saas_service.py",
        "engine/export_market_intelligence.py", "engine/simple_xlsx.py",
        "engine/workflow_runner.py", "collectors/ozon/ozon_collector.py",
        "collectors/ozon/registry.py",
    ]
    for name in files:
        py_compile.compile(str(ROOT / name), doraise=True)

    js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "app.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
    for marker in (
        "kaspi_catalog_collect", "ozon_price_actualize", "halyk_full_sync",
        "characteristicGroupFilter", "reportPlatforms", "reportPreviewBody",
        "reportFiltersPayload", "По текущему фильтру",
    ):
        require(marker in js or marker in html, f"Не найден элемент 3.4.13: {marker}")
    require(".filters.has-reset" in css, "Не найдено динамическое размещение кнопки сброса")

    node = shutil.which("node")
    if node:
        result = subprocess.run([node, "--check", str(ROOT / "static" / "js" / "app.js")], capture_output=True, text=True)
        require(result.returncode == 0, result.stderr or result.stdout)


def test_characteristics_and_filters() -> tuple[DataService, list[str]]:
    require(DB_PATH.exists(), f"Не найдена база: {DB_PATH}")
    data = service()
    options = data.filter_options({"kaspi", "ozon", "halyk_market"})
    groups = options.get("characteristic_groups") or []
    require(groups, "Общие группы характеристик не сформированы")
    group = next((item for item in groups if int(item.get("platform_count") or 0) == 3), None)
    require(group is not None, "Не найдена группа, представленная на трёх площадках")

    result = data.products(1, 200, {
        "platforms": ["kaspi", "ozon", "halyk_market"],
        "characteristic_group": group["value"],
        "scope": "all",
    })
    require(result["total"] > 0, "Фильтр общей группы вернул пустой результат")
    require({row["characteristic_group"] for row in result["items"]} == {group["value"]}, "Фильтр группы пропускает чужие позиции")
    require({row["platform"] for row in result["items"]} == {"kaspi", "ozon", "halyk_market"}, "Группа не покрывает три площадки")

    multi = data.products(1, 20, {"platforms": ["ozon", "halyk_market"], "scope": "all"})
    require(multi["total"] > 0, "Мультивыбор площадок не работает")
    require({row["platform"] for row in multi["items"]} <= {"ozon", "halyk_market"}, "Мультифильтр площадок пропускает Kaspi")

    codes: list[str] = []
    rows = data.rows()
    for platform in ("kaspi", "ozon", "halyk_market"):
        codes.append(next(str(row["product_code"]) for row in rows if row.get("platform") == platform))
    return data, codes


def test_ozon_selection() -> None:
    require(OZON_DB_PATH.exists(), f"Не найдена Ozon DB: {OZON_DB_PATH}")
    registry = Registry(OZON_DB_PATH)
    try:
        rows = registry.select_articles("refresh-prices", 0)
        require(len(rows) >= 2, "Недостаточно Ozon-позиций для теста")
        allowed = set(rows[1:3])
        selected = registry.select_articles("refresh-prices", 100, allowed_articles=allowed)
        require(set(selected) == allowed, "Ozon-фильтр article обработал лишние товары")
    finally:
        registry.close()


def test_workflow() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        cleanup = temp / "cleanup.json"
        manifest = temp / "manifest.json"
        cleanup.write_text("{}", encoding="utf-8")
        manifest.write_text(json.dumps({
            "steps": [
                {"label": "Первый", "command": [sys.executable, "-c", "print('one')"]},
                {"label": "Второй", "command": [sys.executable, "-c", "print('two')"]},
            ],
            "cleanup_files": [str(cleanup)],
        }, ensure_ascii=False), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(ROOT / "engine" / "workflow_runner.py"), "--manifest", str(manifest)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        require(result.returncode == 0, result.stderr or result.stdout)
        require("1/2" in result.stdout and "2/2" in result.stdout, "Прогресс рабочего процесса не выводится")
        require(not manifest.exists() and not cleanup.exists(), "Временные файлы workflow не удалены")


def test_excel_export(codes: list[str]) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        direct = temp / "direct.xlsx"
        write_xlsx(direct, ["Площадка", "Размер"], [["Kaspi", "225/45 R17"]], sheet_name="Товары")
        with zipfile.ZipFile(direct) as archive:
            require("xl/worksheets/sheet1.xml" in archive.namelist(), "XLSX имеет некорректную структуру")

        db_copy = temp / "test.db"
        ozon_copy = temp / "ozon.db"
        output = temp / "output"
        selection = temp / "selection.json"
        shutil.copy2(DB_PATH, db_copy)
        shutil.copy2(OZON_DB_PATH, ozon_copy)
        selection.write_text(json.dumps({"scope": "filtered", "codes": codes}, ensure_ascii=False), encoding="utf-8")
        command = [
            sys.executable, str(ROOT / "engine" / "export_market_intelligence.py"),
            "--db", str(db_copy), "--ozon-db", str(ozon_copy), "--output", str(output),
            "--seller-name", str(CFG["kaspi"]["seller_name"]),
            "--seller-id", str(CFG["kaspi"]["seller_id"]),
            "--user-id", "1", "--selection-file", str(selection),
        ]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        require(result.returncode == 0, result.stderr or result.stdout)
        xlsx = next(output.glob("*.xlsx"), None)
        require(xlsx is not None, "Excel-отчёт не создан")
        with zipfile.ZipFile(xlsx) as archive:
            require("xl/worksheets/sheet1.xml" in archive.namelist(), "Сформированный Excel повреждён")
        conn = sqlite3.connect(db_copy)
        try:
            row = conn.execute("SELECT rows_count FROM app_reports WHERE report_type='market_intelligence_xlsx' ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            conn.close()
        require(row is not None and int(row[0]) == len(codes), "Excel зарегистрирован с неверным количеством строк")


def main() -> int:
    print("[1/5] Проверка исходников")
    test_sources()
    print("[2/5] Нормализация характеристик и фильтры")
    _, codes = test_characteristics_and_filters()
    print("[3/5] Фильтрованные операции Ozon")
    test_ozon_selection()
    print("[4/5] Составные операции")
    test_workflow()
    print("[5/5] Excel-отчёт")
    test_excel_export(codes)
    print("SELF TEST 3.4.13: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
