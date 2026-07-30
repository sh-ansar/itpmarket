from __future__ import annotations

import csv
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from data_service import DataService
from task_manager import TaskManager

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "unityre_kaspi.db"
OZON_DB_PATH = ROOT / "collectors" / "ozon" / "data" / "ozon_registry.db"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_frontend_sources() -> None:
    js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
    require("taskPlatform(task)" in js, "Карточки не используют нормализованную площадку задачи")
    require("data-task-ids" in js and "stopTasks(ids)" in js, "Переключение на остановку не найдено")
    require("operation-card-progress" in js and "operation-progress-track" in css, "Прогресс-бар не найден")
    require("baseQ.set('page_size','200')" in js and "do{" in js and "page<=pages" in js, "Постраничный экспорт не найден")
    require("potential_margin_monthly_kzt" in js and "risk" in js and "review" in js, "Расширенные поля отчёта не найдены")


def service() -> DataService:
    require(DB_PATH.exists(), f"Не найдена база: {DB_PATH}")
    return DataService(
        DB_PATH,
        "Unityre",
        OZON_DB_PATH if OZON_DB_PATH.exists() else None,
        seller_id="Unityre",
    )


def test_filters_and_pagination() -> None:
    data = service()
    all_result = data.products(1, 100000, {"scope": "all"})
    all_codes = data.product_codes({"scope": "all"})
    require(all_result["total"] == len(all_codes), "Количество товаров и кодов не совпадает")
    require(all_result["page_size"] == 200, "Серверный лимит страницы должен оставаться 200")
    if all_result["total"] > 200:
        require(all_result["pages"] > 1, "Каталог больше 200 строк должен иметь несколько страниц")
        require(len(all_result["items"]) == 200, "Первая страница должна содержать 200 строк")
    for scope in ("risks", "opportunities", "unscanned", "watched"):
        result = data.products(1, 200, {"scope": scope})
        codes = data.product_codes({"scope": scope})
        require(result["total"] == len(codes), f"Фильтр {scope} возвращает неполный набор")


def test_task_progress_and_stop() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        manager = TaskManager(root, root / "logs", root / "tasks.json", max_parallel=2)
        task = manager.start(
            "demo",
            "Demo",
            [
                sys.executable,
                "-c",
                'import time; print("[Демо] 1/4", flush=True); time.sleep(0.2); '
                'print("[Демо] 2/4", flush=True); time.sleep(30)',
            ],
            metadata={"platform": "ozon"},
        )
        deadline = time.time() + 5
        current: dict = {}
        while time.time() < deadline:
            current = manager.state(task["id"])
            if int((current.get("progress") or {}).get("current") or 0) >= 2:
                break
            time.sleep(0.1)
        require(current.get("platform") == "ozon", "Площадка не перенесена из metadata")
        require(bool(current.get("running")), "Тестовая операция не запущена")
        require(int((current.get("progress") or {}).get("current") or 0) >= 2, "Прогресс из журнала не распознан")

        tyre_log = root / "logs" / "tyre.log"
        tyre_log.write_text(
            "[ПОЗИЦИЯ 22/30] MICHELIN STARCROSS 80/100 R21\n"
            "Поиск 1/2: MICHELIN 80/100 R21\n",
            encoding="utf-8",
        )
        parsed = manager._enrich({
            "id": "tyre",
            "name": "ozon_market_search",
            "status": "running",
            "pid": task["pid"],
            "metadata": {"platform": "ozon"},
            "log_file": str(tyre_log),
        })
        require(
            (parsed.get("progress") or {}).get("current") == 22
            and (parsed.get("progress") or {}).get("total") == 30,
            "Размер шины ошибочно распознан как прогресс",
        )
        manager.stop(task["id"])
        time.sleep(0.5)
        final = manager.state(task["id"])
        require(final.get("status") == "stopped", "Остановленная операция ошибочно получила другой статус")
        require(not final.get("running"), "Остановленная операция всё ещё отмечена активной")


def test_filtered_report() -> None:
    data = service()
    codes = data.product_codes({"scope": "unscanned"})[:5] or data.product_codes({"scope": "all"})[:5]
    require(bool(codes), "В базе нет товаров для теста отчёта")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        db_copy = temp / "test.db"
        ozon_copy = temp / "ozon.db"
        output = temp / "output"
        selection = temp / "selection.json"
        shutil.copy2(DB_PATH, db_copy)
        ozon_arg: list[str] = []
        if OZON_DB_PATH.exists():
            shutil.copy2(OZON_DB_PATH, ozon_copy)
            ozon_arg = ["--ozon-db", str(ozon_copy)]
        selection.write_text(
            json.dumps({"scope": "filtered", "codes": codes}, ensure_ascii=False),
            encoding="utf-8",
        )
        command = [
            sys.executable,
            str(ROOT / "engine" / "export_market_intelligence.py"),
            "--db",
            str(db_copy),
            *ozon_arg,
            "--output",
            str(output),
            "--seller-name",
            "Unityre",
            "--seller-id",
            "Unityre",
            "--selection-file",
            str(selection),
        ]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=90)
        require(completed.returncode == 0, completed.stderr or completed.stdout)
        require(not selection.exists(), "Временный файл выборки не удалён")
        csv_path = next(output.glob("*.csv"), None)
        require(csv_path is not None, "CSV-отчёт не сформирован")
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        require(len(rows) == len(codes), "Отчёт содержит не все выбранные позиции")
        for field in (
            "risk_flag",
            "review_required",
            "opportunity_flag",
            "price_rank",
            "potential_margin_monthly_kzt",
            "freshness_status",
            "watched",
            "priority",
            "note",
        ):
            require(field in rows[0], f"В отчёте отсутствует поле {field}")
        connection = sqlite3.connect(db_copy)
        try:
            saved = connection.execute(
                "SELECT scope,rows_count FROM app_reports ORDER BY id DESC LIMIT 2"
            ).fetchall()
        finally:
            connection.close()
        require(
            len(saved) == 2 and all(scope == "filtered" and count == len(codes) for scope, count in saved),
            "Метаданные отфильтрованного отчёта сохранены неверно",
        )


def main() -> int:
    tests = [
        ("frontend", test_frontend_sources),
        ("filters", test_filters_and_pagination),
        ("tasks", test_task_progress_and_stop),
        ("report", test_filtered_report),
    ]
    for name, function in tests:
        print(f"[TEST] {name} ...", flush=True)
        function()
        print(f"[OK]   {name}", flush=True)
    print("HOTFIX 3.4.12: ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
