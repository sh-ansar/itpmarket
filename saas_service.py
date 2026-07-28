from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from public_product_service import PUBLIC_CAPABILITIES, CONSENT_VERSION

INTEGRATION_CATALOG = [
    {"code":"kaspi","name":"Kaspi","description":"Каталог, точные предложения одной карточки и история цен.","availability":"available"},
    {"code":"ozon","name":"Ozon","description":"Каталог клиента, рыночные карточки и строгое сопоставление.","availability":"available"},
    {"code":"forte_market","name":"Forte Market","description":"Подключение запланировано после стабилизации общего API интеграций.","availability":"coming_soon"},
    {"code":"halyk_market","name":"Halyk Market","description":"Подключение запланировано как отдельный модуль маркетплейса.","availability":"coming_soon"},
]

SCHEDULE_ACTIONS = {
    "sync_catalog": ("Kaspi","Синхронизация каталога"),
    "update_own_prices": ("Kaspi","Обновление собственных цен"),
    "scan_market": ("Kaspi","Точные предложения продавцов"),
    "refresh_market": ("Kaspi","Обновление точных предложений"),
    "retry_errors": ("Kaspi","Повтор ошибок"),
    "ozon_discover": ("Ozon","Обнаружение товаров"),
    "ozon_enrich": ("Ozon","Характеристики новых товаров"),
    "ozon_refresh_prices": ("Ozon","Обновление цен"),
    "ozon_refresh_stale": ("Ozon","Обновление характеристик"),
    "ozon_retry": ("Ozon","Повтор ошибок"),
    "ozon_full_sync": ("Ozon","Полная синхронизация"),
    "export_report": ("Система","Формирование отчёта"),
    "backup_database": ("Система","Резервное копирование"),
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (value or "").casefold()).strip("-")
    return text or "company"


class SaaSService:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def default_tenant_id(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute("SELECT id FROM tenants ORDER BY id LIMIT 1").fetchone()
            if row is None:
                raise RuntimeError("Рабочее пространство не создано.")
            return int(row["id"])
        finally:
            conn.close()

    def tenant_for_user(self, user_id: int) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT t.*,tu.tenant_role,tu.is_primary
                FROM tenant_users tu JOIN tenants t ON t.id=tu.tenant_id
                WHERE tu.user_id=? AND tu.is_active=1
                ORDER BY tu.is_primary DESC,t.id LIMIT 1
                """,
                (int(user_id),),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def integrations(self, tenant_id: int) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM tenant_integrations WHERE tenant_id=? ORDER BY id",(int(tenant_id),)
            ).fetchall()]
        finally:
            conn.close()

    def public_integrations(self) -> list[dict[str, Any]]:
        return [dict(item) for item in INTEGRATION_CATALOG]

    def create_registration_request(self, payload: dict[str, Any]) -> int:
        company=str(payload.get("company_name") or "").strip(); contact=str(payload.get("contact_name") or "").strip(); email=str(payload.get("email") or "").strip().casefold()
        locale=str(payload.get("locale") or "ru").casefold(); locale=locale if locale in {"ru","kk","en"} else "ru"
        known_capabilities={item["code"] for item in PUBLIC_CAPABILITIES}; requested=payload.get("capabilities")
        if isinstance(requested,str): requested=[requested]
        capabilities=[v for v in dict.fromkeys(str(x).strip() for x in (requested or [])) if v in known_capabilities]
        legacy=payload.get("integrations"); legacy=[legacy] if isinstance(legacy,str) else (legacy or [])
        known_integrations={item["code"] for item in INTEGRATION_CATALOG}; integrations=[v for v in dict.fromkeys(str(x).strip() for x in legacy) if v in known_integrations]
        if len(company)<2: raise ValueError("Укажите название компании.")
        if len(contact)<2: raise ValueError("Укажите контактное лицо.")
        if "@" not in email or "." not in email.rsplit("@",1)[-1]: raise ValueError("Укажите корректную электронную почту.")
        if not capabilities and not integrations: raise ValueError("Выберите хотя бы одну задачу, которую должна решать система.")
        if not bool(payload.get("privacy_consent")): raise ValueError("Необходимо согласие с Политикой конфиденциальности.")
        if not bool(payload.get("terms_consent")): raise ValueError("Необходимо принять Условия использования.")
        try: estimated=max(0,min(int(payload.get("estimated_products") or 0),10_000_000))
        except (TypeError,ValueError): estimated=0
        stamp=now_iso(); conn=self._connect()
        try:
            cur=conn.execute("""INSERT INTO registration_requests(company_name,registration_number,contact_name,email,phone,integrations_json,capabilities_json,estimated_products,comment,status,consent_version,consent_at,locale,source_page,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'new',?,?,?,?,?,?)""",(company,str(payload.get("registration_number") or "").strip(),contact,email,str(payload.get("phone") or "").strip(),json.dumps(integrations,ensure_ascii=False),json.dumps(capabilities,ensure_ascii=False),estimated,str(payload.get("comment") or "").strip(),CONSENT_VERSION,stamp,locale,str(payload.get("source_page") or "public_site"),stamp,stamp))
            conn.commit(); return int(cur.lastrowid)
        finally: conn.close()

    def registration_requests(self) -> list[dict[str, Any]]:
        conn=self._connect()
        try:
            out=[]
            for row in conn.execute("SELECT * FROM registration_requests ORDER BY created_at DESC").fetchall():
                item=dict(row)
                try:item["integrations"]=json.loads(item.pop("integrations_json") or "[]")
                except json.JSONDecodeError:item["integrations"]=[]
                try:item["capabilities"]=json.loads(item.pop("capabilities_json") or "[]")
                except json.JSONDecodeError:item["capabilities"]=[]
                out.append(item)
            return out
        finally: conn.close()

    def _unique_slug(self, conn: sqlite3.Connection, name: str) -> str:
        base=slugify(name); value=base; i=2
        while conn.execute("SELECT 1 FROM tenants WHERE slug=?",(value,)).fetchone():
            value=f"{base}-{i}"; i+=1
        return value

    def review_registration(self, request_id: int, decision: str, actor_user_id: int) -> dict[str, Any]:
        decision=str(decision or "").casefold()
        if decision not in {"approved","declined"}: raise ValueError("Неизвестное решение.")
        stamp=now_iso(); conn=self._connect()
        try:
            row=conn.execute("SELECT * FROM registration_requests WHERE id=?",(int(request_id),)).fetchone()
            if not row: raise ValueError("Заявка не найдена.")
            if row["status"] not in {"new","review"}: raise ValueError("Заявка уже обработана.")
            tenant_id=None
            if decision=="approved":
                cur=conn.execute(
                    """
                    INSERT INTO tenants(name,slug,registration_number,status,plan_code,contact_email,contact_phone,created_at,updated_at,approved_at)
                    VALUES(?,?,?,'setup','demo',?,?,?,?,?)
                    """,
                    (row["company_name"],self._unique_slug(conn,row["company_name"]),row["registration_number"],row["email"],row["phone"],stamp,stamp,stamp),
                )
                tenant_id=int(cur.lastrowid)
                try: requested=json.loads(row["integrations_json"] or "[]")
                except json.JSONDecodeError: requested=[]
                for item in INTEGRATION_CATALOG:
                    status="setup" if item["code"] in requested and item["availability"]=="available" else "coming_soon" if item["code"] in requested and item["availability"]=="coming_soon" else "disabled"
                    conn.execute("INSERT INTO tenant_integrations(tenant_id,integration_code,display_name,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",(tenant_id,item["code"],item["name"],status,stamp,stamp))
            conn.execute("UPDATE registration_requests SET status=?,tenant_id=?,reviewed_by=?,reviewed_at=?,updated_at=? WHERE id=?",(decision,tenant_id,int(actor_user_id),stamp,stamp,int(request_id)))
            self._audit(conn,actor_user_id,"registration_reviewed",tenant_id,"registration_request",str(request_id),{"decision":decision})
            conn.commit(); return dict(conn.execute("SELECT * FROM registration_requests WHERE id=?",(int(request_id),)).fetchone())
        finally: conn.close()

    def platform_overview(self,current_catalog_count:int=0,current_processed_count:int=0)->dict[str,Any]:
        conn=self._connect()
        try:
            default_id=self.default_tenant_id(); tenants=[]
            rows=conn.execute(
                """
                SELECT t.*,COUNT(DISTINCT tu.user_id) users_count,
                       COUNT(DISTINCT CASE WHEN ti.status IN ('active','setup') THEN ti.id END) integrations_count,
                       COUNT(DISTINCT CASE WHEN ti.status='error' OR COALESCE(ti.last_error,'')<>'' THEN ti.id END) integration_errors,
                       MAX(ti.last_sync_at) last_sync_at,
                       (SELECT COUNT(*) FROM operation_schedules os WHERE os.tenant_id=t.id AND os.is_enabled=1) schedules_count
                FROM tenants t LEFT JOIN tenant_users tu ON tu.tenant_id=t.id AND tu.is_active=1
                LEFT JOIN tenant_integrations ti ON ti.tenant_id=t.id
                GROUP BY t.id ORDER BY t.created_at DESC
                """
            ).fetchall()
            for row in rows:
                item=dict(row); item["integrations"]=[dict(x) for x in conn.execute("SELECT * FROM tenant_integrations WHERE tenant_id=? ORDER BY id",(item["id"],)).fetchall()]
                item["product_count"]=int(current_catalog_count) if int(item["id"])==default_id else sum(int(x["product_count"] or 0) for x in item["integrations"])
                item["processed_count"]=int(current_processed_count) if int(item["id"])==default_id else 0
                tenants.append(item)
            return {"tenants":tenants,"totals":{"tenants":len(tenants),"active_tenants":sum(1 for x in tenants if x["status"] in {"active","setup"}),"new_requests":int(conn.execute("SELECT COUNT(*) FROM registration_requests WHERE status='new'").fetchone()[0]),"enabled_schedules":int(conn.execute("SELECT COUNT(*) FROM operation_schedules WHERE is_enabled=1").fetchone()[0]),"products":sum(int(x.get("product_count") or 0) for x in tenants)}}
        finally: conn.close()

    def tenant_detail(self, tenant_id: int) -> dict[str, Any]:
        conn=self._connect()
        try:
            tenant=conn.execute("SELECT * FROM tenants WHERE id=?",(int(tenant_id),)).fetchone()
            if tenant is None: raise ValueError("Компания не найдена.")
            integrations=[dict(r) for r in conn.execute("SELECT * FROM tenant_integrations WHERE tenant_id=? ORDER BY id",(int(tenant_id),)).fetchall()]
            users=[dict(r) for r in conn.execute("""SELECT u.id,u.display_name,u.email,u.role,u.is_active,tu.tenant_role FROM tenant_users tu JOIN app_users u ON u.id=tu.user_id WHERE tu.tenant_id=? ORDER BY u.display_name""",(int(tenant_id),)).fetchall()]
            schedules=[dict(r) for r in conn.execute("""SELECT id,name,action,platform,is_enabled,last_run_at,next_run_at,last_status,last_error FROM operation_schedules WHERE tenant_id=? ORDER BY is_enabled DESC,next_run_at""",(int(tenant_id),)).fetchall()]
            recent_runs=[dict(r) for r in conn.execute("""SELECT r.*,s.name schedule_name FROM schedule_runs r JOIN operation_schedules s ON s.id=r.schedule_id WHERE r.tenant_id=? ORDER BY r.started_at DESC LIMIT 20""",(int(tenant_id),)).fetchall()]
            return {"tenant":dict(tenant),"integrations":integrations,"users":users,"schedules":schedules,"recent_runs":recent_runs}
        finally: conn.close()

    def update_tenant(self,tenant_id:int,payload:dict[str,Any],actor_user_id:int)->dict[str,Any]:
        status=str(payload.get("status") or "").casefold(); name=str(payload.get("name") or "").strip()
        if status and status not in {"setup","active","suspended","archived"}: raise ValueError("Неизвестный статус компании.")
        conn=self._connect()
        try:
            row=conn.execute("SELECT * FROM tenants WHERE id=?",(int(tenant_id),)).fetchone()
            if not row: raise ValueError("Компания не найдена.")
            fields=[]; params=[]
            if name: fields.append("name=?"); params.append(name)
            if status: fields.append("status=?"); params.append(status)
            if fields:
                fields.append("updated_at=?"); params.append(now_iso()); params.append(int(tenant_id)); conn.execute(f"UPDATE tenants SET {', '.join(fields)} WHERE id=?",params)
                self._audit(conn,actor_user_id,"tenant_updated",tenant_id,"tenant",str(tenant_id),payload); conn.commit()
            return dict(conn.execute("SELECT * FROM tenants WHERE id=?",(int(tenant_id),)).fetchone())
        finally: conn.close()

    def update_tenant_profile(self, tenant_id: int, payload: dict[str, Any], actor_user_id: int) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        registration_number = str(payload.get("registration_number") or "").strip()
        contact_email = str(payload.get("contact_email") or "").strip().casefold()
        contact_phone = str(payload.get("contact_phone") or "").strip()
        if len(name) < 2:
            raise ValueError("Укажите название компании.")
        if contact_email and ("@" not in contact_email or "." not in contact_email.rsplit("@", 1)[-1]):
            raise ValueError("Укажите корректный email компании.")
        stamp = now_iso()
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM tenants WHERE id=?", (int(tenant_id),)).fetchone()
            if not row:
                raise ValueError("Компания не найдена.")
            conn.execute(
                """
                UPDATE tenants
                SET name=?, registration_number=?, contact_email=?, contact_phone=?, updated_at=?
                WHERE id=?
                """,
                (name, registration_number, contact_email, contact_phone, stamp, int(tenant_id)),
            )
            self._audit(
                conn,
                actor_user_id,
                "tenant_profile_updated",
                int(tenant_id),
                "tenant",
                str(tenant_id),
                {
                    "name": name,
                    "registration_number": registration_number,
                    "contact_email": contact_email,
                    "contact_phone": contact_phone,
                },
            )
            conn.commit()
            return dict(conn.execute("SELECT * FROM tenants WHERE id=?", (int(tenant_id),)).fetchone())
        finally:
            conn.close()

    @staticmethod
    def next_run_for(
        recurrence_type: str,
        time_of_day: str | None,
        weekdays: list[int] | None,
        interval_minutes: int | None,
        run_date: str | None = None,
        base: datetime | None = None,
    ) -> str | None:
        now = base or datetime.now().astimezone()
        recurrence = str(recurrence_type or "daily").casefold()

        if recurrence == "interval":
            return (
                now + timedelta(minutes=max(60, int(interval_minutes or 360)))
            ).isoformat(timespec="seconds")

        try:
            hour, minute = [int(x) for x in str(time_of_day or "03:00").split(":", 1)]
        except (TypeError, ValueError):
            hour, minute = 3, 0
        hour = max(0, min(hour, 23))
        minute = max(0, min(minute, 59))

        if recurrence == "once":
            value = str(run_date or "").strip()
            if not value:
                return None
            try:
                day = datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                return None
            candidate = day.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
                tzinfo=now.tzinfo,
            )
            return candidate.isoformat(timespec="seconds") if candidate > now else None

        if recurrence == "weekly":
            days = sorted({max(0, min(int(x), 6)) for x in (weekdays or [0])})
            for offset in range(8):
                day = now + timedelta(days=offset)
                if day.weekday() not in days:
                    continue
                candidate = day.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
                if candidate > now:
                    return candidate.isoformat(timespec="seconds")

        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.isoformat(timespec="seconds")

    def schedules(self,tenant_id:int)->list[dict[str,Any]]:
        conn=self._connect()
        try:
            out=[]
            for row in conn.execute("SELECT * FROM operation_schedules WHERE tenant_id=? ORDER BY is_enabled DESC,next_run_at,name",(int(tenant_id),)).fetchall():
                item=dict(row)
                try:item["weekdays"]=json.loads(item.pop("weekdays_json") or "[]")
                except json.JSONDecodeError:item["weekdays"]=[]
                out.append(item)
            return out
        finally: conn.close()

    def schedule(self,schedule_id:int,tenant_id:int|None=None)->dict[str,Any]|None:
        conn=self._connect()
        try:
            q="SELECT * FROM operation_schedules WHERE id=?"; params=[int(schedule_id)]
            if tenant_id is not None:q+=" AND tenant_id=?"; params.append(int(tenant_id))
            row=conn.execute(q,params).fetchone()
            if not row:return None
            item=dict(row)
            try:item["weekdays"]=json.loads(item.pop("weekdays_json") or "[]")
            except json.JSONDecodeError:item["weekdays"]=[]
            return item
        finally: conn.close()

    def create_schedule(
        self, tenant_id: int, payload: dict[str, Any], actor_user_id: int
    ) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        action = str(payload.get("action") or "").strip()
        recurrence = str(payload.get("recurrence_type") or "daily").casefold()

        if len(name) < 2:
            raise ValueError("Укажите название задания.")
        if action not in SCHEDULE_ACTIONS:
            raise ValueError("Выберите поддерживаемую операцию.")
        if recurrence not in {"once", "daily", "weekly", "interval"}:
            raise ValueError("Неизвестный тип расписания.")

        weekdays = payload.get("weekdays") if isinstance(payload.get("weekdays"), list) else []
        if recurrence == "weekly" and not weekdays:
            raise ValueError("Выберите хотя бы один день недели.")

        try:
            interval = max(60, min(int(payload.get("interval_minutes") or 360), 10080))
        except (TypeError, ValueError):
            interval = 360

        tod = str(payload.get("time_of_day") or "03:00")
        run_date = str(payload.get("run_date") or "").strip() or None
        next_run = self.next_run_for(
            recurrence, tod, weekdays, interval, run_date=run_date
        )
        enabled = bool(payload.get("is_enabled", True))
        if recurrence == "once" and enabled and next_run is None:
            raise ValueError("Для однократного запуска выберите будущую дату и время.")

        platform = (
            "ozon" if action.startswith("ozon_")
            else "system" if action in {"export_report", "backup_database"}
            else "kaspi"
        )
        stamp = now_iso()
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO operation_schedules(
                    tenant_id,name,action,platform,scope,recurrence_type,time_of_day,
                    run_date,weekdays_json,interval_minutes,is_enabled,retry_count,
                    max_duration_minutes,next_run_at,created_by,created_at,updated_at
                )
                VALUES(?,?,?,?, 'all',?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(tenant_id), name, action, platform, recurrence, tod,
                    run_date, json.dumps(weekdays), interval,
                    1 if enabled else 0,
                    max(0, min(int(payload.get("retry_count") or 1), 5)),
                    max(10, min(int(payload.get("max_duration_minutes") or 180), 1440)),
                    next_run if enabled else None,
                    int(actor_user_id), stamp, stamp,
                ),
            )
            conn.commit()
            return self.schedule(int(cur.lastrowid), tenant_id) or {}
        finally:
            conn.close()

    def update_schedule(
        self,
        schedule_id: int,
        tenant_id: int,
        payload: dict[str, Any],
        actor_user_id: int,
    ) -> dict[str, Any]:
        current = self.schedule(schedule_id, tenant_id)
        if not current:
            raise ValueError("Расписание не найдено.")

        merged = {**current, **payload}
        recurrence = str(merged.get("recurrence_type") or "daily").casefold()
        if recurrence not in {"once", "daily", "weekly", "interval"}:
            raise ValueError("Неизвестный тип расписания.")

        weekdays = merged.get("weekdays") if isinstance(merged.get("weekdays"), list) else []
        if recurrence == "weekly" and not weekdays:
            raise ValueError("Выберите хотя бы один день недели.")

        try:
            interval = max(60, min(int(merged.get("interval_minutes") or 360), 10080))
        except (TypeError, ValueError):
            interval = 360

        tod = str(merged.get("time_of_day") or "03:00")
        run_date = str(merged.get("run_date") or "").strip() or None
        enabled = bool(merged.get("is_enabled"))
        next_run = self.next_run_for(
            recurrence, tod, weekdays, interval, run_date=run_date
        ) if enabled else None

        if recurrence == "once" and enabled and next_run is None:
            raise ValueError("Для однократного запуска выберите будущую дату и время.")

        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE operation_schedules
                SET name=?,recurrence_type=?,time_of_day=?,run_date=?,
                    weekdays_json=?,interval_minutes=?,is_enabled=?,retry_count=?,
                    max_duration_minutes=?,next_run_at=?,updated_at=?
                WHERE id=? AND tenant_id=?
                """,
                (
                    str(merged.get("name") or current["name"]).strip(),
                    recurrence, tod, run_date, json.dumps(weekdays), interval,
                    1 if enabled else 0,
                    max(0, min(int(merged.get("retry_count") or 1), 5)),
                    max(10, min(int(merged.get("max_duration_minutes") or 180), 1440)),
                    next_run, now_iso(), int(schedule_id), int(tenant_id),
                ),
            )
            self._audit(
                conn, actor_user_id, "schedule_updated", tenant_id,
                "schedule", str(schedule_id), payload,
            )
            conn.commit()
            return self.schedule(schedule_id, tenant_id) or {}
        finally:
            conn.close()

    def delete_schedule(self,schedule_id:int,tenant_id:int,actor_user_id:int)->None:
        conn=self._connect()
        try:
            if not conn.execute("SELECT 1 FROM operation_schedules WHERE id=? AND tenant_id=?",(int(schedule_id),int(tenant_id))).fetchone(): raise ValueError("Расписание не найдено.")
            conn.execute("DELETE FROM operation_schedules WHERE id=? AND tenant_id=?",(int(schedule_id),int(tenant_id))); conn.commit()
        finally: conn.close()

    def schedule_runs(self,tenant_id:int,limit:int=50)->list[dict[str,Any]]:
        conn=self._connect()
        try:return [dict(r) for r in conn.execute("SELECT r.*,s.name schedule_name,s.action,s.platform FROM schedule_runs r JOIN operation_schedules s ON s.id=r.schedule_id WHERE r.tenant_id=? ORDER BY r.started_at DESC LIMIT ?",(int(tenant_id),max(1,min(int(limit),200)))).fetchall()]
        finally:conn.close()

    def due_schedules(self)->list[dict[str,Any]]:
        conn=self._connect()
        try:return [dict(r) for r in conn.execute("SELECT * FROM operation_schedules WHERE is_enabled=1 AND next_run_at IS NOT NULL AND datetime(next_run_at)<=datetime('now','localtime') ORDER BY next_run_at LIMIT 10").fetchall()]
        finally:conn.close()

    def begin_schedule_run(self, schedule: dict[str, Any]) -> int:
        try:
            weekdays = json.loads(schedule.get("weekdays_json") or "[]")
        except json.JSONDecodeError:
            weekdays = []

        recurrence = str(schedule.get("recurrence_type") or "daily").casefold()
        if recurrence == "once":
            next_run = None
            enabled = 0
        else:
            next_run = self.next_run_for(
                recurrence,
                schedule.get("time_of_day"),
                weekdays,
                schedule.get("interval_minutes"),
                run_date=schedule.get("run_date"),
            )
            enabled = 1

        stamp = now_iso()
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO schedule_runs(
                    schedule_id,tenant_id,status,message,started_at
                ) VALUES(?,?,'queued','Ожидает запуска',?)
                """,
                (int(schedule["id"]), int(schedule["tenant_id"]), stamp),
            )
            conn.execute(
                """
                UPDATE operation_schedules
                SET next_run_at=?,is_enabled=?,last_status='queued',updated_at=?
                WHERE id=?
                """,
                (next_run, enabled, stamp, int(schedule["id"])),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def attach_task_to_run(self,run_id:int,task_id:str)->None:
        conn=self._connect(); conn.execute("UPDATE schedule_runs SET task_id=?,status='running',message='Операция запущена' WHERE id=?",(str(task_id),int(run_id))); conn.commit(); conn.close()

    def finish_schedule_run(self,run_id:int,schedule_id:int,status:str,message:str)->None:
        stamp=now_iso(); conn=self._connect()
        try:
            conn.execute("UPDATE schedule_runs SET status=?,message=?,finished_at=? WHERE id=?",(status,message,stamp,int(run_id)))
            conn.execute("UPDATE operation_schedules SET last_run_at=?,last_status=?,last_error=?,updated_at=? WHERE id=?",(stamp,status,message if status!='completed' else None,stamp,int(schedule_id))); conn.commit()
        finally:conn.close()

    @staticmethod
    def _audit(conn:sqlite3.Connection,actor_user_id:int|None,action:str,tenant_id:int|None,entity_type:str|None,entity_id:str|None,details:dict[str,Any])->None:
        conn.execute("INSERT INTO platform_audit_log(actor_user_id,action,tenant_id,entity_type,entity_id,details_json,created_at) VALUES(?,?,?,?,?,?,?)",(actor_user_id,action,tenant_id,entity_type,entity_id,json.dumps(details,ensure_ascii=False),now_iso()))
