"""Optional, execution-free boundary for future conversational backends."""
from dataclasses import asdict, dataclass, field
import re

_SENSITIVE=re.compile(r"(?:token|secret|password|credential|authorization|cookie|api[_-]?key)",re.I)

def bounded_context_snapshot(context):
    context=dict(context or {}); allowed={"session_state","last_intent","last_action","pending_dialogue_state","reference_available","sequence_depth","last_information"}
    safe={}
    for key,value in context.items():
        if key not in allowed or _SENSITIVE.search(str(key)): continue
        if isinstance(value,(int,float,bool,type(None))): safe[key]=value
        elif isinstance(value,str) and not _SENSITIVE.search(value): safe[key]=value
        elif isinstance(value,dict): safe[key]={str(item_key):item_value for item_key,item_value in value.items() if not _SENSITIVE.search(str(item_key)) and isinstance(item_value,(str,int,float,bool,type(None))) and not (isinstance(item_value,str) and _SENSITIVE.search(item_value))}
    return safe

@dataclass(frozen=True)
class ProviderResponse:
    text: str=""
    intent: str=""
    parameters: dict=field(default_factory=dict)
    conversational: bool=False
    confidence: str=""
    requires_clarification: bool=False
    end_session: bool=False
    response_type: str="conversational"
    # A bounded semantic request, never an executable plan or capability name.
    goal: str=""
    resource_type: str=""
    target: str=""
    information_category: str=""
    information_query: str=""
    location: str=""
    time_context: str=""
    source_hint: str=""


@dataclass(frozen=True)
class ProviderValidationDiagnostic:
    """In-memory structural result of the authoritative validator.

    It deliberately contains no provider text, parameter values, prompt/context,
    thinking, exception text, or secrets.  It is observation, not a second
    validator.
    """
    accepted: bool=False
    fields_present: tuple=()
    field_types: dict=field(default_factory=dict)
    missing_fields: tuple=()
    invalid_fields: tuple=()
    text_length: int=0
    parameter_keys: tuple=()
    sensitive_parameter_keys_present: bool=False
    intent_class: str="null"
    failure_category: str="not_checked"

class ConversationProvider:
    def respond(self, turn, context): raise NotImplementedError

@dataclass(frozen=True)
class ProviderCapabilities:
    supports_text: bool=False
    supports_structured_intent: bool=False
    supports_clarification: bool=False
    supports_context: bool=False
    supports_end_session: bool=False

class ProviderAdapter(ConversationProvider):
    """Future external-provider boundary; deliberately contains no transport code."""
    capabilities=ProviderCapabilities()
    @staticmethod
    def bounded_turn(turn):
        source=turn.to_dict() if callable(getattr(turn,"to_dict",None)) else {}
        allowed={"normalized_text","session_state","intent","response_type","pending_state","reference_context","timestamp","source"}
        bounded={key:value for key,value in source.items() if key in allowed and not _SENSITIVE.search(str(key))}
        for key in ("normalized_text","session_state","intent","response_type","source"):
            value=bounded.get(key)
            if not isinstance(value,str) or len(value)>500 or _SENSITIVE.search(value): bounded.pop(key,None)
        for key in ("pending_state","reference_context"):
            value=bounded.get(key,{})
            bounded[key]={str(item_key):item_value for item_key,item_value in dict(value or {}).items() if not _SENSITIVE.search(str(item_key)) and isinstance(item_value,(str,int,float,bool,type(None))) and not (isinstance(item_value,str) and _SENSITIVE.search(item_value))}
        return bounded
    def respond(self, turn, context):
        return self.respond_bounded(self.bounded_turn(turn),dict(context or {}))
    def respond_bounded(self, turn, context): raise NotImplementedError

class StubConversationProvider(ProviderAdapter):
    """Explicitly not-configured adapter used only to preserve deterministic fallback."""
    capabilities=ProviderCapabilities()
    def respond_bounded(self, turn, context): return None

class ConversationProviderRegistry:
    """Execution-free, in-memory lifecycle for an optional conversational provider."""
    def __init__(self):
        self.provider=None; self._enabled=False; self._failed=False
        self._last_validation=ProviderValidationDiagnostic()

    @property
    def last_validation_diagnostic(self):
        """Return a copy-safe, non-persistent validation shape summary."""
        return asdict(self._last_validation)
    @property
    def state(self):
        if self._failed: return "ERROR"
        if self.provider is None: return "UNCONFIGURED"
        return "ENABLED" if self._enabled else "DISABLED"
    def configure(self, provider):
        """Configure a provider without enabling it or persisting any configuration."""
        if provider is not None and not callable(getattr(provider,"respond",None)):
            self.provider=None; self._enabled=False; self._failed=True; return False
        self.provider=provider; self._enabled=False; self._failed=False; return provider is not None
    def enable(self):
        if self.provider is None: return False
        self._enabled=True; self._failed=False; return True
    def disable(self):
        if self.provider is None: return False
        self._enabled=False; return True
    def replace(self, provider, enabled=True):
        if not self.configure(provider): return False
        return self.enable() if enabled else True
    def reset(self):
        self.provider=None; self._enabled=False; self._failed=False
    def register(self, provider):
        """Backward-compatible shortcut for configure + enable."""
        if provider is not None and not callable(getattr(provider,"respond",None)): raise TypeError("provider must implement respond")
        if provider is None: self.reset()
        else: self.replace(provider)
    def available(self): return self.state=="ENABLED"
    def bounded_context(self, context):
        return bounded_context_snapshot(context)
    def _validation_diagnostic(self, response, accepted=False, failure_category="not_checked", invalid_fields=(), missing_fields=()):
        values=vars(response) if isinstance(response,ProviderResponse) else (dict(response) if isinstance(response,dict) else {})
        fields=tuple(sorted(str(key) for key in values if not _SENSITIVE.search(str(key))))
        field_types={key:type(values[key]).__name__ for key in fields}
        text=values.get("text","")
        parameters=values.get("parameters",{})
        parameter_keys=()
        sensitive_parameter_keys=False
        if isinstance(parameters,dict):
            sensitive_parameter_keys=any(_SENSITIVE.search(str(key)) for key in parameters)
            parameter_keys=tuple(sorted(str(key) for key in parameters if not _SENSITIVE.search(str(key))))
        intent=values.get("intent")
        intent_class="null" if not intent else "known"
        self._last_validation=ProviderValidationDiagnostic(
            accepted=accepted,fields_present=fields,field_types=field_types,
            missing_fields=tuple(missing_fields),invalid_fields=tuple(invalid_fields),
            text_length=len(text) if isinstance(text,str) else 0,
            parameter_keys=parameter_keys,sensitive_parameter_keys_present=sensitive_parameter_keys,
            intent_class=intent_class,failure_category=failure_category)

    def validate(self, response, allowed_intents=()):
        if not isinstance(response,ProviderResponse):
            self._validation_diagnostic(response,failure_category="malformed_schema",invalid_fields=("response",),missing_fields=("ProviderResponse",)); return None
        if not isinstance(response.text,str):
            self._validation_diagnostic(response,failure_category="invalid_field_type",invalid_fields=("text",)); return None
        if len(response.text)>500:
            self._validation_diagnostic(response,failure_category="text_too_long",invalid_fields=("text",)); return None
        if response.intent and response.intent not in set(allowed_intents):
            self._validation_diagnostic(response,failure_category="unknown_intent",invalid_fields=("intent",)); return None
        if response.response_type not in {"conversational","informational"}:
            self._validation_diagnostic(response,failure_category="invalid_field_type",invalid_fields=("response_type",)); return None
        if not isinstance(response.parameters,dict):
            self._validation_diagnostic(response,failure_category="invalid_parameter_shape",invalid_fields=("parameters",)); return None
        no_parameter_intents={"CANCEL_PENDING_DIALOGUE","OPEN_BROWSER","OPEN_YOUTUBE","OPEN_FOLDER","INCOMPLETE_OPEN_SITE"}
        if response.intent in no_parameter_intents and response.parameters:
            self._validation_diagnostic(response,failure_category="intent_parameter_mismatch",invalid_fields=("parameters",)); return None
        if response.intent=="SET_VOLUME":
            level=response.parameters.get("level")
            clarification_without_value=response.requires_clarification and response.parameters=={}
            if not clarification_without_value and (set(response.parameters)!={"level"} or not isinstance(level,int) or isinstance(level,bool) or not 0<=level<=100):
                self._validation_diagnostic(response,failure_category="intent_parameter_mismatch",invalid_fields=("parameters",)); return None
        if not isinstance(response.goal,str) or response.goal not in {"","capability"}:
            self._validation_diagnostic(response,failure_category="invalid_goal",invalid_fields=("goal",)); return None
        if response.goal and (response.intent or response.parameters or response.requires_clarification or response.end_session):
            self._validation_diagnostic(response,failure_category="invalid_goal_shape",invalid_fields=("goal",)); return None
        resource_fields=(response.resource_type,response.target)
        if response.intent=="OPEN_RESOURCE":
            unsafe_target=any(not isinstance(item,str) or not item.strip() or len(item)>120 for item in resource_fields) or re.search(r"(?:[\\/:]|\.\.|\x00|;|&&|\||\$\(|cmd(?:\.exe)?|powershell|shell|eval|exec)",response.target,re.I)
            if response.resource_type not in {"application","game","website","folder","file"} or unsafe_target or response.parameters:
                self._validation_diagnostic(response,failure_category="invalid_resource_shape",invalid_fields=("resource_type","target")); return None
        elif any(resource_fields) and response.intent!="INFORMATION_REQUEST":
            self._validation_diagnostic(response,failure_category="invalid_resource_shape",invalid_fields=("resource_type","target")); return None
        information_fields=(response.information_category,response.information_query,response.location,response.time_context,response.source_hint)
        if response.intent=="INFORMATION_REQUEST":
            if response.parameters or response.information_category not in {"weather","web_search","site_information"}:
                self._validation_diagnostic(response,failure_category="invalid_information_shape",invalid_fields=("information_category","parameters")); return None
            information_values=(response.information_query,response.target,response.location,response.time_context,response.source_hint)
            if any(not isinstance(item,str) or len(item)>300 or re.search(r"(?:[\\/:]|\.\.|\x00|;|&&|\||\$\(|cmd(?:\.exe)?|powershell|shell|eval|exec)",item,re.I) for item in information_values):
                self._validation_diagnostic(response,failure_category="invalid_information_shape",invalid_fields=("information_query","target","location")); return None
            if response.information_category=="weather" and response.information_query:
                self._validation_diagnostic(response,failure_category="invalid_information_shape",invalid_fields=("information_query",)); return None
            if response.information_category in {"web_search","site_information"} and not response.information_query:
                self._validation_diagnostic(response,failure_category="invalid_information_shape",invalid_fields=("information_query",)); return None
            if response.information_category=="site_information" and not response.target:
                self._validation_diagnostic(response,failure_category="invalid_information_shape",invalid_fields=("target",)); return None
        elif any(information_fields):
            self._validation_diagnostic(response,failure_category="invalid_information_shape",invalid_fields=("information_category",)); return None
        if re.search(r"(?:token|secret|password|api[_-]?key|subprocess|shell|eval|exec)",response.text,re.I):
            self._validation_diagnostic(response,failure_category="forbidden_pattern",invalid_fields=("text",)); return None
        self._validation_diagnostic(response,accepted=True,failure_category="accepted")
        return response
    def respond(self, turn, context, allowed_intents=()):
        if not self.available(): return None
        try:
            set_allowed_intents=getattr(self.provider,"set_allowed_intents",None)
            if callable(set_allowed_intents): set_allowed_intents(allowed_intents)
            response=self.validate(self.provider.respond(turn,self.bounded_context(context)),allowed_intents)
            return response
        except Exception:
            self._failed=True
            return None
