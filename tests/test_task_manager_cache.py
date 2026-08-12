from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from task_manager import TaskManager


class TaskManagerCacheTests(unittest.TestCase):
    def test_tail_reader_is_bounded_and_completed_tasks_are_cached(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task_manager_cache_") as folder:
            root = Path(folder)
            logs = root / "logs"
            logs.mkdir()
            log = logs / "completed.log"
            log.write_text(
                "".join(f"line {index}\n" for index in range(50_000)),
                encoding="utf-8",
            )
            state_path = root / "tasks.json"
            state_path.write_text(json.dumps({"tasks": [{
                "id": "completed-1", "name": "test", "label": "Test",
                "status": "completed", "running": False, "pid": None,
                "log_file": str(log), "metadata": {},
                "started_at": "2026-08-12T10:00:00+05:00",
                "finished_at": "2026-08-12T10:01:00+05:00",
                "message": "Операция завершена",
            }]}), encoding="utf-8")
            manager = TaskManager(root, logs, state_path)

            tail = manager._read_tail(log, 5)
            self.assertNotIn("line 0\n", tail)
            self.assertIn("line 49999", tail)
            self.assertLessEqual(len(tail.splitlines()), 5)

            with patch.object(manager, "_read_tail", wraps=manager._read_tail) as reader:
                first = manager.states()
                second = manager.states()
            self.assertEqual(first, second)
            self.assertEqual(1, reader.call_count)


if __name__ == "__main__":
    unittest.main()
