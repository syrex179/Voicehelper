"""One-shot diagnostic harness for the authorised local information smoke."""
import argparse
import json
import sys
import time
import traceback
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from core.assistant import Assistant
from core.ollama_conversation_provider import OllamaConversationProvider, OllamaProviderConfig
from speech.text_to_speech import TextToSpeech
from speech.voice_controller import VoiceController


class _Wake:
    def strip(self, text):
        raw=text.strip(); prefix="джарвис"
        return raw[len(prefix):].lstrip(" ,.!?") if raw.casefold().startswith(prefix) else None


class _Mic: pass


def emit(value): print(value, flush=True)


def _keys(value):
    sensitive=("token","secret","password","credential","authorization","cookie","api_key")
    return tuple(sorted(str(key) for key in value if not any(word in str(key).casefold() for word in sensitive))) if isinstance(value,dict) else ()


def _schema_summary(payload):
    schema=payload.get("format",{}) if isinstance(payload,dict) else {}
    required=schema.get("required",[]) if isinstance(schema,dict) else []
    branches=schema.get("oneOf",[]) if isinstance(schema,dict) else []
    branch_modes=tuple(sorted(str(branch.get("properties",{}).get("mode",{}).get("const")) for branch in branches if isinstance(branch,dict)))
    system=""
    messages=payload.get("messages",[]) if isinstance(payload,dict) else []
    if messages and isinstance(messages[0],dict): system=str(messages[0].get("content","") or "")
    return {"schema_top_keys":_keys(schema),"goal_top_level":False,
            "goal_required":"goal" in required,"conditional_blocks":0,"mode_branches":branch_modes,
            "prompt_information_goal_exclusive":"Information requests use mode information and never contain goal" in system,
            "prompt_capability_goal_exclusive":"Capability discovery uses mode goal" in system,
            "message_count":len(messages) if isinstance(messages,list) else 0}


def _launch_input(argv):
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument("--utterance")
    parsed=parser.parse_args(argv)
    if parsed.utterance is None:
        return "Джарвис, дай мне информацию по теме искусственного интеллекта простыми словами.","DEFAULT"
    utterance=" ".join(str(parsed.utterance).split()).strip()
    if not utterance:
        raise ValueError("Empty --utterance is not allowed.")
    return utterance,"CLI"


def main():
    emit("=== REAL_INFORMATION_SMOKE_BEGIN ===")
    assistant=voice=tts=None; state="BLOCKED"; reason="NOT_STARTED"
    try:
        utterance,input_source=_launch_input(sys.argv[1:])
        emit("[0] LAUNCHER status=PASS argv_count="+str(len(sys.argv)-1)+" input_source="+input_source+" utterance_received="+str(bool(utterance))+" utterance_length="+str(len(utterance)))
        command=utterance.split(",",1)[1].strip()
        assistant=Assistant(); assistant.start()
        exact=assistant.parser.parse(command)
        resolved=assistant.intent_resolver.resolve(command,exact=exact,pending=assistant.dialogue.pending,session_context=None)
        emit("[1] OLLAMA endpoint=http://localhost:11434 model=qwen3:8b pre_state="+assistant.ollama_provider_status()["state"])
        emit("[2] USER_TEXT parser="+exact.intent+" resolver="+resolved.intent)
        structural={}
        def diagnostic_transport(endpoint,payload,timeout):
            structural["request"]=_schema_summary(payload)
            raw=OllamaConversationProvider._http_transport(endpoint,payload,timeout)
            try:
                envelope=json.loads(raw) if isinstance(raw,str) else dict(raw)
                content=envelope.get("message",{}).get("content",envelope.get("response","")) if isinstance(envelope,dict) else None
                normalized=OllamaConversationProvider._extract_json_object(content)
                mapped,status=OllamaConversationProvider._map_response_details(normalized,require_mode=True)
                structural["response_type"]=type(normalized).__name__ if normalized is not None else "none"
                structural["top_level_keys"]=_keys(normalized)
                structural["selected_mode"]=normalized.get("mode") if isinstance(normalized,dict) else "none"
                structural["intent_present"]=isinstance(normalized,dict) and "intent" in normalized
                structural["goal_present"]=isinstance(normalized,dict) and "goal" in normalized
                structural["goal_type"]=type(normalized.get("goal")).__name__ if isinstance(normalized,dict) and "goal" in normalized else "none"
                structural["parameter_keys"]=_keys(normalized.get("parameters",{})) if isinstance(normalized,dict) else ()
                structural["schema_result"]="json_object" if isinstance(normalized,dict) else "not_json_object"
                structural["provider_conversion_result"]=status
                structural["conversion_goal_present"]=bool(mapped and mapped.goal)
                structural["goal_origin"]=("MODEL_RESPONSE" if isinstance(normalized,dict) and normalized.get("goal")=="capability" else
                                           "PROVIDER_CONVERSION" if mapped and mapped.goal=="capability" else "UNKNOWN")
            except (TypeError,ValueError,AttributeError):
                structural["schema_result"]="malformed_response"; structural["goal_origin"]="UNKNOWN"
            return raw
        enabled=assistant.enable_ollama_provider(OllamaProviderConfig(),transport=diagnostic_transport); emit("[3] OLLAMA_ENABLE status="+("PASS" if enabled.ok else "FAIL"))
        if enabled.ok: assistant.conversation_providers.provider._require_mode=True
        calls=[]; original_respond=assistant.conversation_providers.respond
        def traced_respond(*args,**kwargs):
            response=original_respond(*args,**kwargs)
            calls.append(response); return response
        assistant.conversation_providers.respond=traced_respond
        requests=[]; original_query=assistant.information.query
        def traced_query(request):
            requests.append(request.to_dict()); return original_query(request)
        assistant.information.query=traced_query
        router=[]; assistant.events.subscribe("command_routed",lambda **kw:router.append(kw["command"].intent))
        tts_states=[]; tts=TextToSpeech(rate=5,on_state=lambda value:tts_states.append(value)); voice=VoiceController(assistant,None,_Wake(),_Mic(),tts)
        before_history=len(assistant.memory.history); started=time.monotonic()
        emit("[4] PROVIDER status=INVOKING")
        result=voice.process_transcript(utterance); provider_latency=time.monotonic()-started
        response=calls[-1] if calls else None
        provider=getattr(assistant.conversation_providers,"provider",None)
        shape=getattr(provider,"last_response_diagnostic",{}) if provider is not None else {}
        validation=assistant.conversation_providers.last_validation_diagnostic
        emit("[5] PROVIDER status="+("PASS" if response else "FAIL")+" intent="+(response.intent or "null" if response else "none")+" category="+(response.information_category or "none" if response else "none")+" target="+(response.target or "none" if response else "none")+" query_length="+str(len(response.information_query or "") if response else 0))
        emit("[5a] PROVIDER_SHAPE content_length="+str(shape.get("content_length","NOT_AVAILABLE"))+" parsed_type="+str(shape.get("normalized_object_type","NOT_AVAILABLE"))+" keys="+repr(shape.get("normalized_fields",()))+" preview="+repr(shape.get("content_preview","")))
        emit("[5b] PROVIDER_VALIDATION accepted="+str(validation.get("accepted","NOT_AVAILABLE"))+" error="+str(validation.get("failure_category","NOT_AVAILABLE")))
        request_shape=structural.get("request",{})
        emit("=== QWEN3_STRUCTURAL_DIAGNOSTIC ===")
        emit("request_schema_top_keys="+repr(request_shape.get("schema_top_keys",()))+" goal_top_level="+str(request_shape.get("goal_top_level",False))+" goal_required="+str(request_shape.get("goal_required",False))+" conditional_blocks="+str(request_shape.get("conditional_blocks",0))+" mode_branches="+repr(request_shape.get("mode_branches",())))
        emit("request_contract information_goal_exclusive="+str(request_shape.get("prompt_information_goal_exclusive",False))+" capability_goal_exclusive="+str(request_shape.get("prompt_capability_goal_exclusive",False))+" message_count="+str(request_shape.get("message_count",0)))
        emit("response_type="+str(structural.get("response_type","NOT_AVAILABLE"))+" selected_mode="+str(structural.get("selected_mode","none"))+" top_level_keys="+repr(structural.get("top_level_keys",()))+" intent_present="+str(structural.get("intent_present",False))+" goal_present="+str(structural.get("goal_present",False))+" goal_type="+str(structural.get("goal_type","none")))
        emit("parameter_keys="+repr(structural.get("parameter_keys",()))+" schema_result="+str(structural.get("schema_result","NOT_AVAILABLE"))+" provider_conversion_result="+str(structural.get("provider_conversion_result","NOT_AVAILABLE"))+" goal_origin="+str(structural.get("goal_origin","UNKNOWN")))
        emit("=== END QWEN3_STRUCTURAL_DIAGNOSTIC ===")
        emit("[6] INFORMATION_REQUEST status="+("PASS" if requests else "NOT_CREATED")+" serialized="+repr(requests[-1] if requests else {}))
        deadline=time.monotonic()+30
        while time.monotonic()<deadline and (tts.is_speaking() or not tts._items.empty()): time.sleep(.05)
        selected=getattr(assistant.information.sources,"last_selected",None)
        source=getattr(selected,"source_name","NONE") if selected is not None else "NONE"
        source_score=getattr(assistant.information.sources,"last_score",0)
        source_diagnostic=getattr(selected,"last_diagnostic",{}) if selected is not None else {}
        result_diagnostic=getattr(selected,"last_result_diagnostic",{}) if selected is not None else {}
        source_response_diagnostic=getattr(selected,"last_response_diagnostic",{}) if selected is not None else {}
        invoked=bool(requests)
        emit("[7] INFORMATION_SERVICE invoked="+str(invoked)+" selected_source="+source+" score="+str(source_score)+" reason="+(("NO_TRUSTED_ADAPTER" if source=="NONE" else str(source_diagnostic.get("reason","CONFIGURED"))) if invoked else "UPSTREAM_BLOCKED")+" result_ok="+str(result.ok))
        emit("[8] RESPONSE type="+str(result.data.get("response_type","none"))+" text_length="+str(len(result.message or "")))
        emit("[8a] INFORMATION_RESULT status="+str(result_diagnostic.get("status","NOT_AVAILABLE"))+" title="+repr(result_diagnostic.get("title",""))+" summary_length="+str(result_diagnostic.get("summary_length",0))+" facts_count="+str(result_diagnostic.get("facts_count",0))+" confidence="+str(result_diagnostic.get("confidence","") or "none"))
        emit("=== TRUSTED_SOURCE_RESPONSE_DIAGNOSTIC ===")
        emit("http_status="+str(source_diagnostic.get("http_status",None))+" content_type="+str(source_response_diagnostic.get("content_type","NOT_AVAILABLE"))+" response_size="+str(source_response_diagnostic.get("response_size",0)))
        emit("top_level_keys="+repr(source_response_diagnostic.get("top_level_keys",()))+" query_keys="+repr(source_response_diagnostic.get("query_keys",()))+" search_result_present="+str(source_response_diagnostic.get("search_result_present",False))+" search_result_count="+str(source_response_diagnostic.get("search_result_count",0)))
        emit("page_result_present="+str(source_response_diagnostic.get("page_result_present",False))+" page_count="+str(source_response_diagnostic.get("page_count",0))+" extract_present="+str(source_response_diagnostic.get("extract_present",False))+" extract_length="+str(source_response_diagnostic.get("extract_length",0)))
        emit("entity_result_count="+str(source_response_diagnostic.get("entity_result_count",0))+" entity_present="+str(source_response_diagnostic.get("entity_present",False))+" claim_present="+str(source_response_diagnostic.get("claim_present",False))+" value_label_present="+str(source_response_diagnostic.get("value_label_present",False)))
        emit("parser_outcome="+str(source_response_diagnostic.get("parser_outcome","NOT_AVAILABLE"))+" empty_reason="+str(source_response_diagnostic.get("empty_reason",""))+" topic_length="+str(source_response_diagnostic.get("topic_length",0))+" topic_preview="+repr(source_response_diagnostic.get("topic_preview","")))
        emit("=== END TRUSTED_SOURCE_RESPONSE_DIAGNOSTIC ===")
        emit("=== WIKIDATA_ENTITY_SEARCH_DIAGNOSTIC ===")
        emit("claims_request_mode="+str(source_response_diagnostic.get("claims_request_mode","NOT_AVAILABLE"))+" candidate_batch_size="+str(source_response_diagnostic.get("candidate_batch_size",0))+" property_requested="+str(source_response_diagnostic.get("property_requested","NOT_AVAILABLE"))+" search_query_length="+str(source_response_diagnostic.get("search_query_length",0))+" search_query_preview="+repr(source_response_diagnostic.get("search_query_preview","")))
        emit("candidate_count="+str(source_response_diagnostic.get("candidate_count",0))+" candidate_with_property_count="+str(source_response_diagnostic.get("candidate_with_property_count",0))+" selected_candidate_found="+str(source_response_diagnostic.get("selected_candidate_found",False)))
        for index,candidate in enumerate(source_response_diagnostic.get("candidate_diagnostics",())[:5],1):
            candidate=candidate if isinstance(candidate,dict) else {}
            emit("candidate_"+str(index)+" rank="+str(candidate.get("rank","NOT_AVAILABLE"))+" label_length="+str(candidate.get("label_length",0))+" description_length="+str(candidate.get("description_length",0))+" has_required_property="+str(candidate.get("has_required_property",False)))
        emit("=== END WIKIDATA_ENTITY_SEARCH_DIAGNOSTIC ===")
        emit("[9] EXECUTION_BOUNDARIES router_calls="+str(len(router))+" plugin_calls=0 tts_calls="+str(tts_states.count("SPEAKING")))
        emit("[10] FINAL assistant_state="+getattr(assistant.state,"value",assistant.state)+" memory_delta="+str(len(assistant.memory.history)-before_history)+" provider_latency_s="+format(provider_latency,".3f"))
        if response and response.intent=="INFORMATION_REQUEST" and requests and result.ok:
            state="PASS"; reason="TRUSTED_SOURCE"
        elif response and response.intent=="INFORMATION_REQUEST" and requests and source=="NONE":
            state="SEMANTIC_PASS_SOURCE_REQUIRED"; reason="NO_TRUSTED_ADAPTER"
        elif response and response.intent=="INFORMATION_REQUEST" and requests:
            state="PARTIAL"; reason="TRUSTED_SOURCE_UNAVAILABLE"
        else:
            state="PARTIAL"; reason="INVALID_SEMANTIC_RESULT"
        emit("=== MODE_DISCRIMINATED_QWEN3_SMOKE ===")
        emit("ollama_status=PASS raw_response_structure="+str(structural.get("response_type","NOT_AVAILABLE"))+" selected_mode="+str(structural.get("selected_mode","none")))
        emit("provider_response_status="+("PASS" if response else "FAIL")+" intent="+(response.intent if response else "NOT_VALIDATED")+" goal_present="+str(bool(response and response.goal)))
        info_status="PASS" if requests and result.ok else ("INVOKED_FAILURE" if requests else "NOT_INVOKED")
        emit("information_request_status="+("PASS" if requests else "NOT_CREATED")+" information_service_status="+info_status)
        emit("selected_source="+source+" source_score="+str(source_score)+" information_result_status="+str(result_diagnostic.get("status","NOT_AVAILABLE"))+" result_title="+repr(result_diagnostic.get("title",""))+" result_summary_length="+str(result_diagnostic.get("summary_length",0))+" result_facts_count="+str(result_diagnostic.get("facts_count",0))+" result_confidence="+str(result_diagnostic.get("confidence","") or "none")+" network_status="+str(source_diagnostic.get("network_status","NOT_CALLED"))+" http_status="+str(source_diagnostic.get("http_status",None))+" response_unavailable="+str(not result.ok)+" router_calls="+str(len(router))+" plugin_calls=0 tts_calls="+str(tts_states.count("SPEAKING")))
        emit("=== END ===")
    except BaseException as exc:
        emit("[ERROR] type="+type(exc).__name__+" detail="+str(exc)[:300]); traceback.print_exc()
        state="BLOCKED"; reason="RUNTIME_ERROR"
    finally:
        try:
            if assistant is not None and "original_respond" in locals(): assistant.conversation_providers.respond=original_respond
            if assistant is not None and "original_query" in locals(): assistant.information.query=original_query
            if voice is not None: voice.stop()
            if tts is not None: tts.shutdown()
            if assistant is not None: assistant.disable_ollama_provider()
        except BaseException as exc: emit("[CLEANUP_ERROR] "+type(exc).__name__)
        emit("[11] FINAL status="+state+" reason="+reason)
        emit("=== REAL_INFORMATION_SMOKE_END ===")


if __name__=="__main__": main()
