import threading
import time
import re
from dataclasses import asdict, dataclass, replace
from core.models import AssistantState, Command, Result
from core.response_composer import ResponseComposer
from core.conversation_turn import ConversationTurn
from core.response_policy import ResponsePolicy
from core.turn_sequence import TurnSequenceCoordinator
from speech.stt_normalizer import RegisteredResource, STTResourceNormalizer

@dataclass
class VoiceSessionContext:
    session_started_at: float
    last_user_text: str=""
    last_assistant_text: str=""
    last_intent: str=""
    last_action: str=""
    last_action_text: str=""
    last_action_parameters: dict=None
    last_information: dict=None
    pending_dialogue_state: dict=None
    last_activity_at: float=0.0

class VoiceController:
    _REPEATABLE_INTENTS={"OPEN_BROWSER","OPEN_YOUTUBE","SEARCH_WEB","SEARCH_YOUTUBE","SET_VOLUME","VOLUME_UP","VOLUME_DOWN","MUTE","UNMUTE"}
    _CONTEXT_ACTION_INTENTS=_REPEATABLE_INTENTS|{"OPEN_FOLDER"}
    _BROWSER_INTENTS={"OPEN_BROWSER","OPEN_YOUTUBE","SEARCH_WEB","SEARCH_YOUTUBE"}
    _VOLUME_INTENTS={"SET_VOLUME","VOLUME_UP","VOLUME_DOWN","MUTE","UNMUTE"}
    def __init__(self, assistant, stt, wake_word, microphone, tts, on_result=None):
        self.assistant,self.stt,self.wake_word,self.microphone,self.tts,self.on_result=assistant,stt,wake_word,microphone,tts,on_result
        self._running=threading.Event(); self._thread=None
        self._context=None; self._last_conversation_turn=None; self._sequence_state=None; self._session_paused=False; self._clock=time.monotonic; self.response_composer=ResponseComposer(); self.response_policy=ResponsePolicy(); self.sequence_coordinator=TurnSequenceCoordinator()
        self._tts_enqueue_count=0; self._last_tts_diagnostic={"response_type":"","tts_text_length":0,"tts_enqueue_count":0}; self._stt_normalizer=STTResourceNormalizer()
    @property
    def listening(self): return self._running.is_set()
    @property
    def tts_diagnostic(self):
        """Safe live voice-output diagnostics; composed response text is never exposed."""
        diagnostic=dict(self._last_tts_diagnostic)
        status={}
        reader=getattr(self.tts,"diagnostic_status",None)
        if callable(reader):
            try:
                candidate=reader()
                status=candidate if isinstance(candidate,dict) else {}
            except Exception: status={}
        error=status.get("last_error_class")
        error=error if isinstance(error,str) and re.fullmatch(r"[A-Z0-9_]{1,80}",error) else None
        diagnostic.update({"speech_started":bool(status.get("speech_started",False)),
                           "speech_finished":bool(status.get("speech_finished",False)),
                           "last_error_class":error})
        return diagnostic
    def start(self):
        if self.listening or (self._thread and self._thread.is_alive()): return False
        self.clear_context()
        self._running.set(); self._thread=threading.Thread(target=self._loop,daemon=True,name="jarvis-listener"); self._thread.start(); return True
    def stop(self):
        self._running.clear()
        if self._thread and self._thread is not threading.current_thread(): self._thread.join(timeout=2)
        self.clear_context()
        self.assistant.set_state(AssistantState.READY)
    def _publish(self, transcript, result, intent="", safe_metadata=None):
        turn=self._create_turn(transcript,result,intent,safe_metadata); self._last_conversation_turn=turn; self._sequence_state=self.sequence_coordinator.advance(self._sequence_state,turn)
        response=self.response_composer.compose(turn=turn,sequence=self._sequence_state)
        if self.on_result:
            # The UI and TTS must consume the identical final composer output.
            # Preserve the original structured data for existing UI consumers.
            published=Result(result.ok,response.text,dict(result.data),result.confirmation_required)
            try: self.on_result(transcript,published)
            except Exception: pass
        self._enqueue_tts(response)
    def _enqueue_tts(self, response):
        """Queue exactly the final composed response on the existing desktop TTS."""
        text=getattr(response,"text","")
        response_type=getattr(response,"response_type","")
        text=text if isinstance(text,str) else ""
        self._last_tts_diagnostic={"response_type":str(response_type or ""),"tts_text_length":len(text),"tts_enqueue_count":self._tts_enqueue_count}
        tts=self.tts
        if tts is None or not callable(getattr(tts,"say",None)): return False
        settings=getattr(getattr(self.assistant,"memory",None),"settings",{})
        # Settings are saved by the desktop UI after this worker already exists.
        # Keep the existing worker, but apply only its current public controls.
        if isinstance(settings,dict):
            for name,key in (("enabled","tts_enabled"),("rate","tts_rate"),("volume","tts_volume")):
                if key in settings and hasattr(tts,name): setattr(tts,name,settings[key])
        try:
            accepted=bool(tts.say(text))
            if accepted: self._tts_enqueue_count+=1
            self._last_tts_diagnostic["tts_enqueue_count"]=self._tts_enqueue_count
            return accepted
        except Exception: return False
    def _has_pending_dialogue(self):
        """A wake-free follow-up is allowed only for an existing safe dialogue."""
        dialogue=getattr(self.assistant,"dialogue",None)
        return bool(getattr(dialogue,"pending",None))
    def _has_local_routine_candidate(self, text):
        """Read-only gate for Assistant-owned routine discovery.

        VoiceController deliberately does not rank, select, confirm, or run a
        routine.  It only prevents generic UNKNOWN guidance from hiding an
        existing RoutineManager candidate when no conversation provider is
        available.  Assistant remains the authority for confidence and the
        following dialogue.
        """
        routines=getattr(self.assistant,"routines",None)
        finder=getattr(routines,"find_routine",None)
        if not callable(finder): return False
        try:
            found=finder(text)
        except Exception:
            return False
        if not isinstance(found,dict): return False
        return bool(found.get("routine") or found.get("candidates"))
    def _timeout_seconds(self):
        settings=getattr(getattr(self.assistant,"memory",None),"settings",{})
        try: return max(1,float(settings.get("conversation_timeout",30)))
        except (TypeError,ValueError): return 30.0
    def _pending_state(self):
        pending=getattr(getattr(self.assistant,"dialogue",None),"pending",None)
        return {key:pending.get(key) for key in ("kind","step")} if isinstance(pending,dict) else None
    def _expire_context(self):
        if self._context and self._clock()-self._context.last_activity_at>=self._timeout_seconds(): self.clear_context()
        return self._context is not None
    @property
    def conversation_context(self):
        """A read-only snapshot of the non-persistent current voice session."""
        self._expire_context()
        if not self._context: return None
        snapshot=asdict(self._context); snapshot["pending_dialogue_state"]=self._pending_state(); return snapshot
    @property
    def conversation_turn(self):
        return self._last_conversation_turn.to_dict() if self._last_conversation_turn else None
    @property
    def sequence_state(self):
        return self._sequence_state.to_dict() if self._sequence_state else None
    def clear_context(self):
        """End this voice session without writing any conversation data to Memory."""
        self._context=None; self._last_conversation_turn=None; self._sequence_state=None; self._session_paused=False
        dialogue=getattr(self.assistant,"dialogue",None)
        if dialogue is not None: dialogue.pending=None
    def _begin_session(self):
        now=self._clock(); self._context=VoiceSessionContext(now,last_activity_at=now)
    @staticmethod
    def _safe_target(intent, parsed=None):
        if intent=="OPEN_YOUTUBE": return "YouTube"
        if intent=="OPEN_FOLDER":
            parameters=getattr(parsed,"parameters",{}) or {}
            query=str(parameters.get("query","")).casefold()
            for value,label in {"загрузки":"Загрузки","документы":"Документы","рабочий стол":"Рабочий стол"}.items():
                if value in query: return label
        return ""
    def _safe_action_metadata(self, intent, parsed):
        parameters=getattr(parsed,"parameters",{}) or {}; metadata={}
        if intent=="SET_VOLUME" and isinstance(parameters.get("level"),int) and 0<=parameters["level"]<=100: metadata["level"]=parameters["level"]
        target=self._safe_target(intent,parsed)
        if target: metadata["safe_target"]=target
        return metadata
    def _create_turn(self, transcript, result, intent="", safe_metadata=None):
        pending=self._pending_state(); last_action=getattr(self._context,"last_action","") or ""; reference={"last_safe_action_type":last_action,"last_safe_action_target":self._safe_target(last_action),"reference_available":bool(getattr(self._context,"last_action_text",""))}
        state="PAUSED" if self._session_paused else getattr(getattr(self.assistant,"state",AssistantState.READY),"value",getattr(self.assistant,"state",AssistantState.READY))
        turn=ConversationTurn.create(user_text=transcript,session_state=state,intent=intent,response_type=result.data.get("response_type",""),result=result,pending_state=pending,reference_context=reference,safe_metadata=safe_metadata,timestamp=self._clock(),source="voice")
        return replace(turn,response_type=self.response_policy.decide(turn).response_type)
    def _parse_command(self,text):
        parser=getattr(self.assistant,"parser",None)
        try:
            exact=parser.parse(text) if parser else None
            resolver=getattr(self.assistant,"intent_resolver",None)
            return resolver.resolve(text,exact=exact,pending=getattr(getattr(self.assistant,"dialogue",None),"pending",None),session_context=self.conversation_context).command(text) if resolver and getattr(exact,"intent","")=="UNKNOWN" else exact
        except Exception: return None
    def _registered_resource_names(self):
        """Return only existing trusted aliases/display names; never discover paths."""
        items=[]; seen=set()
        def add(name, aliases=()):
            if not isinstance(name,str) or not name.strip() or name.casefold() in seen: return
            seen.add(name.casefold()); items.append(RegisteredResource(name.strip(),tuple(value for value in aliases if isinstance(value,str))))
        resources=getattr(self.assistant,"resources",None)
        known=getattr(resources,"known_resource_names",None)
        if callable(known):
            try:
                for name, aliases in known(): add(name,aliases)
            except Exception: pass
        application=getattr(getattr(self.assistant,"plugins",None),"plugins",{}).get("applications")
        for mapping_name in ("ALIASES","BUILTINS"):
            mapping=getattr(application,mapping_name,{})
            if isinstance(mapping,dict):
                for alias, name in mapping.items(): add(str(name),(str(alias),str(name)))
        memory=getattr(self.assistant,"memory",None)
        aliases=getattr(memory,"aliases",{})
        if isinstance(aliases,dict):
            for alias,name in aliases.items(): add(str(name),(str(alias),str(name)))
        for routine in getattr(memory,"learned_routines",[]) or []:
            if isinstance(routine,dict): add(routine.get("name",""),routine.get("aliases",()))
        return items
    def _pending_stt_hints(self):
        """Return one safe Vosk hint category only for an existing clarification."""
        pending=getattr(getattr(self.assistant,"dialogue",None),"pending",None)
        if not isinstance(pending,dict): return "",()
        kind,step=pending.get("kind"),pending.get("step")
        if kind=="volume_clarification" and step=="level": return "numeric",()
        if kind=="browser_clarification" and step=="site":
            values=[]
            for resource in self._registered_resource_names():
                values.append(resource.name); values.extend(resource.aliases)
            return "resource",tuple(values)
        return "",()
    def _configure_pending_stt_hints(self):
        setter=getattr(self.stt,"set_context_hints",None)
        if not callable(setter): return
        kind,candidates=self._pending_stt_hints()
        try: setter(kind,candidates)
        except Exception: pass
    def _clear_pending_stt_hints(self):
        clearer=getattr(self.stt,"clear_context_hints",None)
        if callable(clearer):
            try: clearer()
            except Exception: pass
    def _normalize_stt_resource(self, command):
        return self._stt_normalizer.normalize(command,self._registered_resource_names())
    def _reference_kind(self,text,parsed):
        """Recognize only explicit, deterministic session-reference phrases."""
        lower=text.lower().strip(" .,!?")
        intent=getattr(parsed,"intent","")
        parameters=getattr(parsed,"parameters",{}) or {}
        application=parameters.get("application","").lower().strip()
        placeholder=not application or all(token in {"его","её","ее","это","их","снова","ещё","еще","раз"} for token in re.findall(r"[а-яё]+",application))
        repeat=lower in {"снова","ещё раз","еще раз","повтори","сделай так же","сделай это ещё раз","сделай это еще раз","сделай это снова","повтори это","открой ещё раз","открой еще раз"}
        if repeat: return "repeat" if intent in {"","UNKNOWN"} or placeholder else None
        if re.search(r"\b(?:её|ее)\b",lower) and re.search(r"\b(?:увелич|громче)\w*",lower): return "volume_up"
        if re.search(r"\b(?:её|ее)\b",lower) and re.search(r"\b(?:уменьш|тише)\w*",lower): return "volume_down"
        if re.search(r"\b(?:это|его|их)\b",lower) and (intent in {"","UNKNOWN"} or intent=="OPEN_APPLICATION" and placeholder): return "browser"
        return None
    def _resolve_reference(self,kind):
        if not self._context or not self._context.last_action_text: return None
        last_intent=self._context.last_action
        if kind=="repeat" and last_intent in self._REPEATABLE_INTENTS: return self._context.last_action_text
        if kind=="browser" and last_intent in self._BROWSER_INTENTS: return self._context.last_action_text
        if kind=="volume_up" and last_intent in self._VOLUME_INTENTS: return "громче"
        if kind=="volume_down" and last_intent in self._VOLUME_INTENTS: return "тише"
        return None
    def _update_context(self,text,result,intent="",parsed=None,action_text=""):
        if not self._context: return
        self._context.last_user_text=text; self._context.last_assistant_text=result.message
        self._context.last_intent=intent
        if result.ok and action_text and intent in self._CONTEXT_ACTION_INTENTS:
            self._context.last_action=intent; self._context.last_action_text=action_text
            self._context.last_action_parameters=dict(getattr(parsed,"parameters",{}) or {})
        information=getattr(result,"data",{}).get("information_request") if isinstance(getattr(result,"data",{}),dict) else None
        if isinstance(information,dict): self._context.last_information=dict(information)
        self._context.pending_dialogue_state=self._pending_state(); self._context.last_activity_at=self._clock()
    @staticmethod
    def _ends_session(text):
        return text.strip().lower() in {"стоп","хватит","остановись","stop","пока","до свидания","до встречи"}
    @staticmethod
    def _session_control(text):
        return {"подожди":"pause","продолжай":"resume","слушай дальше":"resume","начнём сначала":"reset","начнем сначала":"reset","сбрось разговор":"reset"}.get(text.strip().lower())
    def _handle_session_control(self, command):
        control=self._session_control(command)
        if not control: return None
        if control=="pause":
            self._session_paused=True; return Result(True,"Хорошо.")
        if control=="resume":
            if not self._session_paused: return Result(True,"Я уже слушаю.")
            self._session_paused=False; return Result(True,"Продолжаю слушать.")
        dialogue=getattr(self.assistant,"dialogue",None)
        if dialogue is not None: dialogue.pending=None
        self._session_paused=False; self._begin_session(); return Result(True,"Начинаем сначала.")
    @staticmethod
    def _is_session_status_query(text):
        return text.strip().lower().strip("?") in {"ты слушаешь","ты сейчас слушаешь","какой у тебя статус","ты на паузе"}
    def _session_status_result(self):
        if self._session_paused: return Result(True,"Я на паузе.")
        state=getattr(self.assistant,"state",AssistantState.READY)
        messages={AssistantState.LISTENING:"Да, я слушаю.",AssistantState.PROCESSING:"Я обрабатываю запрос.",AssistantState.SPEAKING:"Я сейчас говорю.",AssistantState.READY:"Да, я готов."}
        return Result(True,messages.get(state,"Да, я готов."))
    @staticmethod
    def _is_contextual_help_query(text):
        return text.strip().lower().strip("?") in {"что я могу сейчас сказать","что можно сейчас сделать","что ты сейчас от меня ждёшь","что ты сейчас от меня ждешь","как я могу с тобой сейчас говорить"}
    def _contextual_help_result(self):
        if self._session_paused: return Result(True,"Сейчас я на паузе. Скажи «продолжай», чтобы возобновить.")
        dialogue=getattr(self.assistant,"dialogue",None)
        if getattr(dialogue,"pending",None):
            pending=dialogue.pending; kind=pending.get("kind")
            messages={"browser_clarification":"Сейчас я жду адрес или название сайта. Например, YouTube.","volume_clarification":"Сейчас я жду значение громкости от 0 до 100.","folder_clarification":"Сейчас я жду: Загрузки, Документы или Рабочий стол."}
            return Result(True,messages.get(kind,"Сейчас "+dialogue.pending_recap().lower()))
        return Result(True,"Ты можешь попросить меня открыть браузер, изменить громкость, выполнить сохранённую процедуру или задать информационный вопрос.")
    def _unknown_guidance_result(self):
        if self._session_paused: return Result(True,"Я сейчас на паузе. Скажи «продолжай».",{"read_only_guidance":True})
        return Result(True,"Не совсем понял. Скажи, например, «открой браузер», «установи громкость» или задай вопрос.",{"read_only_guidance":True})
    @staticmethod
    def _is_session_acknowledgement(text):
        return text.strip().lower().strip(".!") in {"понял","понятно","ясно","хорошо","ладно","принято"}
    def _session_acknowledgement_result(self):
        if self._session_paused: return Result(True,"Хорошо. Я остаюсь на паузе.",{"read_only_guidance":True})
        if self._has_pending_dialogue(): return Result(True,"Понял. Я всё ещё жду ответ.",{"read_only_guidance":True})
        return Result(True,"Хорошо.",{"read_only_guidance":True})
    def process_transcript(self, transcript):
        """Route one recognized phrase once through the existing Assistant path."""
        if not isinstance(transcript,str) or not transcript.strip(): return None
        wake_command=self.wake_word.strip(transcript)
        active=self._expire_context()
        if wake_command is not None:
            if not active: self._begin_session(); active=True
            else: self._context.last_activity_at=self._clock()
            command=self._normalize_stt_resource(wake_command.strip())
            if not command:
                result=Result(True,"Да, слушаю."); self._update_context(transcript.strip(),result); self._publish("",result); return result
        else:
            command=self._normalize_stt_resource(transcript.strip()) if active else None
        if not command or not command.strip(): return None
        if active and not self._ends_session(command):
            control_result=self._handle_session_control(command)
            if control_result:
                self._update_context(command,control_result)
                self._publish(command,control_result); self.assistant.set_state(AssistantState.READY); return control_result
            if self._is_session_status_query(command):
                result=self._session_status_result(); self._publish(command,result); return result
            if self._is_contextual_help_query(command):
                result=self._contextual_help_result(); self._publish(command,result); return result
            if self._is_session_acknowledgement(command):
                result=self._session_acknowledgement_result(); self._publish(command,result); return result
            if self._session_paused:
                result=Result(True,"Я на паузе. Скажите «продолжай».")
                self._publish(command,result); self.assistant.set_state(AssistantState.READY); return result
        self.assistant.set_state(AssistantState.PROCESSING)
        pending=self._has_pending_dialogue()
        parsed=None if pending else self._parse_command(command)
        intent="" if pending else getattr(parsed,"intent","")
        reference_kind=None if pending else self._reference_kind(command,parsed)
        if reference_kind:
            resolved=self._resolve_reference(reference_kind)
            if not resolved:
                result=Result(False,"Не могу определить, к какому безопасному действию относится эта ссылка.")
                self._update_context(command,result,intent,parsed)
                self._publish(command,result); self.assistant.set_state(AssistantState.READY); return result
            command=resolved; parsed=self._parse_command(command); intent=getattr(parsed,"intent","")
        providers=getattr(self.assistant,"conversation_providers",None)
        provider_available=bool(providers and callable(getattr(providers,"available",None)) and providers.available())
        # An active demonstration is an existing Assistant-owned dialogue.
        # Its completion/cancel/name turns are intentionally not all parser
        # intents, so do not replace them with generic voice guidance before
        # Assistant has evaluated the current learning state.
        learning_active=bool(getattr(self.assistant,"routine_learning",False))
        local_routine_candidate=(intent=="UNKNOWN" and not provider_available and self._has_local_routine_candidate(command))
        if not pending and not learning_active and intent=="UNKNOWN" and not provider_available and not local_routine_candidate:
            result=self._unknown_guidance_result(); self._publish(command,result); self.assistant.set_state(AssistantState.READY); return result
        if wake_command is None and not pending and intent=="":
            self.assistant.set_state(AssistantState.READY); return None
        try:
            contextual=getattr(self.assistant,"handle_with_session_context",None)
            result=contextual(command,self.conversation_context) if callable(contextual) else self.assistant.handle(command)
        except Exception as exc: result=Result(False,f"Ошибка обработки голосовой команды: {exc}")
        if reference_kind and result.ok:
            metadata=dict(result.data); metadata["reference_replay"]=True
            result=Result(True,"Повторяю. "+result.message,metadata,result.confirmation_required)
        executed_intent=result.data.get("executed_intent") if result.ok else ""
        executed_parameters=result.data.get("executed_parameters") if result.ok else None
        context_intent=executed_intent if isinstance(executed_intent,str) else intent
        context_parsed=Command(context_intent,dict(executed_parameters)) if isinstance(executed_parameters,dict) else parsed
        if not result.data.get("read_only_guidance") or result.data.get("information_request"):
            self._update_context(command,result,context_intent,context_parsed,command)
        # Response composition keeps its established parser/result semantics;
        # only the bounded session snapshot needs the provider-resolved intent.
        self._publish(command,result,intent,self._safe_action_metadata(intent,parsed))
        if self._ends_session(command) or result.data.get("end_session"):
            self.clear_context()
        try:
            if not self.tts.is_speaking(): self.assistant.set_state(AssistantState.READY)
        except Exception: self.assistant.set_state(AssistantState.READY)
        return result
    def _loop(self):
        settings=self.assistant.memory.settings
        while self._running.is_set():
            try:
                self._expire_context()
                self.assistant.set_state(AssistantState.LISTENING)
                self._configure_pending_stt_hints()
                try: heard=self.stt.listen_once(self.microphone.selected_index(),settings.get("speech_timeout",6))
                finally: self._clear_pending_stt_hints()
                self.process_transcript(heard)
            except RuntimeError as exc:
                self.assistant.set_state(AssistantState.ERROR)
                if self.on_result:
                    try: self.on_result("",Result(False,str(exc)))
                    except Exception: pass
                threading.Event().wait(.05)
            except Exception as exc:
                self.assistant.set_state(AssistantState.ERROR)
                if self.on_result:
                    try: self.on_result("",Result(False,f"Ошибка голосового контроллера: {exc}"))
                    except Exception: pass
                threading.Event().wait(.05)
            finally:
                if self._running.is_set() and not self.tts.is_speaking(): self.assistant.set_state(AssistantState.READY)
        self.assistant.set_state(AssistantState.READY)
