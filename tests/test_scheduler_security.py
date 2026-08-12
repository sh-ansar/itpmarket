from __future__ import annotations

import unittest

from scheduler_service import SchedulerService


class FakeSaaS:
    def __init__(self):
        self.finished = []

    def due_schedules(self):
        return [{
            "id": 7, "tenant_id": 2, "created_by": 3,
            "action": "ozon_full_sync",
        }]

    def begin_schedule_run(self, schedule):
        return 11

    def finish_schedule_run(self, *args):
        self.finished.append(args)


class FakeTaskManager:
    def __init__(self):
        self.started = False

    def start(self, *args, **kwargs):
        self.started = True
        return {"id": "unexpected"}


class SchedulerSecurityTests(unittest.TestCase):
    def test_permissions_are_rechecked_when_schedule_becomes_due(self) -> None:
        saas = FakeSaaS()
        tasks = FakeTaskManager()
        scheduler = SchedulerService(
            saas,
            tasks,
            {
                "ozon_full_sync": {
                    "label": "Ozon.ru",
                    "roles": {"admin", "operator"},
                    "platform": "ozon",
                }
            },
            lambda action, codes, user_id: ["python", "collector.py"],
            user_loader=lambda user_id: {
                "id": user_id,
                "tenant_id": 2,
                "role": "operator",
                "is_active": True,
                "tenant_status": "approved",
                "tenant_profile_complete": True,
                "platform_role": "",
                "permissions": {"run_operations": True, "create_reports": True},
                "marketplaces": {"ozon": False, "halyk_market": True},
            },
        )
        scheduler.run_due_once()
        self.assertFalse(tasks.started)
        self.assertEqual("failed", saas.finished[0][2])
        self.assertIn("нет доступа", saas.finished[0][3].casefold())


if __name__ == "__main__":
    unittest.main()
