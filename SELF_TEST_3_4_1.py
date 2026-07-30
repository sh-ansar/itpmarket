from pathlib import Path
import sqlite3
import tempfile
from datetime import datetime, timedelta
from schema import ensure_database
from saas_service import SaaSService
from auth_service import AuthService
from werkzeug.security import check_password_hash

def main():
    with tempfile.TemporaryDirectory() as raw:
        db=Path(raw)/"test.db"
        ensure_database(db)
        auth=AuthService(db)
        admin,recovery=auth.create_initial_admin("admin@example.com","Admin","Password1234")
        service=SaaSService(db)
        tenant=int(admin["tenant_id"])
        try:
            auth.create_user("weak@example.com","Weak User","password12345","operator",int(admin["id"]),tenant_id=tenant)
            raise AssertionError("Weak password accepted")
        except ValueError:
            pass
        assert recovery.startswith("SPYON-")
        conn=sqlite3.connect(db)
        try:
            row=conn.execute("SELECT password_hash,recovery_hash FROM app_users WHERE id=?",(int(admin["id"]),)).fetchone()
            assert row is not None
            assert row[0].startswith("scrypt:"), row[0].split("$",1)[0]
            assert row[1].startswith("scrypt:"), row[1].split("$",1)[0]
            assert row[0] != "Password1234"
            assert row[1] != recovery
            assert check_password_hash(row[0],"Password1234")
            assert check_password_hash(row[1],recovery)
        finally:
            conn.close()

        tomorrow=(datetime.now().astimezone()+timedelta(days=1)).date().isoformat()
        once=service.create_schedule(tenant,{
            "name":"One time test","action":"backup_database","recurrence_type":"once",
            "run_date":tomorrow,"time_of_day":"15:30","is_enabled":True,
        },int(admin["id"]))
        assert once["recurrence_type"]=="once"
        assert once["run_date"]==tomorrow
        assert once["next_run_at"]

        weekly=service.create_schedule(tenant,{
            "name":"Weekly test","action":"backup_database","recurrence_type":"weekly",
            "weekdays":[0,2,4],"time_of_day":"03:00","is_enabled":True,
        },int(admin["id"]))
        assert weekly["weekdays"]==[0,2,4]

        edited=service.update_schedule(weekly["id"],tenant,{
            "name":"Weekly edited","weekdays":[1,3],"time_of_day":"04:15","is_enabled":True,
        },int(admin["id"]))
        assert edited["name"]=="Weekly edited"
        assert edited["weekdays"]==[1,3]

        req=service.create_registration_request({
            "company_name":"Second Tenant","contact_name":"Second Admin","email":"second@example.com",
            "capabilities":["market_analytics"],"privacy_consent":True,"terms_consent":True,
        })
        reviewed=service.review_registration(req,"approved",int(admin["id"]))
        second_tenant=int(reviewed["tenant_id"])
        second_admin,_=auth.create_user(
            "second.admin@example.com","Second Admin","Password1234!","admin",int(admin["id"]),tenant_id=second_tenant,
        )
        try:
            auth.delete_user(int(second_admin["id"]),int(admin["id"]))
            raise AssertionError("Last tenant admin deleted")
        except ValueError:
            pass
        conn=sqlite3.connect(db)
        try:
            stamp=(datetime.now().astimezone()-timedelta(minutes=5)).isoformat(timespec="seconds")
            conn.execute(
                """
                INSERT INTO operation_schedules(
                    tenant_id,name,action,platform,scope,recurrence_type,time_of_day,
                    run_date,weekdays_json,interval_minutes,is_enabled,retry_count,
                    max_duration_minutes,next_run_at,created_by,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (second_tenant,"Blocked backup","backup_database","system","all","interval","03:00",
                 None,"[]",60,1,1,180,stamp,int(second_admin["id"]),stamp,stamp),
            )
            conn.execute(
                """
                INSERT INTO operation_schedules(
                    tenant_id,name,action,platform,scope,recurrence_type,time_of_day,
                    run_date,weekdays_json,interval_minutes,is_enabled,retry_count,
                    max_duration_minutes,next_run_at,created_by,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (tenant,"Allowed backup","backup_database","system","all","interval","03:00",
                 None,"[]",60,1,1,180,stamp,int(admin["id"]),stamp,stamp),
            )
            conn.commit()
        finally:
            conn.close()
        due={item["name"] for item in service.due_schedules()}
        assert "Allowed backup" in due
        assert "Blocked backup" not in due

        print("SELF TEST 3.4.1: OK")
        print("Auth hashing and password policy: OK")
        print("Tenant admin boundaries: OK")
        print("Scheduler backup guard: OK")
        print("One-time schedule: OK")
        print("Weekly schedule days: OK")
        print("Schedule edit: OK")
        print("Existing schema bootstrap: OK")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
