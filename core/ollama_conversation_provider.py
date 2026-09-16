"""Local-only Ollama conversational provider behind the existing provider contract."""
from dataclasses import asdict, dataclass
import json
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .conversation_provider import ProviderAdapter, ProviderCapabilities, ProviderResponse, bounded_context_snapshot

_MODEL=re.compile(r"[A-Za-z0-9._:-]{1,128}$")
_SENSITIVE=re.compile(r"(?:token|secret|password|credential|authorization|cookie|api[_-]?key)",re.I)


@dataclass(frozen=True)
class OllamaProviderConfig:
    provider_id: str="ollama"
    enabled: bool=False
    model: str="qwen3:8b"
    endpoint: str="http://localhost:11434"
    request_timeout: float=45.0
    max_output_tokens: int=300

    def valid(self):
        parsed=urlparse(self.endpoint)
        local=parsed.scheme=="http" and parsed.hostname in {"localhost","127.0.0.1","::1"} and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
        return (self.provider_id=="ollama" and isinstance(self.enabled,bool) and bool(_MODEL.fullmatch(self.model)) and local
                and isinstance(self.request_timeout,(int,float)) and not isinstance(self.request_timeout,bool) and 1<=self.request_timeout<=120
                and isinstance(self.max_output_tokens,int) and not isinstance(self.max_output_tokens,bool) and 1<=self.max_output_tokens<=1000)


@dataclass(frozen=True)
class OllamaRequestDTO:
    provider_id: str
    model: str
    endpoint: str
    request_timeout: float
    max_output_tokens: int
    system_instructions: str
    capabilities: dict
    turn: dict
    context: dict


class OllamaConversationProvider(ProviderAdapter):
    """The only local Ollama HTTP boundary; it never executes commands or accesses Memory."""
    provider_id="ollama"
    network_enabled=False
    capabilities=ProviderCapabilities(True,True,True,True,True)

    def __init__(self, config=None, transport=None, strict_mode=None):
        self.config=config or OllamaProviderConfig()
        self._transport=transport or self._http_transport
        # Injected transports are test seams for historical fixtures; the real
        # local HTTP transport always requires the mode discriminator.
        self._require_mode=(transport is None) if strict_mode is None else bool(strict_mode)
        self._allowed_intents=()
        self._last_response_diagnostic={"normalization_status":"not_checked"}

    @property
    def last_response_diagnostic(self):
        """Non-persistent response shape only; never includes model content or prompts."""
        return dict(self._last_response_diagnostic)

    def configured(self): return bool(self.config.valid() and self.config.enabled)
    def activate_transport(self):
        if not self.configured(): return False
        self.network_enabled=True; return True
    def deactivate_transport(self): self.network_enabled=False

    def set_allowed_intents(self, intents):
        """Accept the registry's authoritative runtime allow-list, never a model-defined list."""
        self._allowed_intents=tuple(sorted({intent for intent in intents if isinstance(intent,str) and _MODEL.fullmatch(intent)}))

    def _instructions(self):
        allowed=", ".join(self._allowed_intents) or "none"
        browser_guidance=""
        if "OPEN_BROWSER" in self._allowed_intents:
            browser_guidance=(" OPEN_BROWSER means launch the installed browser application and accepts {} parameters. "
                            "Semantic examples: ‘Мне нужен доступ к сайтам’, ‘Открой сайт’, ‘Зайди в браузер’, "
                            "‘Открой браузер’, and ‘Запусти браузер’ all require intent OPEN_BROWSER.")
        volume_guidance=""
        if "SET_VOLUME" in self._allowed_intents:
            volume_guidance=(" SET_VOLUME means change the system volume and requires exactly parameters {\"level\": N}, "
                            "where N is an integer from 0 through 100. Treat a requested numeric volume level as SET_VOLUME: "
                            "‘Поставь громкость на 50%’, ‘Сделай громкость 50%’, ‘Убавь до 50%’, and ‘Громкость нужна на 50%’ "
                            "all require SET_VOLUME with level 50 when deterministic handling did not already apply.")
        cancellation_guidance=""
        if "CANCEL_PENDING_DIALOGUE" in self._allowed_intents:
            cancellation_guidance=(" CANCEL_PENDING_DIALOGUE means cancel a currently pending or unfinished operation and requires {} parameters. "
                                  "Examples: ‘Отмени это’, ‘Не надо, отмена’, ‘отмена’, ‘отмени’, ‘не надо’, ‘забудь’, and ‘не делай’. "
                                  "Do not choose cancellation merely because a request is short or has a verb. A numeric requested volume level "
                                  "takes priority over cancellation when it asks to change volume. Never use cancellation for an ordinary task, "
                                  "capability, or goal request, and never combine it with numeric or action parameters.")
        informational_guidance=(" Conversation mode is limited to greetings, social interaction, thanks, casual chat, and meta conversation about JARVIS: "
                               "‘Привет, Джарвис’, ‘Как у тебя дела?’, ‘Спасибо’, ‘Кто ты?’, ‘Что ты умеешь?’, and ‘Расскажи о себе’. "
                               "Do not use conversation merely because a user asks a question or asks for an explanation of a topic.")
        site_guidance=""
        if "INCOMPLETE_OPEN_SITE" in self._allowed_intents:
            site_guidance=(" INCOMPLETE_OPEN_SITE requires {} and requires_clarification true. Use it only when the user explicitly asks to "
                           "open or visit a site but omits its name or URL: ‘Открой сайт’, ‘Мне нужно открыть сайт’, ‘Зайди на сайт’, "
                           "or ‘Открой какой-нибудь сайт’. Do not use it for questions about capabilities or ordinary conversation.")
        continuation_guidance=(" The supplied context is bounded and ephemeral. Use a previous safe action only when a follow-up is explicit, "
                               "the context names one compatible allow-listed action, and no missing parameter must be invented. "
                               "If the context is absent, stale, ambiguous, cancelled, or insufficient, do not infer an action; use an existing "
                               "clarification when applicable or intent null. For intent null, provide a natural, relevant answer and never claim an action ran.")
        goal_guidance=(" For a broad user goal that is not a direct action, use mode goal, set goal to capability, intent null, parameters {}, and do not claim "
                       "a plan or action was performed. This only asks JARVIS to check its existing saved capabilities through its normal safety flow. "
                       "Examples: ‘Мне нужно подготовить компьютер к работе’ and ‘Помоги подготовиться к работе’ are goal requests. "
                       "A goal is not permission to invent actions, tools, routines, or commands.")
        resource_guidance=""
        if "OPEN_RESOURCE" in self._allowed_intents:
            resource_guidance=(" OPEN_RESOURCE is a semantic request only: it requires parameters {}, a resource_type of application, game, website, folder, or file, and a short target name. "
                               "You have no filesystem, process, browser, URL, or launch access. Never return a path, executable, shell command, URL, or internal identifier. "
                               "Examples: ‘Открой FIFA’ -> application or game / FIFA; ‘Открой YouTube’ -> website / YouTube; ‘Найди диплом’ -> file / диплом; ‘Открой папку с играми’ -> folder / игры.")
        information_guidance=""
        if "INFORMATION_REQUEST" in self._allowed_intents:
            information_guidance=(" INFORMATION_REQUEST uses mode information and asks a trusted information provider for current external data; it never claims facts itself. "
                                  "Its parameters contain operation GET_INFORMATION, category, and only the safe fields required by that category: weather uses location; "
                                  "web_search uses query; site_information uses target and query. For a factual topic use web_search with a short safe query. "
                                  "Information has priority over every action. A request to receive facts, knowledge, or an explanation is information even if it contains a verb such as give, tell, show, look up, or explain; "
                                  "it is not an action unless the user explicitly asks JARVIS to operate the computer, an application, browser, file, or system setting. "
                                  "Examples of information: ‘Дай мне информацию по теме искусственного интеллекта простыми словами’, ‘Расскажи мне о принципах работы искусственного интеллекта’, "
                                  "‘Что известно об искусственном интеллекте?’, and ‘Объясни, что такое искусственный интеллект.’ all use mode information. "
                                  "Use intent null for ordinary conversation. Never use OPEN_RESOURCE when the user asks to retrieve and report information.")
        mode_guidance=(" Choose exactly one required mode using this semantic priority: information, then action, then goal, then conversation. Do not add fields from another mode. "
                       "Information requests use mode information and never contain goal. Capability discovery uses mode goal and never contains information fields. "
                       "Mode action is only for an allow-listed computer execution request: operating an application, browser, file, resource, screenshot, volume, or other existing system action. "
                       "Do not select action merely because the user used an imperative verb. ‘Открой браузер’, ‘Открой YouTube’, ‘Убавь громкость до 50 процентов’, and ‘Сделай скриншот’ are actions; "
                       "asking for facts or an explanation is information. An action must use a matching allow-listed intent and compatible parameters. "
                       "Conversation is only the remaining social, casual, or JARVIS-meta dialogue. For example, an information request has mode information, intent INFORMATION_REQUEST, "
                       "and GET_INFORMATION parameters; a capability request has mode goal, intent null, goal capability, and empty parameters.")
        return ("You are a bounded conversational layer. You have no OS, shell, tool, filesystem, or execution access. "
                "Return one JSON object matching the supplied JSON schema. The intent may only be JSON null or exactly one "
                f"value from this authoritative runtime allow-list: {allowed}. For an action request, choose the nearest "
                "available allow-listed intent even when the wording is a natural paraphrase of a canonical command. Use null "
                "only for genuinely conversational/informational requests, or when no available action applies; never use null "
                "merely because wording differs. Never invent intent names, natural-language intents, tool names, or function names. "
                "Use minimal structured parameters only when certain; otherwise use intent null or requires_clarification."
                f"{browser_guidance}{volume_guidance}{cancellation_guidance}{informational_guidance}{site_guidance}{continuation_guidance}{mode_guidance}{goal_guidance}{resource_guidance}{information_guidance} Never expose secrets or reasoning.")

    def _response_schema(self):
        common={"text":{"type":"string"},"requires_clarification":{"type":"boolean"},"end_session":{"type":"boolean"},"response_type":{"type":"string","enum":["conversational","informational"]},"conversational":{"type":"boolean"}}
        required=["mode","intent","parameters","text","requires_clarification","end_session"]
        def information_branch(category,fields,required_fields):
            parameters={"operation":{"const":"GET_INFORMATION"},"category":{"const":category},**fields}
            return {"type":"object","description":"Information retrieval only; no goal or action.","additionalProperties":False,"properties":{**common,"mode":{"const":"information","description":"Use for factual topic, data, or explanation requests."},"intent":{"const":"INFORMATION_REQUEST"},"parameters":{"type":"object","additionalProperties":False,"properties":parameters,"required":["operation","category",*required_fields]}},"required":required}
        action_intents=sorted(intent for intent in self._allowed_intents if intent!="INFORMATION_REQUEST")
        action={"type":"object","description":"Allow-listed computer execution only; never use for factual information or topic explanations.","additionalProperties":False,"properties":{**common,"mode":{"const":"action"},"intent":{"type":"string","enum":action_intents},"parameters":{"type":"object"},"resource_type":{"type":["string","null"],"enum":[None,"application","game","website","folder","file"]},"target":{"type":["string","null"]}},"required":required}
        goal={"type":"object","description":"Capability discovery only; never information retrieval.","additionalProperties":False,"properties":{**common,"mode":{"const":"goal"},"intent":{"type":"null"},"goal":{"const":"capability"},"parameters":{"type":"object","maxProperties":0}},"required":[*required,"goal"]}
        conversation={"type":"object","description":"Social, casual, or JARVIS-meta dialogue only; not topic information requests.","additionalProperties":False,"properties":{**common,"mode":{"const":"conversation"},"intent":{"type":"null"},"parameters":{"type":"object","maxProperties":0}},"required":required}
        return {"oneOf":[information_branch("weather",{"location":{"type":"string","minLength":1},"time_context":{"type":"string"},"source_hint":{"type":"string"}},["location"]),
                         information_branch("web_search",{"query":{"type":"string","minLength":1},"target":{"type":"string"},"source_hint":{"type":"string"}},["query"]),
                         information_branch("site_information",{"target":{"type":"string","minLength":1},"query":{"type":"string","minLength":1},"source_hint":{"type":"string"}},["target","query"]),
                         goal,action,conversation]}

    def build_request(self, turn, context):
        bounded_turn=self.bounded_turn(turn) if callable(getattr(turn,"to_dict",None)) else dict(turn or {})
        return OllamaRequestDTO(self.provider_id,self.config.model,self.config.endpoint,float(self.config.request_timeout),self.config.max_output_tokens,self._instructions(),asdict(self.capabilities),bounded_turn,bounded_context_snapshot(context))

    @staticmethod
    def _http_transport(endpoint, payload, timeout):
        request=Request(endpoint.rstrip("/")+"/api/chat",data=json.dumps(payload,ensure_ascii=False).encode("utf-8"),headers={"Content-Type":"application/json"},method="POST")
        with urlopen(request,timeout=timeout) as response: return response.read().decode("utf-8")

    @staticmethod
    def _safe_keys(value):
        return tuple(sorted(str(key) for key in value if not _SENSITIVE.search(str(key)))) if isinstance(value,dict) else ()

    @classmethod
    def _legacy_map_response_details(cls, payload):
        """Legacy mapper retained for direct fixtures; production transport requires mode."""
        if isinstance(payload,ProviderResponse): return payload,"accepted"
        if not isinstance(payload,dict): return None,"malformed_schema"
        payload=dict(payload)
        for key,default in {"intent":"","parameters":{},"conversational":False,"confidence":"","requires_clarification":False,"end_session":False,"response_type":"conversational","goal":"","resource_type":"","target":"","information_category":"","information_query":"","location":"","time_context":"","source_hint":""}.items():
            if payload.get(key) is None: payload[key]=default
        payload.pop("thinking",None); payload.pop("reasoning",None)
        allowed={"text","intent","parameters","conversational","confidence","requires_clarification","end_session","response_type","goal","resource_type","target","information_category","information_query","location","time_context","source_hint"}
        if any(key not in allowed for key in payload): return None,"malformed_schema"
        if not isinstance(payload.get("text",""),str): return None,"invalid_field_type"
        if not payload.get("text") and not payload.get("intent"): return None,"missing_required_field"
        try: return ProviderResponse(**payload),"accepted"
        except TypeError: return None,"malformed_schema"

    @classmethod
    def _map_response_details(cls, payload, require_mode=False):
        """Convert one selected transport mode into the existing ProviderResponse DTO."""
        if isinstance(payload,ProviderResponse): return payload,"accepted"
        if not isinstance(payload,dict): return None,"malformed_schema"
        mode=payload.get("mode")
        if mode is None and not require_mode: return cls._legacy_map_response_details(payload)
        if mode not in {"information","goal","action","conversation"}: return None,"unknown_mode"
        payload=dict(payload)
        common={"mode","text","intent","parameters","conversational","requires_clarification","end_session","response_type"}
        if mode=="information":
            if set(payload)-common or payload.get("intent")!="INFORMATION_REQUEST" or not isinstance(payload.get("parameters"),dict): return None,"mode_payload_mismatch"
            parameters=payload["parameters"]; allowed={"operation","category","query","target","location","time_context","source_hint"}
            if set(parameters)-allowed or parameters.get("operation")!="GET_INFORMATION" or parameters.get("category") not in {"weather","web_search","site_information"}: return None,"mode_payload_mismatch"
            if parameters["category"]=="weather" and not parameters.get("location"): return None,"mode_payload_mismatch"
            if parameters["category"]=="web_search" and not parameters.get("query"): return None,"mode_payload_mismatch"
            if parameters["category"]=="site_information" and (not parameters.get("target") or not parameters.get("query")): return None,"mode_payload_mismatch"
            # The information adapter, not the model, is authoritative for factual
            # text.  Keep the mode schema strict, but do not feed an unbounded
            # model summary into the generic conversational-text validator.
            converted={key:payload.get(key,default) for key,default in {"conversational":False,"requires_clarification":False,"end_session":False,"response_type":"informational"}.items()}
            converted["text"]=""
            return ProviderResponse(**converted,intent="INFORMATION_REQUEST",parameters={},information_category=parameters["category"],information_query=parameters.get("query","") or "",target=parameters.get("target","") or "",location=parameters.get("location","") or "",time_context=parameters.get("time_context","") or "",source_hint=parameters.get("source_hint","") or ""),"accepted"
        if mode=="goal":
            if set(payload)-(common|{"goal"}) or payload.get("intent") is not None or payload.get("goal")!="capability" or payload.get("parameters")!={}: return None,"mode_payload_mismatch"
            return cls._legacy_map_response_details({key:value for key,value in payload.items() if key!="mode"})
        if mode=="conversation":
            if set(payload)-common or payload.get("intent") is not None or payload.get("parameters")!={}: return None,"mode_payload_mismatch"
            return cls._legacy_map_response_details({key:value for key,value in payload.items() if key!="mode"})
        if set(payload)-(common|{"resource_type","target"}) or not isinstance(payload.get("intent"),str) or not payload.get("intent") or payload.get("intent")=="INFORMATION_REQUEST": return None,"mode_payload_mismatch"
        return cls._legacy_map_response_details({key:value for key,value in payload.items() if key!="mode"})

    @classmethod
    def map_response(cls, payload):
        return cls._map_response_details(payload)[0]

    @staticmethod
    def _extract_json_object(content):
        """Accept one model JSON object, including a single Markdown wrapper.

        This is syntactic normalization only: it never adds fields or infers an
        intent.  A second decodable JSON object remains ambiguous and is
        rejected before the authoritative registry validator sees it.
        """
        if not isinstance(content,str): return content
        value=content.strip()
        fenced=re.fullmatch(r"```(?:json)?\s*(.*?)\s*```",value,re.I|re.S)
        if fenced: value=fenced.group(1).strip()
        start=value.find("{")
        if start<0: return None
        try:
            parsed,end=json.JSONDecoder().raw_decode(value[start:])
        except (TypeError,ValueError):
            return None
        if not isinstance(parsed,dict): return None
        remainder=value[start+end:]
        for match in re.finditer(r"\{",remainder):
            try:
                extra,_=json.JSONDecoder().raw_decode(remainder[match.start():])
            except (TypeError,ValueError):
                continue
            if isinstance(extra,dict): return None
        return parsed

    @staticmethod
    def _safe_preview(content,limit=160):
        """A bounded, value-free structural preview for diagnostics.

        Provider text can itself be private, so even a truncated literal is not
        safe to retain or print.  Shape and length remain enough to distinguish
        JSON, a Markdown wrapper, and unstructured content.
        """
        if not isinstance(content,str): return ""
        parsed=OllamaConversationProvider._extract_json_object(content)
        if isinstance(parsed,dict):
            keys=tuple(sorted(str(key) for key in parsed if not _SENSITIVE.search(str(key))))
            return "json_object keys="+repr(keys)
        return "non_json_string length="+str(len(content))

    def _record_response_shape(self, decoded=None, content=None, normalized=None, normalization_status="not_checked"):
        message=decoded.get("message",{}) if isinstance(decoded,dict) else {}
        thinking=message.get("thinking") if isinstance(message,dict) else None
        payload=normalized if isinstance(normalized,dict) else {}
        parameters=payload.get("parameters",{})
        self._last_response_diagnostic={
            "top_level_type":type(decoded).__name__ if decoded is not None else "none",
            "top_level_fields":self._safe_keys(decoded),
            "message_fields":self._safe_keys(message),
            "content_type":type(content).__name__ if content is not None else "none",
            "content_length":len(content) if isinstance(content,str) else 0,
            "content_preview":self._safe_preview(content),
            "thinking_present":thinking is not None,
            "thinking_length":len(thinking) if isinstance(thinking,str) else 0,
            "normalized_object_type":type(normalized).__name__ if normalized is not None else "none",
            "normalized_fields":self._safe_keys(payload),
            "normalized_field_types":{key:type(payload[key]).__name__ for key in self._safe_keys(payload)},
            "parameter_keys":self._safe_keys(parameters),
            "normalization_status":normalization_status,
        }

    def respond_bounded(self, turn, context):
        if not self.network_enabled: return None
        request=self.build_request(turn,context)
        payload={"model":request.model,"stream":False,"think":False,"format":self._response_schema(),"options":{"num_predict":request.max_output_tokens},"messages":[
            {"role":"system","content":request.system_instructions},
            {"role":"user","content":json.dumps({"turn":request.turn,"context":request.context,"capabilities":request.capabilities},ensure_ascii=False)}]}
        raw=self._transport(request.endpoint,payload,request.request_timeout)
        decoded=None; content=None; normalized=None
        try:
            decoded=json.loads(raw) if isinstance(raw,str) else dict(raw)
            content=decoded.get("message",{}).get("content",decoded.get("response",""))
            normalized=self._extract_json_object(content)
            mapped,status=self._map_response_details(normalized,require_mode=self._require_mode)
        except (TypeError,ValueError,AttributeError):
            mapped=None; status="malformed_schema"
        self._record_response_shape(decoded,content,normalized,status)
        if mapped is None: raise RuntimeError("Ollama provider returned an invalid response.")
        return mapped
