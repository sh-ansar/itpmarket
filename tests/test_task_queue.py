from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from task_manager import TaskManager


class TaskQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="task_queue_")
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.manager = TaskManager(
            self.root, self.root / "logs", self.root / "tasks.json", max_parallel=2
        )

    def wait_for(self, task_ids: list[str], statuses: set[str], timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = {self.manager.state(task_id).get("status") for task_id in task_ids}
            if current <= statuses:
                return
            threading.Event().wait(0.02)
        self.fail(f"tasks did not reach {statuses}: {[self.manager.state(task_id) for task_id in task_ids]}")

    def command_waiting_for(self, path: Path, marker: Path | None = None) -> list[str]:
        script = (
            "from pathlib import Path;import sys,time;"
            "gate=Path(sys.argv[1]);marker=Path(sys.argv[2]) if len(sys.argv)>2 else None;"
            "marker and marker.write_text('started');"
            "\nwhile not gate.exists(): time.sleep(.01)"
        )
        result = [sys.executable, "-c", script, str(path)]
        if marker is not None:
            result.append(str(marker))
        return result

    def test_parallel_limit_drains_five_accepted_jobs(self) -> None:
        gate = self.root / "release"
        tasks = [
            self.manager.start(
                "test", f"Job {index}", self.command_waiting_for(gate), [f"seller:{index}"],
                queue_if_busy=True,
            )
            for index in range(5)
        ]
        statuses = [self.manager.state(str(task["id"]))["status"] for task in tasks]
        self.assertEqual(2, statuses.count("running"))
        self.assertEqual(3, statuses.count("queued"))
        gate.touch()
        self.wait_for([str(task["id"]) for task in tasks], {"completed"})

    def test_six_parallel_slots_accept_120_independent_submissions(self) -> None:
        manager = TaskManager(
            self.root, self.root / "bulk-logs", self.root / "bulk-tasks.json", max_parallel=6
        )
        self.manager = manager
        gate = self.root / "bulk-release"
        tasks = [
            manager.start("test", f"Bulk {index}", self.command_waiting_for(gate), [], queue_if_busy=True)
            for index in range(120)
        ]
        statuses = [manager.state(str(task["id"]))["status"] for task in tasks]
        self.assertEqual(6, statuses.count("running"))
        self.assertEqual(114, statuses.count("queued"))

        with manager._state_guard():
            state = manager._load()
            for task in state["tasks"]:
                if task.get("status") == "queued":
                    task["status"] = "stopped"
                    task["finished_at"] = "2026-01-01T00:00:00+00:00"
            manager._save(state)
        gate.touch()
        self.wait_for([str(task["id"]) for task in tasks[:6]], {"completed"}, timeout=20)

    def test_queue_rejects_1001st_waiting_submission(self) -> None:
        queued = [
            {
                "id": f"queued-{index}", "name": "test", "label": f"Queued {index}",
                "status": "queued", "running": False, "command": [], "resources": [],
                "metadata": {}, "queued_at": f"2026-01-01T00:00:{index % 60:02d}+00:00",
                "started_at": None, "finished_at": None, "pid": None,
                "log_file": str(self.root / "logs" / f"queued-{index}.log"), "message": "queued",
            }
            for index in range(1000)
        ]
        self.manager._save({"tasks": queued})
        self.assertEqual(1000, sum(task["status"] == "queued" for task in self.manager.raw_states()))
        with self.assertRaisesRegex(RuntimeError, "Очередь операций заполнена"):
            self.manager.start("test", "Overflow", [sys.executable, "-c", "pass"], queue_if_busy=True)

    def test_blocked_head_does_not_block_independent_job(self) -> None:
        first_gate = self.root / "first-release"
        second_gate = self.root / "second-release"
        first = self.manager.start(
            "ozon", "Ozon 1", self.command_waiting_for(first_gate), ["ozon_browser"]
        )
        blocked = self.manager.start(
            "ozon", "Ozon 2", self.command_waiting_for(first_gate), ["ozon_browser"],
            queue_if_busy=True,
        )
        independent = self.manager.start(
            "kaspi", "Kaspi", self.command_waiting_for(second_gate), ["seller:3:kaspi:10"],
            queue_if_busy=True,
        )
        self.assertEqual("queued", self.manager.state(str(blocked["id"]))["status"])
        self.assertEqual("running", self.manager.state(str(independent["id"]))["status"])
        first_gate.touch()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.manager.state(str(blocked["id"])).get("status") != "running":
            threading.Event().wait(0.02)
        self.assertEqual("running", self.manager.state(str(blocked["id"]))["status"])
        second_gate.touch()
        self.wait_for([str(first["id"]), str(blocked["id"]), str(independent["id"])], {"completed"})

    def test_ozon_ru_and_kz_run_together_but_ru_serializes(self) -> None:
        gate = self.root / "release"
        ru_jobs = [
            self.manager.start("ozon", f"RU {index}", self.command_waiting_for(gate), ["ozon_browser"], queue_if_busy=True)
            for index in range(5)
        ]
        kz = self.manager.start("ozon-kz", "KZ", self.command_waiting_for(gate), ["ozon_kz"], queue_if_busy=True)
        statuses = [self.manager.state(str(task["id"]))["status"] for task in ru_jobs]
        self.assertEqual(1, statuses.count("running"))
        self.assertEqual(4, statuses.count("queued"))
        self.assertEqual("running", self.manager.state(str(kz["id"]))["status"])
        gate.touch()
        self.wait_for([str(task["id"]) for task in [*ru_jobs, kz]], {"completed"})

    def test_twenty_ozon_ru_and_kz_jobs_hold_one_active_slot_each(self) -> None:
        gate = self.root / "ozon-release"
        ru_jobs = [
            self.manager.start("ozon", f"RU {index}", self.command_waiting_for(gate), ["ozon_browser"], queue_if_busy=True)
            for index in range(20)
        ]
        kz_jobs = [
            self.manager.start("ozon-kz", f"KZ {index}", self.command_waiting_for(gate), ["ozon_kz"], queue_if_busy=True)
            for index in range(20)
        ]
        states = [self.manager.state(str(task["id"])) for task in [*ru_jobs, *kz_jobs]]
        self.assertEqual(1, sum(task["status"] == "running" and "ozon_browser" in task["resources"] for task in states))
        self.assertEqual(1, sum(task["status"] == "running" and "ozon_kz" in task["resources"] for task in states))
        self.assertEqual(38, sum(task["status"] == "queued" for task in states))
        running_ids = [str(task["id"]) for task in states if task["status"] == "running"]
        with self.manager._state_guard():
            state = self.manager._load()
            for task in state["tasks"]:
                if task.get("status") == "queued":
                    task["status"] = "stopped"
                    task["finished_at"] = "2026-01-01T00:00:00+00:00"
            self.manager._save(state)
        gate.touch()
        self.wait_for(running_ids, {"completed"}, timeout=20)

    def test_queue_status_is_localized_for_ru_kz_and_en(self) -> None:
        locales = (Path(__file__).parents[1] / "static" / "js" / "queue_locales.js").read_text(encoding="utf-8")
        for value in (
            'task_queued: "В очереди"',
            'task_queued: "Кезекте"',
            'task_queued: "Queued"',
            'task_queued_message: "Операция ожидает запуска в очереди"',
            'task_queued_message: "Операция кезекте іске қосылуын күтуде"',
            'task_queued_message: "The operation is waiting in the queue"',
        ):
            self.assertIn(value, locales)

    def test_queued_task_stops_without_launching(self) -> None:
        gate = self.root / "release"
        marker = self.root / "queued-started"
        first = self.manager.start("one", "One", self.command_waiting_for(gate), ["shared"])
        queued = self.manager.start(
            "two", "Two", self.command_waiting_for(gate, marker), ["shared"], queue_if_busy=True
        )
        self.assertEqual("queued", self.manager.state(str(queued["id"]))["status"])
        stopped = self.manager.stop(str(queued["id"]))
        self.assertEqual("stopped", stopped["status"])
        gate.touch()
        self.wait_for([str(first["id"])], {"completed"})
        self.assertFalse(marker.exists())

    def test_clear_finished_preserves_queued_and_history_trim_preserves_active(self) -> None:
        gate = self.root / "release"
        running = self.manager.start("one", "One", self.command_waiting_for(gate), ["shared"])
        queued = self.manager.start("two", "Two", self.command_waiting_for(gate), ["shared"], queue_if_busy=True)
        self.assertEqual(0, self.manager.clear_finished(delete_logs=False))
        self.assertEqual("queued", self.manager.state(str(queued["id"]))["status"])
        terminal = [{"id": f"done-{index}", "status": "completed", "finished_at": f"2026-01-01T00:00:{index % 60:02d}+00:00"} for index in range(200)]
        retained = TaskManager._trim_tasks([*terminal, {"id": "queued", "status": "queued"}, {"id": "running", "status": "running"}])
        self.assertEqual({"queued", "running"}, {item["id"] for item in retained if item["status"] in {"queued", "running"}})
        self.assertEqual(152, len(retained))
        gate.touch()
        self.wait_for([str(running["id"]), str(queued["id"])], {"completed"})

    def test_restart_launches_persisted_queued_task(self) -> None:
        state = self.root / "restart.json"
        marker = self.root / "restarted"
        command = [sys.executable, "-c", "from pathlib import Path;import sys;Path(sys.argv[1]).touch()", str(marker)]
        state.write_text(json.dumps({"tasks": [{
            "id": "queued-restart", "name": "test", "label": "Restart", "status": "queued",
            "running": False, "command": command, "resources": [], "metadata": {},
            "queued_at": "2026-01-01T00:00:00+00:00", "started_at": None, "finished_at": None,
            "pid": None, "log_file": str(self.root / "logs" / "restart.log"), "message": "queued",
        }]}), encoding="utf-8")
        restarted = TaskManager(self.root, self.root / "logs", state, max_parallel=2)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and restarted.state("queued-restart").get("status") != "completed":
            threading.Event().wait(0.02)
        self.assertTrue(marker.exists())
        self.assertEqual("completed", restarted.state("queued-restart")["status"])

    def test_backward_compatibility_can_reject_busy_start(self) -> None:
        gate = self.root / "release"
        first = self.manager.start("one", "One", self.command_waiting_for(gate), ["shared"])
        with self.assertRaises(RuntimeError):
            self.manager.start("two", "Two", self.command_waiting_for(gate), ["shared"], queue_if_busy=False)
        gate.touch()
        self.wait_for([str(first["id"])], {"completed"})


if __name__ == "__main__":
    unittest.main()
