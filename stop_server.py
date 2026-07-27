from __future__ import annotations

import sys
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent
PID_PATH = ROOT / "data" / "server.pid"


def main() -> int:
    if not PID_PATH.exists():
        print("Сервер не запущен или PID-файл отсутствует.")
        return 0
    try:
        pid = int(PID_PATH.read_text(encoding="ascii").strip())
        process = psutil.Process(pid)
    except (ValueError, psutil.NoSuchProcess):
        PID_PATH.unlink(missing_ok=True)
        print("Процесс уже завершён.")
        return 0
    children = process.children(recursive=True)
    for item in children:
        try:
            item.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    try:
        process.terminate()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    _, alive = psutil.wait_procs([*children, process], timeout=12)
    for item in alive:
        try:
            item.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    PID_PATH.unlink(missing_ok=True)
    print("ITP Market Intelligence остановлен.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
