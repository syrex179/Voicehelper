"""Controlled OpenAI Responses API transport behind explicit user opt-in."""
from dataclasses import asdict, dataclass
import json
import os
import re

from .conversation_provider import ProviderAdapter, ProviderCapabilities, ProviderResponse, bounded_context_snapshot

_MODEL=re.compile(r"[A-Za-z0-9._-]{1,128}$")


@dataclass(frozen=True)
class OpenAIProviderConfig:
    provider_id: str="openai"
    enabled: bool=False
    model: str=""
    request_timeout: float=30.0
    max_output_tokens: int=300

    def valid(self):
        return (self.provider_id=="openai" and isinstance(self.enabled,bool)
                and (not self.model or bool(_MODEL.fullmatch(self.model)))
                and isinstance(self.request_timeout,(int,float)) and not isinstance(self.request_timeout,bool) and 1<=self.request_timeout<=120
                and isinstance(self.max_output_tokens,int) and not isinstance(self.max_output_tokens,bool) and 1<=self.max_output_tokens<=1000)


class OpenAISecretBoundary:
    """Reads a credential only on demand; the value is never retained or serialized."""
    environment_variable="OPENAI_API_KEY"

    @classmethod
    def read(cls, environment=None):
        value=(environment if environment is not None else os.environ).get(cls.environment_variable,"")
        return value if isinstance(value,str) and value.strip() else ""


@dataclass(frozen=True)
class OpenAIRequestDTO:
    provider_id: str
    model: str
    request_timeout: float
    max_output_tokens: int
    system_instructions: str
    capabilities: dict
    turn: dict
    context: dict


class OpenAIConversationProvider(ProviderAdapter):
    """The only OpenAI network boundary; it has no execution or memory access."""
    provider_id="openai"
    network_enabled=False
    capabilities=ProviderCapabilities(True,True,True,True,True)
    _INSTRUCTIONS=("Return one JSON object only, with optional keys text, intent, parameters, "
                   "conversational, requires_clarification, end_session, response_type. "
                   "Never execute actions, call tools, expose secrets, or include explanations outside JSON.")

    def __init__(self, config=None, secret_reader=None, client_factory=None):
        self.config=config or OpenAIProviderConfig()
        self._secret_reader=secret_reader or OpenAISecretBoundary.read
        self._client_factory=client_factory or self._default_client

    def configured(self):
        """A future caller may opt in only with valid non-secret config and a secret boundary."""
        try: return bool(self.config.valid() and self.config.enabled and self._secret_reader())
        except Exception: return False

    def build_request(self, turn, context):
        bounded_turn=self.bounded_turn(turn) if callable(getattr(turn,"to_dict",None)) else dict(turn or {})
        return OpenAIRequestDTO(self.provider_id,self.config.model,float(self.config.request_timeout),self.config.max_output_tokens,self._INSTRUCTIONS,asdict(self.capabilities),bounded_turn,bounded_context_snapshot(context))

    @staticmethod
    def _default_client(api_key, timeout):
        from openai import OpenAI
        return OpenAI(api_key=api_key,timeout=timeout,max_retries=0)

    def activate_transport(self):
        """Enable future requests only after explicit opt-in validation; no request is made here."""
        if not self.configured() or not self.config.model: return False
        self.network_enabled=True; return True

    def deactivate_transport(self): self.network_enabled=False

    @staticmethod
    def map_response(payload):
        """Map a future already-decoded Responses payload; validation remains in the registry."""
        if isinstance(payload,ProviderResponse): return payload
        if not isinstance(payload,dict): return None
        allowed={"text","intent","parameters","conversational","confidence","requires_clarification","end_session","response_type"}
        if any(key not in allowed for key in payload): return None
        try: return ProviderResponse(**payload)
        except TypeError: return None

    def respond_bounded(self, turn, context):
        if not self.network_enabled: return None
        api_key=self._secret_reader()
        if not api_key: raise RuntimeError("OpenAI provider is unavailable.")
        request=self.build_request(turn,context); client=self._client_factory(api_key,request.request_timeout)
        try:
            response=client.responses.create(model=request.model,instructions=request.system_instructions,
                input=json.dumps({"turn":request.turn,"context":request.context,"capabilities":request.capabilities},ensure_ascii=False),
                max_output_tokens=request.max_output_tokens)
            text=getattr(response,"output_text",None)
            if text is None and isinstance(response,dict): text=response.get("output_text")
            try: mapped=self.map_response(json.loads(text))
            except (TypeError,ValueError): mapped=None
            if mapped is None: raise RuntimeError("OpenAI provider returned an invalid response.")
            return mapped
        finally:
            close=getattr(client,"close",None)
            if callable(close): close()
