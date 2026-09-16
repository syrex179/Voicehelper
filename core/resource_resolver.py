"""Bounded resource discovery for provider-described targets.

This module never launches anything and never accepts an executable path from a
provider.  It produces only allow-listed Router commands discovered from local
trusted adapters.
"""
from dataclasses import dataclass
import re

_TYPES={"application","game","website","folder","file"}
_UNSAFE=re.compile(r"(?:[\\/:]|\.\.|\x00|;|&&|\||\$\(|cmd(?:\.exe)?|powershell|shell|eval|exec)",re.I)
# Product-family terms are a bounded local vocabulary used only for ranking
# trusted display names. They are never provider output or launch data.
_PRODUCT_FAMILIES=(frozenset({"fifa","фифа","ea sports fc","football","soccer"}),)
_KNOWN_WEBSITE_TARGETS={"YouTube":("youtube","ютуб")}

def _normalize(value):
    return re.sub(r"\s+"," ",re.sub(r"[™®©]","",str(value)).casefold()).strip()

@dataclass(frozen=True)
class ResourceTarget:
    resource_type: str
    target: str
    hint: str=""
    normalized_name: str=""
    def __post_init__(self):
        kind=str(self.resource_type).strip().lower(); value=str(self.target).strip()
        if kind not in _TYPES or not value or len(value)>120 or _UNSAFE.search(value): raise ValueError("Unsafe resource target.")
        object.__setattr__(self,"resource_type",kind); object.__setattr__(self,"target",value)
        object.__setattr__(self,"normalized_name",self.normalized_name or _normalize(value))

@dataclass(frozen=True)
class ResourceCandidate:
    name: str
    resource_type: str
    source: str
    command: dict
    score: int=0

class ResourceResolver:
    """Ranks trusted adapter candidates; it has no execution capability."""
    def __init__(self, discoverers=None): self.discoverers=dict(discoverers or {})
    @staticmethod
    def known_resource_names():
        return [(name, aliases) for name, aliases in _KNOWN_WEBSITE_TARGETS.items()]
    @staticmethod
    def _safe_command(command):
        if not isinstance(command,dict) or set(command)!={"intent","parameters"}: return None
        intent=command.get("intent"); parameters=command.get("parameters")
        allowed={"OPEN_APPLICATION","OPEN_YOUTUBE","OPEN_FOLDER","SEARCH_FILE"}
        if intent not in allowed or not isinstance(parameters,dict): return None
        if intent=="OPEN_YOUTUBE" and parameters: return None
        if intent in {"OPEN_APPLICATION","OPEN_FOLDER","SEARCH_FILE"}:
            key={"OPEN_APPLICATION":"application","OPEN_FOLDER":"query","SEARCH_FILE":"query"}[intent]
            value=parameters.get(key)
            if set(parameters)!={key} or not isinstance(value,str) or not value.strip() or _UNSAFE.search(value): return None
        return {"intent":intent,"parameters":dict(parameters)}
    @staticmethod
    def _tokens(value):
        return set(re.findall(r"[a-zа-яё]+|\d+",str(value).casefold()))
    @classmethod
    def _family_terms(cls, value):
        lowered=re.sub(r"\s+"," ",str(value).casefold()).strip(); terms=set()
        for family in _PRODUCT_FAMILIES:
            if any(term in lowered or term in cls._tokens(lowered) for term in family): terms.update(family)
        return terms
    @classmethod
    def _score(cls, target, candidate):
        name=_normalize(candidate.get("name","")); query=target.normalized_name
        if name==query: return 100
        aliases=[_normalize(item) for item in candidate.get("aliases",[]) if isinstance(item,str)]
        if query in aliases: return 95
        if name.startswith(query) or query.startswith(name): return 80
        query_tokens=cls._tokens(query); candidate_text=" ".join([name,*aliases]); candidate_tokens=cls._tokens(candidate_text)
        if query_tokens and query_tokens <= candidate_tokens: return 85
        if query in name: return 70
        if query_tokens & candidate_tokens: return 60
        if cls._family_terms(query) & cls._family_terms(candidate_text): return 70
        return 0
    def resolve(self,target):
        if not isinstance(target,ResourceTarget): return {"status":"invalid","candidates":[]}
        if target.resource_type=="website" and target.normalized_name in _KNOWN_WEBSITE_TARGETS["YouTube"]:
            candidate=ResourceCandidate("YouTube","website","known_browser_target",{"intent":"OPEN_YOUTUBE","parameters":{}},100)
            return {"status":"one","confidence":"HIGH","candidates":[candidate]}
        discover=self.discoverers.get(target.resource_type)
        if not callable(discover): return {"status":"none","confidence":"LOW","candidates":[]}
        raw=discover(target) or []; candidates=[]
        for item in raw:
            if not isinstance(item,dict): continue
            command=self._safe_command(item.get("command")); score=self._score(target,item)
            name=item.get("name"); source=item.get("source","")
            if command and isinstance(name,str) and name.strip() and isinstance(source,str) and score:
                candidates.append(ResourceCandidate(name.strip(),target.resource_type,source,command,score))
        candidates.sort(key=lambda item:(-item.score,item.name.casefold(),item.source))
        if not candidates: return {"status":"none","confidence":"LOW","candidates":[]}
        top=candidates[0].score; family_query=bool(self._family_terms(target.normalized_name))
        # Keep close family matches for existing numbered selection rather
        # than silently choosing one installed edition.
        best=[item for item in candidates if item.score==top or family_query and item.score>=max(70,top-15)]
        return {"status":"one" if len(best)==1 else "multiple","confidence":"HIGH" if top>=95 else "MEDIUM" if top>=80 else "LOW","candidates":best}
