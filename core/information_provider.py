"""Bounded external-information boundary; it never executes commands."""
from dataclasses import dataclass, field
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from .models import Result

_CATEGORIES={"weather","web_search","site_information"}
_UNSAFE=re.compile(r"(?:[\\/:]|\.\.|\x00|;|&&|\||\$\(|cmd(?:\.exe)?|powershell|shell|eval|exec)",re.I)
_UNSAFE_SUMMARY=re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

@dataclass(frozen=True)
class InformationRequest:
    category: str
    query: str=""
    target: str=""
    location: str=""
    time_context: str=""
    language: str="ru"
    source_hint: str=""
    operation: str="GET_INFORMATION"
    def __post_init__(self):
        category=str(self.category).strip().lower()
        values={key:str(getattr(self,key) or "").strip() for key in ("query","target","location","time_context","language","source_hint")}
        if str(self.operation)!="GET_INFORMATION" or (category and category not in _CATEGORIES) or any(len(value)>300 or _UNSAFE.search(value) for value in values.values()): raise ValueError("Unsafe information request.")
        if category=="weather" and not values["location"]: raise ValueError("Weather location required.")
        if category in {"web_search","site_information"} and not values["query"]: raise ValueError("Information query required.")
        if category=="site_information" and not values["target"]: raise ValueError("Site target required.")
        object.__setattr__(self,"category",category); object.__setattr__(self,"operation","GET_INFORMATION")
        for key,value in values.items(): object.__setattr__(self,key,value)
    def to_dict(self):
        return {key:getattr(self,key) for key in ("operation","category","query","target","location","time_context","language","source_hint") if getattr(self,key)}

@dataclass(frozen=True)
class InformationResult:
    ok: bool
    category: str=""
    data: dict=field(default_factory=dict)
    error: str=""
    source: str=""
    title: str=""
    summary: str=""
    facts: dict=field(default_factory=dict)
    timestamp: str=""
    confidence: str=""

class InformationProvider:
    def supports(self, request): return 0
    def query(self, request): raise NotImplementedError


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl): return None


class WikipediaInformationSource(InformationProvider):
    """Bounded read-only adapter for the fixed official Russian MediaWiki API."""
    endpoint="https://ru.wikipedia.org/w/api.php"
    source_name="wikipedia"
    max_response_bytes=256*1024
    max_summary_length=500

    def __init__(self, transport=None, timeout=6.0):
        self.timeout=float(timeout); self._transport=transport or self._http_get
        self.last_diagnostic={"network_status":"NOT_CALLED","http_status":None,"reason":"not_called"}
        self.last_result_diagnostic={"status":"NOT_CALLED","title":"","summary_length":0,"confidence":""}
        self.last_response_diagnostic={"content_type":"NOT_AVAILABLE","response_size":0,"top_level_keys":(),"query_keys":(),"search_result_present":False,"search_result_count":0,"page_result_present":False,"page_count":0,"extract_present":False,"extract_length":0,"parser_outcome":"NOT_CALLED","empty_reason":"","topic_length":0,"topic_preview":""}
        self._last_content_type="NOT_AVAILABLE"

    @staticmethod
    def _topic(request):
        return " ".join(str(getattr(request,"target","") or getattr(request,"query","") or "").split())

    def supports(self, request):
        if not isinstance(request,InformationRequest) or request.operation!="GET_INFORMATION" or request.category not in {"","web_search"}: return 0
        topic=self._topic(request)
        return 80 if topic and len(topic)<=300 and not _UNSAFE.search(topic) else 0

    def _http_get(self, params, timeout, limit):
        query=urlencode(params,encoding="utf-8",safe="")
        request=Request(self.endpoint+"?"+query,headers={"Accept":"application/json","User-Agent":"JarvisInformationSource/1.0"},method="GET")
        try:
            with build_opener(_NoRedirect()).open(request,timeout=timeout) as response:
                status=getattr(response,"status",response.getcode())
                self._last_content_type=str(response.headers.get("Content-Type","") or "NOT_AVAILABLE").split(";",1)[0].strip().lower() or "NOT_AVAILABLE"
                if status!=200: return status,b""
                length=response.headers.get("Content-Length","")
                if length.isdigit() and int(length)>limit: return status,None
                body=response.read(limit+1)
                return status,body if len(body)<=limit else None
        except HTTPError as exc: return int(exc.code),b""
        except (URLError,TimeoutError,OSError): return None,b""

    @staticmethod
    def _safe_preview(value, limit=40):
        value=" ".join(str(value or "").split())
        return value[:limit]

    def _record_structure(self, payload, stage, body_size):
        query=payload.get("query") if isinstance(payload.get("query"),dict) else {}
        search=query.get("search") if isinstance(query.get("search"),list) else []
        pages=query.get("pages") if isinstance(query.get("pages"),list) else []
        page=pages[0] if pages and isinstance(pages[0],dict) else {}
        extract=page.get("extract") if isinstance(page.get("extract"),str) else ""
        diagnostic=self.last_response_diagnostic
        diagnostic.update({"content_type":self._last_content_type,"response_size":body_size,"top_level_keys":tuple(sorted(str(key) for key in payload)),"query_keys":tuple(sorted(str(key) for key in query)),"parser_outcome":stage})
        if stage=="search":
            diagnostic.update({"search_result_present":"search" in query,"search_result_count":len(search),"page_result_present":False,"page_count":0,"extract_present":False,"extract_length":0})
        else:
            diagnostic.update({"page_result_present":"pages" in query,"page_count":len(pages),"extract_present":bool(extract),"extract_length":len(extract)})

    def _request(self, params, stage):
        try: status,body=self._transport(dict(params),self.timeout,self.max_response_bytes)
        except (TimeoutError,URLError,OSError):
            self.last_diagnostic={"network_status":"UNAVAILABLE","http_status":None,"reason":"network_error"}; return None
        if status!=200:
            self.last_diagnostic={"network_status":"UNAVAILABLE","http_status":status,"reason":"http_error"}; return None
        if not isinstance(body,(bytes,bytearray)) or len(body)>self.max_response_bytes:
            self.last_diagnostic={"network_status":"UNAVAILABLE","http_status":status,"reason":"response_too_large"}; return None
        try: payload=json.loads(bytes(body).decode("utf-8"))
        except (UnicodeDecodeError,TypeError,ValueError):
            self.last_diagnostic={"network_status":"UNAVAILABLE","http_status":status,"reason":"invalid_json"}; return None
        if not isinstance(payload,dict):
            self.last_diagnostic={"network_status":"UNAVAILABLE","http_status":status,"reason":"invalid_json"}; return None
        self._record_structure(payload,stage,len(body))
        self.last_diagnostic={"network_status":"PASS","http_status":status,"reason":"ok"}; return payload

    @staticmethod
    def _summary(value):
        if not isinstance(value,str): return ""
        value=" ".join(value.split()).strip()
        # This is trusted, read-only encyclopedic prose.  Input routing safety
        # rules deliberately reject URL/path punctuation, but those characters
        # are ordinary prose here.  Normalize them before handing the summary
        # to the existing generic InformationService safety boundary.
        value=re.sub(r"[:\\/;|]",",",value).replace("&&"," и ").replace("$(","").replace("..",".")
        value=" ".join(value.split()).strip()
        if not value or _UNSAFE_SUMMARY.search(value) or _UNSAFE.search(value): return ""
        return value[:WikipediaInformationSource.max_summary_length].rstrip()

    def query(self, request):
        if self.supports(request)<=0:
            self.last_result_diagnostic={"status":"REJECTED","title":"","summary_length":0,"confidence":""}
            return InformationResult(False,request.category,error="Запрос не подходит для энциклопедического источника.",source=self.source_name)
        topic=self._topic(request)
        self.last_response_diagnostic.update({"topic_length":len(topic),"topic_preview":self._safe_preview(topic),"empty_reason":""})
        search=self._request({"action":"query","format":"json","formatversion":"2","list":"search","srlimit":"1","srsearch":topic},"search")
        if search is None:
            self.last_result_diagnostic={"status":"UNAVAILABLE","title":"","summary_length":0,"confidence":""}; return InformationResult(False,request.category,error="Энциклопедический источник сейчас недоступен.",source=self.source_name)
        entries=search.get("query",{}).get("search",[]) if isinstance(search.get("query"),dict) else []
        first=entries[0] if isinstance(entries,list) and entries and isinstance(entries[0],dict) else {}
        title=first.get("title","") if isinstance(first.get("title"),str) else ""
        if not title or len(title)>300 or _UNSAFE.search(title):
            self.last_response_diagnostic["empty_reason"]="search_empty_or_unsafe_title"
            self.last_result_diagnostic={"status":"NOT_FOUND","title":"","summary_length":0,"confidence":""}
            return InformationResult(False,request.category,error="По этому запросу ничего не найдено.",source=self.source_name)
        extract=self._request({"action":"query","format":"json","formatversion":"2","prop":"extracts","exintro":"1","explaintext":"1","redirects":"1","titles":title},"extract")
        if extract is None:
            self.last_result_diagnostic={"status":"UNAVAILABLE","title":"","summary_length":0,"confidence":""}; return InformationResult(False,request.category,error="Энциклопедический источник сейчас недоступен.",source=self.source_name)
        pages=extract.get("query",{}).get("pages",[]) if isinstance(extract.get("query"),dict) else []
        page=pages[0] if isinstance(pages,list) and pages and isinstance(pages[0],dict) else {}
        resolved_title=page.get("title",title) if isinstance(page.get("title",title),str) else title
        summary=self._summary(page.get("extract","") if isinstance(page,dict) else "")
        if not summary:
            self.last_response_diagnostic["empty_reason"]="missing_or_unsafe_extract"
            self.last_result_diagnostic={"status":"EMPTY","title":"","summary_length":0,"confidence":""}; return InformationResult(False,request.category,error="Энциклопедический источник не вернул краткого описания.",source=self.source_name)
        self.last_response_diagnostic["empty_reason"]=""
        self.last_result_diagnostic={"status":"PASS","title":resolved_title,"summary_length":len(summary),"confidence":"high"}
        return InformationResult(True,request.category,source=self.source_name,title=resolved_title,summary=summary,facts={"kind":"encyclopedic_summary"},confidence="high")


class WikidataInformationSource(InformationProvider):
    """Bounded read-only adapter for selected structured facts from Wikidata."""
    endpoint="https://www.wikidata.org/w/api.php"
    source_name="wikidata"
    max_response_bytes=256*1024
    max_summary_length=500
    max_entity_candidates=5
    _FACT_SPECS=(
        ("Столица","P36",re.compile(r"\bстолиц[аы]?\s+(?P<entity>.+)$",re.I)),
        ("Население","P1082",re.compile(r"\bнаселени[ея]\s+(?P<entity>.+)$",re.I)),
        ("Дата основания","P571",re.compile(r"\bдат[аы]\s+основани[яя]\s+(?P<entity>.+)$",re.I)),
        ("Страна","P17",re.compile(r"\bстран[аы]\s+(?P<entity>.+)$",re.I)),
        ("Координаты","P625",re.compile(r"\bкоординат[аы]\s+(?P<entity>.+)$",re.I)),
    )

    def __init__(self, transport=None, timeout=6.0):
        self.timeout=float(timeout); self._transport=transport or self._http_get
        self.last_diagnostic={"network_status":"NOT_CALLED","http_status":None,"reason":"not_called"}
        self.last_result_diagnostic={"status":"NOT_CALLED","title":"","summary_length":0,"facts_count":0,"confidence":""}
        self.last_response_diagnostic={"content_type":"NOT_AVAILABLE","response_size":0,"entity_result_count":0,"candidate_count":0,"candidate_diagnostics":(),"claims_request_mode":"NOT_CALLED","candidate_batch_size":0,"property_requested":"","search_query_length":0,"search_query_preview":"","candidate_with_property_count":0,"selected_candidate_found":False,"entity_present":False,"claim_present":False,"value_label_present":False,"parser_outcome":"NOT_CALLED","empty_reason":"","topic_length":0,"topic_preview":""}
        self._last_content_type="NOT_AVAILABLE"

    @staticmethod
    def _topic(request):
        return " ".join(str(getattr(request,"target","") or getattr(request,"query","") or "").split())

    @classmethod
    def _fact_spec(cls, topic):
        normalized=" ".join(str(topic or "").split()).strip(" .,!?")
        for label,property_id,pattern in cls._FACT_SPECS:
            match=pattern.search(normalized)
            entity=" ".join(match.group("entity").strip(" .,!?").split()) if match else ""
            if entity and len(entity)<=160 and not _UNSAFE.search(entity): return label,property_id,entity
        return None

    @classmethod
    def _property_spec(cls, topic):
        normalized=" ".join(str(topic or "").split()).strip(" .,!?")
        for label,property_id,pattern in cls._FACT_SPECS:
            if pattern.search(normalized) or re.search(r"\b"+re.escape(property_id)+r"\b",normalized,re.I): return label,property_id
            stem=pattern.pattern.split(r"\s+",1)[0].replace(r"\b","")
            if stem and re.search(stem,normalized,re.I): return label,property_id
        return None

    @staticmethod
    def _safe_entity_phrase(value):
        value=" ".join(str(value or "").split()).strip(" .,!?")
        return value if value and len(value)<=160 and not _UNSAFE.search(value) else ""

    @classmethod
    def _entity_search_phrase(cls, value):
        """Return one bounded entity-oriented search phrase, never a raw request."""
        value=cls._safe_entity_phrase(value)
        if not value: return ""
        words=value.split()
        # A narrow Russian genitive-to-nominative normalization for a single
        # entity word (e.g. "Франции" -> "Франция"), not a country list.
        if len(words)==1 and len(words[0])>4 and words[0].casefold().endswith("ии"):
            return words[0][:-2]+("ИЯ" if words[0].isupper() else "ия")
        return value

    @classmethod
    def _request_fact_spec(cls, request):
        query=cls._safe_entity_phrase(getattr(request,"query","") or "")
        target=cls._safe_entity_phrase(getattr(request,"target","") or "")
        spec=cls._fact_spec(query)
        if spec:
            label,property_id,extracted=spec
            entity=target or extracted
            return label,property_id,cls._entity_search_phrase(entity)
        property_spec=cls._property_spec(query) if target else None
        if property_spec:
            label,property_id=property_spec
            return label,property_id,cls._entity_search_phrase(target)
        return None

    def supports(self, request):
        if not isinstance(request,InformationRequest) or request.operation!="GET_INFORMATION" or request.category not in {"","web_search"}: return 0
        topic=self._topic(request)
        return 100 if topic and len(topic)<=300 and self._request_fact_spec(request) else 0

    def _http_get(self, params, timeout, limit):
        query=urlencode(params,encoding="utf-8",safe="")
        request=Request(self.endpoint+"?"+query,headers={"Accept":"application/json","User-Agent":"JarvisInformationSource/1.0"},method="GET")
        try:
            with build_opener(_NoRedirect()).open(request,timeout=timeout) as response:
                status=getattr(response,"status",response.getcode())
                self._last_content_type=str(response.headers.get("Content-Type","") or "NOT_AVAILABLE").split(";",1)[0].strip().lower() or "NOT_AVAILABLE"
                if status!=200: return status,b""
                length=response.headers.get("Content-Length","")
                if length.isdigit() and int(length)>limit: return status,None
                body=response.read(limit+1)
                return status,body if len(body)<=limit else None
        except HTTPError as exc: return int(exc.code),b""
        except (URLError,TimeoutError,OSError): return None,b""

    def _request(self, params, stage):
        try: status,body=self._transport(dict(params),self.timeout,self.max_response_bytes)
        except (TimeoutError,URLError,OSError):
            self.last_diagnostic={"network_status":"UNAVAILABLE","http_status":None,"reason":"network_error"}; return None
        if status!=200:
            self.last_diagnostic={"network_status":"UNAVAILABLE","http_status":status,"reason":"http_error"}; return None
        if not isinstance(body,(bytes,bytearray)) or len(body)>self.max_response_bytes:
            self.last_diagnostic={"network_status":"UNAVAILABLE","http_status":status,"reason":"response_too_large"}; return None
        try: payload=json.loads(bytes(body).decode("utf-8"))
        except (UnicodeDecodeError,TypeError,ValueError):
            self.last_diagnostic={"network_status":"UNAVAILABLE","http_status":status,"reason":"invalid_json"}; return None
        if not isinstance(payload,dict):
            self.last_diagnostic={"network_status":"UNAVAILABLE","http_status":status,"reason":"invalid_json"}; return None
        self.last_response_diagnostic.update({"content_type":self._last_content_type,"response_size":len(body),"parser_outcome":stage})
        self.last_diagnostic={"network_status":"PASS","http_status":status,"reason":"ok"}; return payload

    @staticmethod
    def _label(entity):
        labels=entity.get("labels") if isinstance(entity,dict) else {}
        russian=labels.get("ru") if isinstance(labels,dict) else {}
        value=russian.get("value") if isinstance(russian,dict) else ""
        return WikipediaInformationSource._summary(value)

    @staticmethod
    def _entity(payload, entity_id):
        entities=payload.get("entities") if isinstance(payload,dict) else {}
        value=entities.get(entity_id) if isinstance(entities,dict) else None
        return value if isinstance(value,dict) else {}

    @staticmethod
    def _claim_entity_id(entity, property_id):
        claims=entity.get("claims") if isinstance(entity,dict) else {}
        candidates=claims.get(property_id) if isinstance(claims,dict) else []
        claim=candidates[0] if isinstance(candidates,list) and candidates and isinstance(candidates[0],dict) else {}
        snak=claim.get("mainsnak") if isinstance(claim.get("mainsnak"),dict) else {}
        data=snak.get("datavalue") if isinstance(snak.get("datavalue"),dict) else {}
        value=data.get("value") if isinstance(data.get("value"),dict) else {}
        entity_id=value.get("id") if isinstance(value.get("id"),str) else ""
        return entity_id if re.fullmatch(r"Q\d+",entity_id) else ""

    @classmethod
    def _claim_id_from_property_payload(cls, payload, property_id):
        claims=payload.get("claims") if isinstance(payload,dict) else {}
        return cls._claim_entity_id({"claims":claims},property_id)

    @staticmethod
    def _candidate_text(value):
        return " ".join(re.findall(r"[\w-]+",str(value or "").casefold()))

    @classmethod
    def _candidates(cls, entries):
        """Keep a small, validated search-ordered candidate set only."""
        candidates=[]
        if not isinstance(entries,list): return candidates
        for rank,entry in enumerate(entries[:cls.max_entity_candidates]):
            if not isinstance(entry,dict): continue
            entity_id=entry.get("id") if isinstance(entry.get("id"),str) else ""
            if not re.fullmatch(r"Q\d+",entity_id): continue
            candidates.append({"id":entity_id,"label":entry.get("label","") if isinstance(entry.get("label"),str) else "","description":entry.get("description","") if isinstance(entry.get("description"),str) else "","rank":rank})
        return candidates

    @classmethod
    def _candidate_score(cls, candidate, entity_query, has_property):
        """Property dominates; the search order and compact text overlap break ties."""
        query_tokens=set(cls._candidate_text(entity_query).split())
        candidate_tokens=set(cls._candidate_text(candidate.get("label","")+" "+candidate.get("description","")).split())
        overlap=len(query_tokens & candidate_tokens)
        rank=int(candidate.get("rank",cls.max_entity_candidates))
        return (1000 if has_property else 0)+(overlap*10)+max(0,cls.max_entity_candidates-rank)

    def _failure(self, request, status, error, reason):
        self.last_response_diagnostic["empty_reason"]=reason
        self.last_result_diagnostic={"status":status,"title":"","summary_length":0,"facts_count":0,"confidence":""}
        return InformationResult(False,request.category,error=error,source=self.source_name)

    def query(self, request):
        if self.supports(request)<=0:
            return self._failure(request,"REJECTED","Запрос не подходит для структурированного источника.","unsupported_request")
        topic=self._topic(request); spec=self._request_fact_spec(request)
        property_label,property_id,entity_query=spec
        self.last_response_diagnostic.update({"topic_length":len(topic),"topic_preview":WikipediaInformationSource._safe_preview(topic),"empty_reason":"","entity_result_count":0,"candidate_count":0,"candidate_diagnostics":(),"claims_request_mode":"property_scoped","candidate_batch_size":0,"property_requested":property_id,"search_query_length":len(entity_query),"search_query_preview":WikipediaInformationSource._safe_preview(entity_query,80),"candidate_with_property_count":0,"selected_candidate_found":False,"entity_present":False,"claim_present":False,"value_label_present":False})
        search=self._request({"action":"wbsearchentities","format":"json","language":"ru","uselang":"ru","limit":str(self.max_entity_candidates),"search":entity_query},"entity_search")
        if search is None: return self._failure(request,"UNAVAILABLE","Структурированный источник сейчас недоступен.","entity_search_unavailable")
        entries=search.get("search") if isinstance(search.get("search"),list) else []
        candidates=self._candidates(entries)
        self.last_response_diagnostic.update({"entity_result_count":len(entries),"candidate_count":len(candidates),"candidate_batch_size":len(candidates),"entity_present":bool(candidates)})
        if not candidates: return self._failure(request,"NOT_FOUND","Структурированный факт по этому запросу не найден.","entity_not_found")
        scored=[]
        for candidate in candidates:
            claim_payload=self._request({"action":"wbgetclaims","format":"json","entity":candidate["id"],"property":property_id},"candidate_property_claim")
            if claim_payload is None: return self._failure(request,"UNAVAILABLE","Структурированный источник сейчас недоступен.","candidate_property_claim_unavailable")
            value_id=self._claim_id_from_property_payload(claim_payload,property_id)
            scored.append((self._candidate_score(candidate,entity_query,bool(value_id)),-candidate["rank"],candidate,value_id))
        compatible=[item for item in scored if item[3]]
        self.last_response_diagnostic.update({"candidate_with_property_count":len(compatible),"candidate_diagnostics":tuple({"rank":item[2]["rank"],"label_length":len(item[2]["label"]),"description_length":len(item[2]["description"]),"has_required_property":bool(item[3])} for item in scored)})
        if not compatible: return self._failure(request,"NOT_FOUND","Структурированный факт по этому запросу не найден.","claim_not_found")
        _,_,selected,value_id=max(compatible,key=lambda item:(item[0],item[1]))
        entity_id=selected["id"]
        entity_payload=self._request({"action":"wbgetentities","format":"json","ids":entity_id,"props":"labels","languages":"ru"},"selected_entity_label")
        if entity_payload is None: return self._failure(request,"UNAVAILABLE","Структурированный источник сейчас недоступен.","selected_entity_label_unavailable")
        entity=self._entity(entity_payload,entity_id)
        title=self._label(entity) or WikipediaInformationSource._summary(selected["label"])
        self.last_response_diagnostic.update({"selected_candidate_found":True,"claim_present":True})
        if not title: return self._failure(request,"NOT_FOUND","Структурированный факт по этому запросу не найден.","candidate_title_missing")
        value_payload=self._request({"action":"wbgetentities","format":"json","ids":value_id,"props":"labels","languages":"ru"},"value_label")
        if value_payload is None: return self._failure(request,"UNAVAILABLE","Структурированный источник сейчас недоступен.","value_label_unavailable")
        value_label=self._label(self._entity(value_payload,value_id)); self.last_response_diagnostic["value_label_present"]=bool(value_label)
        if not value_label: return self._failure(request,"EMPTY","Структурированный источник не вернул значение факта.","value_label_missing")
        summary=WikipediaInformationSource._summary(f"{property_label} — {value_label}.")
        if not summary: return self._failure(request,"EMPTY","Структурированный источник не вернул безопасного описания.","unsafe_summary")
        facts={property_label:value_label}; self.last_response_diagnostic["empty_reason"]=""
        self.last_result_diagnostic={"status":"PASS","title":title,"summary_length":len(summary),"facts_count":len(facts),"confidence":"high"}
        return InformationResult(True,request.category,source=self.source_name,title=title,summary=summary,facts=facts,confidence="high")


class BraveWebSearchInformationSource(InformationProvider):
    """Bounded, read-only Brave Web Search adapter for explicit fresh/web requests."""
    endpoint="https://api.search.brave.com/res/v1/web/search"
    source_name="web_search"
    max_response_bytes=256*1024
    max_results=3
    max_summary_length=500
    _WEB_HINTS={"web","search","web_search","current"}

    def __init__(self, api_key=None, transport=None, timeout=6.0):
        candidate=os.environ.get("BRAVE_SEARCH_API_KEY","") if api_key is None else api_key
        self._api_key=str(candidate or "").strip()
        self.timeout=float(timeout); self._transport=transport or self._http_get
        self.last_diagnostic={"network_status":"NOT_CALLED","http_status":None,"reason":"not_called"}
        self.last_result_diagnostic={"status":"NOT_CALLED","title":"","summary_length":0,"facts_count":0,"confidence":""}
        self.last_response_diagnostic={"content_type":"NOT_AVAILABLE","response_size":0,"results_count":0,"query_length":0,"query_preview":"","parser_outcome":"NOT_CALLED","empty_reason":""}
        self._last_content_type="NOT_AVAILABLE"

    def status(self):
        configured=bool(self._api_key) and len(self._api_key)<=300 and not _UNSAFE_SUMMARY.search(self._api_key)
        return {"state":"CONFIGURED" if configured else "UNCONFIGURED","api_key_present":configured}

    @staticmethod
    def _topic(request):
        return " ".join(str(getattr(request,"target","") or getattr(request,"query","") or "").split())

    def _safe_query(self, request):
        topic=self._topic(request)
        return topic if topic and len(topic)<=300 and not _UNSAFE.search(topic) else ""

    def _web_oriented(self, request):
        hint=str(getattr(request,"source_hint","") or "").casefold()
        return hint in self._WEB_HINTS or bool(str(getattr(request,"time_context","") or "").strip())

    def supports(self, request):
        if not isinstance(request,InformationRequest) or request.operation!="GET_INFORMATION" or request.category!="web_search": return 0
        if self.status()["state"]!="CONFIGURED" or not self._safe_query(request): return 0
        if WikidataInformationSource().supports(request)>0: return 0
        return 120 if self._web_oriented(request) else 0

    def _http_get(self, params, timeout, limit):
        query=urlencode(params,encoding="utf-8",safe="")
        request=Request(self.endpoint+"?"+query,headers={"Accept":"application/json","User-Agent":"JarvisInformationSource/1.0","X-Subscription-Token":self._api_key},method="GET")
        try:
            with build_opener(_NoRedirect()).open(request,timeout=timeout) as response:
                status=getattr(response,"status",response.getcode())
                self._last_content_type=str(response.headers.get("Content-Type","") or "NOT_AVAILABLE").split(";",1)[0].strip().lower() or "NOT_AVAILABLE"
                if status!=200: return status,b""
                length=response.headers.get("Content-Length","")
                if length.isdigit() and int(length)>limit: return status,None
                body=response.read(limit+1)
                return status,body if len(body)<=limit else None
        except HTTPError as exc: return int(exc.code),b""
        except (URLError,TimeoutError,OSError): return None,b""

    def _request(self, params):
        try: status,body=self._transport(dict(params),self.timeout,self.max_response_bytes)
        except (TimeoutError,URLError,OSError):
            self.last_diagnostic={"network_status":"UNAVAILABLE","http_status":None,"reason":"network_error"}; return None
        if status!=200:
            reason="authentication_error" if status in {401,403} else "rate_limited" if status==429 else "http_error"
            self.last_diagnostic={"network_status":"UNAVAILABLE","http_status":status,"reason":reason}; return None
        if not isinstance(body,(bytes,bytearray)) or len(body)>self.max_response_bytes:
            self.last_diagnostic={"network_status":"UNAVAILABLE","http_status":status,"reason":"response_too_large"}; return None
        try: payload=json.loads(bytes(body).decode("utf-8"))
        except (UnicodeDecodeError,TypeError,ValueError):
            self.last_diagnostic={"network_status":"UNAVAILABLE","http_status":status,"reason":"invalid_json"}; return None
        if not isinstance(payload,dict):
            self.last_diagnostic={"network_status":"UNAVAILABLE","http_status":status,"reason":"invalid_json"}; return None
        self.last_response_diagnostic.update({"content_type":self._last_content_type,"response_size":len(body),"parser_outcome":"response"})
        self.last_diagnostic={"network_status":"PASS","http_status":status,"reason":"ok"}; return payload

    def _failure(self, request, status, error, reason):
        self.last_response_diagnostic["empty_reason"]=reason
        self.last_result_diagnostic={"status":status,"title":"","summary_length":0,"facts_count":0,"confidence":""}
        return InformationResult(False,request.category,error=error,source=self.source_name)

    def query(self, request):
        if self.status()["state"]!="CONFIGURED": return self._failure(request,"UNCONFIGURED","Поисковый источник не настроен.","unconfigured")
        if self.supports(request)<=0: return self._failure(request,"REJECTED","Запрос не подходит для веб-поиска.","unsupported_request")
        topic=self._safe_query(request)
        self.last_response_diagnostic.update({"results_count":0,"query_length":len(topic),"query_preview":WikipediaInformationSource._safe_preview(topic,80),"empty_reason":""})
        payload=self._request({"q":topic,"count":str(self.max_results),"search_lang":"ru","safesearch":"moderate"})
        if payload is None: return self._failure(request,"UNAVAILABLE","Поисковый источник сейчас недоступен.","search_unavailable")
        web=payload.get("web") if isinstance(payload.get("web"),dict) else {}
        entries=web.get("results") if isinstance(web.get("results"),list) else []
        normalized=[]
        for entry in entries[:self.max_results]:
            if not isinstance(entry,dict): continue
            title=WikipediaInformationSource._summary(entry.get("title","") if isinstance(entry.get("title"),str) else "")
            snippet=WikipediaInformationSource._summary(entry.get("description","") if isinstance(entry.get("description"),str) else "")
            if title and snippet: normalized.append((title,snippet))
        self.last_response_diagnostic["results_count"]=len(normalized)
        if not normalized: return self._failure(request,"NOT_FOUND","Поиск не вернул безопасных результатов.","results_missing")
        title=normalized[0][0]; summary=" ".join(f"{item_title} — {snippet}." for item_title,snippet in normalized)
        summary=WikipediaInformationSource._summary(summary)[:self.max_summary_length].rstrip()
        if not summary: return self._failure(request,"EMPTY","Поиск не вернул безопасного описания.","unsafe_summary")
        facts={"result_count":len(normalized)}
        self.last_result_diagnostic={"status":"PASS","title":title,"summary_length":len(summary),"facts_count":len(facts),"confidence":"medium"}
        return InformationResult(True,request.category,source=self.source_name,title=title,summary=summary,facts=facts,confidence="medium")

class InformationSourceResolver:
    """Ranks configured adapters only; it has no transport or execution code."""
    def __init__(self, providers=None): self.providers=list(providers or []); self.last_selected=None; self.last_score=0
    def resolve(self, request):
        self.last_selected=None; self.last_score=0
        ranked=[]
        for provider in self.providers:
            try: score=provider.supports(request)
            except Exception: continue
            if isinstance(score,(int,float)) and not isinstance(score,bool) and score>0: ranked.append((float(score),provider))
        if not ranked: return None
        self.last_score,self.last_selected=max(ranked,key=lambda item:item[0])
        return self.last_selected

class CategoryAdapter(InformationProvider):
    """Compatibility adapter for existing category-keyed providers."""
    def __init__(self, category, provider): self.category,self.provider=category,provider
    def supports(self, request): return 100 if request.category==self.category else 0
    def query(self, request): return self.provider.query(request)

class InformationService:
    """Formats only validated structured information-provider results."""
    def __init__(self, providers=None):
        self.providers=dict(providers or {}) if isinstance(providers,dict) else {}
        adapters=[CategoryAdapter(category,provider) for category,provider in self.providers.items()]
        if not isinstance(providers,dict): adapters=list(providers or [])
        self.sources=InformationSourceResolver(adapters)
    @staticmethod
    def _metadata(request): return {"information_request":request.to_dict(),"read_only_guidance":True,"response_type":"informational"}
    def query(self, request):
        if not isinstance(request,InformationRequest): return Result(False,"Не удалось безопасно сформировать информационный запрос.",{"read_only_guidance":True})
        provider=self.sources.resolve(request)
        if not callable(getattr(provider,"query",None)):
            message="Для погоды укажите город." if request.category=="weather" and not request.location else "Источник информации сейчас недоступен. Попробуйте позже."
            return Result(False,message,self._metadata(request))
        try: response=provider.query(request)
        except Exception: return Result(False,"Не удалось получить данные от источника.",self._metadata(request))
        if not isinstance(response,InformationResult) or response.category and response.category!=request.category:
            return Result(False,"Источник вернул некорректный ответ.",self._metadata(request))
        if not response.ok: return Result(False,response.error or "Не удалось получить данные от источника.",self._metadata(request))
        text=self._format(request,response)
        return Result(bool(text),text or "Источник не вернул данных.",self._metadata(request))
    @staticmethod
    def _format(request,response):
        if response.summary and isinstance(response.summary,str) and len(response.summary)<=500 and not _UNSAFE.search(response.summary): return response.summary.strip()
        data=response.data if isinstance(response.data,dict) else {}
        if request.category=="weather":
            temperature=data.get("temperature"); condition=data.get("condition"); location=data.get("location") or request.location
            if isinstance(temperature,(int,float)) and not isinstance(temperature,bool) and isinstance(condition,str) and condition.strip() and isinstance(location,str) and location.strip():
                degree=int(temperature) if float(temperature).is_integer() else temperature
                return f"Сейчас в {location} {degree} градусов, {condition.strip()}."
        text=data.get("text")
        return text.strip() if isinstance(text,str) and text.strip() and len(text.strip())<=500 and not _UNSAFE.search(text) else ""
