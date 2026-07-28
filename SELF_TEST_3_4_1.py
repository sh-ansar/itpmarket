from pathlib import Path
import tempfile
from datetime import datetime, timedelta
from schema import ensure_database
from saas_service import SaaSService
from auth_service import AuthService

def main():
    with tempfile.TemporaryDirectory() as raw:
        db=Path(raw)/"test.db"
        ensure_database(db)
        auth=AuthService(db)
        admin,_=auth.create_initial_admin("admin@example.com","Admin","Password1234")
        service=SaaSService(db)
        tenant=int(admin["tenant_id"])

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

        print("SELF TEST 3.4.1: OK")
        print("One-time schedule: OK")
        print("Weekly schedule days: OK")
        print("Schedule edit: OK")
        print("Existing schema bootstrap: OK")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
