from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Безопасная резервная копия SQLite")
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main(args: argparse.Namespace) -> int:
    source = Path(args.db)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        print("[Резервная копия] База данных пока не создана.")
        return 1
    target = output / f"unityre_kaspi_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    src = sqlite3.connect(source, timeout=60)
    dst = sqlite3.connect(target)
    try:
        print("[Резервная копия] 1/2 Чтение базы")
        src.backup(dst)
        print("[Резервная копия] 2/2 Проверка")
        result = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"integrity_check: {result}")
        dst.commit()
    finally:
        dst.close()
        src.close()
    print(f"[Резервная копия] Готово: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(parse_args()))
