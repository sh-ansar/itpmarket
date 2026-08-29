from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import uuid
from contextlib import contextmanager
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


RESULT_PREFIX = "SPYON_RESULT "
RESULT_MESSAGES = {
    "ozon_challenge": "Ozon требует подтверждения. Откройте окно Ozon, пройдите проверку и повторите синхронизацию.",
    "browser_not_open": "Браузер Ozon не открыт. Откройте браузер Ozon и повторите синхронизацию.",
    "browser_hidden_session": "Браузер Ozon запущен в фоновой сессии Windows. Откройте интерактивный браузер Ozon и повторите синхронизацию.",
    "browser_debug_unavailable": "Ozon браузер не открыт. Откройте браузер Ozon и повторите синхронизацию.",
    "chromedriver_unavailable": "Не удалось подключиться к браузеру Ozon. Обратитесь к администратору.",
    "network_error": "Не удалось подключиться к Ozon. Повторите попытку.",
    "collector_failed": "Синхронизацию завершить не удалось. Повторите попытку или обратитесь к администратору.",
    "partial_success": 'Синхронизация завершена частично. Успешные позиции сохранены; отдельные позиции требуют повторной обработки.',
}


def structured_result(log_text: str) -> dict[str, Any] | None:
    for line in reversed(str(log_text or "").splitlines()):
        if not line.startswith(RESULT_PREFIX):
            continue
        try:
            value = json.loads(line[len(RESULT_PREFIX):])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("reason"), str):
            return value
    return None


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
        self._guard_local = threading.local()
        self._state_lock_path = self.state_path.with_suffix(
            self.state_path.suffix + ".lock"
        )
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.log_handles: dict[str, Any] = {}
        self._enrich_cache: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
        self._normalize_state()
        self._recover_running_tasks()

    @contextmanager
    def _state_guard(self):
        """Serialize task-state read/modify/write across threads and processes."""
        with self.lock:
            depth = int(getattr(self._guard_local, "depth", 0))
            handle = None
            if depth == 0:
                self._state_lock_path.parent.mkdir(parents=True, exist_ok=True)
                handle = self._state_lock_path.open("a+b")
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:  # pragma: no cover - production target is Windows
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            self._guard_local.depth = depth + 1
            try:
                yield
            finally:
                self._guard_local.depth = depth
                if handle is not None:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:  # pragma: no cover
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    handle.close()

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
        temporary = self.state_path.with_suffix(
            self.state_path.suffix + f".{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(self.state_path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if not pid:
            return False

    @staticmethod
    def _process_identity_alive(
        pid: int | None, expected_create_time: float | int | None
    ) -> bool:
        if not pid:
            return False
        try:
            process = psutil.Process(int(pid))
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                return False
            if expected_create_time not in (None, ""):
                return abs(
                    float(process.create_time()) - float(expected_create_time)
                ) < 0.01
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, TypeError):
            return False

    def _task_process_alive(self, task: dict[str, Any]) -> bool:
        return self._process_identity_alive(
            task.get("pid"), task.get("process_create_time")
        )
        try:
            process = psutil.Process(int(pid))
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            return False

    def _normalize_state(self) -> None:
        with self._state_guard():
            state = self._load()
            changed = False
            for task in state.get("tasks", []):
                if (
                    task.get("status") == "running"
                    and not self._task_process_alive(task)
                ):
                    # A live owner process still has a watcher responsible for
                    # persisting the exact exit code.  Another web worker must
                    # not race that watcher and downgrade a completed/failed
                    # task to "interrupted" in the tiny post-exit window.
                    if self._process_identity_alive(
                        task.get("manager_pid"), task.get("manager_create_time")
                    ):
                        continue
                    task["status"] = "interrupted"
                    task["running"] = False
                    task["finished_at"] = now_iso()
                    task["message"] = "Процесс отсутствует после перезапуска приложения."
                    changed = True
            state["tasks"] = sorted(
                state.get("tasks", []), key=lambda item: item.get("started_at") or "", reverse=True
            )[:150]
            if changed:
                self._save(state)

    def _recover_running_tasks(self) -> None:
        with self._state_guard():
            tasks = [
                dict(task) for task in self._load().get("tasks", [])
                if task.get("status") == "running" and self._task_process_alive(task)
            ]
        for task in tasks:
            threading.Thread(
                target=self._watch_recovered,
                args=(str(task.get("id") or ""), int(task.get("pid") or 0)),
                daemon=True,
                name=f"task-recovery-{str(task.get('id') or '')[-12:]}",
            ).start()

    def _watch_recovered(self, task_id: str, pid: int) -> None:
        try:
            psutil.Process(pid).wait()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
            pass
        with self._state_guard():
            state = self._load()
            task = next(
                (item for item in state.get("tasks", []) if item.get("id") == task_id),
                None,
            )
            if not task or task.get("status") != "running":
                return
            task["status"] = "interrupted"
            task["running"] = False
            task["finished_at"] = now_iso()
            task["message"] = (
                "Процесс завершился после перезапуска приложения; код выхода недоступен."
            )
            self._save(state)

    @staticmethod
    def _read_tail(path: Path, lines: int = 250) -> str:
        if not path.exists():
            return ""
        try:
            wanted = max(1, min(int(lines), 3000))
            max_bytes = 512 * 1024
            chunks: list[bytes] = []
            total = 0
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                position = handle.tell()
                while position > 0 and total < max_bytes:
                    size = min(64 * 1024, position, max_bytes - total)
                    position -= size
                    handle.seek(position)
                    chunk = handle.read(size)
                    chunks.append(chunk)
                    total += len(chunk)
                    if b"".join(reversed(chunks)).count(b"\n") > wanted:
                        break
            raw = b"".join(reversed(chunks))
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
            return "".join(content[-wanted:])
        except OSError:
            return ""

    def _enrich(self, task: dict[str, Any]) -> dict[str, Any]:
        result = dict(task)
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        result["metadata"] = metadata
        result["platform"] = metadata.get("platform") or result.get("platform") or "system"
        raw = result.get("log_file")
        log_path = Path(raw) if raw else None
        task_id = str(result.get("id") or "")
        base_cache_key = (
            result.get("status"), result.get("pid"), result.get("finished_at"),
            result.get("stop_requested_at"), result.get("message"),
        )
        cached = self._enrich_cache.get(task_id)
        # Finished task logs are immutable. Avoid even stat() calls across the
        # complete history during every three-second UI poll.
        if (
            task_id and result.get("status") != "running" and cached
            and cached[0][:5] == base_cache_key
        ):
            return dict(cached[1])
        try:
            log_stat = log_path.stat() if log_path and log_path.exists() else None
        except OSError:
            log_stat = None
        cache_key = (*base_cache_key,
            log_stat.st_mtime_ns if log_stat else 0,
            log_stat.st_size if log_stat else 0,
        )
        if task_id and cached and cached[0] == cache_key:
            return dict(cached[1])
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
        result["running"] = (
            result.get("status") == "running" and self._task_process_alive(result)
        )
        if log_stat:
            try:
                result["updated_at"] = datetime.fromtimestamp(
                    log_stat.st_mtime
                ).astimezone().isoformat(timespec="seconds")
            except OSError:
                result["updated_at"] = result.get("finished_at") or result.get("started_at")
        else:
            result["updated_at"] = result.get("finished_at") or result.get("started_at")
        if task_id:
            self._enrich_cache[task_id] = (cache_key, dict(result))
            if len(self._enrich_cache) > 300:
                current_ids = {
                    str(item.get("id") or "") for item in self._load().get("tasks", [])
                }
                self._enrich_cache = {
                    key: value for key, value in self._enrich_cache.items()
                    if key in current_ids
                }
        return result

    def states(self) -> list[dict[str, Any]]:
        with self._state_guard():
            self._normalize_state()
            tasks = [dict(task) for task in self._load().get("tasks", [])]
        return [self._enrich(task) for task in tasks]

    def raw_states(self) -> list[dict[str, Any]]:
        """Task state without log parsing, for notifications and access checks."""
        with self._state_guard():
            self._normalize_state()
            return [dict(task) for task in self._load().get("tasks", [])]

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
        with self._state_guard():
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
                    "Для этого продавца уже есть активная операция; общий профиль и каталог "
                    "продавца используются последовательно."
                )

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            task_id = f"{name}_{stamp}_{uuid.uuid4().hex[:7]}"
            metadata_value = dict(metadata or {})
            tenant_part = f"_t{int(metadata_value.get('tenant_id') or 0)}"
            seller_part = f"_s{int(metadata_value.get('tenant_seller_id') or 0)}"
            log_path = self.logs_dir / (
                f"{stamp}_{name}{tenant_part}{seller_part}_{task_id[-7:]}.log"
            )
            log_handle = log_path.open("ab", buffering=0)
            log_context = {
                "timestamp": now_iso(),
                "job_id": task_id,
                "tenant_id": metadata_value.get("tenant_id"),
                "tenant_seller_id": metadata_value.get("tenant_seller_id"),
                "seller_id": metadata_value.get("seller_id"),
                "marketplace": metadata_value.get("platform"),
                "operation": name,
            }
            log_handle.write(
                ("[JOB_CONTEXT] " + json.dumps(
                    log_context, ensure_ascii=False, separators=(",", ":")
                ) + "\n").encode("utf-8")
            )
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
            try:
                process_create_time = psutil.Process(process.pid).create_time()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                process_create_time = None
            try:
                manager_create_time = psutil.Process(os.getpid()).create_time()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                manager_create_time = None
            task = {
                "id": task_id,
                "name": name,
                "label": label,
                "status": "running",
                "running": True,
                "pid": process.pid,
                "process_create_time": process_create_time,
                "manager_pid": os.getpid(),
                "manager_create_time": manager_create_time,
                "command": command,
                "resources": resources_value,
                "metadata": metadata_value,
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
        with self._state_guard():
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
            final_result = structured_result(
                self._read_tail(Path(str(task.get("log_file") or "")))
            )
            if final_result:
                reason = str(final_result.get("reason") or "collector_failed")
                task["result_reason"] = reason
                task["message"] = RESULT_MESSAGES.get(reason, RESULT_MESSAGES["collector_failed"])
            elif task["status"] == "failed":
                task["message"] = RESULT_MESSAGES["collector_failed"]
            self._save(state)
            handle = self.log_handles.pop(task_id, None)
            try:
                if handle:
                    handle.close()
            finally:
                self.processes.pop(task_id, None)

    def stop(self, task_id: str) -> dict[str, Any]:
        with self._state_guard():
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

        with self._state_guard():
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
        with self._state_guard():
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
        with self._state_guard():
            state = self._load()
            active, removed = [], []
            for task in state.get("tasks", []):
                if task.get("status") == "running" and self._task_process_alive(task):
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
