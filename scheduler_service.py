from __future__ import annotations

import threading
from typing import Any, Callable

from marketplace_registry import allowed_marketplaces_from_user, marketplace_for_action
from saas_service import SaaSService
from tenant_security import company_is_approved, has_permission


class SchedulerService:
    def __init__(
        self,
        saas: SaaSService,
        task_manager: Any,
        action_info: dict[str, dict[str, Any]],
        command_builder: Callable[[str, list[str], int], list[str]],
        interval_seconds: int = 30,
        user_loader: Callable[[int], dict[str, Any] | None] | None = None,
        subscription_service: Any | None = None,
    ):
        self.saas = saas
        self.task_manager = task_manager
        self.action_info = action_info
        self.command_builder = command_builder
        self.user_loader = user_loader
        self.subscription_service = subscription_service
        self.interval_seconds = max(15, int(interval_seconds))
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(
            target=self._loop, daemon=True, name="itp-scheduler"
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _loop(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            try:
                self.run_due_once()
            except Exception:
                pass

    def _authorization_error(
        self, schedule: dict[str, Any], info: dict[str, Any]
    ) -> str | None:
        user_id = int(schedule.get("created_by") or 0)
        user = self.user_loader(user_id) if self.user_loader and user_id else None
        if not user or not user.get("is_active"):
            return "Автор расписания неактивен или удалён."
        if int(user.get("tenant_id") or 0) != int(schedule.get("tenant_id") or -1):
            return "Автор больше не состоит в организации расписания."
        if not company_is_approved(user.get("tenant_status")):
            return "Компания автора ещё не подтверждена или заблокирована."
        if not bool(user.get("tenant_profile_complete")) and user.get("platform_role") != "superadmin":
            return "Обязательные реквизиты компании автора не заполнены."
        if not has_permission(user, "run_operations"):
            return "У автора больше нет разрешения на запуск операций."
        action = str(schedule.get("action") or "")
        if action == "export_report" and not has_permission(user, "create_reports"):
            return "У автора больше нет разрешения на формирование отчётов."
        if action == "backup_database" and user.get("platform_role") != "superadmin":
            return "Резервное копирование разрешено только platform superadmin."
        platform = marketplace_for_action(action, self.action_info)
        if platform != "system" and platform not in allowed_marketplaces_from_user(user):
            return "У автора больше нет доступа к площадке расписания."
        if self.subscription_service and user.get("platform_role") != "superadmin":
            entitlement = self.subscription_service.entitlement(int(schedule["tenant_id"]))
            if not entitlement.get("features", {}).get("schedules", False):
                return "Расписания не входят в активный пакет компании."
            subscription_error = self.subscription_service.operation_error(
                int(schedule["tenant_id"]), platform
            )
            if subscription_error:
                return subscription_error
        return None

    def run_due_once(self) -> None:
        for schedule in self.saas.due_schedules():
            action = str(schedule.get("action") or "")
            info = self.action_info.get(action)
            run_id = self.saas.begin_schedule_run(schedule)
            if not info:
                self.saas.finish_schedule_run(
                    run_id, int(schedule["id"]), "failed",
                    "Операция больше не поддерживается.",
                )
                continue
            authorization_error = self._authorization_error(schedule, info)
            if authorization_error:
                self.saas.finish_schedule_run(
                    run_id, int(schedule["id"]), "failed", authorization_error
                )
                continue
            consumed = False
            platform = "system"
            try:
                user_id = int(schedule.get("created_by") or 0)
                platform = marketplace_for_action(action, self.action_info)
                if self.subscription_service:
                    user = self.user_loader(user_id) if self.user_loader else None
                    if not user or user.get("platform_role") != "superadmin":
                        self.subscription_service.consume_operation(
                            int(schedule["tenant_id"]), platform
                        )
                        consumed = True
                task = self.task_manager.start(
                    action,
                    f"{info['label']} — по расписанию",
                    self.command_builder(action, [], user_id),
                    info.get("resource") or [],
                    metadata={
                        "scope": "all",
                        "scheduled": True,
                        "schedule_id": int(schedule["id"]),
                        "tenant_id": int(schedule["tenant_id"]),
                        "requested_by_id": user_id,
                        "platform": platform,
                        "platforms": (
                            sorted(allowed_marketplaces_from_user(
                                self.user_loader(user_id) if self.user_loader else None
                            ))
                            if action == "export_report"
                            else [platform] if platform != "system" else []
                        ),
                    },
                )
                self.saas.attach_task_to_run(run_id, str(task["id"]))
                threading.Thread(
                    target=self._watch,
                    args=(run_id, int(schedule["id"]), str(task["id"])),
                    daemon=True,
                ).start()
            except Exception as exc:
                if consumed and self.subscription_service:
                    self.subscription_service.release_operation(
                        int(schedule["tenant_id"]), platform
                    )
                self.saas.finish_schedule_run(
                    run_id, int(schedule["id"]), "failed", str(exc)
                )

    def _watch(self, run_id: int, schedule_id: int, task_id: str) -> None:
        while not self.stop_event.wait(5):
            task = self.task_manager.state(task_id)
            if task.get("running"):
                continue
            self.saas.finish_schedule_run(
                run_id,
                schedule_id,
                str(task.get("status") or "failed"),
                str(task.get("message") or "Операция завершена"),
            )
            return
