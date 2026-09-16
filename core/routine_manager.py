import uuid
import re
import threading
from copy import deepcopy
from datetime import datetime, timezone
from .models import Command, Result
from .nlp_utils import ordinal

class RoutineManager:
    PARAMETER_FIELDS={"OPEN_APPLICATION":{"application"},"SET_VOLUME":{"level"},"SEARCH_WEB":{"query"},"SEARCH_YOUTUBE":{"query"},"SEARCH_FILE":{"query"},"OPEN_BROWSER":{"url","private"}}
    def __init__(self,memory,router,events=None):
        self.memory,self.router,self.events=memory,router,events
        self._execution_lock=threading.RLock(); self._cancel_requested=threading.Event(); self._active=None
    def create(self,name,actions,description="",aliases=None,parameters=None):
        now=datetime.now(timezone.utc).isoformat(); item={"id":str(uuid.uuid4()),"name":name,"description":description,"actions":actions,"aliases":aliases or [],"parameters":parameters or [],"created_at":now,"updated_at":now,"usage_count":0,"last_used_at":None}
        self.memory.learned_routines.append(item); self.memory.save_learned_routines(); return item
    create_routine=create
    def get_routine(self, routine_id): return next((r for r in self.memory.learned_routines if r["id"]==routine_id),None)
    def _validate_preset_values(self,routine,values,require_complete=True):
        if not isinstance(values,dict): return Result(False,"Некорректные значения preset.")
        schemas={item.get("name"):item for item in routine.get("parameters",[]) if isinstance(item,dict)}
        if set(values)-set(schemas): return Result(False,"Preset содержит неизвестный параметр.")
        if any(schemas[name].get("sensitive") for name in values): return Result(False,"Sensitive параметры нельзя сохранять в preset.")
        required={name for name,item in schemas.items() if item.get("required",True) and "default" not in item}
        if require_complete and not required <= set(values): return Result(False,"Preset не содержит обязательные параметры.")
        _,error=self._prepare_actions(routine,values)
        if error and "Не указан обязательный" not in error.message: return error
        return None
    def create_preset(self,routine_id,name,values):
        routine=self.get_routine(routine_id)
        if not routine or not name or not name.strip(): return None
        if any(item["routine_id"]==routine_id and item["name"].lower()==name.strip().lower() for item in self.memory.routine_presets): return None
        error=self._validate_preset_values(routine,values)
        if error: return None
        now=datetime.now(timezone.utc).isoformat(); preset={"id":str(uuid.uuid4()),"routine_id":routine_id,"name":name.strip(),"values":deepcopy(values),"created_at":now,"updated_at":now}
        self.memory.routine_presets.append(preset); self.memory.save_routine_presets(); return deepcopy(preset)
    def get_preset(self,preset_id):
        preset=next((item for item in self.memory.routine_presets if item["id"]==preset_id),None); return deepcopy(preset) if preset else None
    def list_presets(self,routine_id): return deepcopy([item for item in self.memory.routine_presets if item["routine_id"]==routine_id])
    def find_preset(self,routine_id,name):
        preset=next((item for item in self.memory.routine_presets if item["routine_id"]==routine_id and item["name"].lower()==name.lower().strip()),None); return deepcopy(preset) if preset else None
    def update_preset(self,preset_id,name=None,values=None):
        preset=next((item for item in self.memory.routine_presets if item["id"]==preset_id),None)
        if not preset: return None
        routine=self.get_routine(preset["routine_id"])
        if not routine: return None
        next_values=values if values is not None else preset["values"]; error=self._validate_preset_values(routine,next_values)
        if error: return None
        if name is not None:
            if not name.strip() or any(item is not preset and item["routine_id"]==preset["routine_id"] and item["name"].lower()==name.strip().lower() for item in self.memory.routine_presets): return None
            preset["name"]=name.strip()
        preset["values"]=deepcopy(next_values); preset["updated_at"]=datetime.now(timezone.utc).isoformat(); self.memory.save_routine_presets(); return deepcopy(preset)
    def delete_preset(self,preset_id):
        preset=next((item for item in self.memory.routine_presets if item["id"]==preset_id),None)
        if not preset: return False
        self.memory.routine_presets.remove(preset); self.memory.save_routine_presets(); return True
    def record_execution(self,routine,started_at,status,total_steps,completed_steps,failed_step=None,cancellation_step=None,error=None,parameter_values=None,trigger="manual"):
        record={"id":str(uuid.uuid4()),"routine_id":routine["id"],"routine_name":routine["name"],"started_at":started_at,"finished_at":datetime.now(timezone.utc).isoformat(),"status":status,"total_steps":total_steps,"completed_steps":completed_steps,"failed_step":failed_step,"cancellation_step":cancellation_step,"error":error,"parameter_names":sorted((parameter_values or {}).keys()),"trigger":trigger}
        self.memory.routine_execution_history.insert(0,record)
        limit=self.memory.settings.get("routine_history_limit",100)
        limit=limit if isinstance(limit,int) and limit>0 else 100
        self.memory.routine_execution_history=self.memory.routine_execution_history[:limit]; self.memory.save_routine_execution_history()
        return deepcopy(record)
    def get_execution_history(self,routine_id,limit=10):
        limit=limit if isinstance(limit,int) and limit>0 else 10
        return deepcopy([record for record in self.memory.routine_execution_history if record.get("routine_id")==routine_id][:limit])
    def get_execution_history_by_name(self,name,limit=10):
        limit=limit if isinstance(limit,int) and limit>0 else 10
        return deepcopy([record for record in self.memory.routine_execution_history if record.get("routine_name","").lower()==name.lower()][:limit])
    def get_last_execution(self,routine_id):
        records=self.get_execution_history(routine_id,1); return records[0] if records else None
    def get_execution(self,execution_id):
        record=next((item for item in self.memory.routine_execution_history if item.get("id")==execution_id),None)
        return deepcopy(record) if record else None
    def recommend_routine(self,context=""):
        routines=self.list_routines()
        if not routines: return {"routine":None,"candidates":[],"reason":"none"}
        context=context.strip()
        exact=self.find(context) if context else None
        if exact: return {"routine":exact,"candidates":[],"reason":"exact"}
        discovered=self.find_routine(context) if context else None
        if discovered and discovered.get("confidence")=="HIGH" and discovered.get("routine"): return {"routine":discovered["routine"],"candidates":[],"reason":"high"}
        stem=lambda word: word[:-2] if word.endswith(("ом","ам","ем")) else word[:-1] if word.endswith(("у","а","ы","е")) else word
        recommendation_stop=self.STOP-{"перед"}
        query={stem(word) for word in re.findall(r"[\wа-яё]+",context.lower()) if word not in recommendation_stop}
        ranked=[]
        for routine in routines:
            corpus=routine["name"]+" "+routine.get("description","")+" "+" ".join(routine.get("aliases",[]))
            context_score=len(query & {stem(word) for word in re.findall(r"[\wа-яё]+",corpus.lower()) if word not in recommendation_stop})
            history=self.get_execution_history(routine["id"],100); successful=sum(item["status"]=="success" for item in history); failed=sum(item["status"] in {"failed","cancelled"} for item in history)
            ranked.append(((context_score,successful-failed,successful,routine.get("usage_count",0)),routine))
        best=max(score for score,_ in ranked); candidates=[routine for score,routine in ranked if score==best]
        if len(candidates)==1: return {"routine":candidates[0],"candidates":[],"reason":"ranked"}
        return {"routine":None,"candidates":candidates,"reason":"ambiguous"}
    def list_routines(self): return list(self.memory.learned_routines)
    def update_routine(self,routine_id,**changes):
        item=self.get_routine(routine_id)
        if not item: return None
        for key in ("name","description","actions","parameters"):
            if key in changes: item[key]=changes[key]
        item["updated_at"]=datetime.now(timezone.utc).isoformat(); self.memory.save_learned_routines(); return item
    def delete_routine(self,routine_id):
        item=self.get_routine(routine_id)
        if not item: return False
        self.memory.learned_routines.remove(item); self.memory.save_learned_routines()
        changed=False
        for schedule in getattr(self.memory,"scheduled_routines",[]):
            if schedule.get("routine_id")==routine_id:
                schedule["enabled"]=False; schedule["status"]="routine_deleted"; schedule["updated_at"]=datetime.now().astimezone().isoformat(); changed=True
        if changed: self.memory.save_scheduled_routines()
        return True
    def add_alias(self,routine_id,alias):
        item=self.get_routine(routine_id); alias=alias.strip().lower()
        if not item or not alias or alias in [x.lower() for x in item["aliases"]]: return False
        if any(alias in [x.lower() for x in r.get("aliases",[])] for r in self.memory.learned_routines if r is not item): return False
        if alias in self.memory.custom_commands or any(alias==s.get("trigger","").lower() for s in self.memory.skills): return False
        item["aliases"].append(alias); self.memory.save_learned_routines(); return True
    def remove_alias(self,routine_id,alias):
        item=self.get_routine(routine_id); alias=alias.strip().lower()
        if not item or alias not in [x.lower() for x in item["aliases"]]: return False
        item["aliases"]=[x for x in item["aliases"] if x.lower()!=alias]; self.memory.save_learned_routines(); return True
    def find(self,name):
        query=name.lower().strip(); return next((r for r in self.memory.learned_routines if query==r["name"].lower() or query in [x.lower() for x in r.get("aliases",[])]),None)
    # Exact normalized phrases are HIGH; >=2 significant shared words is MEDIUM.
    STOP={"к","и","для","перед","меня","сделай","мою","как","обычно"}
    def find_routine(self, query):
        norm=lambda value: re.sub(r"\s+"," ",re.sub(r"[^\wа-яё ]"," ",value.lower())).strip()
        q=norm(query)
        exact=[]
        for routine in self.memory.learned_routines:
            if q==norm(routine["name"]): exact.append((routine,"name"))
            for alias in routine.get("aliases",[]):
                if q==norm(alias): exact.append((routine,"alias"))
        if len(exact)==1: return {"routine":exact[0][0],"confidence":"HIGH","matched_by":exact[0][1],"candidates":[]}
        stem=lambda word: word[:-2] if word.endswith(("ом","ам","ем")) else word[:-1] if word.endswith(("у","а","ы","е")) else word
        tokens={stem(word) for word in q.split() if word not in self.STOP}; scored=[]
        for routine in self.memory.learned_routines:
            corpus=norm(routine["name"]+" "+routine.get("description","")+" "+" ".join(routine.get("aliases",[])))
            score=len(tokens & {stem(word) for word in corpus.split() if word not in self.STOP});
            if score: scored.append((score,routine))
        if not scored: return {"routine":None,"confidence":"LOW","matched_by":None,"candidates":[]}
        scored.sort(key=lambda x:x[0],reverse=True); best=scored[0][0]; candidates=[r for score,r in scored if score==best]
        if best>=2 and len(candidates)==1: return {"routine":candidates[0],"confidence":"MEDIUM","matched_by":"tokens","candidates":[]}
        return {"routine":None,"confidence":"LOW","matched_by":None,"candidates":candidates}
    def discover(self,text):
        words=set(text.lower().split()); best=None; score=0
        for routine in self.memory.learned_routines:
            tokens=set((routine["name"]+" "+routine.get("description","")+" "+" ".join(routine.get("aliases",[]))).lower().split()); current=len(words & tokens)
            if current>score: best,score=routine,current
        return (best,"HIGH" if score>=3 else "MEDIUM" if score>=2 else "LOW") if best else (None,"LOW")
    def _action_description(self,action):
        parameters=action.get("parameters",{}) if isinstance(action,dict) else {}
        intent=action.get("intent","UNKNOWN") if isinstance(action,dict) else "UNKNOWN"
        if intent=="OPEN_APPLICATION": return f"открываю {parameters.get('application','приложение')}"
        if intent=="SET_VOLUME": return f"устанавливаю громкость {parameters.get('level','')}%"
        if intent=="OPEN_BROWSER": return "открываю браузер"
        return f"выполняю {intent}"
    def find_step_indexes(self,routine,selector):
        selector=selector.lower().strip(" ."); by_number=ordinal(selector)
        if by_number is not None and "шаг" in selector:
            return [by_number] if 1<=by_number<=len(routine.get("actions",[])) else []
        wanted=set(re.findall(r"[\wа-яё]+",selector))
        if not wanted: return []
        matches=[]
        for index,action in enumerate(routine.get("actions",[]),1):
            values=" ".join(str(value) for value in action.get("parameters",{}).values()) if isinstance(action,dict) else ""
            haystack=f"{self._action_description(action)} {values}".lower()
            if wanted <= set(re.findall(r"[\wа-яё]+",haystack)): matches.append(index)
        return matches
    def _progress(self,callback,**payload):
        try:
            if callback: callback(payload)
        except Exception: pass
        if self.events: self.events.emit("routine_progress",**payload)
    def _prepare_actions(self,routine,parameter_values):
        supplied=parameter_values or {}; schemas=routine.get("parameters",[])
        if not isinstance(schemas,list) or not isinstance(supplied,dict): return None,Result(False,"Некорректные параметры процедуры.")
        bindings={}; names=set()
        validators={"string":lambda value:isinstance(value,str),"integer":lambda value:isinstance(value,int) and not isinstance(value,bool),"boolean":lambda value:isinstance(value,bool)}
        for schema in schemas:
            if not isinstance(schema,dict) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",str(schema.get("name",""))) or schema.get("type") not in validators: return None,Result(False,"Некорректное описание параметра процедуры.")
            name=schema["name"]
            if name in names: return None,Result(False,"Параметры процедуры содержат повторяющееся имя.")
            names.add(name)
            value=supplied[name] if name in supplied else schema.get("default")
            if name not in supplied and "default" not in schema and schema.get("required",True): return None,Result(False,f"Не указан обязательный параметр «{name}».")
            if name in supplied or "default" in schema:
                if not validators[schema["type"]](value): return None,Result(False,f"Параметр «{name}» имеет неверный тип.")
                if isinstance(value,str) and ("\x00" in value or ".." in value or any(token in value.lower() for token in ("cmd.exe","powershell",";","&&","|","$(","\n","\r"))): return None,Result(False,f"Недопустимое значение параметра «{name}».")
                bindings[name]=value
        unknown=set(supplied)-names
        if unknown: return None,Result(False,"Передан неизвестный параметр процедуры.")
        resolved=deepcopy(routine.get("actions",[]))
        for action in resolved:
            if not isinstance(action,dict) or not isinstance(action.get("intent"),str) or "{" in action["intent"] or "}" in action["intent"]: return None,Result(False,"Тип действия нельзя параметризовать.")
            if "plugin" in action: return None,Result(False,"Маршрут plugin нельзя задавать в Routine.")
            parameters=action.get("parameters",{})
            if not isinstance(parameters,dict): return None,Result(False,"Некорректные параметры действия.")
            for field,value in list(parameters.items()):
                if isinstance(value,str) and ("{" in value or "}" in value):
                    match=re.fullmatch(r"\{([A-Za-z_][A-Za-z0-9_]*)\}",value)
                    if not match or field not in self.PARAMETER_FIELDS.get(action["intent"],set()): return None,Result(False,"Подстановка разрешена только в безопасных полях действия.")
                    key=match.group(1)
                    if key not in bindings: return None,Result(False,f"Не указан обязательный параметр «{key}».")
                    parameters[field]=bindings[key]
        return resolved,None
    def validate_definition(self,actions,parameters):
        samples={"string":"value","integer":1,"boolean":True}; values={}
        if isinstance(parameters,list):
            for parameter in parameters:
                if isinstance(parameter,dict) and parameter.get("name"):
                    values[parameter["name"]]=parameter.get("default",samples.get(parameter.get("type")))
        _,error=self._prepare_actions({"actions":actions,"parameters":parameters},values)
        return error
    def get_execution_state(self):
        with self._execution_lock:
            if not self._active: return {"active":False,"routine_name":None,"current_step":0,"total_steps":0,"can_cancel":False}
            return {"active":True,"routine_name":self._active["routine_name"],"current_step":self._active["current_step"],"total_steps":self._active["total_steps"],"skipped_step_indexes":list(self._active.get("skipped_step_indexes",[])),"parameter_names":list(self._active.get("parameter_names",[])),"can_cancel":True}
    def cancel_active(self):
        with self._execution_lock:
            if not self._active: return Result(False,"Сейчас ничего не выполняется.")
            if self._cancel_requested.is_set(): return Result(True,"Отмена выполнения уже запрошена.")
            self._cancel_requested.set()
            return Result(True,f"Отмена процедуры «{self._active['routine_name']}» запрошена.")
    def _cancel_result(self,routine,results,callback):
        state=self.get_execution_state(); current,total=state["current_step"],state["total_steps"]
        message=f"Выполнение процедуры остановлено на шаге {current} из {total}."
        self._progress(callback,routine_name=routine["name"],current_step=current,total_steps=total,status="cancelled",success=False,cancelled=True,action_description="",message=message)
        return Result(False,message,{"results":[result.message for result in results],"cancelled":True})
    def run(self,name,progress_callback=None,skipped_indexes=None,parameter_values=None,trigger="manual"):
        routine=self.find(name)
        if not routine: return Result(False,"Процедура не найдена.")
        started_at=datetime.now(timezone.utc).isoformat()
        actions,error=self._prepare_actions(routine,parameter_values)
        if error:
            self.record_execution(routine,started_at,"failed",0,0,error=error.message,parameter_values=parameter_values,trigger=trigger)
            return error
        original_total=len(actions); skipped_indexes=set(skipped_indexes or [])
        planned=[(index,action) for index,action in enumerate(actions,1) if index not in skipped_indexes]
        total=len(planned)
        if not actions:
            message=f"Процедура «{routine['name']}» не содержит действий."
            self._progress(progress_callback,routine_name=routine["name"],current_step=0,total_steps=0,status="empty",success=False,action_description="",message=message)
            self.record_execution(routine,started_at,"failed",0,0,error="В процедуре нет действий.",parameter_values=parameter_values,trigger=trigger)
            return Result(False,message,{"results":[]})
        if not planned:
            message=f"В процедуре «{routine['name']}» не осталось действий для выполнения."
            self._progress(progress_callback,routine_name=routine["name"],current_step=0,total_steps=0,original_total_steps=original_total,status="empty",success=False,cancelled=False,action_description="",message=message)
            self.record_execution(routine,started_at,"failed",0,0,error="Нет действий для выполнения.",parameter_values=parameter_values,trigger=trigger)
            return Result(False,message,{"results":[]})
        with self._execution_lock:
            if self._active: return Result(False,"Другая процедура уже выполняется.")
            self._cancel_requested.clear(); self._active={"routine_name":routine["name"],"current_step":0,"total_steps":total,"skipped_step_indexes":sorted(skipped_indexes),"parameter_names":sorted((parameter_values or {}).keys())}
        try:
            suffix=" с пропуском выбранных шагов" if skipped_indexes else ""
            self._progress(progress_callback,routine_name=routine["name"],current_step=0,total_steps=total,original_total_steps=original_total,status="started",success=None,cancelled=False,action_description="",message=f"Запускаю процедуру «{routine['name']}»{suffix}.")
            for original_index in sorted(skipped_indexes):
                if 1<=original_index<=original_total:
                    description=self._action_description(actions[original_index-1])
                    self._progress(progress_callback,routine_name=routine["name"],current_step=0,total_steps=total,original_step=original_index,original_total_steps=original_total,status="skipped",success=None,cancelled=False,skipped=True,action_description=description,message=f"Пропущен шаг {original_index} из {original_total} — {description}.")
            results=[]
            for index,(original_index,action) in enumerate(planned,1):
                if self._cancel_requested.is_set():
                    cancelled=self._cancel_result(routine,results,progress_callback)
                    self.record_execution(routine,started_at,"cancelled",total,sum(result.ok for result in results),cancellation_step=index,error="Выполнение отменено.",parameter_values=parameter_values,trigger=trigger)
                    return cancelled
                with self._execution_lock: self._active["current_step"]=index
                description=self._action_description(action)
                try:
                    if not isinstance(action,dict) or not action.get("intent"): raise ValueError("Некорректное действие.")
                    result=self.router.route(Command(action["intent"],action.get("parameters",{}),routine["name"]))
                    if not isinstance(result,Result): raise ValueError("Executor вернул некорректный результат.")
                except Exception as exc:
                    result=Result(False,f"Действие не выполнено: {exc}")
                results.append(result)
                message=f"Шаг {index} из {total} — {description}." if result.ok else f"Шаг {index} из {total} не выполнен: {result.message}"
                self._progress(progress_callback,routine_name=routine["name"],current_step=index,total_steps=total,original_step=original_index,original_total_steps=original_total,status="step_succeeded" if result.ok else "step_failed",success=result.ok,cancelled=False,action_description=description,message=message)
            success=all(result.ok for result in results)
            if success:
                routine["usage_count"]+=1; routine["last_used_at"]=datetime.now(timezone.utc).isoformat(); self.memory.save_learned_routines()
            message=f"Процедура «{routine['name']}» выполнена." if success else f"Процедура «{routine['name']}» выполнена не полностью."
            failed_step=next((index for index,result in enumerate(results,1) if not result.ok),None)
            self.record_execution(routine,started_at,"success" if success else "failed",total,sum(result.ok for result in results),failed_step=failed_step,error=None if success else f"Шаг {failed_step} не выполнен.",parameter_values=parameter_values,trigger=trigger)
            self._progress(progress_callback,routine_name=routine["name"],current_step=total,total_steps=total,status="completed" if success else "failed",success=success,cancelled=False,action_description="",message="Готово." if success else "Выполнение процедуры завершено с ошибками.")
            return Result(success,message,{"results":[result.message for result in results]})
        finally:
            with self._execution_lock: self._active=None; self._cancel_requested.clear()
