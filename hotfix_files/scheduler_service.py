from __future__ import annotations
import threading
from typing import Any, Callable
from saas_service import SaaSService

class SchedulerService:
    def __init__(self,saas:SaaSService,task_manager:Any,action_info:dict[str,dict[str,Any]],command_builder:Callable[[str,list[str],int],list[str]],interval_seconds:int=30):
        self.saas=saas; self.task_manager=task_manager; self.action_info=action_info; self.command_builder=command_builder; self.interval_seconds=max(15,int(interval_seconds)); self.stop_event=threading.Event(); self.thread=None
    def start(self)->None:
        if self.thread and self.thread.is_alive():return
        self.thread=threading.Thread(target=self._loop,daemon=True,name='itp-scheduler'); self.thread.start()
    def stop(self)->None:self.stop_event.set()
    def _loop(self)->None:
        while not self.stop_event.wait(self.interval_seconds):
            try:self.run_due_once()
            except Exception:pass
    def run_due_once(self)->None:
        for schedule in self.saas.due_schedules():
            action=str(schedule.get('action') or ''); info=self.action_info.get(action); run_id=self.saas.begin_schedule_run(schedule)
            if not info:self.saas.finish_schedule_run(run_id,int(schedule['id']),'failed','Операция больше не поддерживается.'); continue
            try:
                uid=int(schedule.get('created_by') or 0); platform=info.get('platform') or ('halyk_market' if action.startswith('halyk_') else 'ozon' if action.startswith('ozon_') else 'system' if action in {'export_report','audit_catalog','backup_database'} else 'kaspi'); task=self.task_manager.start(action,f"{info['label']} — по расписанию",self.command_builder(action,[],uid),info.get('resource') or [],metadata={'scope':'all','scheduled':True,'schedule_id':int(schedule['id']),'tenant_id':int(schedule['tenant_id']),'requested_by_id':uid,'platform':platform})
                self.saas.attach_task_to_run(run_id,str(task['id'])); threading.Thread(target=self._watch,args=(run_id,int(schedule['id']),str(task['id'])),daemon=True).start()
            except Exception as exc:self.saas.finish_schedule_run(run_id,int(schedule['id']),'failed',str(exc))
    def _watch(self,run_id:int,schedule_id:int,task_id:str)->None:
        while not self.stop_event.wait(5):
            task=self.task_manager.state(task_id)
            if task.get('running'):continue
            self.saas.finish_schedule_run(run_id,schedule_id,str(task.get('status') or 'failed'),str(task.get('message') or 'Операция завершена')); return
