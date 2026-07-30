from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

PROGRESS_PATTERNS = (
    # [ПОЗИЦИЯ 22/30] ... 80/100 R21 -> use 22/30, never the tyre size.
    re.compile(r"^\s*\[[^\]]*?(\d{1,7})\s*/\s*(\d{1,7})[^\]]*\]", re.IGNORECASE),
    # [Каталог] Segment: 2/14 or [Экспорт] 1/4.
    re.compile(r"^\s*\[[^\]]+\]\s*.*?(\d{1,7})\s*/\s*(\d{1,7})(?!\d)", re.IGNORECASE),
    # Unbracketed final summaries such as "Готово: 30/30".
    re.compile(
        r"\b(?:готово|выполнено|обработано|позици(?:я|и|й)|страниц(?:а|ы)?|"
        r"источник(?:а|ов)?|сегмент(?:а|ов)?|прогресс)\b[^\r\n]{0,80}?"
        r"(\d{1,7})\s*/\s*(\d{1,7})(?!\d)",
        re.IGNORECASE,
    ),
)
PERCENT_RE = re.compile(
    r"\b(?:прогресс|готово|выполнено|обработано)\b[^\r\n]{0,80}?"
    r"(\d{1,3}(?:[.,]\d+)?)\s*%",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class TaskManager:
    """Runs background commands with resource locks and persistent task history."""

    def __init__(self, root: Path, logs_dir: Path, state_path: Path, max_parallel: int = 3):
        self.root = root
        self.logs_dir = logs_dir
        self.state_path = state_path
        self.max_parallel = max(1, int(max_parallel))
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.log_handles: dict[str, Any] = {}
        self._normalize_state()

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"tasks": []}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and isinstance(value.get("tasks"), list):
                return value
        except Exception:
            pass
        return {"tasks": []}

    def _save(self, state: dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if not pid:
            return False
        try:
            process = psutil.Process(int(pid))
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            return False

    def _normalize_state(self) -> None:
        with self.lock:
            state = self._load()
            changed = False
            for task in state.get("tasks", []):
                if task.get("status") == "running" and not self._pid_alive(task.get("pid")):
                    task["status"] = "interrupted"
                    task["running"] = False
                    task["finished_at"] = now_iso()
                    task["message"] = "Процесс был прерван при завершении приложения."
                    changed = True
            state["tasks"] = sorted(
                state.get("tasks", []), key=lambda item: item.get("started_at") or "", reverse=True
            )[:150]
            if changed:
                self._save(state)

    @staticmethod
    def _read_tail(path: Path, lines: int = 250) -> str:
        if not path.exists():
            return ""
        try:
            raw = path.read_bytes()
            # New processes always write UTF-8. Only use legacy Windows
            # encodings when UTF-8 decoding is genuinely impossible.
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                candidates: list[tuple[int, str]] = []
                for encoding in ("cp1251", "cp866"):
                    try:
                        candidate = raw.decode(encoding)
                    except UnicodeDecodeError:
                        continue
                    penalty = candidate.count("�") * 20 + candidate.count("����") * 30
                    cyrillic = sum(
                        1 for char in candidate if "А" <= char <= "я" or char in "Ёё"
                    )
                    candidates.append((penalty - min(cyrillic, 5000), candidate))
                text = (
                    min(candidates, key=lambda item: item[0])[1]
                    if candidates
                    else raw.decode("utf-8", errors="replace")
                )
            content = text.splitlines(keepends=True)
            return "".join(content[-max(1, min(int(lines), 3000)):])
        except OSError:
            return ""

    def _enrich(self, task: dict[str, Any]) -> dict[str, Any]:
        result = dict(task)
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        result["metadata"] = metadata
        result["platform"] = metadata.get("platform") or result.get("platform") or "system"
        raw = result.get("log_file")
        log_path = Path(raw) if raw else None
        text = self._read_tail(log_path, 220) if log_path else ""
        progress: dict[str, Any] | None = None
        for line in reversed(text.splitlines()):
            for pattern in PROGRESS_PATTERNS:
                match = pattern.search(line)
                if not match:
                    continue
                current, total = int(match.group(1)), int(match.group(2))
                if total > 0 and 0 <= current <= total:
                    progress = {
                        "current": current,
                        "total": total,
                        "percent": round(current / total * 100, 2),
                    }
                    break
            if progress is not None:
                break
        if progress is None:
            percentages = PERCENT_RE.findall(text)
            if percentages:
                value = float(percentages[-1].replace(",", "."))
                if 0 <= value <= 100:
                    progress = {"current": None, "total": None, "percent": round(value, 2)}
        if result.get("status") == "completed":
            if progress and progress.get("total"):
                progress["current"] = progress["total"]
                progress["percent"] = 100.0
            else:
                progress = {"current": None, "total": None, "percent": 100.0}
        result["progress"] = progress
        result["last_line"] = next(
            (line.strip()[-700:] for line in reversed(text.splitlines()) if line.strip()), ""
        )
        result["running"] = result.get("status") == "running" and self._pid_alive(result.get("pid"))
        if log_path and log_path.exists():
            try:
                result["updated_at"] = datetime.fromtimestamp(
                    log_path.stat().st_mtime
                ).astimezone().isoformat(timespec="seconds")
            except OSError:
                result["updated_at"] = result.get("finished_at") or result.get("started_at")
        else:
            result["updated_at"] = result.get("finished_at") or result.get("started_at")
        return result

    def states(self) -> list[dict[str, Any]]:
        self._normalize_state()
        return [self._enrich(task) for task in self._load().get("tasks", [])]

    def running(self) -> list[dict[str, Any]]:
        return [task for task in self.states() if task.get("running")]

    def state(self, task_id: str) -> dict[str, Any]:
        return next(
            (task for task in self.states() if task.get("id") == task_id),
            {"id": task_id, "status": "missing", "running": False},
        )

    def start(
        self,
        name: str,
        label: str,
        command: list[str],
        resources: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resources_value = sorted({str(item) for item in (resources or []) if str(item).strip()})
        with self.lock:
            active = self.running()
            if len(active) >= self.max_parallel:
                raise RuntimeError(f"Одновременно можно выполнять не более {self.max_parallel} операций.")
            used = {resource for task in active for resource in task.get("resources", [])}
            conflict = sorted(set(resources_value) & used)
            if conflict:
                owner = next(
                    (task for task in active if set(task.get("resources", [])) & set(conflict)), None
                )
                raise RuntimeError(
                    f"Сейчас выполняется «{(owner or {}).get('label') or 'другая операция'}». "
                    "Операции, использующие один браузерный профиль площадки, запускаются последовательно."
                )

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            task_id = f"{name}_{stamp}_{uuid.uuid4().hex[:7]}"
            log_path = self.logs_dir / f"{stamp}_{name}_{task_id[-7:]}.log"
            log_handle = log_path.open("ab", buffering=0)
            kwargs: dict[str, Any] = {}
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            child_env = os.environ.copy()
            child_env["PYTHONUTF8"] = "1"
            child_env["PYTHONIOENCODING"] = "utf-8"
            child_env["PYTHONUNBUFFERED"] = "1"
            process = subprocess.Popen(
                command,
                cwd=self.root,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                env=child_env,
                **kwargs,
            )
            task = {
                "id": task_id,
                "name": name,
                "label": label,
                "status": "running",
                "running": True,
                "pid": process.pid,
                "command": command,
                "resources": resources_value,
                "metadata": metadata or {},
                "log_file": str(log_path),
                "started_at": now_iso(),
                "finished_at": None,
                "exit_code": None,
                "message": "Операция запущена",
            }
            state = self._load()
            state.setdefault("tasks", []).insert(0, task)
            state["tasks"] = state["tasks"][:150]
            self._save(state)
            self.processes[task_id] = process
            self.log_handles[task_id] = log_handle
            threading.Thread(target=self._watch, args=(task_id, process), daemon=True).start()
            return self._enrich(task)

    def _watch(self, task_id: str, process: subprocess.Popen[bytes]) -> None:
        code = process.wait()
        with self.lock:
            state = self._load()
            task = next((item for item in state.get("tasks", []) if item.get("id") == task_id), None)
            if task is None:
                return
            stop_requested = bool(task.get("stop_requested_at")) or task.get("status") == "stopped"
            task["status"] = (
                "stopped"
                if stop_requested or code == 130
                else "completed"
                if code == 0
                else "failed"
            )
            task["running"] = False
            task["exit_code"] = code
            task["finished_at"] = now_iso()
            task["message"] = (
                "Операция остановлена. Уже сохранённые позиции не потеряны."
                if task["status"] == "stopped"
                else "Операция завершена"
                if task["status"] == "completed"
                else f"Операция завершилась с кодом {code}"
            )
            self._save(state)
            handle = self.log_handles.pop(task_id, None)
            try:
                if handle:
                    handle.close()
            finally:
                self.processes.pop(task_id, None)

    def stop(self, task_id: str) -> dict[str, Any]:
        with self.lock:
            task = self.state(task_id)
            if not task.get("running") or not task.get("pid"):
                return task
            pid = int(task["pid"])
            state = self._load()
            stored = next((item for item in state.get("tasks", []) if item.get("id") == task_id), None)
            if stored:
                stored["stop_requested_at"] = now_iso()
                stored["message"] = "Выполняется безопасная остановка"
                self._save(state)

        try:
            process = psutil.Process(pid)
            children = process.children(recursive=True)
            managed = self.processes.get(task_id)
            if os.name == "nt" and managed and managed.poll() is None:
                try:
                    managed.send_signal(signal.CTRL_BREAK_EVENT)
                    managed.wait(timeout=20)
                except Exception:
                    pass
            else:
                try:
                    if os.name == "nt":
                        process.terminate()
                    else:
                        os.killpg(os.getpgid(pid), signal.SIGINT)
                except Exception:
                    pass
            _, alive = psutil.wait_procs([process, *children], timeout=20)
            for item in alive:
                try:
                    item.terminate()
                except Exception:
                    pass
            _, alive = psutil.wait_procs(alive, timeout=8)
            for item in alive:
                try:
                    item.kill()
                except Exception:
                    pass
        except psutil.NoSuchProcess:
            pass

        with self.lock:
            state = self._load()
            stored = next((item for item in state.get("tasks", []) if item.get("id") == task_id), None)
            if stored:
                stored["status"] = "stopped"
                stored["running"] = False
                stored["finished_at"] = now_iso()
                stored["message"] = "Операция остановлена. Уже сохранённые позиции не потеряны."
                self._save(state)
                return self._enrich(stored)
        return {"id": task_id, "status": "stopped", "running": False}

    def stop_all(self) -> list[dict[str, Any]]:
        return [self.stop(str(task["id"])) for task in list(self.running())]


    def delete(self, task_id: str, delete_log: bool = True) -> dict[str, Any]:
        with self.lock:
            current = self.state(task_id)
            if current.get("running"):
                raise RuntimeError("Сначала остановите выполняемую операцию.")
            state = self._load()
            task = next((item for item in state.get("tasks", []) if item.get("id") == task_id), None)
            if task is None:
                return {"id": task_id, "status": "missing", "running": False}
            state["tasks"] = [item for item in state.get("tasks", []) if item.get("id") != task_id]
            self._save(state)
            if delete_log and task.get("log_file"):
                try:
                    Path(str(task["log_file"])).unlink(missing_ok=True)
                except OSError:
                    pass
            return {"id": task_id, "status": "deleted", "running": False}

    def clear_finished(self, delete_logs: bool = True) -> int:
        with self.lock:
            state = self._load()
            active, removed = [], []
            for task in state.get("tasks", []):
                if task.get("status") == "running" and self._pid_alive(task.get("pid")):
                    active.append(task)
                else:
                    removed.append(task)
            state["tasks"] = active
            self._save(state)
            if delete_logs:
                for task in removed:
                    try:
                        if task.get("log_file"):
                            Path(str(task["log_file"])).unlink(missing_ok=True)
                    except OSError:
                        pass
            return len(removed)

    def tail(self, task_id: str, lines: int = 300) -> str:
        task = self.state(task_id)
        raw = task.get("log_file")
        return self._read_tail(Path(raw), lines) if raw else ""
