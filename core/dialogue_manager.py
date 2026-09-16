"""Finite, confirmation-first voice dialogues for skill and alias management."""
import re
import time
from copy import deepcopy
from .nlp_utils import ordinal, ORDINALS
from .models import Result

class DialogueManager:
    TIMEOUT_SECONDS=300
    def __init__(self, learning, skills, memory):
        self.learning,self.skills,self.memory=learning,skills,memory; self.pending=None
    def _set_pending(self, value): self.pending=value; self.pending and self.pending.update(updated_at=time.monotonic())
    @staticmethod
    def _yes(text): return text.strip().lower().rstrip(" .!?…").strip() in {"да","так","yes","ага","подтверждаю"}
    def start_create(self, name=None, description=""):
        if not name: self._set_pending({"kind":"create","step":"name"}); return Result(True,"Как назвать новый режим?")
        self._set_pending({"kind":"create","name":name,"step":"actions"})
        if description:
            return self.handle(description)
        return Result(True,f"Что должен делать режим «{name}»?")
    def start_delete(self,name):
        if not self.skills.find(name): return Result(False,"Режим не найден.")
        self.pending={"kind":"delete","name":name,"step":"confirm"}; return Result(True,f"Удалить режим «{name}" + "»?",confirmation_required=True)
    def start_edit(self,name):
        skill=self.skills.find(name)
        if not skill: return Result(False,"Режим не найден.")
        self._set_pending({"kind":"edit","name":name,"actions":deepcopy(skill["actions"]),"step":"change"}); return Result(True,f"Что изменить в режиме «{name}»?")
    def _preview(self, pending):
        items=[]
        parameters=pending.get("parameters",[])
        if parameters:
            items.append("Параметры:")
            for parameter in parameters:
                suffix=" (необязательный)" if not parameter.get("required",True) else ""
                default=f", по умолчанию: {parameter['default']}" if "default" in parameter else ""
                items.append(f"- {parameter['name']}: {parameter['type']}{suffix}{default}")
            items.append("Действия:")
        for index,action in enumerate(pending.get("actions",pending.get("draft",{}).get("actions",[])),1):
            label=action["intent"].replace("OPEN_APPLICATION","Open ").replace("SET_VOLUME","Volume ").replace("_"," ")
            value=action.get("parameters",{}).get("application") or action.get("parameters",{}).get("level")
            items.append(f"{index}. {label}{(' '+str(value)) if value is not None else ''}")
        return "\n".join(items) or "(нет действий)"
    def _action_index(self, actions, text):
        value=ordinal(text)
        if value is not None and 1<=value<=len(actions): return value-1
        target=re.sub(r".*?(?:перемести|поставь|убери|удали)\s+", "", text.lower()).split()[0] if text else ""
        return next((i for i,a in enumerate(actions) if target and target in str(a.get("parameters",{})).lower()),None)
    def _ordinals(self, text):
        values=[]
        for token in re.findall(r"\d+|[а-яё]+",text.lower()):
            if token.isdigit(): values.append(int(token))
            elif token in ORDINALS: values.append(ORDINALS[token])
        return values
    def start_alias(self,alias,target):
        self.pending={"kind":"alias","alias":alias.lower(),"target":target,"step":"confirm"}
        existing=self.memory.aliases.get(alias.lower())
        message=f"«{alias}» уже связан с {existing}. Заменить на {target}?" if existing and existing.lower()!=target.lower() else f"Запомнить: «{alias}» — это «{target}»?"
        return Result(True,message,confirmation_required=True)
    def start_custom_command(self, phrase, skill_name):
        if not skill_name:
            self.pending={"kind":"custom","phrase":phrase.strip().lower(),"step":"skill"}; return Result(True,"Что должна делать команда? Укажите существующий режим.")
        if not self.skills.find(skill_name): return Result(False,"Указанный режим не найден.")
        key=phrase.strip().lower()
        replacement=key in self.memory.custom_commands
        self.pending={"kind":"custom","phrase":key,"skill":skill_name,"step":"confirm"}
        message=("Команда уже существует. Заменить её" if replacement else "Создать команду") + f" «{phrase}» для режима «{skill_name}»?"
        return Result(True,message,confirmation_required=True)
    def start_ambiguous_application(self, application):
        self._set_pending({"kind":"ambiguous","application":application,"step":"confirm"})
        return Result(True,f"Я не уверен. Возможно, вы имеете в виду {application}. Открыть?",confirmation_required=True)
    def start_routine_confirmation(self, routine):
        self._set_pending({"kind":"routine_execution","routine":routine,"step":"confirm"}); return Result(True,f"Я нашёл процедуру «{routine['name']}». Выполнить её?",confirmation_required=True)
    def start_routine_candidates(self,candidates,run_context=None):
        self._set_pending({"kind":"routine_candidates","candidates":candidates,"step":"select","run_context":run_context}); return Result(True,"Я нашёл несколько процедур:\n"+"\n".join(f"{i+1}. {r['name']}" for i,r in enumerate(candidates)))
    def start_resource_confirmation(self,candidate):
        self._set_pending({"kind":"resource_confirmation","candidate":candidate,"step":"confirm"})
        return Result(True,f"Нашёл {candidate['name']}. Открыть?",{"read_only_guidance":True},confirmation_required=True)
    def start_resource_candidates(self,candidates):
        self._set_pending({"kind":"resource_candidates","candidates":candidates,"step":"select"})
        return Result(True,"Я нашёл несколько вариантов:\n"+"\n".join(f"{i+1}. {item['name']}" for i,item in enumerate(candidates)),{"read_only_guidance":True})
    def start_schedule_candidates(self,candidates,action,update=None):
        self._set_pending({"kind":"schedule_candidates","candidates":candidates,"action":action,"update":update,"step":"select"})
        return Result(True,"Я нашёл несколько расписаний:\n"+"\n".join(f"{i+1}. {item.get('label',item['routine_name'])}" for i,item in enumerate(candidates)))
    @staticmethod
    def _parameter_question(parameter):
        labels={"platform":"Какую платформу использовать?","level":"Какой уровень громкости установить?","volume":"Какой уровень громкости установить?"}
        return labels.get(parameter["name"],f"Укажите значение параметра «{parameter['name']}».")
    def start_routine_parameter_clarification(self,routine,values=None,skipped_indexes=None):
        values=dict(values or {})
        missing=[item for item in routine.get("parameters",[]) if item.get("required",True) and "default" not in item and item.get("name") not in values]
        if not missing: return None
        self._set_pending({"kind":"routine_parameters","step":"value","routine":routine,"values":values,"missing":missing,"index":0,"skipped_indexes":list(skipped_indexes or [])})
        return Result(True,self._parameter_question(missing[0]))
    def start_browser_clarification(self):
        self._set_pending({"kind":"browser_clarification","step":"site"})
        return Result(True,"Какой сайт открыть?")
    def start_volume_clarification(self):
        self._set_pending({"kind":"volume_clarification","step":"level"})
        return Result(True,"Какую громкость установить?")
    def start_folder_clarification(self):
        self._set_pending({"kind":"folder_clarification","step":"folder"})
        return Result(True,"Какую папку открыть: Загрузки, Документы или Рабочий стол?")
    def pending_recap(self):
        """Describe the current pending dialogue without changing it."""
        if not self.pending: return "Сейчас я ничего не жду."
        kind,step=self.pending.get("kind"),self.pending.get("step")
        if kind=="browser_clarification" and step=="site": return "Я жду название или адрес сайта."
        if kind=="volume_clarification" and step=="level": return "Я жду значение громкости от 0 до 100."
        if kind=="folder_clarification" and step=="folder": return "Я жду: Загрузки, Документы или Рабочий стол."
        if kind=="routine_parameters" and step=="value":
            missing=self.pending.get("missing",[]); index=self.pending.get("index",0)
            if 0<=index<len(missing): return "Я жду параметр. "+self._parameter_question(missing[index])
        return "Я жду ответ для текущего диалога."
    @staticmethod
    def _parameter_value(parameter,text):
        value=text.strip()
        if parameter["type"]=="string": return value if value else None
        if parameter["type"]=="integer": return int(value) if re.fullmatch(r"-?\d+",value) else None
        if parameter["type"]=="boolean":
            return True if value.lower() in {"да","true","включить"} else False if value.lower() in {"нет","false","выключить"} else None
        return None
    @staticmethod
    def _correction_value(text):
        """Extract a replacement only from explicit deterministic correction forms."""
        value=text.strip().strip(" .")
        lower=value.lower()
        if lower in {"не это","другое значение"}: return True,""
        patterns=(r"^нет\s*,?\s*(.+)$",r"^исправь\s+на\s+(.+)$",r"^не\s+.+?\s*,?\s*а\s+(.+)$",r"^поставь\s+(.+?)\s+вместо\s+.+$")
        for pattern in patterns:
            match=re.match(pattern,value,flags=re.I)
            if match: return True,match.group(1).strip(" .")
        return False,value
    def add_routine_alias(self,routine,alias):
        from .routine_manager import RoutineManager
        if not alias.strip(): return Result(False,"Алиас не может быть пустым.")
        if RoutineManager(self.memory,self.skills.router).add_alias(routine["id"],alias): return Result(True,"Алиас добавлен.")
        return Result(False,"Не удалось добавить алиас: он уже занят или существует.")
    def remove_routine_alias(self,routine,alias):
        from .routine_manager import RoutineManager
        if RoutineManager(self.memory,self.skills.router).remove_alias(routine["id"],alias): return Result(True,"Алиас удалён.")
        return Result(False,"Алиас не найден.")
    def handle(self,text):
        if not self.pending: return None
        if self.pending.get("updated_at") and time.monotonic()-self.pending["updated_at"]>self.TIMEOUT_SECONDS:
            self.pending=None; return Result(False,"Время черновика истекло; изменения не сохранены.")
        pending=self.pending; kind,step=pending["kind"],pending["step"]
        pending["updated_at"]=time.monotonic()
        lower=text.lower().strip()
        if lower in {"отмена","отмени","не надо","стоп","нет","cancel"} and kind in {"create","edit","routine_candidates","routine_parameters","resource_candidates","resource_confirmation","schedule_candidates","browser_clarification","volume_clarification","folder_clarification"}:
            self.pending=None; return Result(True,"Черновик отменён. Сохранённый режим не изменён.")
        if kind=="browser_clarification" and step=="site":
            corrected,value=self._correction_value(text)
            if corrected and not value: return Result(False,"Укажите новый сайт.")
            if value.lower() in {"youtube","ютуб"}:
                self.pending=None; return Result(True,"Открываю YouTube.",{"command":{"intent":"OPEN_YOUTUBE","parameters":{}},"corrected":corrected})
            if re.fullmatch(r"https?://[^\s/]+(?:/[^\s]*)?",value,re.I):
                self.pending=None; return Result(True,"Открываю сайт.",{"command":{"intent":"OPEN_BROWSER","parameters":{"url":value}},"corrected":corrected})
            return Result(False,"Я не понял название сайта. Назови сайт ещё раз.",{"read_only_guidance":True})
        if kind=="volume_clarification" and step=="level":
            corrected,value=self._correction_value(text)
            if corrected and not value: return Result(False,"Укажите новое значение громкости.")
            if not re.fullmatch(r"\d{1,3}",value) or not 0<=int(value)<=100: return Result(False,"Я не понял. Назови громкость числом от 0 до 100.",{"read_only_guidance":True})
            self.pending=None; return Result(True,"Устанавливаю громкость.",{"command":{"intent":"SET_VOLUME","parameters":{"level":int(value)}},"corrected":corrected})
        if kind=="folder_clarification" and step=="folder":
            options={"загрузки":"загрузки","документы":"документы","рабочий стол":"рабочий стол"}
            corrected,raw_value=self._correction_value(text); value=next((target for phrase,target in options.items() if phrase in raw_value.lower()),None)
            if corrected and not raw_value: return Result(False,"Укажите новую папку.")
            if not value: return Result(False,"Я не понял. Выберите: Загрузки, Документы или Рабочий стол.",{"read_only_guidance":True})
            self.pending=None; return Result(True,"Открываю папку.",{"command":{"intent":"OPEN_FOLDER","parameters":{"query":value}},"corrected":corrected})
        if kind=="custom" and step=="skill":
            match=re.search(r"(?:запускать )?(?:режим )?(.+)",text.lower())
            return self.start_custom_command(pending["phrase"],match.group(1).strip(" .") if match else text.strip())
        if kind=="routine_candidates" and step=="select":
            index={"первую":1,"вторую":2}.get(lower)
            if lower.isdigit(): index=int(lower)
            if not index or not 1<=index<=len(pending["candidates"]): return Result(False,"Пожалуйста, выберите номер процедуры.")
            selected=pending["candidates"][index-1]; self.pending=None
            if pending.get("run_context") is not None:
                context=pending["run_context"]; return Result(True,"Выбрано.",{"routine_run":{"name":selected["name"],"values":context.get("values",{}),"skipped_indexes":context.get("skipped_indexes",[]),"skip_selector":context.get("skip_selector",""),"value":context.get("value","")}})
            return Result(True,"Выбрано.",{"routine":selected["name"]})
        if kind=="resource_candidates" and step=="select":
            index={"первый":1,"первую":1,"второй":2,"вторую":2}.get(lower)
            if lower.isdigit(): index=int(lower)
            if not index or not 1<=index<=len(pending["candidates"]): return Result(False,"Пожалуйста, выберите номер варианта.")
            selected=pending["candidates"][index-1]; self.pending=None
            return Result(True,"Выбрано.",{"command":selected["command"]})
        if kind=="schedule_candidates" and step=="select":
            index={"первую":1,"вторую":2}.get(lower)
            if lower.isdigit(): index=int(lower)
            if not index or not 1<=index<=len(pending["candidates"]): return Result(False,"Пожалуйста, выберите номер расписания.")
            selected=pending["candidates"][index-1]; self.pending=None
            return Result(True,"Выбрано.",{"schedule_action":{"id":selected["id"],"action":pending["action"],"update":pending.get("update")}})
        if kind=="routine_parameters" and step=="value":
            parameter=pending["missing"][pending["index"]]; corrected,raw_value=self._correction_value(text); value=self._parameter_value(parameter,raw_value)
            if corrected and not raw_value: return Result(False,"Укажите новое значение. "+self._parameter_question(parameter))
            if value is None:
                message="Мне нужно числовое значение." if parameter["type"]=="integer" else "Мне нужно значение да или нет." if parameter["type"]=="boolean" else "Мне нужно непустое текстовое значение."
                return Result(False,message+" "+self._parameter_question(parameter),{"read_only_guidance":True})
            pending["values"][parameter["name"]]=value; pending["index"]+=1
            if pending["index"]<len(pending["missing"]): return Result(True,self._parameter_question(pending["missing"][pending["index"]]))
            self.pending=None; return Result(True,"Параметры приняты.",{"routine_run":{"name":pending["routine"]["name"],"values":pending["values"],"skipped_indexes":pending["skipped_indexes"]},"corrected":corrected})
        if kind=="routine" and step=="name":
            name=re.sub(r"^назови (?:её|ее)\s+", "", text.strip(), flags=re.I).strip(" .")
            if not name: return Result(False,"Назовите процедуру.")
            pending.update(name=name,step="confirm"); return Result(True,"Черновик:\n"+self._preview(pending)+"\nСохранить?",confirmation_required=True)
        if kind=="create" and step=="name":
            pending.update(name=text.strip().strip(". !"),step="actions"); return Result(True,f"Что должен делать режим «{pending['name']}»?")
        if kind=="demonstration" and step=="name":
            pending.update(name=text.strip().strip(". !"),step="icon"); return Result(True,"Выберите иконку (emoji) или скажите «по умолчанию».")
        if kind=="demonstration" and step=="icon":
            icon=None if text.strip().lower() in {"по умолчанию","default"} else text.strip()
            pending.update(icon=icon,step="confirm"); return Result(True,"Сохранить записанные действия как новый режим?",confirmation_required=True)
        if kind=="create" and step=="actions":
            if lower in {"сохрани","сохранить","готово","preview","покажи что получилось","покажи действия"}:
                draft=pending.get("draft",{"name":pending["name"],"trigger":pending["name"].lower(),"actions":[]})
                if not draft["actions"]: return Result(False,"В черновике нет действий.")
                pending.update(draft=draft,step="confirm"); return Result(True,"Черновик:\n"+self._preview(pending)+"\nСохранить?",confirmation_required=True)
            draft=pending.setdefault("draft",{"name":pending["name"],"trigger":pending["name"].lower(),"actions":[]})
            new=self.learning.extract_actions(text)
            if not new and lower.startswith("добавь "):
                app=re.sub(r"^добавь\s+", "", lower).replace("в начало","").replace("в конец","").strip(" .")
                if app: new=[{"intent":"OPEN_APPLICATION","parameters":{"application":app}}]
            if not new: return Result(False,"Я не распознал действий. Скажите «добавь …», «покажи действия» или «сохрани».")
            if "в начало" in lower: draft["actions"][0:0]=new
            else: draft["actions"].extend(new)
            return Result(True,"Добавлено. Что ещё? Скажите «сохрани» для preview.")
        if step=="confirm":
            if not self._yes(text): self.pending=None; return Result(False,"Отменено.")
            self.pending=None
            if kind=="create":
                skill=self.skills.create(**pending["draft"]); return Result(True,f"Режим «{skill['name']}» создан.",{"skill":skill})
            if kind=="demonstration":
                skill=self.skills.create(pending["name"],pending["actions"],icon=pending.get("icon")); return Result(True,f"Режим «{skill['name']}» создан из демонстрации.",{"skill":skill})
            if kind=="delete": self.skills.delete(pending["name"]); return Result(True,"Режим удалён.")
            if kind=="alias": self.memory.aliases[pending["alias"]]=pending["target"]; self.memory.save_aliases(); return Result(True,"Псевдоним сохранён.")
            if kind=="custom": self.memory.custom_commands[pending["phrase"]]=pending["skill"]; self.memory.save_custom_commands(); return Result(True,"Пользовательская команда сохранена.")
            if kind=="ambiguous": return Result(True,"Подтверждено.",{"command":{"intent":"OPEN_APPLICATION","parameters":{"application":pending["application"]}}})
            if kind=="routine_execution": return Result(True,"Подтверждено.",{"routine":pending["routine"]["name"]})
            if kind=="resource_confirmation": return Result(True,"Подтверждено.",{"command":pending["candidate"]["command"]})
            if kind=="delete_routine":
                from .routine_manager import RoutineManager
                RoutineManager(self.memory,self.skills.router).delete_routine(pending["routine"]["id"]); return Result(True,"Процедура удалена.")
            if kind=="schedule_action": return Result(True,"Подтверждено.",{"schedule_action":{"id":pending["id"],"action":pending["action"],"confirmed":True}})
            if kind=="routine":
                existing=next((r for r in self.memory.learned_routines if r["name"].lower()==pending["name"].lower()),None)
                if existing: self.memory.learned_routines.remove(existing)
                from .routine_manager import RoutineManager
                manager=RoutineManager(self.memory,self.skills.router); error=manager.validate_definition(pending["actions"],pending.get("parameters",[]))
                if error: return error
                routine=manager.create(pending["name"],pending["actions"],parameters=pending.get("parameters",[]))
                return Result(True,f"Готово. Процедура «{routine['name']}» сохранена.",{"routine":routine})
            if kind=="edit":
                skill=self.skills.update(pending["name"],actions=pending["actions"]); return Result(True,f"Режим «{skill['name']}» обновлён.")
        if kind=="edit" and step=="change":
            actions=pending["actions"]
            before=deepcopy(actions)
            if lower in {"покажи действия","покажи режим","preview"}: return Result(True,"Черновик:\n"+self._preview(pending)+"\nСохранить изменения?")
            if lower in {"сохрани","сохранить","готово"}:
                pending["step"]="confirm"; return Result(True,"Черновик:\n"+self._preview(pending)+"\nСохранить изменения?",confirmation_required=True)
            if lower in {"очисти режим","очисти"}: actions=[]
            elif "поменяй" in lower and "мест" in lower:
                values=[ordinal(part) for part in re.split(r"\s+и\s+",lower)]; values=[v for v in values if v]
                if len(values)==2 and max(values)<=len(actions): a,b=values[0]-1,values[1]-1
                else:
                    names=re.findall(r"(?:поменяй(?: местами)?|и)\s*([\w-]+)",lower); found=[next((i for i,a in enumerate(actions) if name in str(a.get("parameters",{})).lower()),None) for name in names]
                    if len(found)<2 or None in found: return Result(False,"Не удалось определить два действия для обмена.")
                    a,b=found[:2]
                actions[a],actions[b]=actions[b],actions[a]
            elif "замени" in lower or "вместо" in lower:
                source=self._action_index(actions,lower); replacement=re.search(r"(?:на|поставь)\s+([\w-]+)",lower)
                if source is None or not replacement: return Result(False,"Не удалось определить заменяемое действие.")
                actions[source]={"intent":"OPEN_APPLICATION","parameters":{"application":replacement.group(1)}}
            elif "перемести" in lower or "поставь" in lower and ("перв" in lower or "после" in lower or "перед" in lower or "конец" in lower):
                indexes=self._ordinals(lower); source=(indexes[0]-1 if indexes else self._action_index(actions,lower))
                if source is None: return Result(False,"Я не нашёл указанное действие среди действий режима.")
                if not 0<=source<len(actions): return Result(False,f"Такого действия нет. Сейчас доступно {len(actions)} действия.")
                item=actions.pop(source)
                specified=indexes[1] if len(indexes)>1 else None
                if specified is not None:
                    if not 1<=specified<=len(actions)+1: return Result(False,"Указанная позиция вне диапазона действий.")
                    destination=specified-1
                elif "начал" in lower or "перв" in lower: destination=0
                elif "конец" in lower: destination=len(actions)
                elif "после" in lower:
                    target=lower.split("после",1)[1].strip().split()[0]; found=next((i for i,a in enumerate(actions) if target in str(a.get("parameters",{})).lower()),None)
                    if found is None: return Result(False,"Не найдено действие, после которого нужно переместить.")
                    destination=found+1
                elif "перед" in lower:
                    target=lower.split("перед",1)[1].strip().split()[0]; destination=next((i for i,a in enumerate(actions) if target in str(a.get("parameters",{})).lower()),None)
                    if destination is None: return Result(False,"Не найдено действие, перед которым нужно переместить.")
                else: destination=0
                actions.insert(destination,item)
            elif "убери" in lower or "удали" in lower:
                index=self._action_index(actions,lower)
                if index is None: return Result(False,"Я не нашёл указанное действие среди действий режима.")
                actions.pop(index)
            else:
                draft=self.learning.draft(text)
                if "youtube" in lower or "ютуб" in lower:
                    draft["actions"].append({"intent":"OPEN_YOUTUBE","parameters":{}})
                actions=actions+draft["actions"]
            if actions==before: return Result(False,"Я не распознал изменение.")
            pending["actions"]=actions
            if "сохрани" in lower or "покажи" in lower: pending["step"]="confirm"; return Result(True,"Черновик:\n"+self._preview(pending)+"\nСохранить изменения?",confirmation_required=True)
            return Result(True,"Изменение добавлено в черновик. Скажите «покажи действия» или «сохрани».")
        return Result(False,"Неожиданное состояние диалога.")
