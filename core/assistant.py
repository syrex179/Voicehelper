import logging
import re
from dataclasses import replace
from datetime import datetime
from .models import AssistantState, Command, Result
from .event_bus import EventBus
from .memory_manager import MemoryManager
from .command_registry import CommandRegistry
from .plugin_manager import PluginManager
from .permissions import PermissionManager
from .intent_parser import IntentParser
from .skill_manager import SkillManager
from .command_router import CommandRouter
from .learning_engine import LearningEngine
from .dialogue_manager import DialogueManager
from .demonstration import DemonstrationRecorder
from .autostart import WindowsAutostart
from .routine_manager import RoutineManager
from .schedule_manager import ScheduledRoutineManager
from .conversational_intent_resolver import ConversationalIntentResolver
from .conversation_provider import ConversationProviderRegistry
from .conversation_turn import ConversationTurn
from .openai_conversation_provider import OpenAIConversationProvider, OpenAIProviderConfig
from .ollama_conversation_provider import OllamaConversationProvider, OllamaProviderConfig
from .browser_chatgpt_provider import BrowserChatGPTProvider
from .resource_resolver import ResourceResolver, ResourceTarget
from .information_provider import InformationService, InformationRequest, WikipediaInformationSource, WikidataInformationSource, BraveWebSearchInformationSource

class Assistant:
    def __init__(self):
        self.state=AssistantState.OFFLINE; self.events=EventBus(); self.memory=MemoryManager(); self.registry=CommandRegistry()
        self.skills=SkillManager(self.memory,None); self.plugins=PluginManager(self.registry,{"memory":self.memory,"events":self.events})
        self.router=CommandRouter(self.registry,self.plugins,PermissionManager(),self.skills,self.events); self.skills.router=self.router
        self.parser=IntentParser(self.memory); self.intent_resolver=ConversationalIntentResolver(); self.conversation_providers=ConversationProviderRegistry(); self.learning=LearningEngine(self.parser,self.skills)
        self.browser_chatgpt_provider=BrowserChatGPTProvider()
        self.dialogue=DialogueManager(self.learning,self.skills,self.memory)
        self.demonstration=DemonstrationRecorder(self.events); self.autostart=WindowsAutostart()
        self.routines=RoutineManager(self.memory,self.router,self.events)
        self.resources=ResourceResolver({"application":self._discover_application_resource,"game":self._discover_application_resource,"folder":self._discover_folder_resource,"file":self._discover_file_resource})
        self.information=InformationService([WikipediaInformationSource(),WikidataInformationSource(),BraveWebSearchInformationSource()])
        self.schedules=ScheduledRoutineManager(self.memory,self.routines)
        self.last_schedule_id=None
        self.routine_learning=False; self.routine_parameters=[]
        self._voice_session_context=None
    def handle_with_session_context(self,text,session_context):
        """Handle one voice turn with ephemeral context that is never persisted."""
        self._voice_session_context=dict(session_context or {})
        self._voice_session_context.setdefault("session_state",getattr(self.state,"value",self.state))
        self._voice_session_context.setdefault("reference_available",bool(self._voice_session_context.get("last_action_text")))
        try: return self.handle(text)
        finally: self._voice_session_context=None
    def start(self): self.plugins.load_all(); self.set_state(AssistantState.READY)
    def openai_provider_status(self):
        """Safe runtime status only; it never reads or exposes a credential."""
        provider=self.conversation_providers.provider
        state=self.conversation_providers.state if isinstance(provider,OpenAIConversationProvider) else "UNCONFIGURED"
        return {"provider_id":"openai","state":state,"mode":"provider" if state=="ENABLED" else "deterministic","network_enabled":bool(getattr(provider,"network_enabled",False))}
    def enable_openai_provider(self, config=None, secret_reader=None, client_factory=None):
        """Explicit opt-in only.  It configures no transport and persists no secret."""
        source=config or OpenAIProviderConfig()
        if not isinstance(source,OpenAIProviderConfig): return Result(False,"Не удалось включить OpenAI provider.")
        candidate=OpenAIConversationProvider(replace(source,enabled=True),secret_reader=secret_reader,client_factory=client_factory)
        if not candidate.activate_transport(): return Result(False,"OpenAI provider не настроен.")
        self.conversation_providers.replace(candidate,enabled=True)
        return Result(True,"OpenAI provider включён.",{"provider_id":"openai","state":"ENABLED","network_enabled":False})
    def disable_openai_provider(self):
        provider=self.conversation_providers.provider
        if not isinstance(provider,OpenAIConversationProvider): return Result(False,"OpenAI provider не настроен.")
        provider.deactivate_transport()
        self.conversation_providers.disable()
        return Result(True,"OpenAI provider отключён.",{"provider_id":"openai","state":"DISABLED","network_enabled":False})
    def ollama_provider_status(self):
        provider=self.conversation_providers.provider
        state=self.conversation_providers.state if isinstance(provider,OllamaConversationProvider) else "UNCONFIGURED"
        return {"provider_id":"ollama","state":state,"mode":"provider" if state=="ENABLED" else "deterministic","network_enabled":bool(getattr(provider,"network_enabled",False))}
    def browser_chatgpt_provider_status(self):
        state=self.browser_chatgpt_provider.diagnostic_status()
        return {"provider_id":"browser_chatgpt","state":state,"mode":"explicit","network_enabled":False,"preflight":self.browser_chatgpt_provider.preflight()}
    def enable_ollama_provider(self, config=None, transport=None):
        source=config or OllamaProviderConfig()
        if not isinstance(source,OllamaProviderConfig): return Result(False,"Не удалось включить локальный provider.")
        candidate=OllamaConversationProvider(replace(source,enabled=True),transport=transport)
        if not candidate.activate_transport(): return Result(False,"Локальный provider не настроен.")
        self.conversation_providers.replace(candidate,enabled=True)
        return Result(True,"Локальный provider включён.",{"provider_id":"ollama","state":"ENABLED","network_enabled":True})
    def disable_ollama_provider(self):
        provider=self.conversation_providers.provider
        if not isinstance(provider,OllamaConversationProvider): return Result(False,"Локальный provider не настроен.")
        provider.deactivate_transport(); self.conversation_providers.disable()
        return Result(True,"Локальный provider отключён.",{"provider_id":"ollama","state":"DISABLED","network_enabled":False})
    def set_state(self,state): self.state=state; self.events.emit("state_changed",state=state)
    def _parse_intent(self,text):
        exact=self.parser.parse(text)
        if exact.intent!="UNKNOWN": return exact
        resolved=self.intent_resolver.resolve(text,exact=exact,pending=self.dialogue.pending,session_context=self._voice_session_context)
        return exact if resolved.no_execute and resolved.intent=="CANCEL_PENDING_DIALOGUE" else resolved.command(text)
    _PROVIDER_ROUTABLE={"OPEN_BROWSER","OPEN_YOUTUBE","SET_VOLUME","OPEN_RESOURCE","INFORMATION_REQUEST"}
    _PROVIDER_CANCELLATION="CANCEL_PENDING_DIALOGUE"
    _PROVIDER_CLARIFICATIONS={"SET_VOLUME":"volume","OPEN_FOLDER":"folder","INCOMPLETE_OPEN_SITE":"browser"}
    def _plugin_discovery(self, plugin_id, target):
        plugin=getattr(self.plugins,"plugins",{}).get(plugin_id)
        discover=getattr(plugin,"discover_resources",None)
        try: return discover(target) if callable(discover) else []
        except Exception: return []
    def _discover_application_resource(self,target): return self._plugin_discovery("applications",target)
    def _discover_folder_resource(self,target): return self._plugin_discovery("files",target)
    def _discover_file_resource(self,target): return self._plugin_discovery("files",target)
    @staticmethod
    def _resource_candidate_payload(candidate):
        return {"name":candidate.name,"resource_type":candidate.resource_type,"source":candidate.source,"command":dict(candidate.command)}
    def _resolve_resource_target(self,response):
        try: target=ResourceTarget(response.resource_type,response.target)
        except (TypeError,ValueError): return Result(False,"Не удалось безопасно определить ресурс.",{"read_only_guidance":True})
        return self._resolve_resource_target_value(target,f"Я не нашёл {target.target} на этом компьютере.")
    def _resolve_resource_target_value(self,target,not_found_message="Я не нашёл подходящее приложение."):
        """Resolve a trusted semantic target; discovery never executes it."""
        found=self.resources.resolve(target); candidates=[self._resource_candidate_payload(item) for item in found.get("candidates",[])]
        if not candidates: return Result(False,not_found_message,{"read_only_guidance":True,"resource_not_found":True})
        if found.get("status")=="one": return self.dialogue.start_resource_confirmation(candidates[0])
        return self.dialogue.start_resource_candidates(candidates)
    def _fallback_application_resource(self, command, direct_result):
        """After an applications-plugin miss, use bounded discovery only.

        The direct Router/plugin attempt remains authoritative.  This helper
        receives no paths and returns only the existing confirmation dialogue.
        """
        if command.intent!="OPEN_APPLICATION" or direct_result.ok or not direct_result.data.get("resource_not_found"):
            return direct_result
        value=command.parameters.get("application")
        try: target=ResourceTarget("application",value)
        except (TypeError,ValueError): return direct_result
        result=self._resolve_resource_target_value(target)
        if not result.data.get("resource_not_found"): return result
        # Applications and games share trusted sources today, but retain the
        # second existing resource type for future adapters without provider use.
        try: game_target=ResourceTarget("game",value)
        except (TypeError,ValueError): return result
        game_result=self._resolve_resource_target_value(game_target)
        return game_result if not game_result.data.get("resource_not_found") else result
    def _provider_command(self,response,text):
        """Accept only already allow-listed, fully validated provider commands."""
        parameters=dict(response.parameters or {})
        if response.intent=="OPEN_BROWSER" or response.intent=="OPEN_YOUTUBE":
            return Command(response.intent,{},text) if not parameters else None
        if response.intent=="SET_VOLUME":
            level=parameters.get("level")
            return Command("SET_VOLUME",{"level":level},text) if set(parameters)=={"level"} and isinstance(level,int) and not isinstance(level,bool) and 0<=level<=100 else None
        return None
    def _information_request(self,response):
        try:
            return InformationRequest(response.information_category,response.information_query,response.target,response.location,response.time_context,"ru",response.source_hint)
        except (TypeError,ValueError):
            return None
    def _capability_candidate(self,text,provider_text=""):
        """Reuse routine discovery only as a non-executing capability suggestion."""
        combined=" ".join(part for part in (text,provider_text) if isinstance(part,str) and part.strip())
        found=self.routines.find_routine(combined)
        if found.get("confidence") in {"HIGH","MEDIUM"} and found.get("routine"):
            return self.dialogue.start_routine_confirmation(found["routine"])
        candidates=found.get("candidates") or []
        return self.dialogue.start_routine_candidates(candidates) if len(candidates)>1 else None
    def _provider_candidate(self,text,command):
        """Return a validated provider result or command; providers never execute actions."""
        if command.intent!="UNKNOWN" or self.dialogue.pending: return None
        context=dict(self._voice_session_context or {})
        turn=ConversationTurn.create(user_text=text,session_state=getattr(self.state,"value",self.state),intent="UNKNOWN",result=Result(False,""),timestamp=0.0,source="provider")
        allowed=self._PROVIDER_ROUTABLE|set(self._PROVIDER_CLARIFICATIONS)|{self._PROVIDER_CANCELLATION}
        response=self.conversation_providers.respond(turn,context,allowed)
        if response is None: return None
        # Information mode already has an authoritative, complete shape after
        # provider validation.  It must reach the existing generic service even
        # when a provider also sets its optional clarification flag.
        if response.intent=="INFORMATION_REQUEST":
            request=self._information_request(response)
            if request is None:
                message="Укажите город для погоды." if response.information_category=="weather" and not response.location else "Уточните информационный запрос."
                return ("result",Result(False,message,{"read_only_guidance":True,"response_type":"informational","provider_response_type":"informational"}))
            # InformationService is the existing read-only authority for this
            # structured request.  Preserve its complete message as final
            # informational text; it must not enter generic action composition.
            information_result=self.information.query(request)
            data=dict(information_result.data)
            data.update({"provider_response_type":"informational","authoritative_response_text":True})
            return ("result",Result(information_result.ok,information_result.message,data,information_result.confirmation_required))
        if response.requires_clarification:
            if response.parameters: return None
            kind=self._PROVIDER_CLARIFICATIONS.get(response.intent)
            if kind=="volume": return ("result",self.dialogue.start_volume_clarification())
            if kind=="folder": return ("result",self.dialogue.start_folder_clarification())
            if kind=="browser": return ("result",self.dialogue.start_browser_clarification())
            # A null-intent provider response can ask a conversational question.
            # It must retain its authoritative safe text rather than fall through
            # to the legacy unknown-command response.
            if response.intent: return None
        if response.end_session:
            if response.intent or response.parameters: return None
            return ("result",Result(True,response.text or "До свидания.",{"provider_response":True,"read_only_guidance":True,"end_session":True}))
        if response.intent==self._PROVIDER_CANCELLATION:
            if response.parameters: return None
            self.dialogue.pending=None
            return ("result",Result(True,response.text or "Отменено.",{"provider_response":True,"read_only_guidance":True,"cancelled":True,"provider_response_type":response.response_type}))
        if response.intent=="OPEN_RESOURCE":
            return ("result",self._resolve_resource_target(response))
        if response.goal=="capability":
            # The provider may identify a goal, but existing discovery and dialogue
            # remain the only capability and execution authorities.
            capability=self._capability_candidate(text)
            if capability: return ("result",capability)
            return ("result",Result(True,"Я могу помочь с сохранёнными процедурами, но подходящей процедуры сейчас не найдено.",{"provider_response":True,"read_only_guidance":True,"goal_request":True,"provider_response_type":response.response_type}))
        if not response.intent:
            if not response.text: return None
            # A provider may legitimately omit the optional goal marker.  Existing
            # capability discovery is a bounded, confirmation-first bridge; it
            # never executes an inferred routine or creates a new one.
            capability=self._capability_candidate(text,response.text)
            if capability: return ("result",capability)
            # A null intent is conversational/informational only.  The text is
            # already schema-validated and is authoritative for this turn; it
            # can never be interpreted as a Router command or replaced by a
            # generic successful-action response.
            return ("result",Result(True,response.text,{"provider_response":True,"read_only_guidance":True,"provider_response_type":response.response_type,"authoritative_response_text":True}))
        provider_command=self._provider_command(response,text)
        return ("command",provider_command) if provider_command else None
    @staticmethod
    def _browser_chatgpt_prompt(text):
        value=" ".join(str(text or "").split())
        value=re.sub(r"^джарвис\s*,?\s*","",value,flags=re.I)
        match=re.match(r"^(?:спроси|задай\s+вопрос)\s+(?:у\s+)?(?:chatgpt|чат\s*gpt|чатджипити)\s*[:,\-]?\s*(.+)$",value,re.I)
        prompt=match.group(1).strip() if match else ""
        return prompt if prompt and len(prompt)<=500 else ""
    def _browser_chatgpt_candidate(self,text,command):
        if command.intent!="UNKNOWN" or self.dialogue.pending: return None
        prompt=self._browser_chatgpt_prompt(text)
        if not prompt: return None
        turn=ConversationTurn.create(user_text=prompt,session_state=getattr(self.state,"value",self.state),intent="UNKNOWN",result=Result(False,""),timestamp=0.0,source="browser_chatgpt")
        response=self.browser_chatgpt_provider.respond(turn,{})
        if response is not None: response=self.conversation_providers.validate(response,())
        if response is not None: return Result(True,response.text,{"provider_response":True,"read_only_guidance":True,"provider_id":"browser_chatgpt","provider_response_type":"conversational"})
        state=self.browser_chatgpt_provider.last_state
        messages={"LOGIN_REQUIRED":"Войдите в ChatGPT в открытом браузере и повторите запрос.","UNAVAILABLE":"Browser ChatGPT provider сейчас недоступен.","BROWSER_CLOSED":"Окно ChatGPT закрыто.","SEND_CONTROL_NOT_FOUND":"Не удалось найти безопасный элемент отправки ChatGPT.","RESPONSE_TIMEOUT":"ChatGPT не ответил за отведённое время.","EMPTY_RESPONSE":"ChatGPT не вернул видимого ответа.","BUSY":"Browser ChatGPT provider занят.","MULTIPLE_TABS_AMBIGUOUS":"Не удалось однозначно выбрать вкладку ChatGPT."}
        return Result(False,messages.get(state,"Browser ChatGPT provider вернул ошибку."),{"provider_response":True,"read_only_guidance":True,"provider_id":"browser_chatgpt","provider_state":state})
    def _find_routine_for_management(self,name):
        routine=self.routines.find(name)
        if routine: return routine
        discovered=self.routines.find_routine(name)
        return discovered["routine"] if discovered["confidence"] in {"HIGH","MEDIUM"} else None
    def _routine_details(self,routine):
        lines=[f"Процедура «{routine['name']}». "]
        if routine.get("description"): lines.append(f"Описание: {routine['description']}")
        actions=routine.get("actions",[])
        if not actions: lines.append("Сохранённых действий нет.")
        else:
            lines.append("Действия:")
            for index,action in enumerate(actions,1):
                parameters=action.get("parameters",{})
                values=", ".join(f"{key}={value}" for key,value in parameters.items())
                lines.append(f"{index}. {action.get('intent','UNKNOWN')}{(': '+values) if values else ''}")
        return "\n".join(lines)
    def _run_routine_with_clarification(self,routine,values=None,skipped_indexes=None):
        clarification=self.dialogue.start_routine_parameter_clarification(routine,values,skipped_indexes)
        if clarification: return clarification
        return self.routines.run(routine["name"],skipped_indexes=skipped_indexes,parameter_values=values)
    def _recommendation_values(self,routine,value="",options=None):
        values={}; schemas=routine.get("parameters",[])
        if value:
            platform=next((item for item in schemas if item.get("name")=="platform"),None)
            if platform: values["platform"]=value
            elif len(schemas)==1: values[schemas[0].get("name")]=value
            else: return None
        for key,item_value in (options or {}).items():
            target=next((item for item in schemas if item.get("name")==key or key=="level" and item.get("name")=="volume"),None)
            if not target: return None
            values[target["name"]]=item_value
        return values
    def _run_composite(self,query,value="",skip="",options=None):
        routine=self.routines.find(query)
        if not routine:
            discovered=self.routines.find_routine(query)
            if discovered.get("candidates"): return self.dialogue.start_routine_candidates(discovered["candidates"],{"values":{},"skipped_indexes":[],"skip_selector":skip,"value":value})
            routine=discovered.get("routine")
        if not routine: return Result(False,"Процедура не найдена.")
        values=self._recommendation_values(routine,value,options)
        if values is None: return Result(False,"Не удалось однозначно определить параметр процедуры.")
        matches=self.routines.find_step_indexes(routine,skip) if skip else []
        if skip and not matches: return Result(False,"Шаг не найден. Укажите понятное действие или номер шага.")
        if len(matches)>1: return Result(False,"Найдено несколько шагов. Укажите номер конкретного шага.")
        return self._run_routine_with_clarification(routine,values,matches)
    def _run_with_preset(self,query,preset_name,skip="",options=None):
        routine=self._find_routine_for_management(query)
        if not routine: return Result(False,"Процедура не найдена.")
        preset=self.routines.find_preset(routine["id"],preset_name)
        if not preset: return Result(False,"Профиль не найден.")
        overrides=self._recommendation_values(routine,"",options)
        if overrides is None: return Result(False,"Не удалось применить параметры процедуры.")
        values=dict(preset["values"]); values.update(overrides); matches=self.routines.find_step_indexes(routine,skip) if skip else []
        if skip and not matches: return Result(False,"Шаг не найден. Укажите понятное действие или номер шага.")
        if len(matches)>1: return Result(False,"Найдено несколько шагов. Укажите номер конкретного шага.")
        return self._run_routine_with_clarification(routine,values,matches)
    def _run_as_usual(self,context,value="",skip="",options=None,preset_name=""):
        recommendation=self.routines.recommend_routine(context); routine=recommendation["routine"]
        if not routine:
            candidates=recommendation["candidates"]
            return self.dialogue.start_routine_candidates(candidates,{"values":{},"skipped_indexes":[],"skip_selector":skip,"value":value}) if candidates else Result(False,"Подходящая обычная процедура не найдена.")
        preset=self.routines.find_preset(routine["id"],preset_name) if preset_name else None
        if preset_name and not preset: return Result(False,"Профиль не найден.")
        values=dict(preset["values"]) if preset else {}
        explicit=self._recommendation_values(routine,value,options)
        values.update(explicit) if explicit is not None else None
        if explicit is None: values=None
        if values is None: return Result(False,"Не удалось однозначно определить параметр процедуры.")
        matches=self.routines.find_step_indexes(routine,skip) if skip else []
        if skip and not matches: return Result(False,"Шаг не найден. Укажите понятное действие или номер шага.")
        if len(matches)>1: return Result(False,"Найдено несколько шагов. Укажите номер конкретного шага.")
        return self._run_routine_with_clarification(routine,values,matches)
    def _history_for_name(self,name,limit=10):
        routine=self.routines.find(name)
        return self.routines.get_execution_history(routine["id"],limit) if routine else self.routines.get_execution_history_by_name(name,limit)
    def _schedule_routine(self,name,schedule,preset_name=""):
        routine=self._find_routine_for_management(name)
        if not routine: return Result(False,"Процедура не найдена.")
        preset=self.routines.find_preset(routine["id"],preset_name) if preset_name else None
        if preset_name and not preset: return Result(False,"Профиль не найден.")
        item=self.schedules.create_schedule(routine["id"],schedule,preset_id=preset["id"] if preset else None)
        return Result(True,f"Расписание для процедуры «{routine['name']}» сохранено.",{"schedule":item}) if item else Result(False,"Не удалось создать расписание.")
    def _schedule_label(self,item):
        definition=item["schedule"]; kind=definition["type"]
        weekdays=["понедельник","вторник","среда","четверг","пятница","суббота","воскресенье"]
        when=definition.get("at","") if kind=="once" else ("каждый день" if kind=="daily" else f"каждый {weekdays[definition.get('weekday',0)]}")+f" в {definition.get('time','')}"
        preset=self.routines.get_preset(item.get("preset_id")); suffix=f" — {preset['name']}" if preset else ""
        return f"{item['routine_name']}{suffix}: {when} ({'включено' if item.get('enabled') else 'отключено'})"
    def _matching_schedules(self,name="",preset_name="",one_time=False):
        routine=self._find_routine_for_management(name) if name else None
        items=self.schedules.list_schedules()
        if routine: items=[item for item in items if item["routine_id"]==routine["id"]]
        elif name:
            words=set(re.findall(r"[\wа-яё]+",name.lower()))
            items=[item for item in items if words and words <= set(re.findall(r"[\wа-яё]+",item.get("routine_name","").lower()))]
        if one_time: items=[item for item in items if item["schedule"]["type"]=="once"]
        if preset_name: items=[item for item in items if (self.routines.get_preset(item.get("preset_id")) or {}).get("name","").lower()==preset_name.lower()]
        for item in items: item["label"]=self._schedule_label(item)
        return items
    def _apply_schedule_action(self,payload):
        action,schedule_id=payload["action"],payload["id"]
        if action=="next":
            item=self.schedules.get_schedule(schedule_id); self.last_schedule_id=schedule_id
            return Result(True,f"Следующий запуск: {item.get('next_run_at') or 'нет'}.")
        if action=="update":
            update=dict(payload.get("update") or {})
            if "time" in update:
                item=self.schedules.get_schedule(schedule_id); definition=dict(item["schedule"]); hour,minute=(int(x) for x in update.pop("time").split(":"))
                if definition["type"]=="once": definition["at"]=datetime.fromisoformat(definition["at"]).replace(hour=hour,minute=minute,second=0,microsecond=0).isoformat()
                else: definition["time"]=f"{hour:02d}:{minute:02d}"
                update["schedule"]=definition
            value=self.schedules.update_schedule(schedule_id,**update); return Result(bool(value),"Расписание обновлено." if value else "Не удалось обновить расписание.")
        if action in {"delete","disable"}:
            self.dialogue._set_pending({"kind":"schedule_action","id":schedule_id,"action":action,"step":"confirm"})
            item=self.schedules.get_schedule(schedule_id); return Result(True,("Удалить" if action=="delete" else "Отключить")+f" расписание процедуры «{item['routine_name']}»?",confirmation_required=True)
        value=self.schedules.enable_schedule(schedule_id); return Result(bool(value),"Расписание включено." if value else "Расписание не найдено.")
    def _manage_schedule(self,name,action,preset_name="",update=None,one_time=False):
        items=self._matching_schedules(name,preset_name,one_time)
        if not items: return Result(False,"Расписание не найдено.")
        if len(items)>1: return self.dialogue.start_schedule_candidates(items,action,update)
        return self._apply_schedule_action({"id":items[0]["id"],"action":action,"update":update})
    @staticmethod
    def _history_summary(record):
        return f"Запуск {record['routine_name']}: {record['status']}; шагов выполнено {record['completed_steps']} из {record['total_steps']}."
    def _declare_learning_parameter(self,text,initial=False):
        pattern=r"(?:добавь|здесь будет|с)\s+параметр(?:ом)?\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s+типа\s+([\wа-яё]+))?(?:\s+(обязательный|необязательный))?(?:\s+по умолчанию\s+(.+))?"
        match=re.search(pattern,text.lower())
        if not match: return None
        name,kind,required,default=match.groups(); kind={"строка":"string","целое":"integer","логический":"boolean"}.get(kind,kind or "string")
        if kind not in {"string","integer","boolean"}: return Result(False,"Допустимы только типы string, integer и boolean.")
        if any(item["name"]==name for item in self.routine_parameters): return Result(False,"Параметр с таким именем уже существует.")
        parameter={"name":name,"type":kind,"required":required!="необязательный"}
        if default is not None:
            value=default.strip(" .\"«»")
            if kind=="integer":
                if not re.fullmatch(r"-?\d+",value): return Result(False,"Значение по умолчанию должно быть integer.")
                value=int(value)
            elif kind=="boolean":
                if value not in {"true","false","да","нет"}: return Result(False,"Значение по умолчанию должно быть boolean.")
                value=value in {"true","да"}
            parameter["default"]=value; parameter["required"]=False
        error=self.routines.validate_definition([], [parameter])
        if error: return error
        self.routine_parameters.append(parameter); return Result(True,f"Параметр {name} добавлен.")
    def _bind_learning_parameter(self,text):
        match=re.search(r"(?:используй|использовать)\s+параметр\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s+для\s+([A-Za-z_][A-Za-z0-9_]*))?",text.lower())
        if not match: return None
        name,field=match.groups(); parameter=next((item for item in self.routine_parameters if item["name"]==name),None)
        if not parameter: return Result(False,"Параметр не объявлен.")
        if not self.demonstration.actions: return Result(False,"Сначала выполните разрешённое действие JARVIS.")
        action=self.demonstration.actions[-1]; allowed=self.routines.PARAMETER_FIELDS.get(action.get("intent"),set())
        if not field:
            if len(allowed)!=1: return Result(False,"Укажите безопасное поле действия для параметра.")
            field=next(iter(allowed))
        if field not in allowed or field not in action.get("parameters",{}): return Result(False,"Параметр можно использовать только в безопасном поле текущего действия.")
        action["parameters"][field]=f"{{{name}}}"; return Result(True,f"Параметр {name} привязан к последнему действию.")
    def handle(self,text,confirmed=False):
        lower=text.lower().strip()
        # Keep the existing deterministic completion vocabulary, but compare
        # its normalised terminal form.  Voice transcripts routinely include
        # punctuation; punctuation must not change the meaning of a complete
        # learning-control utterance.
        normalized_lower=lower.rstrip(" .!?…").strip()
        if self._parse_intent(text).intent=="PENDING_DIALOGUE_RECAP": return Result(True,self.dialogue.pending_recap())
        interruption=self._parse_intent(text).intent
        if self.dialogue.pending and interruption in {"CANCEL_PENDING_DIALOGUE","CANCEL_ROUTINE_EXECUTION","SESSION_GOODBYE"}:
            self.dialogue.pending=None
            return Result(True,"Отменено." if interruption=="CANCEL_PENDING_DIALOGUE" else "Хорошо, прекращаю.")
        if self.routine_learning:
            if normalized_lower in {"отмена","отмени обучение","забудь","не надо"}:
                self.demonstration.cancel(); self.routine_learning=False; self.routine_parameters=[]; return Result(True,"Обучение процедуре отменено.")
            declared=self._declare_learning_parameter(text)
            if declared: return declared
            bound=self._bind_learning_parameter(text)
            if bound: return bound
            if normalized_lower in {"закончил обучение","закончил","завершить обучение","закончить обучение","готово","всё","все","на этом всё","на этом все"}:
                stopped=self.demonstration.stop(); self.routine_learning=False
                if not stopped.ok: return Result(False,"Я не записал ни одного действия. Обучение отменено.")
                self.dialogue.pending={"kind":"routine","step":"name","actions":stopped.data["actions"],"parameters":self.routine_parameters}; self.routine_parameters=[]
                return Result(True,f"Я записал {len(stopped.data['actions'])} действий. Как назвать эту процедуру?")
        pending=self.dialogue.pending
        pending_kind=pending.get("kind") if isinstance(pending,dict) else ""
        # Routine execution has a dedicated structured audit trail in
        # RoutineManager.  Its dialogue turns can contain a natural request,
        # candidate number, confirmation, or parameter values, none of which
        # belong in general conversational history.
        routine_pending_kind=pending_kind in {"routine","routine_execution","routine_candidates","routine_parameters","resource_confirmation","resource_candidates"}
        if pending and pending.get("kind") in {"browser_clarification","volume_clarification","folder_clarification"}:
            candidate=self._parse_intent(text).intent
            if candidate not in {"UNKNOWN","INCOMPLETE_OPEN_SITE","INCOMPLETE_SET_VOLUME","INCOMPLETE_OPEN_FOLDER","HELP","PENDING_DIALOGUE_RECAP","CANCEL_ROUTINE_EXECUTION"}: self.dialogue.pending=None
        dialog_result=self.dialogue.handle(text)
        if dialog_result:
            payload=dialog_result.data.get("command")
            if payload:
                routed=self.router.route(Command(payload["intent"],payload["parameters"],text))
                if dialog_result.data.get("corrected"):
                    metadata=dict(routed.data); metadata["corrected"]=True
                    dialog_result=Result(routed.ok,"Хорошо, исправляю. "+routed.message,metadata,routed.confirmation_required)
                else: dialog_result=routed
            schedule_action=dialog_result.data.get("schedule_action")
            if schedule_action:
                action=schedule_action["action"]
                if schedule_action.get("confirmed"):
                    schedule_id=schedule_action["id"]; value=self.schedules.delete_schedule(schedule_id) if action=="delete" else self.schedules.disable_schedule(schedule_id)
                    dialog_result=Result(bool(value),"Расписание удалено." if action=="delete" else "Расписание отключено.")
                else: dialog_result=self._apply_schedule_action(schedule_action)
            routine_run=dialog_result.data.get("routine_run")
            if routine_run:
                corrected=dialog_result.data.get("corrected")
                routine=self.routines.find(routine_run["name"])
                if not routine: dialog_result=Result(False,"Процедура не найдена.")
                else:
                    values=routine_run["values"] or self._recommendation_values(routine,routine_run.get("value","")); selector=routine_run.get("skip_selector",""); matches=self.routines.find_step_indexes(routine,selector) if selector else routine_run["skipped_indexes"]
                    dialog_result=Result(False,"Шаг не найден. Укажите понятное действие или номер шага.") if selector and not matches else Result(False,"Найдено несколько шагов. Укажите номер конкретного шага.") if len(matches)>1 else self._run_routine_with_clarification(routine,values,matches)
                if corrected and dialog_result.ok: dialog_result=Result(True,"Хорошо, исправляю. "+dialog_result.message,dialog_result.data,dialog_result.confirmation_required)
            elif isinstance(dialog_result.data.get("routine"),str):
                routine=self.routines.find(dialog_result.data["routine"]); dialog_result=self._run_routine_with_clarification(routine) if routine else Result(False,"Процедура не найдена.")
            # Demonstration review/name/confirmation is deliberately
            # ephemeral.  The routine's canonical structured actions are the
            # only durable artefact of this flow; do not retain raw spoken
            # transcript in ordinary history.
            if not dialog_result.data.get("read_only_guidance") and not routine_pending_kind: self.memory.add_history(text,dialog_result.message)
            return dialog_result
        self.set_state(AssistantState.PROCESSING); command=self._parse_intent(text); self.set_state(AssistantState.EXECUTING)
        browser_result=self._browser_chatgpt_candidate(text,command)
        if browser_result is not None:
            self.set_state(AssistantState.READY); return browser_result
        provider_candidate=self._provider_candidate(text,command)
        provider_used=False
        if provider_candidate:
            kind,value=provider_candidate
            if kind=="result":
                self.set_state(AssistantState.READY)
                return value
            command=value
            provider_used=True
        elif command.intent=="UNKNOWN" and self.conversation_providers.state in {"ENABLED","ERROR"}:
            self.set_state(AssistantState.READY)
            return Result(False,"Я не понял команду.",{"provider_fallback":True,"read_only_guidance":True})
        informational=any(word in lower for word in ("расскажи","что входит","какая"))
        if not informational and command.intent=="UNKNOWN":
            found=self.routines.find_routine(text)
            if found["confidence"]=="HIGH" and found["routine"]: result=self._run_routine_with_clarification(found["routine"]); self.set_state(AssistantState.READY); return result
            if found["confidence"]=="MEDIUM" and found["routine"]: result=self.dialogue.start_routine_confirmation(found["routine"]); self.set_state(AssistantState.READY); return result
            if found.get("candidates"):
                result=self.dialogue.start_routine_candidates(found["candidates"]); self.set_state(AssistantState.READY); return result
        if command.intent=="START_DEMONSTRATION": result=self.demonstration.start()
        elif command.intent=="START_ROUTINE_LEARNING":
            self.routine_learning=True; self.routine_parameters=[]; result=self.demonstration.start(); declared=self._declare_learning_parameter(text,initial=True)
            result.message="Хорошо. Показывай действия. Когда закончишь, скажи «закончил обучение»." + (" "+declared.message if declared and declared.ok else "")
        elif command.intent=="CANCEL_DEMONSTRATION": result=self.demonstration.cancel()
        elif command.intent=="STOP_DEMONSTRATION":
            result=self.demonstration.stop()
            if result.ok:
                self.dialogue.pending={"kind":"demonstration","step":"name","actions":result.data["actions"]}
        elif command.intent=="CREATE_SKILL":
            result=self.dialogue.start_create(command.parameters.get("name"),command.parameters.get("description"))
        elif command.intent=="CREATE_CUSTOM_COMMAND": result=self.dialogue.start_custom_command(command.parameters["phrase"],command.parameters.get("skill"))
        elif command.intent=="DELETE_SKILL": result=self.dialogue.start_delete(command.parameters["name"])
        elif command.intent=="EDIT_SKILL": result=self.dialogue.start_edit(command.parameters["name"])
        elif command.intent=="SET_ALIAS": result=self.dialogue.start_alias(command.parameters["alias"],command.parameters["target"])
        elif command.intent=="RUN_CUSTOM_COMMAND": result=self.skills.run(command.parameters["skill"])
        elif command.intent=="RUN_SKILL": result=self.skills.run(command.parameters["skill"])
        elif command.intent=="RUN_ROUTINE":
            routine=self.routines.find(command.parameters["routine"]); result=self._run_routine_with_clarification(routine) if routine else Result(False,"Процедура не найдена.")
        elif command.intent=="CANCEL_ROUTINE_EXECUTION": result=self.routines.cancel_active()
        elif command.intent=="RUN_ROUTINE_AS_USUAL": result=self._run_as_usual(command.parameters["context"],command.parameters["value"],command.parameters["skip"],command.parameters.get("options"),command.parameters.get("preset",""))
        elif command.intent=="RUN_ROUTINE_COMPOSITE": result=self._run_composite(command.parameters["query"],command.parameters["value"],command.parameters["skip"],command.parameters.get("options"))
        elif command.intent=="RUN_ROUTINE_WITH_PRESET": result=self._run_with_preset(command.parameters["query"],command.parameters["preset"],command.parameters["skip"],command.parameters.get("options"))
        elif command.intent=="CREATE_ROUTINE_SCHEDULE": result=self._schedule_routine(command.parameters["name"],command.parameters["schedule"],command.parameters.get("preset",""))
        elif command.intent=="LIST_ROUTINE_SCHEDULES":
            schedules=self._matching_schedules(); self.last_schedule_id=schedules[0]["id"] if len(schedules)==1 else None
            result=Result(True,"\n".join(self._schedule_label(item)+f"; следующий запуск: {item.get('next_run_at') or 'нет'}" for item in schedules) if schedules else "Запланированных процедур пока нет.")
        elif command.intent=="NEXT_ROUTINE_SCHEDULE":
            schedules=[self.schedules.get_schedule(self.last_schedule_id)] if command.parameters.get("pronoun") and self.last_schedule_id else self._matching_schedules(command.parameters.get("name",""))
            schedules=[item for item in schedules if item]
            result=self._apply_schedule_action({"id":schedules[0]["id"],"action":"next"}) if len(schedules)==1 else self.dialogue.start_schedule_candidates(schedules,"next") if schedules else Result(False,"Расписание не найдено.")
        elif command.intent in {"DISABLE_ROUTINE_SCHEDULE","DELETE_ROUTINE_SCHEDULE","ENABLE_ROUTINE_SCHEDULE"}:
            action={"DISABLE_ROUTINE_SCHEDULE":"disable","DELETE_ROUTINE_SCHEDULE":"delete","ENABLE_ROUTINE_SCHEDULE":"enable"}[command.intent]
            result=self._manage_schedule(command.parameters["name"],action,command.parameters.get("preset",""))
        elif command.intent=="UPDATE_ROUTINE_SCHEDULE": result=self._manage_schedule(command.parameters["name"],"update",command.parameters.get("preset",""),command.parameters["update"],command.parameters.get("one_time",False))
        elif command.intent=="ROUTINE_RECOMMENDATION_INFO":
            recommendation=self.routines.recommend_routine(command.parameters["context"]); result=Result(True,f"Обычно используется процедура «{recommendation['routine']['name']}»." if recommendation["routine"] else "Есть несколько одинаково подходящих процедур; уточните контекст." if recommendation["candidates"] else "Подходящей процедуры пока нет.")
        elif command.intent=="ROUTINE_HISTORY_LAST":
            records=self._history_for_name(command.parameters["name"],1); result=Result(True,self._history_summary(records[0]) if records else "Истории запусков нет.")
        elif command.intent=="ROUTINE_HISTORY_LAST_RESULT":
            records=self._history_for_name(command.parameters["name"],1); result=Result(True,self._history_summary(records[0]) if records else "Истории запусков нет.")
        elif command.intent=="ROUTINE_HISTORY_LIST":
            records=self._history_for_name(command.parameters["name"],5); result=Result(True,"\n".join(self._history_summary(record) for record in records) if records else "Истории запусков нет.")
        elif command.intent=="ROUTINE_HISTORY_FAILURE":
            records=self._history_for_name(command.parameters["name"],10); failed=next((record for record in records if record["status"]=="failed"),None); result=Result(True,failed.get("error","Причина не сохранена.") if failed else "Ошибочных запусков не найдено.")
        elif command.intent=="RUN_ROUTINE_WITH_SKIP":
            routine=self._find_routine_for_management(command.parameters["query"])
            if not routine: result=Result(False,"Процедура не найдена.")
            else:
                matches=self.routines.find_step_indexes(routine,command.parameters["skip"])
                if not matches: result=Result(False,"Шаг не найден. Укажите понятное действие или номер шага.")
                elif len(matches)>1: result=Result(False,"Найдено несколько шагов. Укажите номер конкретного шага.")
                else: result=self._run_routine_with_clarification(routine,skipped_indexes=matches)
        elif command.intent=="RUN_ROUTINE_WITH_PARAMETERS":
            routine=self._find_routine_for_management(command.parameters["query"])
            if not routine: result=Result(False,"Процедура не найдена.")
            else:
                schemas=routine.get("parameters",[]); platform=next((item for item in schemas if item.get("name")=="platform"),None)
                if platform: values={"platform":command.parameters["value"]}
                elif len(schemas)==1: values={schemas[0].get("name"):command.parameters["value"]}
                else: values=None
                if not values or not next(iter(values)): result=Result(False,"Не удалось однозначно определить параметр процедуры.")
                else:
                    selector=command.parameters["skip"]; matches=self.routines.find_step_indexes(routine,selector) if selector else []
                    if selector and not matches: result=Result(False,"Шаг не найден. Укажите понятное действие или номер шага.")
                    elif len(matches)>1: result=Result(False,"Найдено несколько шагов. Укажите номер конкретного шага.")
                    else: result=self._run_routine_with_clarification(routine,values,matches)
        elif command.intent=="LIST_ROUTINES": result=Result(True,"\n".join(r["name"] for r in self.routines.list_routines()) or "Процедур пока нет.")
        elif command.intent=="SHOW_ROUTINE":
            routine=self._find_routine_for_management(command.parameters["name"])
            result=Result(False,"Процедура не найдена.") if not routine else Result(True,self._routine_details(routine),{"routine":self.routines.get_routine(routine["id"])})
        elif command.intent=="ADD_ROUTINE_ALIAS":
            routine=self._find_routine_for_management(command.parameters["name"]); alias=command.parameters["alias"]
            if not routine: result=Result(False,"Процедура не найдена.")
            else: result=self.dialogue.add_routine_alias(routine,alias)
        elif command.intent=="REMOVE_ROUTINE_ALIAS":
            routine=self._find_routine_for_management(command.parameters["name"]); alias=command.parameters["alias"]
            if not routine: result=Result(False,"Процедура не найдена.")
            else: result=self.dialogue.remove_routine_alias(routine,alias)
        elif command.intent=="RENAME_ROUTINE":
            routine=self.routines.find(command.parameters["old"]); result=Result(False,"Процедура не найдена.") if not routine else Result(True,"Процедура переименована.",{"routine":self.routines.update_routine(routine["id"],name=command.parameters["new"])})
        elif command.intent=="DELETE_ROUTINE":
            routine=self.routines.find(command.parameters["name"])
            if not routine: result=Result(False,"Процедура не найдена.")
            else: self.dialogue._set_pending({"kind":"delete_routine","routine":routine,"step":"confirm"}); result=Result(True,f"Удалить процедуру «{routine['name']}»?",confirmation_required=True)
        elif command.intent=="AMBIGUOUS_APPLICATION": result=self.dialogue.start_ambiguous_application(command.parameters["application"])
        elif command.intent=="HELP": result=Result(True,"Я могу выполнять безопасные команды, запускать процедуры и отвечать на запросы о сохранённых процедурах. Скажите команду или задайте вопрос.")
        elif command.intent=="SESSION_GREETING": result=Result(True,"Здравствуйте. Чем могу помочь?")
        elif command.intent=="SESSION_THANKS": result=Result(True,"Пожалуйста.")
        elif command.intent=="SESSION_GOODBYE": result=Result(True,"До свидания.")
        elif command.intent=="SESSION_ACKNOWLEDGEMENT": result=Result(True,"Хорошо.")
        elif command.intent=="SESSION_RECAP":
            context=self._voice_session_context or {}; action=context.get("last_action","")
            labels={"OPEN_BROWSER":"открытие браузера","OPEN_YOUTUBE":"открытие YouTube","SEARCH_WEB":"поиск в интернете","SEARCH_YOUTUBE":"поиск на YouTube","SET_VOLUME":"установка громкости","VOLUME_UP":"увеличение громкости","VOLUME_DOWN":"уменьшение громкости","OPEN_FOLDER":"открытие папки"}
            result=Result(True,f"Последнее действие: {labels.get(action,action)}. {context.get('last_assistant_text','')}".strip() if action else "В этой сессии пока не было выполненных действий.")
        elif command.intent=="SESSION_RESPONSE_REPEAT":
            previous=(self._voice_session_context or {}).get("last_assistant_text","")
            result=Result(True,"Повторяю: "+previous if previous else "В этой сессии пока нечего повторить.")
        elif command.intent=="INCOMPLETE_OPEN_SITE": result=self.dialogue.start_browser_clarification()
        elif command.intent=="INCOMPLETE_SET_VOLUME": result=self.dialogue.start_volume_clarification()
        elif command.intent=="INCOMPLETE_OPEN_FOLDER": result=self.dialogue.start_folder_clarification()
        elif command.intent=="INVALID_VOLUME": result=Result(False,"Громкость должна быть от 0 до 100 процентов.")
        elif command.intent=="MULTI_ACTION":
            results=[self.router.route(Command(a["intent"],a.get("parameters",{}),text),confirmed) for a in command.actions]
            result=Result(all(x.ok for x in results),"; ".join(x.message for x in results),{"results":[x.message for x in results]})
        else:
            result=self.router.route(command,confirmed)
            result=self._fallback_application_resource(command,result)
        # A provider-selected command is still executed exclusively by the
        # Router.  Preserve its already validated, successful identity for the
        # ephemeral voice-session snapshot; the parser may have been UNKNOWN.
        # This is intentionally result metadata, not Memory or provider state.
        if provider_used and result.ok:
            data=dict(result.data)
            data["executed_intent"]=command.intent
            data["executed_parameters"]=dict(command.parameters)
            result=Result(True,result.message,data,result.confirmation_required)
        # Once learning starts, its raw control/action phrases are not normal
        # conversational history.  DemonstrationRecorder persists only its
        # canonical validated action snapshot.
        routine_execution_command=command.intent in {"RUN_ROUTINE","CANCEL_ROUTINE_EXECUTION","RUN_ROUTINE_AS_USUAL","RUN_ROUTINE_COMPOSITE","RUN_ROUTINE_WITH_PRESET","RUN_ROUTINE_WITH_SKIP","RUN_ROUTINE_WITH_PARAMETERS"}
        if not provider_used and not self.routine_learning and not routine_execution_command and not result.data.get("read_only_guidance"):
            self.memory.add_history(text,result.message)
        self.set_state(AssistantState.READY); return result
