import uuid
from copy import deepcopy
from datetime import datetime, timedelta
from .models import Result


class ScheduledRoutineManager:
    """Persistent, polling scheduler; callers provide the clock in tests or UI loop."""
    def __init__(self, memory, routines, now=None):
        self.memory, self.routines, self._now = memory, routines, now or (lambda: datetime.now().astimezone())

    def _local(self, value=None):
        value=value or self._now()
        return value.astimezone() if value.tzinfo else value.replace(tzinfo=datetime.now().astimezone().tzinfo)
    def _time(self, value):
        try:
            hour, minute=(int(x) for x in value.split(":")); assert 0<=hour<24 and 0<=minute<60
            return hour,minute
        except (ValueError, AttributeError, AssertionError):
            return None
    def _next(self, definition, now=None):
        now=self._local(now); kind=definition.get("type")
        if kind=="once":
            try: return datetime.fromisoformat(definition["at"]).astimezone()
            except (KeyError, ValueError): return None
        parsed=self._time(definition.get("time",""))
        if not parsed: return None
        hour,minute=parsed; candidate=now.replace(hour=hour,minute=minute,second=0,microsecond=0)
        if kind=="daily": return candidate if candidate>=now else candidate+timedelta(days=1)
        if kind=="weekly" and isinstance(definition.get("weekday"),int) and 0<=definition["weekday"]<=6:
            candidate+=timedelta(days=(definition["weekday"]-candidate.weekday())%7)
            return candidate if candidate>=now else candidate+timedelta(days=7)
        return None
    def _definition(self, schedule):
        if not isinstance(schedule,dict): return None
        kind=schedule.get("type")
        if kind=="once": return {"type":"once","at":schedule.get("at")}
        if kind in {"daily","weekly"}:
            value={"type":kind,"time":schedule.get("time")}
            if kind=="weekly": value["weekday"]=schedule.get("weekday")
            return value
        return None
    def create_schedule(self,routine_id,schedule,preset_id=None,parameter_values=None):
        routine=self.routines.get_routine(routine_id); definition=self._definition(schedule)
        if not routine or not definition: return None
        if preset_id and not self.routines.get_preset(preset_id): return None
        if preset_id and self.routines.get_preset(preset_id).get("routine_id")!=routine_id: return None
        values=deepcopy(parameter_values or {})
        if self.routines._validate_preset_values(routine,values,require_complete=False): return None
        duplicate=next((x for x in self.memory.scheduled_routines if x.get("routine_id")==routine_id and x.get("schedule")==definition and x.get("preset_id")==preset_id and x.get("parameter_values",{})==values),None)
        if duplicate: return deepcopy(duplicate)
        now=self._local(); next_run=self._next(definition,now)
        if not next_run: return None
        item={"id":str(uuid.uuid4()),"routine_id":routine_id,"routine_name":routine["name"],"enabled":True,"status":"scheduled","schedule":definition,"preset_id":preset_id,"parameter_values":values,"created_at":now.isoformat(),"updated_at":now.isoformat(),"last_run_at":None,"next_run_at":next_run.isoformat()}
        self.memory.scheduled_routines.append(item); self.memory.save_scheduled_routines(); return deepcopy(item)
    def get_schedule(self,schedule_id):
        item=next((x for x in self.memory.scheduled_routines if x.get("id")==schedule_id),None); return deepcopy(item) if item else None
    def list_schedules(self): return deepcopy(self.memory.scheduled_routines)
    def update_schedule(self,schedule_id,**changes):
        item=next((x for x in self.memory.scheduled_routines if x.get("id")==schedule_id),None)
        if not item: return None
        if "schedule" in changes:
            definition=self._definition(changes["schedule"])
            if not definition: return None
            next_run=self._next(definition)
            if not next_run: return None
            item["schedule"],item["next_run_at"]=definition,next_run.isoformat()
        if "parameter_values" in changes:
            routine=self.routines.get_routine(item["routine_id"])
            if not routine or self.routines._validate_preset_values(routine,changes["parameter_values"],require_complete=False): return None
            item["parameter_values"]=deepcopy(changes["parameter_values"])
        if "preset_id" in changes:
            preset=self.routines.get_preset(changes["preset_id"]) if changes["preset_id"] else None
            if changes["preset_id"] and (not preset or preset.get("routine_id")!=item["routine_id"]): return None
            item["preset_id"]=changes["preset_id"]
        item["updated_at"]=self._local().isoformat(); self.memory.save_scheduled_routines(); return deepcopy(item)
    def delete_schedule(self,schedule_id):
        item=next((x for x in self.memory.scheduled_routines if x.get("id")==schedule_id),None)
        if not item: return False
        self.memory.scheduled_routines.remove(item); self.memory.save_scheduled_routines(); return True
    def _set_enabled(self,schedule_id,enabled):
        item=next((x for x in self.memory.scheduled_routines if x.get("id")==schedule_id),None)
        if not item: return None
        item["enabled"]=enabled; item["status"]="scheduled" if enabled else "disabled"; item["updated_at"]=self._local().isoformat()
        if enabled: item["next_run_at"]=self._next(item["schedule"]).isoformat()
        self.memory.save_scheduled_routines(); return deepcopy(item)
    def enable_schedule(self,schedule_id): return self._set_enabled(schedule_id,True)
    def disable_schedule(self,schedule_id): return self._set_enabled(schedule_id,False)
    def _invalid(self,item,status):
        item["enabled"]=False; item["status"]=status; item["updated_at"]=self._local().isoformat()
    def run_schedule(self,schedule_id,progress_callback=None):
        item=next((x for x in self.memory.scheduled_routines if x.get("id")==schedule_id),None)
        if not item or not item.get("enabled"): return Result(False,"Расписание отключено или не найдено.")
        routine=self.routines.get_routine(item["routine_id"])
        if not routine: self._invalid(item,"routine_deleted"); self.memory.save_scheduled_routines(); return Result(False,"Процедура удалена; расписание отключено.")
        values=deepcopy(item.get("parameter_values",{})); preset_id=item.get("preset_id")
        if preset_id:
            preset=self.routines.get_preset(preset_id)
            if not preset or preset.get("routine_id")!=routine["id"]: self._invalid(item,"preset_deleted"); self.memory.save_scheduled_routines(); return Result(False,"Профиль удалён; расписание отключено.")
            preset_values=deepcopy(preset["values"]); preset_values.update(values); values=preset_values
        result=self.routines.run(routine["name"],progress_callback=progress_callback,parameter_values=values,trigger="scheduled")
        now=self._local(); item["last_run_at"]=now.isoformat(); item["updated_at"]=now.isoformat(); item["status"]="completed" if result.ok else "failed"
        if item["schedule"]["type"]=="once": item["enabled"]=False
        else: item["next_run_at"]=self._next(item["schedule"],now+timedelta(seconds=1)).isoformat()
        self.memory.save_scheduled_routines(); return result
    def run_due(self,now=None,progress_callback=None):
        now=self._local(now); results=[]; changed=False
        for item in self.memory.scheduled_routines:
            if not item.get("enabled"): continue
            try: due=datetime.fromisoformat(item["next_run_at"]).astimezone()
            except (KeyError, ValueError): self._invalid(item,"invalid_schedule"); changed=True; continue
            if due>now: continue
            if now-due>timedelta(minutes=1):
                if item["schedule"]["type"]=="once": self._invalid(item,"missed")
                else: item["next_run_at"]=self._next(item["schedule"],now).isoformat(); item["status"]="missed"; item["updated_at"]=now.isoformat()
                changed=True; continue
            results.append(self.run_schedule(item["id"],progress_callback))
        if changed: self.memory.save_scheduled_routines()
        return results
