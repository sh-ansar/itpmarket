from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any



PROGRESS_PREFIX = "SPYON_PROGRESS "

def emit_progress(
    *,
    label: str,
    step: int,
    total: int,
    completed: int,
    state: str,
) -> None:
    payload = {
        "phase": "workflow",
        "phase_label": label,
        "phase_current": step,
        "phase_total": total,
        "current": completed,
        "total": total,
        "percent": (
            round(
                completed / total * 100,
                2,
            )
            if total > 0
            else None
        ),
        "message": label,
        "state": state,
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    print(
        PROGRESS_PREFIX
        + json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )


def load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("steps"), list):
        raise ValueError("Некорректный файл рабочего процесса.")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Spyon workflow runner")
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    path = Path(args.manifest).resolve()
    manifest = load_manifest(path)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass

    steps = [item for item in manifest.get("steps", []) if isinstance(item, dict)]
    cleanup_files = [Path(str(value)).resolve() for value in manifest.get("cleanup_files", []) if str(value).strip()]
    if not steps:
        print("В рабочем процессе нет этапов.", flush=True)
        return 2

    active: subprocess.Popen[str] | None = None

    def stop_handler(signum: int, frame: Any) -> None:
        nonlocal active
        if active and active.poll() is None:
            try:
                if os.name == "nt":
                    active.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    active.send_signal(signal.SIGINT)
            except Exception:
                try:
                    active.terminate()
                except Exception:
                    pass
        raise KeyboardInterrupt

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop_handler)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, stop_handler)
    if os.name == "nt" and hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, stop_handler)

    total = len(steps)
    try:
        for index, step in enumerate(steps, start=1):
            command = step.get("command")
            if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
                print(f"[ЭТАП {index}/{total}] Некорректная команда.", flush=True)
                return 2
            label = str(step.get("label") or f"Этап {index}")
            emit_progress(
                label=label,
                step=index,
                total=total,
                completed=index - 1,
                state="running",
            )
            print(f"[ЭТАП {index}/{total}] {label}", flush=True)
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            kwargs: dict[str, Any] = {}
            if os.name != "nt":
                kwargs["start_new_session"] = True
            active = subprocess.Popen(
                command,
                cwd=str(Path.cwd()),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
                env=os.environ.copy(),
                **kwargs,
            )
            assert active.stdout is not None
            for line in active.stdout:
                print(line, end="", flush=True)
            code = active.wait()
            if code != 0:
                print(f"[ЭТАП {index}/{total}] Ошибка, код {code}.", flush=True)
                emit_progress(
                    label=label,
                    step=index,
                    total=total,
                    completed=index - 1,
                    state="failed",
                )
                return int(code or 1)

            emit_progress(
                label=label,
                step=index,
                total=total,
                completed=index,
                state="completed",
            )
            print(f"[ЭТАП {index}/{total}] Завершено.", flush=True)
        print(f"Готово: {total}/{total} этапов.", flush=True)
        return 0
    except KeyboardInterrupt:
        print("Рабочий процесс остановлен пользователем.", flush=True)
        return 130
    finally:
        if active and active.poll() is None:
            try:
                active.terminate()
            except Exception:
                pass
        for cleanup_path in cleanup_files:
            try:
                cleanup_path.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
