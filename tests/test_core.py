import json
import importlib.util
import subprocess
import shutil
import sys
import tempfile
import unittest
import time
import threading
from unittest.mock import patch
from pathlib import Path
from core.assistant import Assistant
from core.command_registry import CommandRegistry
from core.command_router import CommandRouter
from core.dialogue_manager import DialogueManager
from core.learning_engine import LearningEngine
from core.memory_manager import MemoryManager
from core.models import Command, Result
from core.permissions import PermissionManager
from core.plugin_manager import PluginManager
from core.skill_manager import SkillManager
from core.demonstration import DemonstrationRecorder
from core.event_bus import EventBus
from core.autostart import WindowsAutostart
from core.models import AssistantState
from speech.voice_controller import VoiceController
from speech.microphone import MicrophoneManager
from core.routine_manager import RoutineManager
from core.response_composer import ResponseComposer
from core.conversation_turn import ConversationTurn
from core.response_policy import ResponsePolicy
from core.turn_sequence import TurnSequenceCoordinator
from core.response_enricher import ResponseEnricher
from core.structured_result_inventory import StructuredResultInventory
from core.intent_parser import IntentParser
from core.conversational_intent_resolver import ConversationalIntentResolver
from core.conversation_provider import ConversationProvider, ProviderAdapter, ProviderCapabilities, ProviderResponse, StubConversationProvider, ConversationProviderRegistry
from core.openai_conversation_provider import OpenAIConversationProvider, OpenAIProviderConfig, OpenAISecretBoundary
from core.ollama_conversation_provider import OllamaConversationProvider, OllamaProviderConfig
from core.browser_chatgpt_provider import BrowserChatGPTProvider, PlaywrightChatGPTBrowserTransport
from core.resource_resolver import ResourceResolver, ResourceTarget
from core.information_provider import InformationRequest, InformationResult, InformationProvider, InformationService, InformationSourceResolver, WikipediaInformationSource, WikidataInformationSource, BraveWebSearchInformationSource
from core.smoke_diagnostics import format_smoke_diagnostic
from plugins.browser.plugin import Plugin as BrowserPlugin
from plugins.applications.plugin import Plugin as ApplicationsPlugin
from plugins.files.plugin import Plugin as FilesPlugin
from plugins.volume.plugin import Plugin as VolumePlugin
from core.schedule_manager import ScheduledRoutineManager
from core.diagnostics import RuntimeDiagnostics, format_report
from speech.speech_to_text import SpeechToText, LocalVoskSTTBackend, STTUnavailable
from speech.text_to_speech import TextToSpeech
from speech.stt_normalizer import RegisteredResource, STTResourceNormalizer
from datetime import datetime, timedelta, timezone

class RouterStub:
    def __init__(self): self.calls=[]
    def route(self, command, confirmed=False): self.calls.append((command,confirmed)); return Result(True,"ok")

class CoreTests(unittest.TestCase):
    @staticmethod
    def _isolated_assistant(directory, load_plugins=False):
        """Test-only dependency wiring; production Assistant still uses DATA_DIR."""
        assistant=Assistant(); memory=MemoryManager(directory)
        assistant.memory=memory; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
        assistant.plugins.context["memory"]=memory
        assistant.routines=RoutineManager(memory,assistant.router,assistant.events)
        assistant.schedules=ScheduledRoutineManager(memory,assistant.routines)
        if load_plugins: assistant.start()
        return assistant
    def test_response_composer_is_deterministic_and_side_effect_free(self):
        composer=ResponseComposer()
        self.assertEqual(composer.compose(intent="OPEN_BROWSER").text,"Готово, браузер открыт.")
        self.assertEqual(composer.compose(intent="SET_VOLUME",metadata={"level":50}).text,"Готово, громкость установлена на 50%.")
        self.assertEqual(composer.compose(success=False).response_type,"action_failure")
        self.assertEqual(composer.compose(pending={"kind":"browser_clarification"}).response_type,"clarification")
        self.assertEqual(composer.compose(metadata={"corrected":True}).response_type,"correction")
        self.assertEqual(composer.compose(metadata={"reference_replay":True}).response_type,"reference_replay")
        self.assertEqual(composer.compose(intent="SESSION_GREETING").response_type,"social")
        self.assertEqual(composer.compose(intent="SESSION_STATUS_QUERY").response_type,"status")
        self.assertEqual(composer.compose(intent="HELP").response_type,"help")
        self.assertEqual(composer.compose(intent="UNKNOWN").response_type,"unknown")
        context={"last_action":"OPEN_BROWSER"}; response=composer.compose(intent="OPEN_BROWSER",text="Сохранённый текст.",metadata={"context":context}); self.assertEqual(response.text,"Сохранённый текст."); self.assertEqual(context,{"last_action":"OPEN_BROWSER"})
    def test_conversation_turn_is_normalized_copy_safe_and_composer_compatible(self):
        pending={"kind":"browser_clarification","step":"site","secret":"hidden"}; reference={"last_safe_action_type":"OPEN_BROWSER","reference_available":True}; result=Result(True,"Браузер открыт.",{"level":50,"token":"hidden"})
        turn=ConversationTurn.create(user_text="  Открой   Браузер ",session_state="READY",intent="OPEN_BROWSER",response_type="action_success",result=result,pending_state=pending,reference_context=reference,timestamp=12.5)
        pending["kind"]="changed"; reference["reference_available"]=False
        self.assertEqual(turn.normalized_text,"открой браузер"); self.assertEqual(turn.intent,"OPEN_BROWSER"); self.assertEqual(turn.pending_state["kind"],"browser_clarification"); self.assertTrue(turn.reference_context["reference_available"]); self.assertEqual(turn.action_result["metadata"],{"level":50}); self.assertNotIn("token",turn.to_dict()["action_result"]["metadata"])
        with self.assertRaises(TypeError): turn.pending_state["kind"]="changed"
        self.assertEqual(ResponseComposer().compose(turn=turn).text,"Браузер открыт.")
    def test_response_policy_classifies_immutable_turns_without_side_effects(self):
        policy=ResponsePolicy(); composer=ResponseComposer()
        def turn(text="",intent="",success=True,pending=None,metadata=None):
            return ConversationTurn.create(user_text=text,session_state="READY",intent=intent,result=Result(success,"Сохранённый текст.",metadata or {}),pending_state=pending,timestamp=1)
        cases=[
            (turn("стоп"),"social"),(turn("отмена"),"informational"),(turn("x",pending={"kind":"browser_clarification","step":"site"}),"clarification"),(turn("x",pending={"kind":"browser_clarification"},metadata={"corrected":True}),"correction"),
            (turn("подожди"),"informational"),(turn("ты слушаешь?"),"status"),(turn("что можно сейчас сделать?"),"help"),(turn("снова",metadata={"reference_replay":True}),"reference_replay"),
            (turn("x",intent="OPEN_BROWSER"),"action_success"),(turn("x",intent="OPEN_BROWSER",success=False),"action_failure"),(turn("x",intent="SESSION_RECAP"),"informational"),(turn("x",intent="SESSION_GREETING"),"social"),
            (turn("x",intent="SESSION_ACKNOWLEDGEMENT"),"social"),(turn("x",intent="UNKNOWN"),"unknown"),(turn("x",success=False),"unknown")]
        for item,expected in cases: self.assertEqual(policy.decide(item).response_type,expected)
        source=turn("снова",intent="OPEN_BROWSER",metadata={"reference_replay":True,"token":"hidden"}); before=source.to_dict(); decision=policy.decide(source); self.assertEqual(source.to_dict(),before); self.assertEqual(composer.compose(turn=source).text,"Сохранённый текст."); self.assertEqual(decision.reason,"reference")
    def test_turn_sequence_coordinator_is_bounded_and_copy_safe(self):
        coordinator=TurnSequenceCoordinator()
        def pending_turn(text="x"):
            return ConversationTurn.create(user_text=text,session_state="READY",response_type="clarification",result=Result(False,"Уточните."),pending_state={"kind":"browser_clarification","step":"site"},timestamp=1)
        first=coordinator.advance(None,pending_turn()); second=coordinator.advance(first,pending_turn("не понял")); third=coordinator.advance(second,pending_turn("ещё раз"))
        self.assertEqual(first.depth,1); self.assertEqual(second.depth,2); self.assertIsNone(third); self.assertEqual(first.pending_kind,"browser_clarification"); self.assertEqual(first.to_dict()["depth"],1)
        action=ConversationTurn.create(user_text="открой браузер",session_state="READY",intent="OPEN_BROWSER",response_type="action_success",result=Result(True,"ok"),timestamp=1)
        sequence=coordinator.advance(None,action); self.assertEqual(sequence.last_safe_action_type,"OPEN_BROWSER"); self.assertIs(sequence,coordinator.advance(sequence,ConversationTurn.create(user_text="ты слушаешь?",session_state="READY",response_type="status",result=Result(True,"ok"),timestamp=2)))
        correction=ConversationTurn.create(user_text="нет, 70",session_state="READY",response_type="correction",result=Result(True,"ok",{"corrected":True}),pending_state={"kind":"volume_clarification","step":"level"},timestamp=2); self.assertEqual(coordinator.advance(None,correction).last_response_type,"correction")
        new_action=ConversationTurn.create(user_text="открой youtube",session_state="READY",intent="OPEN_YOUTUBE",response_type="action_success",result=Result(True,"ok"),timestamp=3); self.assertIsNone(coordinator.advance(sequence,new_action))
    def test_response_enrichment_uses_only_safe_turn_facts(self):
        composer=ResponseComposer(); enricher=ResponseEnricher()
        def turn(intent="",response_type="action_success",success=True,pending=None,reference=None,metadata=None):
            return ConversationTurn.create(user_text="x",session_state="READY",intent=intent,response_type=response_type,result=Result(success,"",metadata or {}),pending_state=pending,reference_context=reference,timestamp=1)
        browser=turn("OPEN_BROWSER",metadata={"safe_target":"YouTube","token":"hidden"}); self.assertEqual(composer.compose(turn=browser).text,"Готово, браузер открыт: YouTube.")
        volume=turn("SET_VOLUME",metadata={"level":50}); self.assertEqual(composer.compose(turn=volume).text,"Готово, громкость установлена на 50%.")
        replay=turn("OPEN_YOUTUBE","reference_replay",reference={"last_safe_action_type":"OPEN_YOUTUBE","last_safe_action_target":"YouTube","reference_available":True}); self.assertEqual(composer.compose(turn=replay).text,"Повторяю открытие YouTube.")
        self.assertEqual(composer.compose(turn=turn(response_type="clarification",pending={"kind":"browser_clarification"})).text,"Я жду название или адрес сайта.")
        self.assertEqual(composer.compose(turn=turn(response_type="clarification",pending={"kind":"volume_clarification"})).text,"Я жду значение громкости от 0 до 100.")
        self.assertEqual(composer.compose(turn=turn(response_type="correction",metadata={"level":70})).text,"Хорошо, исправляю значение на 70.")
        failed=turn("OPEN_BROWSER",success=False,metadata={"error":"raw stack trace","token":"hidden"}); self.assertEqual(composer.compose(turn=failed).text,"Не удалось выполнить запрос."); self.assertNotIn("token",failed.to_dict()["action_result"]["metadata"]); self.assertEqual(enricher.enrich(browser).text,"Готово, браузер открыт: YouTube.")
        before=browser.to_dict(); composer.compose(turn=browser); self.assertEqual(browser.to_dict(),before)
    def test_generic_structured_response_mapping_prefers_safe_facts_then_legacy(self):
        composer=ResponseComposer()
        def turn(intent="",message="",metadata=None,success=True): return ConversationTurn.create(user_text="x",session_state="READY",intent=intent,response_type="action_success",result=Result(success,message,metadata or {}),timestamp=1)
        self.assertEqual(composer.compose(turn=turn("OPEN_APPLICATION","legacy",{"safe_target":"Документы"})).text,"Готово, открываю Документы.")
        self.assertEqual(composer.compose(turn=turn("SET_SETTING","legacy",{"value":42,"validated_value":True})).text,"Готово, значение установлено на 42.")
        self.assertEqual(composer.compose(turn=turn("OPEN_FOLDER","Папка открыта.")).text,"Папка открыта.")
        self.assertEqual(composer.compose(turn=turn("OPEN_BROWSER","legacy",{"composer_owned_template":"open_browser_success","safe_target":"Игнор"})).text,"Готово, браузер открыт.")
        self.assertEqual(composer.compose(turn=turn("UNKNOWN","raw failure",success=False)).text,"Не удалось выполнить запрос.")
        self.assertEqual(composer.compose(turn=turn("OPEN_APPLICATION","Сохранённый текст.",{"safe_target":"token=secret"})).text,"Сохранённый текст.")
    def test_generic_success_category_handles_existing_safe_intents_once_without_side_effects(self):
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text); return True
            def is_speaking(self): return False
        class Plugin:
            def __init__(self): self.calls=[]
            def execute(self,command): self.calls.append(command); return Result(True,"")
        class Router:
            def __init__(self,plugin): self.calls=[]; self.plugin=plugin
            def route(self,command,confirmed=False): self.calls.append(command); return self.plugin.execute(command)
        class AssistantStub:
            def __init__(self,intent,router):
                self.intent,self.router=intent,router; self.calls=[]; self.memory=type("M",(),{"settings":{"conversation_timeout":2}})(); self.dialogue=type("D",(),{"pending":None})(); self.state=AssistantState.READY
                self.parser=type("P",(),{"parse":lambda _self,text: Command(intent,{})})()
            def set_state(self,state): self.state=state
            def handle(self,text): self.calls.append(text); return self.router.route(self.parser.parse(text))
        for intent in ("OPEN_APPLICATION","OPEN_SETTINGS"):
            plugin=Plugin(); router=Router(plugin); assistant=AssistantStub(intent,router); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
            self.assertTrue(voice.process_transcript("Джарвис, безопасная команда").ok)
            self.assertEqual(StructuredResultInventory().classify(ConversationTurn.create(user_text="x",session_state="READY",intent=intent,result=Result(True,""),timestamp=1)).category,"generic_success")
            self.assertEqual(tts.messages,["Готово."]); self.assertEqual(len(assistant.calls),1); self.assertEqual(len(router.calls),1); self.assertEqual(len(plugin.calls),1); self.assertEqual(assistant.memory.settings,{"conversation_timeout":2})
    def test_structured_result_inventory_maps_safe_categories_and_legacy_fallback(self):
        inventory=StructuredResultInventory()
        def turn(intent,message="ok",metadata=None,success=True): return ConversationTurn.create(user_text="x",session_state="READY",intent=intent,response_type="action_success",result=Result(success,message,metadata or {}),timestamp=1)
        self.assertEqual(inventory.classify(turn("OPEN_YOUTUBE","ok",{"safe_target":"YouTube"})).category,"safe_target")
        self.assertEqual(inventory.classify(turn("SET_VOLUME","ok",{"level":50})).category,"validated_numeric")
        self.assertEqual(inventory.classify(turn("OPEN_APPLICATION","",{})).category,"generic_success")
        self.assertEqual(inventory.classify(turn("OPEN_APPLICATION","failed",{},False)).category,"generic_failure")
        folder=inventory.classify(turn("OPEN_FOLDER","Открываю файл.",{"path":"C:\\secret\\x"})); self.assertEqual(folder.category,"legacy_only"); self.assertEqual(folder.fallback,"authoritative Result.message")
        self.assertEqual(inventory.classify(turn("SEARCH_YOUTUBE","Открываю страницу.",{"url":"https://x"})).category,"legacy_only")
    def test_generic_failure_category_preserves_safe_messages_and_redacts_technical_failures(self):
        composer=ResponseComposer(); inventory=StructuredResultInventory()
        def turn(intent,result): return ConversationTurn.create(user_text="x",session_state="READY",intent=intent,response_type="action_failure",result=result,timestamp=1)
        class Router:
            def __init__(self,plugin): self.calls=[]; self.plugin=plugin
            def route(self,command,confirmed=False): self.calls.append(command); return self.plugin.execute(command)
        def routed(plugin,command):
            router=Router(plugin)
            with patch.object(plugin,"execute",wraps=plugin.execute) as execute:
                result=router.route(command)
            self.assertEqual(len(router.calls),1); execute.assert_called_once_with(command)
            return result
        browser=BrowserPlugin(); files=FilesPlugin(); cases=[]
        with patch.object(browser,"_browser_executable",return_value=None): cases.append(("OPEN_BROWSER",routed(browser,Command("OPEN_BROWSER",{})),"Не найден установленный браузер."))
        cases.append(("OPEN_BROWSER",routed(browser,Command("OPEN_BROWSER",{"url":"about:blank"})),"Разрешены только URL http:// или https://."))
        files.initialize({"memory":None}); cases.append(("OPEN_FOLDER",routed(files,Command("OPEN_FOLDER",{"query":"неизвестная папка"})),"Уточните папку: Загрузки, Документы или Рабочий стол."))
        for intent,result,expected in cases:
            item=turn(intent,result); category=inventory.classify(item)
            self.assertEqual(category.category,"generic_failure"); self.assertTrue(category.authoritative_message_safe); self.assertEqual(composer.compose(turn=item).text,expected)
        raw=turn("OPEN_BROWSER",Result(False,"RuntimeError: token=secret at C:\\Users\\danik\\trace.py"))
        category=inventory.classify(raw); self.assertEqual(category.category,"generic_failure"); self.assertFalse(category.authoritative_message_safe); self.assertEqual(composer.compose(turn=raw).text,"Не удалось выполнить запрос.")
    def test_generic_failure_voice_tts_uses_safe_fallback_without_execution_access(self):
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        class AssistantStub:
            def __init__(self):
                self.calls=[]; self.memory=type("M",(),{"settings":{"conversation_timeout":2}})(); self.dialogue=type("D",(),{"pending":None})(); self.state=AssistantState.READY
                self.parser=type("P",(),{"parse":lambda _self,text: Command("OPEN_BROWSER",{})})()
            def set_state(self,state): self.state=state
            def handle(self,text): self.calls.append(text); return Result(False,"OSError: password=hidden C:\\secret\\stack.py")
        assistant=AssistantStub(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
        self.assertFalse(voice.process_transcript("Джарвис, открой браузер").ok); self.assertEqual(tts.messages,["Не удалось выполнить запрос."]); self.assertEqual(len(assistant.calls),1); self.assertEqual(assistant.memory.settings,{"conversation_timeout":2})
    def test_safe_target_category_uses_only_normalized_allow_listed_voice_metadata(self):
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        class Memory:
            settings={"conversation_timeout":2}; aliases={}; custom_commands={}; learned_routines=[]; skills=[]
        class Plugin:
            def __init__(self): self.calls=[]
            def execute(self,command): self.calls.append(command); return Result(True,"Открываю папку.")
        class Router:
            def __init__(self,plugin): self.calls=[]; self.plugin=plugin
            def route(self,command,confirmed=False): self.calls.append(command); return self.plugin.execute(command)
        class AssistantStub:
            def __init__(self,router):
                self.memory=Memory(); self.parser=IntentParser(self.memory); self.router=router; self.dialogue=type("D",(),{"pending":None})(); self.state=AssistantState.READY; self.calls=[]
            def set_state(self,state): self.state=state
            def handle(self,text): self.calls.append(text); return self.router.route(self.parser.parse(text))
        inventory=StructuredResultInventory()
        for phrase,target in (("открой документы","Документы"),("открой загрузки","Загрузки")):
            plugin=Plugin(); router=Router(plugin); assistant=AssistantStub(router); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
            self.assertTrue(voice.process_transcript("Джарвис, "+phrase).ok); turn=voice.conversation_turn
            structured=ConversationTurn.create(user_text="x",session_state="READY",intent=turn["intent"],result=Result(True,"Открываю папку.",turn["action_result"]["metadata"]),timestamp=1)
            self.assertEqual(turn["intent"],"OPEN_FOLDER"); self.assertEqual(turn["action_result"]["metadata"],{"safe_target":target}); self.assertEqual(inventory.classify(structured).category,"safe_target"); self.assertEqual(tts.messages,[f"Готово, открываю {target}."])
            self.assertEqual(len(assistant.calls),1); self.assertEqual(len(router.calls),1); self.assertEqual(len(plugin.calls),1); self.assertEqual(assistant.memory.settings,{"conversation_timeout":2})
        for unsafe in ("C:\\Users\\danik", "file:///secret", "about:blank", "https://example.com", "C:\\Program Files\\app.exe", "cmd /c whoami", "RuntimeError: boom", "token=secret"):
            turn=ConversationTurn.create(user_text="x",session_state="READY",intent="OPEN_APPLICATION",result=Result(True,"Сохранённый текст.",{"safe_target":unsafe}),timestamp=1)
            self.assertEqual(inventory.classify(turn).category,"legacy_only"); self.assertEqual(ResponseComposer().compose(turn=turn).text,"Сохранённый текст.")
    def test_validated_numeric_category_uses_existing_volume_validation_and_special_template(self):
        class Endpoint:
            def __init__(self): self.levels=[]
            def SetMasterVolumeLevelScalar(self,value,_): self.levels.append(value)
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        class Memory:
            settings={"conversation_timeout":2}; aliases={}; custom_commands={}; learned_routines=[]; skills=[]
        class Router:
            def __init__(self,plugin): self.calls=[]; self.plugin=plugin
            def route(self,command,confirmed=False): self.calls.append(command); return self.plugin.execute(command)
        class AssistantStub:
            def __init__(self,router):
                self.memory=Memory(); self.parser=IntentParser(self.memory); self.router=router; self.dialogue=type("D",(),{"pending":None})(); self.state=AssistantState.READY; self.calls=[]
            def set_state(self,state): self.state=state
            def handle(self,text): self.calls.append(text); return self.router.route(self.parser.parse(text))
        inventory=StructuredResultInventory()
        for level in (0,50,100):
            endpoint=Endpoint(); plugin=VolumePlugin(); plugin._endpoint=lambda:endpoint; router=Router(plugin); assistant=AssistantStub(router); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
            with patch.object(plugin,"execute",wraps=plugin.execute) as execute:
                self.assertTrue(voice.process_transcript(f"Джарвис, поставь громкость {level}").ok)
            execute.assert_called_once(); turn=voice.conversation_turn
            structured=ConversationTurn.create(user_text="x",session_state="READY",intent=turn["intent"],result=Result(True,"Громкость.",turn["action_result"]["metadata"]),timestamp=1)
            self.assertEqual(inventory.classify(structured).category,"validated_numeric"); self.assertEqual(turn["action_result"]["metadata"],{"level":level,"composer_owned_template":"set_volume_success"}); self.assertEqual(endpoint.levels,[level/100]); self.assertEqual(tts.messages,[f"Готово, громкость установлена на {level}%."])
            self.assertEqual(len(assistant.calls),1); self.assertEqual(len(router.calls),1); self.assertEqual(assistant.memory.settings,{"conversation_timeout":2})
        parser=IntentParser(Memory())
        for text in ("поставь громкость -1","поставь громкость 101","поставь громкость много"):
            self.assertEqual(parser.parse(text).intent,"INVALID_VOLUME")
        for metadata in ({"level":-1},{"level":101},{"level":"50"},{"value":50}):
            turn=ConversationTurn.create(user_text="raw input",session_state="READY",intent="SET_VOLUME",result=Result(True,"Сохранённый текст.",metadata),timestamp=1)
            self.assertEqual(inventory.classify(turn).category,"legacy_only"); self.assertEqual(ResponseComposer().compose(turn=turn).text,"Сохранённый текст.")
    def test_legacy_only_audit_preserves_safe_messages_and_blocks_unsafe_payloads(self):
        inventory=StructuredResultInventory(); composer=ResponseComposer()
        def turn(intent,message,metadata=None,pending=None):
            return ConversationTurn.create(user_text="x",session_state="READY",intent=intent,response_type="action_success",result=Result(True,message,metadata or {}),pending_state=pending,timestamp=1)
        cases=(
            ("SEARCH_YOUTUBE","Найдено видео.",{"url":"https://example.com"}),
            ("OPEN_FOUND_FILE","Файл открыт.",{"path":"C:\\private\\report.pdf"}),
            ("OPEN_APPLICATION","Приложение открыто.",{"application":"custom-app"}),
            ("TAKE_SCREENSHOT","Скриншот сохранён.",{"path":"C:\\private\\shot.png"}),
            ("PLAY_PAUSE","Команда мультимедиа отправлена.",{}),
            ("RUN_ROUTINE","Процедура выполнена.",{"routine":"Подготовка"}),
        )
        for intent,message,metadata in cases:
            item=turn(intent,message,metadata); self.assertEqual(inventory.classify(item).category,"legacy_only"); self.assertEqual(composer.compose(turn=item).text,message)
        pending=turn("OPEN_BROWSER","Какой сайт открыть?",{}, {"kind":"browser_clarification","step":"site"})
        confirmation=turn("DELETE_ROUTINE","Подтвердите удаление.",{}, {"kind":"delete_routine","step":"confirm"})
        self.assertEqual(composer.compose(turn=pending).text,"Какой сайт открыть?"); self.assertEqual(composer.compose(turn=confirmation).text,"Подтвердите удаление.")
        for raw in ("Открываю C:\\Users\\danik\\secret.txt", "Открываю /tmp/secret.txt", "Открываю https://example.com", "Открываю file:///secret", "Открываю about:blank", "cmd /c whoami", "RuntimeError: token=secret", "Пароль password=hidden", "<Plugin object at 0x1>"):
            item=turn("OPEN_APPLICATION",raw,{"path":"C:\\secret","token":"hidden"}); self.assertEqual(inventory.classify(item).category,"legacy_only"); self.assertEqual(composer.compose(turn=item).text,"Готово.")
    def test_bounded_response_layer_end_to_end_audit(self):
        policy=ResponsePolicy(); inventory=StructuredResultInventory(); enricher=ResponseEnricher(); composer=ResponseComposer()
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
        tts=Tts()
        class Plugin:
            def __init__(self,result): self.result=result; self.calls=[]
            def execute(self,command): self.calls.append(command); return self.result
        class Router:
            def __init__(self,plugin): self.plugin=plugin; self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return self.plugin.execute(command)
        def routed(intent,result):
            plugin=Plugin(result); router=Router(plugin); routed_result=router.route(Command(intent,{}))
            self.assertEqual(len(router.calls),1); self.assertEqual(len(plugin.calls),1); return routed_result
        def pipeline(name,intent,result,category,expected,response_type,metadata=None,pending=None,reference=None):
            before={"message":result.message,"data":dict(result.data)}
            source=ConversationTurn.create(user_text=name,session_state="READY",intent=intent,result=result,pending_state=pending,reference_context=reference,safe_metadata=metadata,timestamp=1)
            decision=policy.decide(source)
            turn=ConversationTurn.create(user_text=name,session_state="READY",intent=intent,response_type=decision.response_type,result=result,pending_state=pending,reference_context=reference,safe_metadata=metadata,timestamp=1)
            self.assertEqual(turn.intent,intent); self.assertEqual(turn.normalized_text,name.casefold()); self.assertEqual(decision.response_type,response_type); self.assertEqual(inventory.classify(turn).category,category)
            enriched=enricher.enrich(turn); response=composer.compose(turn=turn); tts.say(response.text)
            self.assertEqual(response.text,expected); self.assertEqual(tts.messages[-1],response.text); self.assertEqual({"message":result.message,"data":dict(result.data)},before); self.assertFalse(hasattr(enricher,"router")); self.assertFalse(hasattr(composer,"router")); return enriched,response
        pipeline("browser","OPEN_BROWSER",routed("OPEN_BROWSER",Result(True,"Открываю браузер.",{"composer_owned_template":"open_browser_success"})),"legacy_only","Готово, браузер открыт.","action_success")
        pipeline("volume","SET_VOLUME",routed("SET_VOLUME",Result(True,"Громкость 50%.",{"level":50,"composer_owned_template":"set_volume_success"})),"validated_numeric","Готово, громкость установлена на 50%.","action_success")
        pipeline("youtube","OPEN_YOUTUBE",routed("OPEN_YOUTUBE",Result(True,"Открываю страницу.",{"safe_target":"YouTube","composer_owned_template":"open_youtube_success"})),"safe_target","Готово, YouTube открыт.","action_success")
        pipeline("generic","OPEN_APPLICATION",routed("OPEN_APPLICATION",Result(True,"")),"generic_success","Готово.","action_success")
        pipeline("failure","OPEN_BROWSER",routed("OPEN_BROWSER",Result(False,"RuntimeError: token=hidden")),"generic_failure","Не удалось выполнить запрос.","action_failure")
        pipeline("folder","OPEN_FOLDER",Result(True,"Открываю папку."),"safe_target","Готово, открываю Документы.","action_success",{"safe_target":"Документы"})
        pipeline("numeric","SET_VOLUME",Result(True,"Громкость 50%.",{"level":50}),"validated_numeric","Готово, громкость установлена на 50%.","action_success")
        pipeline("legacy","SEARCH_YOUTUBE",Result(True,"Найдено видео.",{"url":"https://example.com"}),"legacy_only","Найдено видео.","action_success")
        pipeline("pending","OPEN_BROWSER",Result(True,"Какой сайт открыть?"),"legacy_only","Какой сайт открыть?","clarification",pending={"kind":"browser_clarification","step":"site"})
        pipeline("correction","SET_VOLUME",Result(True,"Хорошо, исправляю значение на 70.",{"corrected":True,"level":70}),"validated_numeric","Хорошо, исправляю значение на 70.","correction",pending={"kind":"volume_clarification","step":"level"})
        pipeline("reference","OPEN_YOUTUBE",Result(True,"Повторяю открытие YouTube.",{"reference_replay":True}),"legacy_only","Повторяю открытие YouTube.","reference_replay",reference={"last_safe_action_target":"YouTube"})
        _,response=pipeline("unsafe","OPEN_APPLICATION",Result(True,"Открываю C:\\secret\\token.txt",{"path":"C:\\secret","token":"hidden"}),"legacy_only","Готово.","action_success")
        self.assertNotIn("secret",response.text.casefold())
    def test_conversational_intent_resolver_is_bounded_pure_and_exact_parser_priority(self):
        resolver=ConversationalIntentResolver(); context={"last_action":"OPEN_BROWSER"}; pending={"kind":"browser_clarification","step":"site"}
        self.assertEqual([resolver.resolve(text).intent for text in ("запусти браузер","открой мне браузер","мне нужен браузер")],["OPEN_BROWSER"]*3)
        self.assertEqual(resolver.resolve("запусти ютуб").intent,"OPEN_YOUTUBE")
        self.assertEqual(resolver.resolve("скажи ещё раз").intent,"SESSION_RESPONSE_REPEAT")
        self.assertEqual(resolver.resolve("всё понятно").intent,"SESSION_ACKNOWLEDGEMENT")
        self.assertTrue(resolver.resolve("сделай это снова",session_context=context).reference_to_session)
        self.assertEqual(resolver.resolve("не надо",pending=pending).intent,"CANCEL_PENDING_DIALOGUE")
        self.assertTrue(resolver.resolve("не надо").no_execute)
        volume=resolver.resolve("поставь громкость процентов пятьдесят"); self.assertEqual((volume.intent,volume.requires_clarification),("INCOMPLETE_SET_VOLUME",True))
        exact=Command("OPEN_BROWSER",{}); self.assertEqual(resolver.resolve("не надо",exact=exact,pending=pending).source,"parser")
        self.assertEqual(context,{"last_action":"OPEN_BROWSER"}); self.assertEqual(pending,{"kind":"browser_clarification","step":"site"}); self.assertFalse(hasattr(resolver,"router")); self.assertFalse(hasattr(resolver,"plugins"))
    def test_assistant_conversational_resolver_fallback_uses_existing_flow_once(self):
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"Выполнено.")
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.routines.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory
            resolver=assistant.intent_resolver; original=resolver.resolve; calls=[]
            def spy(*args,**kwargs): calls.append(args[0]); return original(*args,**kwargs)
            resolver.resolve=spy
            self.assertTrue(assistant.handle("открой браузер").ok); self.assertEqual(router.calls[-1].intent,"OPEN_BROWSER"); self.assertEqual(calls,[])
            self.assertTrue(assistant.handle("мне нужен браузер").ok); self.assertEqual(router.calls[-1].intent,"OPEN_BROWSER"); self.assertTrue(calls and all(text=="мне нужен браузер" for text in calls))
            self.assertTrue(assistant.handle("все понятно").ok); self.assertEqual(router.calls[-1].intent,"OPEN_BROWSER")
            assistant.dialogue._set_pending({"kind":"browser_clarification","step":"site"}); self.assertTrue(assistant.handle("я передумал").ok); self.assertIsNone(assistant.dialogue.pending); self.assertEqual(router.calls[-1].intent,"OPEN_BROWSER")
            unknown=assistant.handle("совершенно непонятная фраза"); self.assertTrue(unknown.ok); self.assertEqual(router.calls[-1].intent,"UNKNOWN")
    def test_voice_session_resolver_fallback_uses_existing_lifecycle_once(self):
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"Браузер открыт.")
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.settings["conversation_timeout"]=1; router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.routines.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory
            resolver=assistant.intent_resolver; original=resolver.resolve; resolved=[]
            def spy(*args,**kwargs): resolved.append(args[0]); return original(*args,**kwargs)
            resolver.resolve=spy; tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
            self.assertTrue(voice.process_transcript("Джарвис, открой браузер").ok); self.assertEqual(router.calls[-1].intent,"OPEN_BROWSER"); self.assertEqual(resolved,[])
            self.assertTrue(voice.process_transcript("мне нужен браузер").ok); self.assertEqual(router.calls[-1].intent,"OPEN_BROWSER"); self.assertTrue(resolved)
            before=len(router.calls); self.assertTrue(voice.process_transcript("все понятно").ok); self.assertEqual(len(router.calls),before); self.assertTrue(tts.messages)
            assistant.dialogue._set_pending({"kind":"browser_clarification","step":"site"}); self.assertTrue(voice.process_transcript("я передумал").ok); self.assertIsNone(assistant.dialogue.pending)
            voice.stop(); self.assertIsNone(voice.conversation_context); self.assertTrue(voice.start()); voice.stop(); self.assertFalse(voice.listening)
    def test_conversation_provider_registry_isolated_validated_and_fallback_safe(self):
        class Fake(ConversationProvider):
            def __init__(self,response=None,error=False): self.response=response; self.error=error; self.context=None
            def respond(self,turn,context):
                self.context=context
                if self.error: raise TimeoutError()
                return self.response
        registry=ConversationProviderRegistry(); context={"session_state":"READY","pending_dialogue_state":{"kind":"x"},"token":"hidden","history":["secret"]}
        self.assertFalse(registry.available()); self.assertIsNone(registry.respond({},context,{"OPEN_BROWSER"}))
        with self.assertRaises(TypeError): registry.register(object())
        good=Fake(ProviderResponse("Готово.","OPEN_BROWSER",{},True,"test")); registry.register(good); self.assertTrue(registry.available()); self.assertEqual(registry.respond({},context,{"OPEN_BROWSER"}).intent,"OPEN_BROWSER"); self.assertEqual(good.context,{"session_state":"READY","pending_dialogue_state":{"kind":"x"}}); self.assertEqual(context["token"],"hidden")
        for response in (ProviderResponse("x"*501),ProviderResponse("ok","UNKNOWN"),ProviderResponse("exec command"),ProviderResponse("token=secret")):
            registry.register(Fake(response)); self.assertIsNone(registry.respond({},context,{"OPEN_BROWSER"}))
        registry.register(Fake(error=True)); self.assertIsNone(registry.respond({},context,{"OPEN_BROWSER"})); self.assertFalse(hasattr(registry,"memory")); self.assertFalse(hasattr(registry,"router")); self.assertFalse(hasattr(registry,"plugins"))
    def test_assistant_integrates_only_validated_provider_output(self):
        class Provider(ConversationProvider):
            def __init__(self,response=None,error=None): self.response,self.error,self.context=response,error,None
            def respond(self,turn,context):
                self.context=context
                if self.error: raise self.error
                return self.response
        class Router:
            def __init__(self): self.calls=[]; self.plugin_calls=[]
            def route(self,command,confirmed=False):
                self.calls.append(command); self.plugin_calls.append(command.intent)
                return Result(True,"Браузер открыт." if command.intent=="OPEN_BROWSER" else "Громкость установлена.")
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            assistant=Assistant(); memory=MemoryManager(directory); router=Router()
            assistant.memory=memory; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory; assistant.routines.memory=memory
            assistant.router=router; assistant.skills.router=router; assistant.routines.router=router
            provider=Provider(ProviderResponse("Это безопасный ответ.",conversational=True)); assistant.conversation_providers.register(provider)
            before=list(memory.history); text_result=assistant.handle_with_session_context("неизвестный вопрос",{"session_state":"READY","last_action":"OPEN_BROWSER","token":"hidden","history":["secret"]})
            self.assertTrue(text_result.ok); self.assertEqual(text_result.message,"Это безопасный ответ."); self.assertEqual(router.calls,[]); self.assertEqual(memory.history,before); self.assertEqual(provider.context,{"session_state":"READY","last_action":"OPEN_BROWSER","reference_available":False})
            provider.response=ProviderResponse("Открываю браузер.","OPEN_BROWSER",{})
            browser=assistant.handle("совсем другая фраза"); self.assertTrue(browser.ok); self.assertEqual([item.intent for item in router.calls],["OPEN_BROWSER"]); self.assertEqual(router.plugin_calls,["OPEN_BROWSER"]); self.assertEqual(memory.history,before)
            provider.response=ProviderResponse("Устанавливаю громкость.","SET_VOLUME",{"level":50})
            volume=assistant.handle("ещё одна другая фраза"); self.assertTrue(volume.ok); self.assertEqual(router.calls[-1].parameters,{"level":50}); self.assertEqual(len(router.calls),2)
            provider.response=ProviderResponse("Какую громкость?","SET_VOLUME",{},requires_clarification=True)
            clarification=assistant.handle("ещё один неопределённый запрос"); self.assertTrue(clarification.ok); self.assertEqual(assistant.dialogue.pending["kind"],"volume_clarification"); self.assertEqual(len(router.calls),2); assistant.dialogue.pending=None
            provider.response=ProviderResponse("До свидания.",conversational=True,end_session=True)
            ended=assistant.handle("закончим"); self.assertTrue(ended.data["end_session"]); self.assertEqual(len(router.calls),2)
            for response in (ProviderResponse("x","UNKNOWN",{}),ProviderResponse("x","SET_VOLUME",{"level":101}),ProviderResponse("exec command",conversational=True)):
                provider.response=response; fallback=assistant.handle("непонятно"); self.assertFalse(fallback.ok); self.assertEqual(len(router.calls),2)
            provider.error=TimeoutError(); fallback=assistant.handle("таймаут"); self.assertFalse(fallback.ok); self.assertEqual(len(router.calls),2); provider.error=None; self.assertTrue(assistant.conversation_providers.enable())
            provider.response=ProviderResponse("Голосовой ответ.",conversational=True); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
            spoken=voice.process_transcript("Джарвис, неизвестный голосовой вопрос"); self.assertTrue(spoken.ok); self.assertEqual(tts.messages,["Голосовой ответ."]); self.assertEqual(len(router.calls),2)
            provider.response=ProviderResponse("До свидания.",conversational=True,end_session=True); self.assertTrue(voice.process_transcript("закончим").ok); self.assertIsNone(voice.conversation_context)
            self.assertFalse(hasattr(provider,"router")); self.assertFalse(hasattr(provider,"memory"))
    def test_provider_response_policy_keeps_non_actions_read_only(self):
        class Provider(ConversationProvider):
            def __init__(self,response): self.response,self.context=response,None
            def respond(self,turn,context): self.context=context; return self.response
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"executed")
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            assistant=Assistant(); memory=MemoryManager(directory); router=Router()
            assistant.memory=memory; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory; assistant.routines.memory=memory
            assistant.router=router; assistant.skills.router=router; assistant.routines.router=router
            provider=Provider(ProviderResponse("Это справочная информация.",response_type="informational")); assistant.conversation_providers.register(provider)
            tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
            info=voice.process_transcript("Джарвис, нераспознанный справочный запрос")
            self.assertTrue(info.ok); self.assertEqual(tts.messages,["Это справочная информация."]); self.assertEqual(voice.conversation_turn["response_type"],"informational"); self.assertEqual(voice.conversation_context["last_action"],""); self.assertEqual(router.calls,[]); self.assertEqual(memory.history,[]); self.assertEqual(provider.context["session_state"],"PROCESSING"); self.assertFalse(provider.context["reference_available"]); self.assertNotIn("history",provider.context); self.assertNotIn("token",provider.context)
            provider.response=ProviderResponse("Отменено.","CANCEL_PENDING_DIALOGUE",{})
            cancelled=voice.process_transcript("другая нераспознанная фраза"); self.assertTrue(cancelled.ok); self.assertTrue(cancelled.data["cancelled"]); self.assertEqual(voice.conversation_context["last_action"],""); self.assertEqual(router.calls,[]); self.assertEqual(memory.history,[])
            assistant.dialogue._set_pending({"kind":"browser_clarification","step":"site"}); deterministic=assistant.handle("отмена")
            self.assertTrue(deterministic.ok); self.assertIsNone(assistant.dialogue.pending); self.assertEqual(router.calls,[])
            provider.response=ProviderResponse("x","SET_VOLUME",{"level":101})
            self.assertFalse(voice.process_transcript("ещё одна фраза").ok); self.assertEqual(voice.conversation_context["last_action"],""); self.assertEqual(router.calls,[])
            provider.response=ProviderResponse("x",response_type="unsupported")
            self.assertFalse(assistant.handle("ещё одна фраза").ok); self.assertEqual(router.calls,[])
    def test_conversation_provider_lifecycle_is_optional_and_resettable(self):
        class Provider(ConversationProvider):
            def __init__(self,response=None,error=None): self.response,self.error,self.calls=response,error,0
            def respond(self,turn,context):
                self.calls+=1
                if self.error: raise self.error
                return self.response
        registry=ConversationProviderRegistry(); first=Provider(ProviderResponse("Первый.",conversational=True)); second=Provider(ProviderResponse("Второй.",conversational=True))
        self.assertEqual(registry.state,"UNCONFIGURED"); self.assertFalse(registry.available()); self.assertFalse(registry.enable())
        self.assertTrue(registry.configure(first)); self.assertEqual(registry.state,"DISABLED"); self.assertIsNone(registry.respond({}, {}, set())); self.assertEqual(first.calls,0)
        self.assertTrue(registry.enable()); self.assertEqual(registry.state,"ENABLED"); self.assertEqual(registry.respond({}, {}, set()).text,"Первый."); self.assertEqual(first.calls,1)
        self.assertTrue(registry.disable()); self.assertEqual(registry.state,"DISABLED"); self.assertIsNone(registry.respond({}, {}, set())); self.assertEqual(first.calls,1)
        self.assertTrue(registry.replace(second)); self.assertEqual(registry.state,"ENABLED"); self.assertEqual(registry.respond({}, {}, set()).text,"Второй."); self.assertEqual(first.calls,1); self.assertEqual(second.calls,1)
        failing=Provider(error=RuntimeError("unavailable")); self.assertTrue(registry.replace(failing)); self.assertIsNone(registry.respond({}, {}, set())); self.assertEqual(registry.state,"ERROR"); self.assertFalse(registry.available()); self.assertEqual(failing.calls,1)
        timeout=Provider(error=TimeoutError()); self.assertTrue(registry.replace(timeout)); self.assertIsNone(registry.respond({}, {}, set())); self.assertEqual(registry.state,"ERROR")
        self.assertFalse(registry.configure(object())); self.assertEqual(registry.state,"ERROR")
        registry.replace(second); registry.reset(); self.assertEqual(registry.state,"UNCONFIGURED"); self.assertFalse(registry.available()); self.assertFalse(hasattr(registry,"memory")); self.assertFalse(hasattr(registry,"router"))
        with tempfile.TemporaryDirectory() as directory:
            assistant=Assistant(); memory=MemoryManager(directory); assistant.memory=memory; assistant.parser.memory=memory; assistant.dialogue.memory=memory
            assistant.conversation_providers.replace(first); context={"last_action":"OPEN_BROWSER","last_action_text":"открой браузер"}; assistant.handle_with_session_context("неизвестная фраза",context)
            self.assertEqual(memory.history,[]); assistant.dialogue._set_pending({"kind":"browser_clarification","step":"site"}); assistant.conversation_providers.disable()
            self.assertEqual(assistant.dialogue.pending["kind"],"browser_clarification"); self.assertEqual(context["last_action"],"OPEN_BROWSER")
    def test_provider_adapter_contract_is_bounded_and_transport_free(self):
        class Adapter(ProviderAdapter):
            capabilities=ProviderCapabilities(True,True,True,True,True)
            def __init__(self,response=None,error=None): self.response,self.error,self.turn,self.context=response,error,None,None
            def respond_bounded(self,turn,context):
                self.turn,self.context=turn,context
                if self.error: raise self.error
                return self.response
        turn=ConversationTurn.create(user_text="мой token secret",session_state="READY",intent="UNKNOWN",response_type="unknown",result=Result(False,"raw exception"),pending_state={"kind":"site","token":"hidden"},reference_context={"reference_available":True,"path":"C:\\secret"},timestamp=1,source="voice")
        registry=ConversationProviderRegistry(); stub=StubConversationProvider(); self.assertEqual(stub.capabilities,ProviderCapabilities()); self.assertTrue(registry.replace(stub)); self.assertIsNone(registry.respond(turn,{"token":"hidden"},set()))
        adapter=Adapter(ProviderResponse("Безопасный ответ.",conversational=True)); self.assertTrue(registry.replace(adapter)); response=registry.respond(turn,{"session_state":"READY","pending_dialogue_state":{"kind":"site","password":"hidden"},"token":"hidden","history":["secret"]},set())
        self.assertEqual(response.text,"Безопасный ответ."); self.assertEqual(adapter.capabilities,ProviderCapabilities(True,True,True,True,True)); self.assertNotIn("user_text",adapter.turn); self.assertNotIn("action_result",adapter.turn); self.assertNotIn("normalized_text",adapter.turn); self.assertEqual(adapter.context,{"session_state":"READY","pending_dialogue_state":{"kind":"site"}}); self.assertFalse(hasattr(adapter,"router")); self.assertFalse(hasattr(adapter,"plugins")); self.assertFalse(hasattr(adapter,"memory"))
        adapter.response=ProviderResponse("exec command",conversational=True); self.assertIsNone(registry.respond(turn,{},set())); adapter.error=TimeoutError(); self.assertIsNone(registry.respond(turn,{},set())); self.assertEqual(registry.state,"ERROR")
    def test_openai_provider_definition_is_offline_non_secret_and_bounded(self):
        secret="test-only-not-a-real-key"; turn=ConversationTurn.create(user_text="открой браузер",session_state="READY",intent="UNKNOWN",result=Result(False,"raw exception"),pending_state={"kind":"site","token":"hidden"},reference_context={"reference_available":True,"path":"C:\\secret"},timestamp=1)
        default=OpenAIConversationProvider(); self.assertEqual(default.provider_id,"openai"); self.assertFalse(default.network_enabled); self.assertFalse(default.configured()); self.assertTrue(default.config.valid()); self.assertEqual(OpenAISecretBoundary.read({}),""); self.assertEqual(OpenAISecretBoundary.read({"OPENAI_API_KEY":secret}),secret)
        invalid=OpenAIProviderConfig(enabled=True,model="bad model!",request_timeout=0,max_output_tokens=0); self.assertFalse(invalid.valid())
        provider=OpenAIConversationProvider(OpenAIProviderConfig(enabled=True,model="future-model",request_timeout=20,max_output_tokens=200),secret_reader=lambda:secret)
        self.assertTrue(provider.configured()); dto=provider.build_request(turn,{"session_state":"READY","last_action":"OPEN_BROWSER","pending_dialogue_state":{"kind":"site","password":"hidden"},"token":secret,"history":["secret"],"path":"C:\\secret"})
        serialized=repr(dto); self.assertNotIn(secret,serialized); self.assertNotIn("password",serialized); self.assertNotIn("path",serialized); self.assertNotIn("action_result",serialized); self.assertEqual(dto.provider_id,"openai"); self.assertEqual(dto.model,"future-model"); self.assertEqual(dto.context,{"session_state":"READY","last_action":"OPEN_BROWSER","pending_dialogue_state":{"kind":"site"}}); self.assertNotIn("user_text",dto.turn); self.assertEqual(dto.capabilities,{"supports_text":True,"supports_structured_intent":True,"supports_clarification":True,"supports_context":True,"supports_end_session":True})
        with patch("socket.create_connection",side_effect=AssertionError("network must remain unused")): self.assertIsNone(provider.respond(turn,{}))
        self.assertFalse(hasattr(provider,"router")); self.assertFalse(hasattr(provider,"plugins")); self.assertFalse(hasattr(provider,"memory")); self.assertIsNone(OpenAIConversationProvider.map_response({"unexpected":"value"})); mapped=OpenAIConversationProvider.map_response({"text":"Открываю браузер.","intent":"OPEN_BROWSER","parameters":{}}); self.assertIsInstance(mapped,ProviderResponse); self.assertIsNone(ConversationProviderRegistry().validate(ProviderResponse("x","UNKNOWN"),{"OPEN_BROWSER"}))
    def test_openai_provider_requires_explicit_opt_in_and_stays_offline(self):
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(False,"Я не понял команду.")
        class Client:
            def __init__(self): self.responses=self; self.calls=[]
            def create(self,**kwargs): self.calls.append(kwargs); return {"output_text":"{\"text\": \"Ответ.\", \"conversational\": true}"}
        secret="fake-test-secret"; config=OpenAIProviderConfig(model="future-model",request_timeout=20,max_output_tokens=200)
        with tempfile.TemporaryDirectory() as directory, patch("socket.create_connection",side_effect=AssertionError("network must remain unused")):
            assistant=Assistant(); memory=MemoryManager(directory); router=Router(); assistant.memory=memory; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.routines.router=router
            self.assertEqual(assistant.openai_provider_status(),{"provider_id":"openai","state":"UNCONFIGURED","mode":"deterministic","network_enabled":False})
            self.assertFalse(assistant.conversation_providers.available()); self.assertEqual(assistant.handle("неизвестная команда").message,"Я не понял команду."); self.assertEqual(len(router.calls),1)
            missing=assistant.enable_openai_provider(config,secret_reader=lambda:""); self.assertFalse(missing.ok); self.assertEqual(assistant.openai_provider_status()["state"],"UNCONFIGURED")
            self.assertFalse(assistant.enable_openai_provider(OpenAIProviderConfig(model="bad model!"),secret_reader=lambda:secret).ok)
            client=Client(); enabled=assistant.enable_openai_provider(config,secret_reader=lambda:secret,client_factory=lambda *_:client); self.assertTrue(enabled.ok); self.assertEqual(assistant.openai_provider_status(),{"provider_id":"openai","state":"ENABLED","mode":"provider","network_enabled":True}); self.assertTrue(assistant.conversation_providers.provider.network_enabled)
            before=len(router.calls); self.assertTrue(assistant.handle("другая неизвестная команда").ok); self.assertEqual(len(router.calls),before); self.assertEqual(len(client.calls),1); self.assertNotIn(secret,repr(enabled.data)); self.assertNotIn(secret,repr(memory.settings)); self.assertNotIn(secret,repr(assistant.openai_provider_status()))
            reference={"last_action":"OPEN_BROWSER","last_action_text":"открой браузер"}; assistant.dialogue._set_pending({"kind":"browser_clarification","step":"site"}); disabled=assistant.disable_openai_provider(); self.assertTrue(disabled.ok); self.assertEqual(assistant.openai_provider_status()["state"],"DISABLED"); self.assertEqual(assistant.dialogue.pending["kind"],"browser_clarification"); self.assertEqual(reference["last_action"],"OPEN_BROWSER")
            assistant.dialogue.pending=None; self.assertFalse(assistant.handle("снова неизвестная команда").ok); self.assertEqual(len(router.calls),before+1); self.assertTrue(assistant.enable_openai_provider(config,secret_reader=lambda:secret,client_factory=lambda *_:client).ok); self.assertEqual(assistant.openai_provider_status()["state"],"ENABLED")
    def test_openai_responses_transport_is_mocked_bounded_and_routes_only_via_assistant(self):
        class Client:
            def __init__(self,output=None,error=None): self.responses=self; self.output,self.error,self.calls,self.closed=output,error,[],False
            def create(self,**kwargs):
                self.calls.append(kwargs)
                if self.error: raise self.error
                return {"output_text":self.output}
            def close(self): self.closed=True
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"Браузер открыт.")
        secret="fake-test-secret"; config=OpenAIProviderConfig(enabled=True,model="future-model",request_timeout=12,max_output_tokens=123)
        turn=ConversationTurn.create(user_text="открой браузер",session_state="READY",intent="UNKNOWN",result=Result(False,"raw exception",{"token":secret}),pending_state={"kind":"site","password":"hidden"},reference_context={"path":"C:\\secret","reference_available":True},timestamp=1)
        client=Client('{"text":"Короткий ответ.","conversational":true}'); provider=OpenAIConversationProvider(config,secret_reader=lambda:secret,client_factory=lambda *_:client); self.assertTrue(provider.activate_transport())
        registry=ConversationProviderRegistry(); registry.replace(provider); response=registry.respond(turn,{"session_state":"READY","token":secret,"history":["private"]},set())
        self.assertEqual(response.text,"Короткий ответ."); self.assertEqual(len(client.calls),1); self.assertTrue(client.closed); request=client.calls[0]; payload=request["input"]; self.assertNotIn(secret,payload); self.assertNotIn("token",payload); self.assertNotIn("password",payload); self.assertNotIn("path",payload); self.assertNotIn("tools",request); self.assertEqual(request["model"],"future-model"); self.assertEqual(request["max_output_tokens"],123)
        invalid=Client("not json"); provider=OpenAIConversationProvider(config,secret_reader=lambda:secret,client_factory=lambda *_:invalid); provider.activate_transport(); registry.replace(provider); self.assertIsNone(registry.respond(turn,{},set())); self.assertEqual(registry.state,"ERROR")
        timeout=Client(error=TimeoutError()); provider=OpenAIConversationProvider(config,secret_reader=lambda:secret,client_factory=lambda *_:timeout); provider.activate_transport(); registry.replace(provider); self.assertIsNone(registry.respond(turn,{},set())); self.assertEqual(registry.state,"ERROR")
        recovered=Client('{"text":"Восстановлено.","conversational":true}'); provider=OpenAIConversationProvider(config,secret_reader=lambda:secret,client_factory=lambda *_:recovered); provider.activate_transport(); self.assertTrue(registry.replace(provider)); self.assertEqual(registry.respond(turn,{},set()).text,"Восстановлено."); self.assertEqual(registry.state,"ENABLED")
        with tempfile.TemporaryDirectory() as directory:
            assistant=Assistant(); memory=MemoryManager(directory); router=Router(); assistant.memory=memory; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.routines.router=router
            structured=Client('{"text":"Открываю браузер.","intent":"OPEN_BROWSER","parameters":{}}'); self.assertTrue(assistant.enable_openai_provider(config,secret_reader=lambda:secret,client_factory=lambda *_:structured).ok)
            result=assistant.handle("неизвестная фраза"); self.assertTrue(result.ok); self.assertEqual([call.intent for call in router.calls],["OPEN_BROWSER"]); self.assertFalse(hasattr(assistant.conversation_providers.provider,"router")); self.assertEqual(memory.history,[])
    def test_ollama_local_provider_is_bounded_mocked_and_router_safe(self):
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"Браузер открыт.")
        turn=ConversationTurn.create(user_text="открой браузер",session_state="READY",intent="UNKNOWN",result=Result(False,"raw exception",{"token":"hidden"}),pending_state={"kind":"site","password":"hidden"},reference_context={"path":"C:\\secret","reference_available":True},timestamp=1)
        calls=[]
        def transport(endpoint,payload,timeout):
            calls.append((endpoint,payload,timeout)); return '{"message":{"content":"{\\"text\\":\\"Локальный ответ.\\",\\"conversational\\":true}"}}'
        config=OllamaProviderConfig(enabled=True,model="qwen3:8b"); provider=OllamaConversationProvider(config,transport=transport); self.assertTrue(provider.activate_transport())
        registry=ConversationProviderRegistry(); registry.replace(provider); response=registry.respond(turn,{"session_state":"READY","token":"hidden","history":["private"]},set())
        self.assertEqual(response.text,"Локальный ответ."); self.assertEqual(calls[0][0],"http://localhost:11434"); self.assertEqual(calls[0][2],45.0); prompt=calls[0][1]["messages"][1]["content"]; self.assertNotIn("token",prompt); self.assertNotIn("password",prompt); self.assertNotIn("path",prompt); self.assertFalse(calls[0][1]["think"]); self.assertEqual({branch["properties"]["mode"]["const"] for branch in calls[0][1]["format"]["oneOf"]},{"information","goal","action","conversation"}); self.assertNotIn("tools",calls[0][1]); self.assertFalse(hasattr(provider,"router")); self.assertFalse(hasattr(provider,"plugins")); self.assertFalse(hasattr(provider,"memory"))
        self.assertFalse(OllamaProviderConfig(enabled=True,model="bad model!",endpoint="http://localhost:11434").valid()); self.assertFalse(OllamaProviderConfig(enabled=True,endpoint="https://api.openai.com").valid()); self.assertFalse(OllamaProviderConfig(enabled=True,endpoint="http://example.com").valid())
        malformed=OllamaConversationProvider(config,transport=lambda *_:"not json"); malformed.activate_transport(); registry.replace(malformed); self.assertIsNone(registry.respond(turn,{},set())); self.assertEqual(registry.state,"ERROR")
        unavailable=OllamaConversationProvider(config,transport=lambda *_:(_ for _ in ()).throw(TimeoutError())); unavailable.activate_transport(); registry.replace(unavailable); self.assertIsNone(registry.respond(turn,{},set())); self.assertEqual(registry.state,"ERROR")
        with tempfile.TemporaryDirectory() as directory:
            assistant=Assistant(); memory=MemoryManager(directory); router=Router(); assistant.memory=memory; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.routines.router=router
            structured=lambda *_:'{"message":{"content":"{\\"text\\":\\"Открываю браузер.\\",\\"intent\\":\\"OPEN_BROWSER\\",\\"parameters\\":{}}"}}'; self.assertTrue(assistant.enable_ollama_provider(config,transport=structured).ok)
            result=assistant.handle("неизвестная фраза"); self.assertTrue(result.ok); self.assertEqual([item.intent for item in router.calls],["OPEN_BROWSER"]); self.assertEqual(memory.history,[]); self.assertTrue(assistant.disable_ollama_provider().ok); self.assertEqual(assistant.ollama_provider_status()["state"],"DISABLED")
    def test_ollama_qwen3_envelope_normalization_keeps_validator_strict(self):
        fixture={"message":{"role":"assistant","thinking":"internal reasoning must not reach TTS","content":"{\n  \"text\": \"Я Qwen, локальная языковая модель.\",\n  \"conversational\": true\n}"},"done":True,"model":"qwen3:8b"}
        provider=OllamaConversationProvider(OllamaProviderConfig(enabled=True),transport=lambda *_:fixture); self.assertTrue(provider.activate_transport())
        turn=ConversationTurn.create(user_text="кто ты",session_state="READY",intent="UNKNOWN",result=Result(False,""),timestamp=1)
        response=provider.respond(turn,{}); self.assertEqual(response.text,"Я Qwen, локальная языковая модель."); self.assertTrue(response.conversational); self.assertIsNone(OllamaConversationProvider.map_response({"thinking":"only reasoning"})); self.assertIsNone(OllamaConversationProvider.map_response({"text":"","reasoning":"only reasoning"}))
        nulls=OllamaConversationProvider.map_response({"text":"Короткий ответ.","intent":None,"parameters":None,"response_type":None}); self.assertEqual(nulls.intent,""); self.assertEqual(nulls.parameters,{})
        unsafe=OllamaConversationProvider.map_response({"text":"exec command","conversational":True}); self.assertIsNotNone(unsafe); self.assertIsNone(ConversationProviderRegistry().validate(unsafe,set())); self.assertFalse(hasattr(provider,"router")); self.assertFalse(hasattr(provider,"plugins")); self.assertFalse(hasattr(provider,"memory"))
    def test_ollama_information_json_wrapper_normalizes_one_object_and_keeps_validation_strict(self):
        content="""```json
        {"mode":"information","text":"","intent":"INFORMATION_REQUEST","parameters":{"operation":"GET_INFORMATION","category":"web_search","query":"искусственный интеллект"},"requires_clarification":false,"end_session":false}
        ```"""
        fixture={"message":{"thinking":"hidden","content":content}}
        provider=OllamaConversationProvider(OllamaProviderConfig(enabled=True),transport=lambda *_:fixture); self.assertTrue(provider.activate_transport())
        turn=ConversationTurn.create(user_text="дай информацию",session_state="READY",intent="UNKNOWN",result=Result(False,""),timestamp=1)
        response=provider.respond(turn,{})
        self.assertEqual((response.intent,response.information_category,response.information_query),("INFORMATION_REQUEST","web_search","искусственный интеллект"))
        registry=ConversationProviderRegistry(); self.assertIs(registry.validate(response,{"INFORMATION_REQUEST"}),response)
        diagnostic=provider.last_response_diagnostic
        self.assertEqual(diagnostic["normalization_status"],"accepted"); self.assertIn("intent",diagnostic["content_preview"]); self.assertNotIn("INFORMATION_REQUEST",diagnostic["content_preview"])
        schema=provider._response_schema(); information=[branch for branch in schema["oneOf"] if branch["properties"]["mode"]["const"]=="information"]
        self.assertEqual(len(information),3); self.assertTrue(all("goal" not in branch["properties"] for branch in information))
        web=next(branch for branch in information if branch["properties"]["parameters"]["properties"]["category"]["const"]=="web_search")
        self.assertEqual(web["properties"]["parameters"]["required"],["operation","category","query"])
        incomplete=ProviderResponse("","INFORMATION_REQUEST",{})
        self.assertIsNone(registry.validate(incomplete,{"INFORMATION_REQUEST"}))
        self.assertEqual(registry.last_validation_diagnostic["failure_category"],"invalid_information_shape")
        self.assertIsNone(OllamaConversationProvider._extract_json_object('{"text":"one"} {"text":"two"}'))
        self.assertIsNone(OllamaConversationProvider._extract_json_object("not structured"))
    def test_ollama_information_and_capability_modes_are_contractually_exclusive(self):
        registry=ConversationProviderRegistry(); allowed={"INFORMATION_REQUEST"}
        valid=ProviderResponse("","INFORMATION_REQUEST",{},information_category="web_search",information_query="искусственный интеллект")
        self.assertIs(registry.validate(valid,allowed),valid)
        mixed=ProviderResponse("","INFORMATION_REQUEST",{},goal="capability",information_category="web_search",information_query="искусственный интеллект")
        self.assertIsNone(registry.validate(mixed,allowed)); self.assertEqual(registry.last_validation_diagnostic["failure_category"],"invalid_goal_shape")
        provider=OllamaConversationProvider(OllamaProviderConfig(enabled=True)); provider.set_allowed_intents(allowed)
        instructions=provider._instructions(); self.assertIn("Information requests use mode information and never contain goal",instructions)
        branches=provider._response_schema()["oneOf"]
        information=[branch for branch in branches if branch["properties"]["mode"]["const"]=="information"]
        goal=next(branch for branch in branches if branch["properties"]["mode"]["const"]=="goal")
        self.assertTrue(all("goal" not in branch["properties"] for branch in information)); self.assertEqual(goal["properties"]["intent"],{"type":"null"})
    def test_ollama_conversion_does_not_materialize_capability_goal(self):
        registry=ConversationProviderRegistry(); allowed={"INFORMATION_REQUEST"}
        information={"text":"","intent":"INFORMATION_REQUEST","parameters":{},"requires_clarification":False,"end_session":False,"information_category":"web_search","information_query":"безопасная тема"}
        mapped=OllamaConversationProvider.map_response(information)
        self.assertEqual(mapped.goal,""); self.assertIs(registry.validate(mapped,allowed),mapped)
        mixed=dict(information,goal="capability")
        mixed_mapped=OllamaConversationProvider.map_response(mixed)
        self.assertEqual(mixed_mapped.goal,"capability"); self.assertIsNone(registry.validate(mixed_mapped,allowed)); self.assertEqual(registry.last_validation_diagnostic["failure_category"],"invalid_goal_shape")
        capability=OllamaConversationProvider.map_response({"text":"Проверю доступные возможности.","intent":None,"parameters":{},"requires_clarification":False,"end_session":False,"goal":"capability"})
        self.assertEqual((capability.intent,capability.goal),("","capability")); self.assertIs(registry.validate(capability,allowed),capability)
    def test_ollama_mode_information_converts_to_existing_provider_response(self):
        payload={"mode":"information","text":"","intent":"INFORMATION_REQUEST","parameters":{"operation":"GET_INFORMATION","category":"web_search","query":"безопасная тема"},"requires_clarification":False,"end_session":False}
        response,status=OllamaConversationProvider._map_response_details(payload,require_mode=True)
        self.assertEqual(status,"accepted"); self.assertEqual((response.intent,response.parameters,response.goal,response.information_category,response.information_query),("INFORMATION_REQUEST",{},"","web_search","безопасная тема")); self.assertIs(ConversationProviderRegistry().validate(response,{"INFORMATION_REQUEST"}),response)
    def test_ollama_mode_goal_converts_to_existing_capability_response(self):
        payload={"mode":"goal","text":"Проверю возможности.","intent":None,"goal":"capability","parameters":{},"requires_clarification":False,"end_session":False}
        response,status=OllamaConversationProvider._map_response_details(payload,require_mode=True)
        self.assertEqual(status,"accepted"); self.assertEqual((response.intent,response.goal,response.parameters),("","capability",{})); self.assertIs(ConversationProviderRegistry().validate(response,{"INFORMATION_REQUEST"}),response)
    def test_ollama_mode_information_with_goal_is_rejected_before_conversion(self):
        payload={"mode":"information","text":"","intent":"INFORMATION_REQUEST","goal":"capability","parameters":{"operation":"GET_INFORMATION","category":"web_search","query":"тема"},"requires_clarification":False,"end_session":False}
        self.assertEqual(OllamaConversationProvider._map_response_details(payload,require_mode=True),(None,"mode_payload_mismatch"))
    def test_ollama_mode_goal_with_information_parameters_is_rejected_before_conversion(self):
        payload={"mode":"goal","text":"","intent":None,"goal":"capability","parameters":{"operation":"GET_INFORMATION","category":"web_search","query":"тема"},"requires_clarification":False,"end_session":False}
        self.assertEqual(OllamaConversationProvider._map_response_details(payload,require_mode=True),(None,"mode_payload_mismatch"))
    def test_ollama_unknown_mode_is_rejected_before_conversion(self):
        self.assertEqual(OllamaConversationProvider._map_response_details({"mode":"other"},require_mode=True),(None,"unknown_mode"))
    def test_ollama_mode_action_preserves_existing_action_contract(self):
        payload={"mode":"action","text":"Открываю браузер.","intent":"OPEN_BROWSER","parameters":{},"requires_clarification":False,"end_session":False}
        response,status=OllamaConversationProvider._map_response_details(payload,require_mode=True)
        self.assertEqual(status,"accepted"); self.assertEqual(response.intent,"OPEN_BROWSER"); self.assertIs(ConversationProviderRegistry().validate(response,{"OPEN_BROWSER"}),response)
    def test_ollama_information_priority_contract_contrasts_topic_requests_and_social_turns(self):
        provider=OllamaConversationProvider(OllamaProviderConfig(enabled=True)); provider.set_allowed_intents({"INFORMATION_REQUEST"})
        instructions=provider._instructions()
        for phrase in ("Дай мне информацию по теме искусственного интеллекта простыми словами","Расскажи мне о принципах работы искусственного интеллекта","Что известно об искусственном интеллекте?"):
            self.assertIn(phrase,instructions)
        for phrase in ("Привет, Джарвис","Как у тебя дела?","Спасибо"):
            self.assertIn(phrase,instructions)
        self.assertIn("semantic priority: information, then action, then goal, then conversation",instructions)
        self.assertIn("Do not use conversation merely because a user asks a question",instructions)
        branches=provider._response_schema()["oneOf"]
        information=[branch for branch in branches if branch["properties"]["mode"]["const"]=="information"]
        conversation=next(branch for branch in branches if branch["properties"]["mode"]["const"]=="conversation")
        self.assertTrue(all("Information retrieval only" in branch["description"] for branch in information)); self.assertIn("not topic information requests",conversation["description"])
    def test_ollama_information_action_boundary_contract_keeps_execution_and_validation_strict(self):
        provider=OllamaConversationProvider(OllamaProviderConfig(enabled=True)); provider.set_allowed_intents({"INFORMATION_REQUEST","OPEN_BROWSER","OPEN_YOUTUBE","SET_VOLUME"})
        instructions=provider._instructions()
        for phrase in ("Дай мне информацию по теме искусственного интеллекта простыми словами.","Расскажи мне о принципах работы искусственного интеллекта.","Что известно об искусственном интеллекте?","Объясни, что такое искусственный интеллект."):
            self.assertIn(phrase.rstrip("."),instructions)
        for phrase in ("Открой браузер","Открой YouTube","Убавь громкость до 50 процентов","Сделай скриншот"):
            self.assertIn(phrase,instructions)
        self.assertIn("Information has priority over every action",instructions); self.assertIn("Mode action is only for an allow-listed computer execution request",instructions)
        branches=provider._response_schema()["oneOf"]
        action=next(branch for branch in branches if branch["properties"]["mode"]["const"]=="action")
        self.assertIn("never use for factual information",action["description"])
        information={"mode":"information","text":"","intent":"INFORMATION_REQUEST","parameters":{"operation":"GET_INFORMATION","category":"web_search","query":"искусственный интеллект"},"requires_clarification":False,"end_session":False}
        response,status=OllamaConversationProvider._map_response_details(information,require_mode=True)
        self.assertEqual(status,"accepted"); self.assertIs(ConversationProviderRegistry().validate(response,{"INFORMATION_REQUEST","OPEN_BROWSER"}),response)
        long_information=dict(information,text="x"*501)
        normalized,status=OllamaConversationProvider._map_response_details(long_information,require_mode=True)
        self.assertEqual((status,normalized.text),("accepted","")); self.assertIs(ConversationProviderRegistry().validate(normalized,{"INFORMATION_REQUEST"}),normalized)
        invalid=ProviderResponse("","OPEN_BROWSER",{"operation":"GET_INFORMATION","category":"web_search","query":"искусственный интеллект"})
        registry=ConversationProviderRegistry(); self.assertIsNone(registry.validate(invalid,{"OPEN_BROWSER"})); self.assertEqual(registry.last_validation_diagnostic["failure_category"],"intent_parameter_mismatch")
    def test_provider_validation_diagnostic_is_structural_and_authoritative(self):
        registry=ConversationProviderRegistry()
        cases=[
            (ProviderResponse("x"*501),"text_too_long","text"),
            (ProviderResponse("safe","UNKNOWN"),"unknown_intent","intent"),
            (ProviderResponse("exec command"),"forbidden_pattern","text"),
        ]
        for response,reason,field in cases:
            with self.subTest(reason=reason):
                self.assertIsNone(registry.validate(response,{"OPEN_BROWSER"}))
                diagnostic=registry.last_validation_diagnostic
                self.assertFalse(diagnostic["accepted"]); self.assertEqual(diagnostic["failure_category"],reason); self.assertIn(field,diagnostic["invalid_fields"])
                self.assertNotIn(response.text,repr(diagnostic)); self.assertNotIn("UNKNOWN",repr(diagnostic))
        self.assertIsNone(registry.validate({"text":"private user text","token":"secret"},{"OPEN_BROWSER"}))
        malformed=registry.last_validation_diagnostic
        self.assertEqual(malformed["failure_category"],"malformed_schema"); self.assertNotIn("private user text",repr(malformed)); self.assertNotIn("secret",repr(malformed))
        self.assertIsNone(registry.validate(ProviderResponse("safe",parameters=[]),set()))
        self.assertEqual(registry.last_validation_diagnostic["failure_category"],"invalid_parameter_shape")
        accepted=registry.validate(ProviderResponse("safe",conversational=True),set())
        self.assertIsNotNone(accepted); self.assertTrue(registry.last_validation_diagnostic["accepted"])
    def test_ollama_rejection_shape_diagnostic_excludes_payload_and_thinking(self):
        model_text="private model answer"; thinking="private chain of thought"; user_text="private user transcript"; secret="test-secret-value"
        fixture={"message":{"role":"assistant","thinking":thinking,"content":json.dumps({"text":model_text,"conversational":True,"parameters":{"token":secret}})},"token":secret}
        provider=OllamaConversationProvider(OllamaProviderConfig(enabled=True),transport=lambda *_:fixture); self.assertTrue(provider.activate_transport())
        turn=ConversationTurn.create(user_text=user_text,session_state="READY",intent="UNKNOWN",result=Result(False,""),timestamp=1)
        response=provider.respond(turn,{})
        diagnostic=provider.last_response_diagnostic
        self.assertEqual(response.text,model_text); self.assertTrue(diagnostic["thinking_present"]); self.assertEqual(diagnostic["thinking_length"],len(thinking)); self.assertEqual(diagnostic["normalization_status"],"accepted")
        rendered=repr(diagnostic)
        for forbidden in (model_text,thinking,user_text,secret,"token"):
            self.assertNotIn(forbidden,rendered)
        registry=ConversationProviderRegistry(); self.assertIsNotNone(registry.validate(response,set()))
        self.assertTrue(registry.last_validation_diagnostic["sensitive_parameter_keys_present"]); self.assertNotIn("token",repr(registry.last_validation_diagnostic))
    def test_ollama_intent_contract_uses_registry_allow_list_without_relaxing_validation(self):
        captured=[]
        def transport(_endpoint,payload,_timeout):
            captured.append(payload)
            return '{"message":{"thinking":"hidden","content":"{\\"text\\":\\"Короткий ответ.\\",\\"intent\\":null,\\"parameters\\":{},\\"requires_clarification\\":false,\\"end_session\\":false}"}}'
        provider=OllamaConversationProvider(OllamaProviderConfig(enabled=True),transport=transport); self.assertTrue(provider.activate_transport())
        registry=ConversationProviderRegistry(); registry.replace(provider)
        response=registry.respond({}, {}, {"OPEN_BROWSER","SET_VOLUME"})
        self.assertEqual(response.intent,""); self.assertTrue(response.text); self.assertTrue(registry.last_validation_diagnostic["accepted"])
        schema=captured[0]["format"]; action=next(branch for branch in schema["oneOf"] if branch["properties"]["mode"]["const"]=="action"); self.assertEqual(action["properties"]["intent"]["enum"],["OPEN_BROWSER","SET_VOLUME"]); self.assertIn("OPEN_BROWSER",captured[0]["messages"][0]["content"])
        for fake in ("open browser","browser_tool","UNKNOWN"):
            self.assertIsNone(registry.validate(ProviderResponse("safe",fake),{"OPEN_BROWSER","SET_VOLUME"}))
            self.assertEqual(registry.last_validation_diagnostic["failure_category"],"unknown_intent")
        self.assertIsNone(registry.validate(ProviderResponse("safe","SET_VOLUME",parameters=[]),{"OPEN_BROWSER","SET_VOLUME"}))
        self.assertEqual(registry.last_validation_diagnostic["failure_category"],"invalid_parameter_shape")
        self.assertIsNotNone(registry.validate(ProviderResponse("Какую громкость?","SET_VOLUME",{},requires_clarification=True),{"SET_VOLUME"}))
        self.assertIsNotNone(registry.validate(ProviderResponse("До свидания.",end_session=True),set()))
        self.assertFalse(hasattr(provider,"router")); self.assertFalse(hasattr(provider,"plugins")); self.assertFalse(hasattr(provider,"memory"))
    def test_ollama_unknown_phrase_routes_one_validated_open_browser_action_once(self):
        class Plugins:
            def __init__(self): self.calls=[]; self.launches=[]; self.browser=BrowserPlugin(launcher=self.launches.append); self.browser._browser_executable=lambda:"C:\\Test\\chrome.exe"
            def execute(self,plugin_id,command): self.calls.append((plugin_id,command)); return self.browser.execute(command)
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text); return True
            def is_speaking(self): return False
        phrase="Мне нужен доступ к сайтам"
        with tempfile.TemporaryDirectory() as directory:
            assistant=Assistant(); memory=MemoryManager(directory); assistant.memory=memory; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory; assistant.routines.memory=memory
            exact=assistant.parser.parse(phrase); resolved=assistant.intent_resolver.resolve(phrase,exact=exact,pending=None,session_context=None)
            self.assertEqual(exact.intent,"UNKNOWN"); self.assertEqual(resolved.intent,"UNKNOWN")
            plugins=Plugins(); assistant.registry.register("OPEN_BROWSER","browser"); assistant.plugins=plugins; assistant.router=CommandRouter(assistant.registry,plugins,PermissionManager(),assistant.skills,assistant.events); assistant.skills.router=assistant.router; assistant.routines.router=assistant.router
            provider_calls=[]
            def transport(_endpoint,_payload,_timeout):
                provider_calls.append(True); return '{"message":{"content":"{\\"text\\":\\"Открываю браузер.\\",\\"intent\\":\\"OPEN_BROWSER\\",\\"parameters\\":{},\\"requires_clarification\\":false,\\"end_session\\":false}"}}'
            self.assertTrue(assistant.enable_ollama_provider(OllamaProviderConfig(enabled=True),transport=transport).ok)
            tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); result=voice.process_transcript("Джарвис, "+phrase)
            self.assertTrue(result.ok); self.assertEqual(len(provider_calls),1); self.assertTrue(assistant.conversation_providers.last_validation_diagnostic["accepted"])
            self.assertEqual(len(plugins.calls),1); self.assertEqual(plugins.calls[0][0],"browser"); self.assertEqual(plugins.calls[0][1].intent,"OPEN_BROWSER"); self.assertEqual(plugins.launches,["C:\\Test\\chrome.exe"]); self.assertEqual(tts.messages,["Открываю браузер."]); self.assertEqual(memory.history,[])
            self.assertIsNone(ConversationProviderRegistry().validate(ProviderResponse("x","browser_tool"),{"OPEN_BROWSER"}))
    def test_ollama_semantic_action_contract_keeps_null_for_non_actions(self):
        examples={"Мне нужен доступ к сайтам":("OPEN_BROWSER",{}),"Открой браузер":("OPEN_BROWSER",{}),"Убавь до пятидесяти процентов":("SET_VOLUME",{"level":50}),"Сделай громкость 50 процентов":("SET_VOLUME",{"level":50}),"Отмени это":("CANCEL_PENDING_DIALOGUE",{}),"Не надо":("CANCEL_PENDING_DIALOGUE",{}),"Кто ты?":("",{}),"Неизвестная просьба":("",{})}; expected_by_normalized={key.lower():value for key,value in examples.items()}; captured=[]
        def transport(_endpoint,payload,_timeout):
            captured.append(payload); source=json.loads(payload["messages"][1]["content"])["turn"]["normalized_text"]; intent,parameters=expected_by_normalized[source]
            response={"text":"Короткий ответ.","intent":intent or None,"parameters":parameters,"requires_clarification":False,"end_session":False}
            return {"message":{"content":json.dumps(response)}}
        provider=OllamaConversationProvider(OllamaProviderConfig(enabled=True),transport=transport); self.assertTrue(provider.activate_transport())
        registry=ConversationProviderRegistry(); registry.replace(provider)
        allowed={"OPEN_BROWSER","SET_VOLUME","CANCEL_PENDING_DIALOGUE"}
        for phrase,(expected,parameters) in examples.items():
            with self.subTest(phrase=phrase):
                turn=ConversationTurn.create(user_text=phrase,session_state="READY",intent="UNKNOWN",result=Result(False,""),timestamp=1)
                response=registry.respond(turn,{},allowed); self.assertEqual(response.intent,expected); self.assertEqual(response.parameters,parameters); self.assertTrue(registry.last_validation_diagnostic["accepted"])
        prompt=captured[0]["messages"][0]["content"]
        for phrase in ("Мне нужен доступ к сайтам","Открой сайт","Зайди в браузер","Открой браузер","Запусти браузер"):
            self.assertIn(phrase,prompt)
        for phrase in ("Убавь до 50%","Сделай громкость 50%","Отмени это","Не надо, отмена"):
            self.assertIn(phrase,prompt)
        self.assertIn("numeric requested volume level takes priority",prompt); self.assertIn("nearest available allow-listed intent",prompt); self.assertFalse(hasattr(provider,"router")); self.assertFalse(hasattr(provider,"plugins")); self.assertFalse(hasattr(provider,"memory"))
    def test_ollama_information_contract_distinguishes_questions_from_incomplete_sites(self):
        examples={
            "Кто ты и что ты умеешь?":("",{},False),"Кто ты?":("",{},False),"Что ты умеешь?":("",{},False),"Расскажи о себе":("",{},False),
            "Открой сайт":("INCOMPLETE_OPEN_SITE",{},True),"Мне нужно открыть сайт":("INCOMPLETE_OPEN_SITE",{},True),"Зайди на сайт":("INCOMPLETE_OPEN_SITE",{},True),
            "Открой YouTube":("OPEN_YOUTUBE",{},False),"Открой браузер":("OPEN_BROWSER",{},False)}
        expected_by_text={key.casefold():value for key,value in examples.items()}; captured=[]
        def transport(_endpoint,payload,_timeout):
            captured.append(payload); text=json.loads(payload["messages"][1]["content"])["turn"]["normalized_text"]
            intent,parameters,clarification=expected_by_text[text]
            return {"message":{"content":json.dumps({"text":"Короткий безопасный ответ.","intent":intent or None,"parameters":parameters,"requires_clarification":clarification,"end_session":False})}}
        provider=OllamaConversationProvider(OllamaProviderConfig(enabled=True),transport=transport); self.assertTrue(provider.activate_transport())
        registry=ConversationProviderRegistry(); registry.replace(provider)
        allowed={"OPEN_BROWSER","OPEN_YOUTUBE","INCOMPLETE_OPEN_SITE"}
        for phrase,(intent,parameters,clarification) in examples.items():
            with self.subTest(phrase=phrase):
                turn=ConversationTurn.create(user_text=phrase,session_state="READY",intent="UNKNOWN",result=Result(False,""),timestamp=1)
                response=registry.respond(turn,{},allowed)
                self.assertEqual((response.intent,response.parameters,response.requires_clarification),(intent,parameters,clarification)); self.assertTrue(registry.last_validation_diagnostic["accepted"])
        self.assertIsNone(registry.validate(ProviderResponse("safe","INVALID_INTENT",{}),allowed))
        prompt=captured[0]["messages"][0]["content"]
        for phrase in ("Кто ты?","Что ты умеешь?","Расскажи о себе","Открой сайт","Мне нужно открыть сайт","Зайди на сайт"):
            self.assertIn(phrase,prompt)
        turn=ConversationTurn.create(user_text="Кто ты?",session_state="READY",intent="UNKNOWN",result=Result(True,"Безопасный ответ.",{"provider_response_type":"conversational"}),timestamp=1)
        self.assertEqual(ResponsePolicy().decide(turn).response_type,"conversational"); self.assertEqual(ResponseComposer().compose(turn=turn).text,"Безопасный ответ.")
        self.assertFalse(hasattr(provider,"router")); self.assertFalse(hasattr(provider,"plugins")); self.assertFalse(hasattr(provider,"memory"))
    def test_ollama_natural_conversation_contract_uses_only_bounded_safe_semantics(self):
        examples={
            "Мне сейчас мешает музыка":("",{},False),"Как ты вообще понимаешь мои команды?":("",{},False),
            "Сделай здесь потише":("SET_VOLUME",{},True),"Открой то, что мы сейчас обсуждали":("OPEN_BROWSER",{},False)}
        expected={text.casefold():value for text,value in examples.items()}; captured=[]
        def transport(_endpoint,payload,_timeout):
            captured.append(payload); text=json.loads(payload["messages"][1]["content"])["turn"]["normalized_text"]
            intent,parameters,clarification=expected[text]
            response={"text":"Безопасный естественный ответ.","intent":intent or None,"parameters":parameters,"requires_clarification":clarification,"end_session":False}
            return {"message":{"content":json.dumps(response)}}
        provider=OllamaConversationProvider(OllamaProviderConfig(enabled=True),transport=transport); self.assertTrue(provider.activate_transport())
        registry=ConversationProviderRegistry(); registry.replace(provider); allowed={"OPEN_BROWSER","SET_VOLUME","INCOMPLETE_OPEN_SITE"}
        safe_context={"session_state":"READY","last_action":"OPEN_BROWSER","reference_available":True}
        for phrase,(intent,parameters,clarification) in examples.items():
            with self.subTest(phrase=phrase):
                turn=ConversationTurn.create(user_text=phrase,session_state="READY",intent="UNKNOWN",result=Result(False,""),timestamp=1)
                response=registry.respond(turn,safe_context,allowed)
                self.assertEqual((response.intent,response.parameters,response.requires_clarification),(intent,parameters,clarification))
        prompt=captured[0]["messages"][0]["content"]
        self.assertIn("bounded and ephemeral",prompt); self.assertIn("never claim an action ran",prompt); self.assertIn("do not infer an action",prompt)
        self.assertIsNone(registry.validate(ProviderResponse("eval command",conversational=True),allowed))
        expired={"session_state":"READY","last_action":"","reference_available":False}
        self.assertEqual(registry.bounded_context(expired),expired)
        self.assertFalse(hasattr(provider,"router")); self.assertFalse(hasattr(provider,"plugins")); self.assertFalse(hasattr(provider,"memory"))
    def test_natural_conversation_v2_goal_contract_uses_existing_capability_flows(self):
        examples={
            "Мне нужно подготовить компьютер к работе.":("capability","",{},False),
            "Помоги подготовиться к работе.":("capability","",{},False),
            "Зачем мне голосовой помощник?":("","",{},False),
            "Открой сайт":("","INCOMPLETE_OPEN_SITE",{},True),
            "Поставь громкость на 50 процентов":("","SET_VOLUME",{"level":50},False),
        }
        expected={text.casefold():value for text,value in examples.items()}; captured=[]
        def transport(_endpoint,payload,_timeout):
            captured.append(payload); text=json.loads(payload["messages"][1]["content"])["turn"]["normalized_text"]
            goal,intent,parameters,clarification=expected[text]
            return {"message":{"content":json.dumps({"text":"Безопасный ответ.","goal":goal or None,"intent":intent or None,"parameters":parameters,"requires_clarification":clarification,"end_session":False})}}
        provider=OllamaConversationProvider(OllamaProviderConfig(enabled=True),transport=transport); self.assertTrue(provider.activate_transport())
        registry=ConversationProviderRegistry(); registry.replace(provider); allowed={"SET_VOLUME","INCOMPLETE_OPEN_SITE"}
        for phrase,(goal,intent,parameters,clarification) in examples.items():
            turn=ConversationTurn.create(user_text=phrase,session_state="READY",intent="UNKNOWN",result=Result(False,""),timestamp=1)
            response=registry.respond(turn,{},allowed)
            self.assertEqual((response.goal,response.intent,response.parameters,response.requires_clarification),(goal,intent,parameters,clarification))
        self.assertIsNone(registry.validate(ProviderResponse("x",goal="unknown"),allowed))
        self.assertIsNone(registry.validate(ProviderResponse("x","SET_VOLUME",{"level":50},goal="capability"),allowed))
        prompt=captured[0]["messages"][0]["content"]; self.assertIn("broad user goal",prompt); self.assertIn("not permission to invent actions",prompt)
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"ok")
        class Provider(ConversationProvider):
            def __init__(self): self.calls=[]
            def respond(self,turn,context): self.calls.append((turn,context)); return ProviderResponse("",goal="capability")
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events)
            goal_provider=Provider(); assistant.conversation_providers.register(goal_provider)
            limitation=assistant.handle("Мне нужна помощь со стартом смены")
            self.assertTrue(limitation.ok); self.assertTrue(limitation.data["goal_request"]); self.assertEqual(router.calls,[]); self.assertEqual(memory.history,[])
            first=assistant.routines.create("Подготовка к работе",[{"intent":"OPEN_BROWSER","parameters":{}}]); second=assistant.routines.create("Подготовка к стриму",[{"intent":"OPEN_YOUTUBE","parameters":{}}])
            choices=assistant.handle("подготовка"); self.assertTrue(choices.ok); self.assertEqual(assistant.dialogue.pending["kind"],"routine_candidates"); self.assertEqual(len(goal_provider.calls),2)
            cancelled=assistant.handle("отмена"); self.assertTrue(cancelled.ok); self.assertIsNone(assistant.dialogue.pending); self.assertEqual(router.calls,[])
            assistant.dialogue.start_routine_candidates([first,second]); assistant.dialogue.pending["goal_context"]=True; assistant.handle("до свидания"); self.assertIsNone(assistant.dialogue.pending)
            selected=assistant.handle("подготовка"); self.assertTrue(selected.ok); self.assertTrue(assistant.handle("1").ok); self.assertEqual(router.calls[-1].intent,"OPEN_BROWSER"); self.assertEqual(memory.history,[]); self.assertIsNotNone(first); self.assertIsNotNone(second)
    def test_null_intent_provider_fallback_reuses_bounded_routine_discovery(self):
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"ok")
        class Provider(ConversationProvider):
            def __init__(self): self.calls=[]; self.text="Обычный безопасный ответ."; self.clarification=False
            def respond(self,turn,context): self.calls.append((turn,context)); return ProviderResponse(self.text,requires_clarification=self.clarification)
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events)
            provider=Provider(); assistant.conversation_providers.register(provider)
            casual=assistant.handle("Мне вообще нравится твоя работа."); self.assertTrue(casual.ok); self.assertEqual(casual.message,provider.text); self.assertIsNone(assistant.dialogue.pending); self.assertEqual(router.calls,[])
            provider.clarification=True; preserved=assistant.handle("Мне просто интересно."); self.assertTrue(preserved.ok); self.assertEqual(preserved.message,provider.text); self.assertIsNone(assistant.dialogue.pending); self.assertEqual(router.calls,[]); provider.clarification=False
            routine=assistant.routines.create("Рабочая среда",[{"intent":"OPEN_BROWSER","parameters":{}}],"подготовить компьютер к работе")
            provider.text="Я вижу, что нужно подготовить компьютер к работе."
            goal=assistant.handle("Хочу быстро привести всё в готовность."); self.assertTrue(goal.confirmation_required); self.assertEqual(assistant.dialogue.pending["kind"],"routine_execution"); self.assertEqual(router.calls,[]); self.assertEqual(len(provider.calls),3)
            self.assertTrue(assistant.handle("отмена").ok); self.assertIsNone(assistant.dialogue.pending)
            assistant.routines.create("Учебная среда",[{"intent":"OPEN_YOUTUBE","parameters":{}}],"подготовить рабочую среду")
            assistant.routines.update_routine(routine["id"],description="подготовить рабочую среду")
            provider.text="Нужно подготовить рабочую среду."
            choices=assistant.handle("Хочу быстро начать."); self.assertTrue(choices.ok); self.assertEqual(assistant.dialogue.pending["kind"],"routine_candidates"); self.assertEqual(router.calls,[])
            self.assertTrue(assistant.handle("до свидания").ok); self.assertIsNone(assistant.dialogue.pending)
            self.assertEqual(assistant.parser.parse("Поставь громкость на 50 процентов").intent,"SET_VOLUME")
            exact=assistant.parser.parse("сделай ещё тише"); self.assertEqual(assistant.intent_resolver.resolve("сделай ещё тише",exact=exact,pending=None,session_context={"last_action":"SET_VOLUME"}).intent,"VOLUME_DOWN")
            self.assertEqual(memory.history,[])
    def test_provider_semantic_parameters_gate_capability_discovery_order(self):
        allowed={"CANCEL_PENDING_DIALOGUE","SET_VOLUME","OPEN_BROWSER","OPEN_YOUTUBE","INCOMPLETE_OPEN_SITE"}; registry=ConversationProviderRegistry()
        self.assertIsNone(registry.validate(ProviderResponse("x","CANCEL_PENDING_DIALOGUE",{"level":0}),allowed))
        self.assertIsNone(registry.validate(ProviderResponse("x","CANCEL_PENDING_DIALOGUE",{"anything":"x"}),allowed))
        self.assertIsNotNone(registry.validate(ProviderResponse("x","CANCEL_PENDING_DIALOGUE",{}),allowed))
        self.assertIsNotNone(registry.validate(ProviderResponse("x","SET_VOLUME",{"level":0}),allowed))
        self.assertIsNotNone(registry.validate(ProviderResponse("x","SET_VOLUME",{"level":50}),allowed))
        self.assertIsNone(registry.validate(ProviderResponse("x","SET_VOLUME",{}),allowed))
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"ok")
        class Provider(ConversationProvider):
            def __init__(self,response): self.response=response; self.calls=0
            def respond(self,turn,context): self.calls+=1; return self.response
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events)
            find_calls=[]; original_find=assistant.routines.find_routine; assistant.routines.find_routine=lambda text: find_calls.append(text) or original_find(text)
            provider=Provider(ProviderResponse("x","CANCEL_PENDING_DIALOGUE",{"level":0})); assistant.conversation_providers.register(provider)
            rejected=assistant.handle("неизвестная фраза"); self.assertFalse(rejected.ok); self.assertEqual(provider.calls,1); self.assertEqual(find_calls,[]); self.assertEqual(router.calls,[])
            provider.response=ProviderResponse("Готово.","SET_VOLUME",{"level":50}); direct=assistant.handle("другая неизвестная фраза"); self.assertTrue(direct.ok); self.assertEqual(find_calls,[]); self.assertEqual(router.calls[-1].parameters,{"level":50})
            router.calls=[]; provider.response=ProviderResponse("Отменено.","CANCEL_PENDING_DIALOGUE",{}); cancelled=assistant.handle("ещё неизвестнее"); self.assertTrue(cancelled.ok); self.assertEqual(find_calls,[]); self.assertEqual(router.calls,[])
            provider.response=ProviderResponse("Авторитетный безопасный ответ."); conversational=assistant.handle("совсем неизвестная цель"); self.assertTrue(conversational.ok); self.assertEqual(conversational.message,provider.response.text); self.assertEqual(len(find_calls),1); self.assertEqual(router.calls,[])
            routine=assistant.routines.create("Рабочая среда",[{"intent":"OPEN_BROWSER","parameters":{}}],"подготовить компьютер к работе"); provider.response=ProviderResponse("Нужно подготовить компьютер к работе."); one=assistant.handle("абракадабра"); self.assertTrue(one.confirmation_required); self.assertEqual(assistant.dialogue.pending["kind"],"routine_execution"); self.assertEqual(len(find_calls),2); self.assertEqual(router.calls,[]); self.assertTrue(assistant.handle("отмена").ok)
            assistant.routines.create("Учебная среда",[{"intent":"OPEN_YOUTUBE","parameters":{}}],"подготовить рабочую среду"); assistant.routines.update_routine(routine["id"],description="подготовить рабочую среду"); provider.response=ProviderResponse("Нужно подготовить рабочую среду."); multiple=assistant.handle("хочу другой режим"); self.assertTrue(multiple.ok); self.assertEqual(assistant.dialogue.pending["kind"],"routine_candidates"); self.assertEqual(len(find_calls),3); self.assertEqual(router.calls,[]); self.assertTrue(assistant.handle("до свидания").ok); self.assertIsNone(assistant.dialogue.pending); self.assertEqual(memory.history,[])
    def test_natural_routine_learning_reuses_structured_recorder_and_confirmation(self):
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"ok")
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events)
            for phrase in ("Запомни, как я готовлюсь к стриму","Научи тебя, как я обычно начинаю работу","Давай я покажу тебе, что я делаю перед игрой","Запомни эту последовательность"):
                self.assertEqual(assistant.parser.parse(phrase).intent,"START_ROUTINE_LEARNING")
            self.assertTrue(assistant.handle("Запомни, как я готовлюсь к стриму").ok); self.assertTrue(assistant.routine_learning)
            assistant.events.emit("command_routed",command=Command("OPEN_BROWSER",{}),result=Result(True,"ok")); assistant.events.emit("command_routed",command=Command("OPEN_YOUTUBE",{}),result=Result(True,"ok")); assistant.events.emit("command_routed",command=Command("SET_VOLUME",{"level":50}),result=Result(True,"ok")); assistant.events.emit("command_routed",command=Command("UNKNOWN",{}),result=Result(False,"bad"))
            self.assertTrue(assistant.handle("всё").ok); self.assertFalse(assistant.routine_learning); self.assertTrue(assistant.handle("Подготовка к стриму").confirmation_required); self.assertTrue(assistant.handle("да").ok)
            saved=assistant.routines.find("Подготовка к стриму"); self.assertEqual(saved["actions"],[{"intent":"OPEN_BROWSER","parameters":{}},{"intent":"OPEN_YOUTUBE","parameters":{}},{"intent":"SET_VOLUME","parameters":{"level":50}}]); self.assertEqual(router.calls,[])
            self.assertTrue(assistant.handle("Запомни эту последовательность").ok); self.assertTrue(assistant.handle("отмена").ok); self.assertFalse(assistant.routine_learning); self.assertEqual(len(memory.learned_routines),1); self.assertFalse(any("shell" in str(item).lower() or "eval" in str(item).lower() for item in saved["actions"]))
    def test_routine_learning_normalizes_completion_and_records_canonical_steps(self):
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"ok")
        completion_forms=("Всё","Всё.","Всё!","Всё...","На этом всё.","Готово.","Закончить обучение.")
        for index,finish in enumerate(completion_forms):
            with self.subTest(finish=finish), tempfile.TemporaryDirectory() as directory:
                memory=MemoryManager(directory); router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events)
                self.assertTrue(assistant.handle("Запомни, как я готовлюсь к стриму.").ok)
                assistant.events.emit("command_routed",command=Command("OPEN_BROWSER",{"query":"открой браузер."}),result=Result(True,"ok"))
                assistant.events.emit("command_routed",command=Command("OPEN_YOUTUBE",{"query":"открой youtube."}),result=Result(True,"ok"))
                assistant.events.emit("command_routed",command=Command("SET_VOLUME",{"level":50}),result=Result(True,"ok"))
                assistant.events.emit("command_routed",command=Command("SEARCH_WEB",{"query":"безопасный запрос"}),result=Result(True,"ok"))
                assistant.events.emit("command_routed",command=Command("SET_VOLUME",{"level":101}),result=Result(True,"ok"))
                assistant.events.emit("command_routed",command=Command("UNKNOWN",{}),result=Result(False,"bad"))
                review=assistant.handle(finish); self.assertTrue(review.ok); self.assertFalse(assistant.routine_learning); self.assertEqual(assistant.dialogue.pending["kind"],"routine")
                self.assertEqual(assistant.dialogue.pending["actions"],[{"intent":"OPEN_BROWSER","parameters":{}},{"intent":"OPEN_YOUTUBE","parameters":{}},{"intent":"SET_VOLUME","parameters":{"level":50}},{"intent":"SEARCH_WEB","parameters":{"query":"безопасный запрос"}}])
                self.assertTrue(assistant.handle("Подготовка к стриму "+str(index)).confirmation_required); self.assertTrue(assistant.handle("Да.").ok)
                saved=assistant.routines.find("Подготовка к стриму "+str(index)); self.assertIsNotNone(saved); self.assertEqual(saved["actions"],assistant.demonstration.actions); self.assertFalse(any("открой браузер" in str(step).lower() or "shell" in str(step).lower() for step in saved["actions"]))
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events)
            assistant.handle("Запомни эту последовательность"); assistant.events.emit("command_routed",command=Command("OPEN_BROWSER",{}),result=Result(True,"ok")); result=assistant.handle("Всё равно открой браузер")
            self.assertTrue(assistant.routine_learning); self.assertIsNone(assistant.dialogue.pending); self.assertNotEqual(result.message,"Я записал 1 действий. Как назвать эту процедуру?")
    def test_voice_learning_context_precedes_unknown_guidance_and_preserves_save_flow(self):
        class Plugins:
            def __init__(self): self.calls=[]
            def execute(self,plugin_id,command): self.calls.append((plugin_id,command)); return Result(True,"ok")
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text); return True
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); assistant=Assistant(); assistant.memory=memory; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            plugins=Plugins()
            for intent in ("OPEN_BROWSER","OPEN_YOUTUBE","SET_VOLUME"): assistant.registry.register(intent,"safe")
            assistant.plugins=plugins; assistant.router=CommandRouter(assistant.registry,plugins,PermissionManager(),assistant.skills,assistant.events); assistant.skills.router=assistant.router; assistant.routines=RoutineManager(memory,assistant.router,assistant.events)
            tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
            self.assertTrue(voice.process_transcript("Джарвис, запомни, как я готовлюсь к стриму.").ok); self.assertTrue(assistant.routine_learning)
            for turn in ("Открой браузер.","Открой YouTube.","Поставь громкость на 50 процентов."):
                self.assertTrue(voice.process_transcript(turn).ok)
            self.assertEqual([item[1].intent for item in plugins.calls],["OPEN_BROWSER","OPEN_YOUTUBE","SET_VOLUME"])
            review=voice.process_transcript("Всё."); self.assertTrue(review.ok); self.assertFalse(assistant.routine_learning); self.assertEqual(assistant.dialogue.pending["kind"],"routine"); self.assertEqual(assistant.dialogue.pending["step"],"name")
            self.assertEqual(assistant.dialogue.pending["actions"],[{"intent":"OPEN_BROWSER","parameters":{}},{"intent":"OPEN_YOUTUBE","parameters":{}},{"intent":"SET_VOLUME","parameters":{"level":50}}])
            for finish in ("Готово.","Закончить обучение."):
                assistant.routine_learning=True; assistant.demonstration.start(); assistant.events.emit("command_routed",command=Command("OPEN_BROWSER",{}),result=Result(True,"ok")); assistant.dialogue.pending=None
                self.assertTrue(voice.process_transcript(finish).ok); self.assertFalse(assistant.routine_learning); self.assertEqual(assistant.dialogue.pending["kind"],"routine")
            assistant.dialogue.pending={"kind":"routine","step":"name","actions":[{"intent":"OPEN_BROWSER","parameters":{}},{"intent":"OPEN_YOUTUBE","parameters":{}},{"intent":"SET_VOLUME","parameters":{"level":50}}],"parameters":[]}
            self.assertTrue(voice.process_transcript("Подготовка к стриму").confirmation_required); saved=voice.process_transcript("Да."); self.assertTrue(saved.ok); self.assertFalse(assistant.routine_learning)
            routine=assistant.routines.find("Подготовка к стриму"); self.assertIsNotNone(routine); self.assertEqual(len(routine["actions"]),3)
            self.assertEqual(memory.history,[])
            assistant.routine_learning=True; assistant.demonstration.start(); unrelated=voice.process_transcript("абракадабра")
            self.assertFalse(unrelated.ok); self.assertNotIn("Не совсем понял",unrelated.message); assistant.demonstration.cancel(); assistant.routine_learning=False
            voice.stop(); outside=voice.process_transcript("абракадабра"); self.assertIsNone(outside)
            self.assertTrue(voice.process_transcript("Джарвис, абракадабра").ok); self.assertIn("Не совсем понял",tts.messages[-1])
            self.assertTrue(voice.process_transcript("Открой браузер").ok); self.assertEqual(plugins.calls[-1][1].intent,"OPEN_BROWSER")
    def test_ollama_unknown_volume_phrase_routes_real_router_and_intercepted_endpoint(self):
        class Endpoint:
            def __init__(self): self.levels=[]; self.current=.5
            def SetMasterVolumeLevelScalar(self,value,_): self.levels.append(value); self.current=value
            def GetMasterVolumeLevelScalar(self): return self.current
        class Plugins:
            def __init__(self): self.calls=[]; self.endpoint=Endpoint(); self.volume=VolumePlugin(endpoint_factory=lambda:self.endpoint)
            def execute(self,plugin_id,command): self.calls.append((plugin_id,command)); return self.volume.execute(command)
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text); return True
            def is_speaking(self): return False
        phrase="Убавь до пятидесяти процентов"
        with tempfile.TemporaryDirectory() as directory:
            assistant=Assistant(); memory=MemoryManager(directory); assistant.memory=memory; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory; assistant.routines.memory=memory
            exact=assistant.parser.parse(phrase); resolved=assistant.intent_resolver.resolve(phrase,exact=exact,pending=None,session_context=None)
            self.assertEqual(exact.intent,"UNKNOWN"); self.assertEqual(resolved.intent,"UNKNOWN")
            plugins=Plugins(); assistant.registry.register("SET_VOLUME","volume"); assistant.registry.register("VOLUME_DOWN","volume"); assistant.plugins=plugins; assistant.router=CommandRouter(assistant.registry,plugins,PermissionManager(),assistant.skills,assistant.events); assistant.skills.router=assistant.router; assistant.routines.router=assistant.router
            calls=[]
            def transport(_endpoint,_payload,_timeout):
                calls.append(True); return '{"message":{"content":"{\\"text\\":\\"Устанавливаю громкость.\\",\\"intent\\":\\"SET_VOLUME\\",\\"parameters\\":{\\"level\\":50},\\"requires_clarification\\":false,\\"end_session\\":false}"}}'
            self.assertTrue(assistant.enable_ollama_provider(OllamaProviderConfig(enabled=True),transport=transport).ok)
            tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); result=voice.process_transcript("Джарвис, "+phrase)
            self.assertTrue(result.ok); self.assertEqual(len(calls),1); self.assertTrue(assistant.conversation_providers.last_validation_diagnostic["accepted"])
            self.assertEqual(len(plugins.calls),1); self.assertEqual(plugins.calls[0][0],"volume"); self.assertEqual(plugins.calls[0][1].parameters,{"level":50}); self.assertEqual(plugins.endpoint.levels,[0.5]); self.assertEqual(voice.conversation_context["last_action"],"SET_VOLUME"); self.assertNotEqual(voice.conversation_context["last_action"],"UNKNOWN")
            follow_up=voice.process_transcript("А теперь сделай ещё тише")
            self.assertTrue(follow_up.ok); self.assertEqual(len(calls),1); self.assertEqual(len(plugins.calls),2); self.assertEqual(plugins.calls[-1][0],"volume"); self.assertEqual(plugins.calls[-1][1].intent,"VOLUME_DOWN"); self.assertEqual(plugins.endpoint.levels,[0.5,0.4]); self.assertEqual(voice.conversation_context["last_action"],"VOLUME_DOWN"); self.assertTrue(tts.messages); self.assertEqual(len(memory.history),1)
    def test_conversational_layer_regression_keeps_deterministic_turns_out_of_provider(self):
        class Endpoint:
            def __init__(self): self.levels=[]; self.current=.5
            def SetMasterVolumeLevelScalar(self,value,_): self.levels.append(value); self.current=value
            def GetMasterVolumeLevelScalar(self): return self.current
        class Plugins:
            def __init__(self):
                self.calls=[]; self.endpoint=Endpoint(); self.launches=[]
                self.volume=VolumePlugin(endpoint_factory=lambda:self.endpoint)
                self.browser=BrowserPlugin(launcher=self.launches.append)
            def execute(self,plugin_id,command):
                self.calls.append((plugin_id,command))
                return self.volume.execute(command) if plugin_id=="volume" else self.browser.execute(command)
        class Provider(ConversationProvider):
            def __init__(self): self.calls=0
            def respond(self,turn,context):
                self.calls+=1; return ProviderResponse("Устанавливаю громкость.","SET_VOLUME",{"level":50})
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text); return True
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory, patch("plugins.browser.plugin.webbrowser.open",return_value=True) as url_open:
            assistant=Assistant(); memory=MemoryManager(directory); assistant.memory=memory; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory; assistant.routines.memory=memory
            plugins=Plugins()
            for intent,plugin_id in (("SET_VOLUME","volume"),("VOLUME_DOWN","volume"),("OPEN_BROWSER","browser"),("OPEN_YOUTUBE","browser")): assistant.registry.register(intent,plugin_id)
            assistant.plugins=plugins; assistant.router=CommandRouter(assistant.registry,plugins,PermissionManager(),assistant.skills,assistant.events); assistant.skills.router=assistant.router; assistant.routines.router=assistant.router
            provider=Provider(); assistant.conversation_providers.register(provider); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
            first=voice.process_transcript("Джарвис, Убавь до пятидесяти процентов")
            self.assertTrue(first.ok); self.assertEqual(provider.calls,1); self.assertEqual(voice.conversation_turn["response_type"],"action_success"); self.assertEqual(voice.conversation_context["last_action"],"SET_VOLUME"); self.assertEqual(voice.conversation_context["last_action_parameters"],{"level":50}); self.assertEqual(plugins.endpoint.levels,[.5])
            follow_up=voice.process_transcript("А теперь сделай ещё тише")
            self.assertTrue(follow_up.ok); self.assertEqual(provider.calls,1); self.assertEqual(voice.conversation_turn["response_type"],"action_success"); self.assertEqual(plugins.calls[-1][1].intent,"VOLUME_DOWN"); self.assertEqual(plugins.endpoint.levels,[.5,.4])
            previous_action=voice.conversation_context["last_action"]; help_turn=voice.process_transcript("Что я могу сейчас сказать?")
            self.assertTrue(help_turn.ok); self.assertEqual(provider.calls,1); self.assertEqual(voice.conversation_turn["response_type"],"help"); self.assertEqual(voice.conversation_context["last_action"],previous_action)
            clarification=voice.process_transcript("Открой сайт")
            self.assertTrue(clarification.ok); self.assertEqual(provider.calls,1); self.assertEqual(voice.conversation_turn["response_type"],"clarification"); self.assertEqual(assistant.dialogue.pending["kind"],"browser_clarification")
            correction=voice.process_transcript("Нет, YouTube")
            self.assertTrue(correction.ok); self.assertEqual(provider.calls,1); self.assertEqual(voice.conversation_turn["response_type"],"correction"); self.assertIsNone(assistant.dialogue.pending); self.assertEqual(plugins.calls[-1][1].intent,"OPEN_YOUTUBE"); self.assertEqual(url_open.call_count,1)
            replay=voice.process_transcript("Сделай это ещё раз")
            self.assertTrue(replay.ok); self.assertEqual(provider.calls,1); self.assertEqual(voice.conversation_turn["response_type"],"reference_replay"); self.assertEqual(plugins.calls[-1][1].intent,"VOLUME_DOWN")
            self.assertTrue(voice.process_transcript("Открой сайт").ok); self.assertIsNotNone(assistant.dialogue.pending)
            cancelled=voice.process_transcript("Отмена")
            self.assertTrue(cancelled.ok); self.assertEqual(provider.calls,1); self.assertEqual(voice.conversation_turn["response_type"],"informational"); self.assertIsNone(assistant.dialogue.pending); self.assertIsNotNone(voice.conversation_context)
            reset=voice.process_transcript("Сбрось разговор")
            self.assertTrue(reset.ok); self.assertEqual(provider.calls,1); self.assertEqual(voice.conversation_turn["response_type"],"informational"); self.assertEqual(voice.conversation_context["last_action"],""); self.assertIsNone(assistant.dialogue.pending)
            status=voice.process_transcript("Ты слушаешь?")
            self.assertTrue(status.ok); self.assertEqual(provider.calls,1); self.assertEqual(voice.conversation_turn["response_type"],"status"); self.assertEqual(plugins.calls[-1][1].intent,"VOLUME_DOWN")
            farewell=voice.process_transcript("Пока")
            self.assertTrue(farewell.ok); self.assertEqual(provider.calls,1); self.assertEqual(voice.conversation_turn, None); self.assertIsNone(voice.conversation_context); self.assertIsNone(assistant.dialogue.pending)
            self.assertTrue(tts.messages); self.assertEqual(plugins.launches,[]); self.assertEqual(len(memory.history),6)
    def test_smoke_diagnostic_handles_missing_optional_fields_without_masking_runtime_error(self):
        response=ProviderResponse("Безопасный ответ.","OPEN_BROWSER")
        report=format_smoke_diagnostic(exact_intent="UNKNOWN",resolver_intent="UNKNOWN",provider_response=response,
            validation=None,router_calls=None,plugin_calls=0,tts_ready=None,final_state=None)
        self.assertEqual(report["provider_intent"],"OPEN_BROWSER"); self.assertEqual(report["provider_validation"],"NOT_AVAILABLE")
        self.assertEqual(report["router_calls"],0); self.assertEqual(report["plugin_calls"],0); self.assertEqual(report["tts_ready"],"NOT_AVAILABLE")
        production_error=RuntimeError("private provider detail")
        failed=format_smoke_diagnostic(provider_response=response,production_error=production_error)
        self.assertEqual(failed["production_error"],"RuntimeError"); self.assertNotIn("private provider detail",repr(failed))
    def test_voice_tts_receives_enriched_safe_response_text(self):
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        class Parser:
            def parse(self,text): return Command("OPEN_YOUTUBE",{})
        class AssistantStub:
            def __init__(self): self.memory=type("M",(),{"settings":{"conversation_timeout":2}})(); self.dialogue=type("D",(),{"pending":None})(); self.parser=Parser(); self.state=AssistantState.READY
            def set_state(self,state): self.state=state
            def handle(self,text): return Result(True,"")
        assistant=AssistantStub(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
        self.assertTrue(voice.process_transcript("Джарвис, открой YouTube").ok); self.assertEqual(tts.messages,["Готово, браузер открыт: YouTube."]); self.assertEqual(voice.conversation_turn["action_result"]["metadata"],{"safe_target":"YouTube"})
    def test_voice_conversation_turn_is_ephemeral_and_tracks_router_result(self):
        class Clock:
            def __init__(self): self.value=0.0
            def __call__(self): return self.value
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"Браузер открыт.")
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.settings["conversation_timeout"]=2; router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            clock=Clock(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); voice._clock=clock
            self.assertTrue(voice.process_transcript("Джарвис, открой браузер").ok); turn=voice.conversation_turn; self.assertEqual(turn["intent"],"OPEN_BROWSER"); self.assertTrue(turn["action_result"]["success"]); self.assertEqual(turn["action_result"]["action_type"],"OPEN_BROWSER"); self.assertEqual(turn["pending_state"],{}); self.assertEqual(tts.messages[-1],"Браузер открыт."); self.assertEqual(len(router.calls),1)
            self.assertEqual(voice.sequence_state["depth"],1); self.assertTrue(voice.process_transcript("снова").ok); self.assertTrue(voice.conversation_turn["reference_context"]["reference_available"]); self.assertEqual(voice.conversation_turn["response_type"],"reference_replay"); self.assertIsNone(voice.sequence_state); self.assertEqual(len(router.calls),2)
            clock.value=3; self.assertIsNone(voice.conversation_context); self.assertIsNone(voice.conversation_turn)
            self.assertTrue(voice.process_transcript("Джарвис, открой браузер").ok); voice.stop(); self.assertIsNone(voice.conversation_turn); self.assertTrue(voice.start()); voice.stop(); self.assertIsNone(voice.conversation_turn)
    def test_voice_turn_sequences_coordinate_existing_pending_flows_only(self):
        class Clock:
            def __init__(self): self.value=0.0
            def __call__(self): return self.value
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"Выполнено.")
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.settings["conversation_timeout"]=2; router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            clock=Clock(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); voice._clock=clock
            self.assertTrue(voice.process_transcript("Джарвис, открой сайт").ok); sequence=voice.sequence_state; self.assertEqual(sequence["pending_kind"],"browser_clarification"); self.assertEqual(sequence,voice.sequence_state); self.assertTrue(voice.process_transcript("ты слушаешь?").ok); self.assertEqual(voice.sequence_state,sequence); self.assertTrue(voice.process_transcript("что я могу сейчас сказать?").ok); self.assertEqual(voice.sequence_state,sequence); self.assertTrue(voice.process_transcript("YouTube").ok); self.assertIsNone(voice.sequence_state); self.assertEqual(router.calls[-1].intent,"OPEN_YOUTUBE")
            self.assertTrue(voice.process_transcript("установи громкость").ok); self.assertEqual(voice.sequence_state["pending_kind"],"volume_clarification"); self.assertTrue(voice.process_transcript("50").ok); self.assertIsNone(voice.sequence_state); self.assertEqual(router.calls[-1].parameters,{"level":50})
            self.assertTrue(voice.process_transcript("открой папку").ok); self.assertEqual(voice.sequence_state["pending_kind"],"folder_clarification"); self.assertTrue(voice.process_transcript("Документы").ok); self.assertIsNone(voice.sequence_state); self.assertEqual(router.calls[-1].intent,"OPEN_FOLDER")
            assistant.dialogue._set_pending({"kind":"routine_parameters","step":"value","missing":[{"name":"count","type":"integer","required":True}],"index":0,"values":{}}); self.assertTrue(voice.process_transcript("понял").ok); self.assertEqual(voice.sequence_state["pending_kind"],"routine_parameters"); self.assertTrue(voice.process_transcript("отмена").ok); self.assertIsNone(voice.sequence_state); self.assertIsNone(assistant.dialogue.pending); self.assertEqual(tts.messages[-1],"Отменено.")
            self.assertTrue(voice.process_transcript("Джарвис, открой сайт").ok); self.assertIsNotNone(voice.sequence_state); clock.value=3; self.assertIsNone(voice.conversation_context); self.assertIsNone(voice.sequence_state); self.assertTrue(voice.process_transcript("Джарвис, открой сайт").ok); self.assertTrue(voice.process_transcript("стоп").ok); self.assertIsNone(voice.sequence_state)
    def test_voice_tts_receives_response_composer_text_without_memory_write(self):
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        class AssistantStub:
            def __init__(self): self.memory=type("M",(),{"settings":{"conversation_timeout":2}})(); self.dialogue=type("D",(),{"pending":None})(); self.state=AssistantState.READY
            def set_state(self,state): self.state=state
            def handle(self,text): return Result(True,"",{"response_type":"action_success"})
        assistant=AssistantStub(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
        result=voice.process_transcript("Джарвис, команда"); self.assertTrue(result.ok); self.assertEqual(tts.messages,["Готово."])
    def test_open_browser_success_is_migrated_to_composer_owned_template_once(self):
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        class Parser:
            def parse(self,text): return Command("OPEN_BROWSER",{})
        class PluginSpy:
            def __init__(self): self.calls=[]
            def execute(self,command): self.calls.append(command); return Result(True,"Открываю браузер.",{"composer_owned_template":"open_browser_success"})
        class Router:
            def __init__(self,plugin): self.plugin=plugin; self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return self.plugin.execute(command)
        class AssistantStub:
            def __init__(self,router): self.memory=type("M",(),{"settings":{"conversation_timeout":2}})(); self.dialogue=type("D",(),{"pending":None})(); self.parser=Parser(); self.router=router; self.state=AssistantState.READY; self.received=[]
            def set_state(self,state): self.state=state
            def handle(self,text): self.received.append(text); return self.router.route(self.parser.parse(text))
        plugin=PluginSpy(); router=Router(plugin); assistant=AssistantStub(router); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
        result=voice.process_transcript("Джарвис, открой браузер")
        self.assertTrue(result.ok); self.assertEqual(result.message,"Открываю браузер."); self.assertEqual(len(assistant.received),1); self.assertEqual(len(router.calls),1); self.assertEqual(len(plugin.calls),1); self.assertEqual(voice.conversation_turn["intent"],"OPEN_BROWSER"); self.assertEqual(voice.conversation_turn["response_type"],"action_success"); self.assertEqual(voice.conversation_turn["action_result"]["metadata"],{"composer_owned_template":"open_browser_success"}); self.assertEqual(tts.messages,["Готово, браузер открыт."])
    def test_set_volume_success_is_migrated_to_composer_owned_template(self):
        from plugins.volume.plugin import Plugin
        class Endpoint:
            def __init__(self): self.calls=[]
            def SetMasterVolumeLevelScalar(self,value,_): self.calls.append(value)
        endpoint=Endpoint(); plugin=Plugin(); plugin._endpoint=lambda:endpoint
        for level in (0,50,100):
            result=plugin.execute(Command("SET_VOLUME",{"level":level})); self.assertTrue(result.ok); self.assertEqual(result.data,{"level":level,"composer_owned_template":"set_volume_success"})
        self.assertEqual(endpoint.calls,[0,0.5,1])
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        class Parser:
            def parse(self,text): return Command("SET_VOLUME",{"level":int(text)})
        class Router:
            def __init__(self): self.calls=[]; self.plugin_calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); self.plugin_calls.append(command); level=command.parameters["level"]; return Result(True,f"Громкость {level}%.",{"level":level,"composer_owned_template":"set_volume_success"})
        class AssistantStub:
            def __init__(self,router): self.memory=type("M",(),{"settings":{"conversation_timeout":2}})(); self.dialogue=type("D",(),{"pending":None})(); self.parser=Parser(); self.router=router; self.state=AssistantState.READY; self.received=[]
            def set_state(self,state): self.state=state
            def handle(self,text): self.received.append(text); return self.router.route(self.parser.parse(text))
        for level in (0,50,100):
            router=Router(); assistant=AssistantStub(router); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); result=voice.process_transcript(f"Джарвис, {level}")
            self.assertTrue(result.ok); self.assertEqual(result.message,f"Громкость {level}%."); self.assertEqual(len(assistant.received),1); self.assertEqual(len(router.calls),1); self.assertEqual(len(router.plugin_calls),1); self.assertEqual(voice.conversation_turn["action_result"]["metadata"],{"level":level,"composer_owned_template":"set_volume_success"}); self.assertEqual(tts.messages,[f"Готово, громкость установлена на {level}%."])
    def test_open_youtube_success_is_migrated_to_composer_owned_template(self):
        from plugins.browser.plugin import Plugin
        plugin=Plugin(); plugin._open_url=lambda url:Result(True,"Открываю страницу в браузере.",{"url":url})
        result=plugin.execute(Command("OPEN_YOUTUBE",{})); self.assertTrue(result.ok); self.assertEqual(result.data,{"url":"https://www.youtube.com","safe_target":"YouTube","composer_owned_template":"open_youtube_success"})
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]; self.calls=0
            def say(self,text): self.calls+=1; self.messages.append(text); return True
            def is_speaking(self): return False
        class Parser:
            def parse(self,text): return Command("OPEN_YOUTUBE",{})
        class Router:
            def __init__(self): self.calls=[]; self.plugin_calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); self.plugin_calls.append(command); return Result(True,"Открываю страницу в браузере.",{"safe_target":"YouTube","composer_owned_template":"open_youtube_success"})
        class AssistantStub:
            def __init__(self,router): self.memory=type("M",(),{"settings":{"conversation_timeout":2}})(); self.dialogue=type("D",(),{"pending":None})(); self.parser=Parser(); self.router=router; self.state=AssistantState.READY; self.received=[]
            def set_state(self,state): self.state=state
            def handle(self,text): self.received.append(text); return self.router.route(self.parser.parse(text))
        router=Router(); assistant=AssistantStub(router); tts=Tts(); published=[]; voice=VoiceController(assistant,None,Wake(),Mic(),tts,lambda _text,item:published.append(item)); handled=voice.process_transcript("Джарвис, открой YouTube")
        self.assertTrue(handled.ok); self.assertEqual(len(assistant.received),1); self.assertEqual(len(router.calls),1); self.assertEqual(len(router.plugin_calls),1); self.assertEqual(voice.conversation_turn["intent"],"OPEN_YOUTUBE"); self.assertEqual(tts.messages,["Готово, YouTube открыт."]); self.assertEqual(tts.calls,1); self.assertEqual(len(published),1); self.assertEqual(published[0].message,tts.messages[0])
    def test_open_browser_launch_and_url_validation(self):
        from plugins.browser.plugin import Plugin
        with patch.object(__import__("plugins.browser.plugin",fromlist=["os"]).os,"startfile",create=True) as startfile, patch("plugins.browser.plugin.webbrowser.open") as open_url:
            plugin=Plugin(launcher=startfile); plugin._browser_executable=lambda:"C:\\Browser\\browser.exe"
            launched=plugin.execute(Command("OPEN_BROWSER",{})); self.assertTrue(launched.ok); self.assertEqual(launched.data["executable"],"C:\\Browser\\browser.exe"); self.assertEqual(launched.data["composer_owned_template"],"open_browser_success"); startfile.assert_called_once_with("C:\\Browser\\browser.exe"); open_url.assert_not_called()
            page=plugin.execute(Command("OPEN_BROWSER",{"url":"https://example.com"})); self.assertTrue(page.ok); open_url.assert_called_once_with("https://example.com")
            open_url.reset_mock(); self.assertFalse(plugin.execute(Command("OPEN_BROWSER",{"url":"about:blank"})).ok); self.assertFalse(plugin.execute(Command("OPEN_BROWSER",{"url":"file:///tmp/x"})).ok); open_url.assert_not_called()
            search=plugin.execute(Command("SEARCH_WEB",{"query":"jarvis"})); self.assertTrue(search.ok); self.assertIn("https://www.google.com/search",search.data["url"])
    def test_stt_backends_selection_and_local_contract(self):
        class FakeAudio:
            def get_raw_data(self,**kwargs): self.options=kwargs; return b"audio"
        class FakeRecognizer:
            def AcceptWaveform(self,data): self.data=data; return True
            def FinalResult(self): return '{"text":"локальная команда"}'
        class FakeVosk:
            def Model(self,path): self.path=path; return "model"
            def KaldiRecognizer(self,model,rate): self.model,self.rate=model,rate; return FakeRecognizer()
        with tempfile.TemporaryDirectory() as directory:
            missing=LocalVoskSTTBackend(Path(directory)/"missing",FakeVosk()); self.assertTrue(missing.status()["backend_installed"]); self.assertFalse(missing.status()["model_available"]); self.assertRaises(STTUnavailable,missing.initialize)
            local=LocalVoskSTTBackend(directory,FakeVosk()); local.initialize(); audio=FakeAudio(); self.assertEqual(local.transcribe(audio,"ru-RU"),"локальная команда"); self.assertEqual(audio.options,{"convert_rate":16000,"convert_width":2})
            stt=SpeechToText(backend="local",backend_impl=local); self.assertTrue(stt.backend_status()["available"]); self.assertEqual(stt._transcribe(FakeAudio()),"локальная команда")
            class EmptyLocal:
                def status(self): return {"backend_installed":True,"model_available":True,"model_path":"fake"}
                def transcribe(self,audio,language): return ""
            self.assertEqual(SpeechToText(backend="local",backend_impl=EmptyLocal())._transcribe(FakeAudio()),"")
            class MissingLocal:
                def status(self): return {"backend_installed":False,"model_available":False,"model_path":""}
                def transcribe(self,audio,language): raise STTUnavailable("missing")
            unavailable_stt=SpeechToText(backend="local",backend_impl=MissingLocal()); self.assertFalse(unavailable_stt.backend_status()["available"]); self.assertRaises(STTUnavailable,unavailable_stt._transcribe,FakeAudio())
            google=SpeechToText(); self.assertEqual(google.backend,"google"); self.assertTrue(google.backend_status()["available"])
            fallback=SpeechToText(backend="local",fallback_to_google=True,backend_impl=MissingLocal()); fallback._google=type("Google",(),{"transcribe":lambda self,a,l:"google transcript"})(); self.assertEqual(fallback._transcribe(FakeAudio()),"google transcript")
            class VoiceAssistant:
                def __init__(self): self.memory=type("M",(),{"settings":{"speech_timeout":1}})(); self.received=[]
                def set_state(self,state): self.state=state
                def handle(self,text): self.received.append(text); return Result(True,"ok")
            class Wake:
                def strip(self,text): return text.split(",",1)[1].strip() if "," in text else None
            class Mic:
                def selected_index(self): return None
            class Tts:
                def say(self,text): return True
                def is_speaking(self): return False
            voice_stt=SpeechToText(backend="local",backend_impl=local); calls=[]
            def local_listen(*_):
                calls.append(1)
                if len(calls)==1: return "джарвис, покажи мои процедуры"
                raise RuntimeError("done")
            voice_stt.listen_once=local_listen; assistant=VoiceAssistant(); controller=VoiceController(assistant,voice_stt,Wake(),Mic(),Tts()); controller.start(); time.sleep(.06); controller.stop(); self.assertEqual(assistant.received,["покажи мои процедуры"])
    def test_stt_capture_vad_tuning_preserves_existing_timeouts(self):
        class Audio:
            pass
        class Source:
            SAMPLE_RATE=44100
            SAMPLE_WIDTH=2
            CHUNK=1024
        class Microphone:
            def __init__(self,device_index=None): self.device_index=device_index; self.source=Source()
            def __enter__(self): return self.source
            def __exit__(self,*_): return False
        class Recognizer:
            def __init__(self):
                self.dynamic_energy_threshold=False; self.dynamic_energy_adjustment_damping=None; self.phrase_threshold=None; self.calibration=None; self.listen_args=None
            def adjust_for_ambient_noise(self,source,duration): self.calibration=(source,duration)
            def listen(self,source,timeout,phrase_time_limit): self.listen_args=(source,timeout,phrase_time_limit); return Audio()
        class Local:
            def status(self): return {"backend_installed":True,"model_available":True,"model_path":"fake"}
            def transcribe(self,audio,language): return "тихое начало фразы"
        recognizer=Recognizer()
        with patch("speech_recognition.Recognizer",return_value=recognizer), patch("speech_recognition.Microphone",Microphone):
            stt=SpeechToText(silence_timeout=5,backend="local",backend_impl=Local())
            self.assertEqual(stt.listen_once(device_index=3,timeout=6),"тихое начало фразы")
        self.assertTrue(recognizer.dynamic_energy_threshold)
        self.assertEqual(recognizer.dynamic_energy_adjustment_damping,0.5)
        self.assertEqual(recognizer.phrase_threshold,0.2)
        self.assertEqual(recognizer.calibration[1],0.8)
        self.assertEqual(recognizer.listen_args[1:],(6,5))
    def test_stt_capture_accepts_short_pre_speech_pause(self):
        class Audio: pass
        class Source: pass
        class Microphone:
            def __init__(self,device_index=None): self.device_index=device_index
            def __enter__(self): return Source()
            def __exit__(self,*_): return False
        class Recognizer:
            def adjust_for_ambient_noise(self,*_args,**_kwargs): pass
            def listen(self,_source,timeout,phrase_time_limit): self.timeout,self.limit=timeout,phrase_time_limit; return Audio()
        class Local:
            def status(self): return {"backend_installed":True,"model_available":True,"model_path":"fake"}
            def transcribe(self,audio,language): return "обычная фраза"
        recognizer=Recognizer()
        with patch("speech_recognition.Recognizer",return_value=recognizer), patch("speech_recognition.Microphone",Microphone):
            self.assertEqual(SpeechToText(backend="local",backend_impl=Local()).listen_once(timeout=6),"обычная фраза")
        self.assertEqual((recognizer.timeout,recognizer.limit),(6,5))
    def test_stt_capture_timeout_without_speech_preserves_error(self):
        class Source: pass
        class Microphone:
            def __init__(self,device_index=None): self.device_index=device_index
            def __enter__(self): return Source()
            def __exit__(self,*_): return False
        class Recognizer:
            def adjust_for_ambient_noise(self,*_args,**_kwargs): pass
            def listen(self,*_args,**_kwargs): raise TimeoutError("no speech")
        class Local:
            def status(self): return {"backend_installed":True,"model_available":True,"model_path":"fake"}
            def transcribe(self,audio,language): return "unused"
        with patch("speech_recognition.Recognizer",return_value=Recognizer()), patch("speech_recognition.Microphone",Microphone):
            with self.assertRaises(RuntimeError): SpeechToText(backend="local",backend_impl=Local()).listen_once(timeout=6)
    def test_vosk_context_grammar_is_bounded_and_one_turn_only(self):
        class Audio:
            def get_raw_data(self,**_): return b"audio"
        class Recognizer:
            def AcceptWaveform(self,data): self.data=data; return True
            def FinalResult(self): return '{"text":"семьдесят"}'
        class Vosk:
            def Model(self,path): return "model"
            def KaldiRecognizer(self,*args): self.args=args; return Recognizer()
        with tempfile.TemporaryDirectory() as directory:
            backend=LocalVoskSTTBackend(directory,Vosk()); backend.initialize()
            stt=SpeechToText(backend="local",backend_impl=backend); stt.set_context_hints("numeric")
            self.assertEqual(stt._transcribe(Audio()),"семьдесят")
            grammar=json.loads(backend._vosk.args[2]); self.assertIn("70",grammar); self.assertIn("семьдесят",grammar); self.assertIn("100",grammar); self.assertIn("сто",grammar)
            self.assertLessEqual(len(grammar),256)
            stt.clear_context_hints(); self.assertFalse(stt.context_hints_active)
            self.assertEqual(stt._transcribe(Audio()),"семьдесят"); self.assertEqual(len(backend._vosk.args),2)
    def test_voice_pending_context_hints_use_registered_resources_only(self):
        class Resources:
            @staticmethod
            def known_resource_names(): return [("YouTube",("youtube","ютуб")),("Steam",("steam","стим"))]
        class Memory: aliases={}; learned_routines=[]
        class Dialogue: pending={"kind":"browser_clarification","step":"site"}
        class AssistantStub:
            resources=Resources(); memory=Memory(); dialogue=Dialogue(); plugins=type("P",(),{"plugins":{}})()
        class Stt:
            def set_context_hints(self,*args): self.args=args
            def clear_context_hints(self): self.cleared=True
        voice=VoiceController(AssistantStub(),Stt(),None,None,None)
        kind,values=voice._pending_stt_hints(); self.assertEqual(kind,"resource"); self.assertIn("YouTube",values); self.assertIn("ютуб",values); self.assertIn("Steam",values); self.assertIn("стим",values)
        voice.assistant.dialogue.pending={"kind":"volume_clarification","step":"level"}; self.assertEqual(voice._pending_stt_hints(),("numeric",()))
        voice.assistant.dialogue.pending=None; self.assertEqual(voice._pending_stt_hints(),("",()))
    def test_runtime_diagnostics_are_read_only(self):
        class FakeMic:
            def __init__(self): self.checked=False
            def devices(self): self.checked=True; return [{"index":1,"name":"Fake microphone"}]
            def selected_index(self): return 1
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); routines=RoutineManager(memory,RouterStub()); routine=routines.create("Диагностика",[{"intent":"OPEN_APPLICATION","parameters":{"application":"obs"}}]); preset=routines.create_preset(routine["id"],"Default",{}); schedules=ScheduledRoutineManager(memory,routines); schedule=schedules.create_schedule(routine["id"],{"type":"daily","time":"18:00"})
            before=(json.loads(json.dumps(memory.learned_routines)),json.loads(json.dumps(memory.routine_presets)),json.loads(json.dumps(memory.scheduled_routines)),json.loads(json.dumps(memory.routine_execution_history)))
            microphone=FakeMic(); report=RuntimeDiagnostics(memory,microphone).collect(); after=(memory.learned_routines,memory.routine_presets,memory.scheduled_routines,memory.routine_execution_history)
            self.assertTrue(microphone.checked); self.assertEqual(report["Python"]["state"],"PASS"); self.assertEqual(report["Microphone devices"]["state"],"PASS"); self.assertEqual(report["Selected microphone"]["state"],"PASS"); self.assertEqual(report["Audio stream"]["state"],"NOT TESTED"); self.assertIn(report["STT"]["state"],{"READY","FAILED"}); self.assertIn(report["TTS"]["state"],{"READY","FAILED"}); self.assertEqual(report["VoiceController"]["state"],"PASS"); self.assertEqual(report["Assistant"]["state"],"PASS"); self.assertEqual(report["Safety"]["state"],"PASS"); self.assertIn("Python: PASS",format_report(report)); self.assertEqual(before,after); self.assertIsNotNone(preset); self.assertIsNotNone(schedule)
    def test_scheduled_routine_user_management(self):
        class Router:
            def __init__(self): self.calls=[]
            def route(self, command, confirmed=False): self.calls.append(command); return Result(True,"ok")
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); a=Assistant(); a.memory=memory; a.routines=RoutineManager(memory,router,a.events); a.schedules=ScheduledRoutineManager(memory,a.routines); a.parser.memory=memory; a.dialogue.memory=memory; a.skills.memory=memory
            self.assertEqual(a.handle("покажи мои расписания").message,"Запланированных процедур пока нет.")
            routine=a.routines.create("Подготовка",[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}}],parameters=[{"name":"platform","type":"string","required":True}]); untouched=json.loads(json.dumps(routine)); yt=a.routines.create_preset(routine["id"],"YouTube",{"platform":"youtube"}); tw=a.routines.create_preset(routine["id"],"Twitch",{"platform":"twitch"})
            first=a.schedules.create_schedule(routine["id"],{"type":"daily","time":"18:00"},preset_id=yt["id"]); second=a.schedules.create_schedule(routine["id"],{"type":"weekly","weekday":0,"time":"18:00"},preset_id=tw["id"]); one=a.schedules.create_schedule(routine["id"],{"type":"once","at":"2030-01-02T18:00:00+03:00"})
            listed=a.handle("что у меня запланировано"); self.assertTrue(listed.ok); self.assertIn("Подготовка",listed.message); self.assertIn("следующий запуск",listed.message); self.assertEqual(router.calls,[]); self.assertEqual(a.routines.get_execution_history(routine["id"]),[])
            next_run=a.handle("когда следующая подготовка?"); self.assertTrue(next_run.ok); self.assertEqual(a.dialogue.pending["kind"],"schedule_candidates"); self.assertIn("Следующий запуск",a.handle("1").message); self.assertEqual(router.calls,[])
            ambiguous=a.handle("отключи подготовка"); self.assertTrue(ambiguous.ok); self.assertEqual(a.dialogue.pending["kind"],"schedule_candidates"); self.assertTrue(a.handle("1").confirmation_required); self.assertTrue(a.handle("да").ok); self.assertFalse(a.schedules.get_schedule(first["id"])["enabled"]); self.assertTrue(a.schedules.get_schedule(second["id"])["enabled"])
            self.assertTrue(a.handle("включи расписание подготовка для youtube").ok); self.assertTrue(a.schedules.get_schedule(first["id"])["enabled"]); self.assertTrue(a.handle("включи расписание подготовка для youtube").ok)
            other=a.routines.create("Другая",[{"intent":"OPEN_APPLICATION","parameters":{"application":"obs"}}]); daily=a.schedules.create_schedule(other["id"],{"type":"daily","time":"18:00"}); self.assertTrue(a.handle("перенеси другая на 19:00").ok); self.assertEqual(a.schedules.get_schedule(daily["id"])["schedule"]["time"],"19:00"); self.assertTrue(a.handle("измени расписание другая на каждый понедельник в 20:00").ok); self.assertEqual(a.schedules.get_schedule(daily["id"])["schedule"]["type"],"weekly")
            self.assertTrue(a.handle("перенеси завтрашнюю подготовка на 20:00").ok); self.assertIn("20:00",a.schedules.get_schedule(one["id"])["schedule"]["at"])
            prompt=a.handle("удали расписание подготовка для twitch"); self.assertTrue(prompt.confirmation_required); self.assertFalse(a.handle("нет").ok); self.assertIsNotNone(a.schedules.get_schedule(second["id"])); prompt=a.handle("удали расписание подготовка для twitch"); self.assertTrue(prompt.confirmation_required); self.assertTrue(a.handle("да").ok); self.assertIsNone(a.schedules.get_schedule(second["id"])); self.assertIsNotNone(a.routines.get_routine(routine["id"])); self.assertIsNotNone(a.routines.get_preset(tw["id"]))
            self.assertFalse(a.handle("стоп").ok); self.assertIsNotNone(a.schedules.get_schedule(first["id"])); self.assertEqual(a.routines.get_execution_history(routine["id"]),[]); self.assertEqual(a.routines.get_routine(routine["id"])["actions"],untouched["actions"])
            reloaded=ScheduledRoutineManager(MemoryManager(directory),RoutineManager(MemoryManager(directory),router)); self.assertIsNotNone(reloaded.get_schedule(first["id"])); self.assertTrue(a.schedules.run_schedule(first["id"]).ok); self.assertEqual(a.routines.get_last_execution(routine["id"])["trigger"],"scheduled")
    def test_scheduled_learned_routines(self):
        class Router:
            def __init__(self, outcomes=None): self.calls=[]; self.outcomes=list(outcomes or [])
            def route(self, command, confirmed=False): self.calls.append(command); return self.outcomes.pop(0) if self.outcomes else Result(True,"ok")
        with tempfile.TemporaryDirectory() as directory:
            now=datetime(2026,9,14,17,59,tzinfo=timezone(timedelta(hours=3)))
            memory=MemoryManager(directory); router=Router(); routines=RoutineManager(memory,router); schedules=ScheduledRoutineManager(memory,routines,now=lambda: now)
            parser=Assistant().parser; parser.memory=memory
            self.assertEqual(parser.parse("каждый день запускай стрим в 18:00").intent,"CREATE_ROUTINE_SCHEDULE")
            self.assertEqual(parser.parse("запусти стрим каждый понедельник в 18:00").parameters["schedule"]["type"],"weekly")
            routine=routines.create("Стрим",[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}}],parameters=[{"name":"platform","type":"string","required":True}]); original=json.loads(json.dumps(routine))
            once=schedules.create_schedule(routine["id"],{"type":"once","at":(now+timedelta(minutes=1)).isoformat()},parameter_values={"platform":"youtube"})
            daily=schedules.create_schedule(routine["id"],{"type":"daily","time":"18:00"},parameter_values={"platform":"youtube"})
            weekly=schedules.create_schedule(routine["id"],{"type":"weekly","weekday":0,"time":"18:00"},parameter_values={"platform":"youtube"})
            self.assertEqual(len(schedules.list_schedules()),3); self.assertEqual(schedules.get_schedule(once["id"])["routine_id"],routine["id"]); self.assertEqual(once["next_run_at"],(now+timedelta(minutes=1)).isoformat())
            self.assertEqual(schedules.create_schedule(routine["id"],{"type":"daily","time":"18:00"},parameter_values={"platform":"youtube"})["id"],daily["id"])
            self.assertTrue(schedules.update_schedule(daily["id"],schedule={"type":"daily","time":"19:00"})); self.assertTrue(schedules.disable_schedule(daily["id"])); self.assertFalse(schedules.get_schedule(daily["id"])["enabled"]); self.assertTrue(schedules.enable_schedule(daily["id"])["enabled"])
            events=[]; result=schedules.run_schedule(once["id"],events.append); self.assertTrue(result.ok); self.assertEqual(router.calls[-1].parameters,{"application":"youtube"}); self.assertFalse(schedules.get_schedule(once["id"])["enabled"]); self.assertEqual(routines.get_last_execution(routine["id"])["trigger"],"scheduled"); self.assertTrue(events)
            failure=schedules.create_schedule(routine["id"],{"type":"daily","time":"20:00"},parameter_values={}); self.assertFalse(schedules.run_schedule(failure["id"]).ok); self.assertEqual(routines.get_last_execution(routine["id"])["trigger"],"scheduled"); self.assertTrue(schedules.get_schedule(failure["id"])["enabled"]); self.assertIsNone(memory.history and memory.history[-1] if memory.history else None)
            preset=routines.create_preset(routine["id"],"YT",{"platform":"yt"}); with_preset=schedules.create_schedule(routine["id"],{"type":"daily","time":"21:00"},preset_id=preset["id"]); self.assertTrue(schedules.run_schedule(with_preset["id"]).ok); self.assertEqual(router.calls[-1].parameters,{"application":"yt"}); preset_before=routines.get_preset(preset["id"]); self.assertTrue(routines.delete_preset(preset["id"])); self.assertFalse(schedules.run_schedule(with_preset["id"]).ok); self.assertEqual(schedules.get_schedule(with_preset["id"])["status"],"preset_deleted"); self.assertEqual(preset_before["values"],{"platform":"yt"})
            missed=schedules.create_schedule(routine["id"],{"type":"weekly","weekday":0,"time":"18:00"},parameter_values={"platform":"youtube"}); schedules.run_due(now+timedelta(minutes=3)); self.assertEqual(schedules.get_schedule(missed["id"])["status"],"missed")
            cancel_routine=routines.create("Отмена",[{"intent":"OPEN_APPLICATION","parameters":{"application":"wait"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"after"}}]); cancel_schedule=schedules.create_schedule(cancel_routine["id"],{"type":"daily","time":"23:00"})
            entered=threading.Event(); release=threading.Event(); original_route=router.route
            def blocking_route(command, confirmed=False):
                if command.parameters.get("application")=="wait": entered.set(); release.wait(2)
                return original_route(command,confirmed)
            router.route=blocking_route; holder=[]; worker=threading.Thread(target=lambda: holder.append(schedules.run_schedule(cancel_schedule["id"]))); worker.start(); self.assertTrue(entered.wait(2)); self.assertTrue(routines.cancel_active().ok); release.set(); worker.join(2)
            self.assertFalse(holder[0].ok); self.assertEqual(routines.get_last_execution(cancel_routine["id"])["status"],"cancelled"); self.assertEqual(routines.get_last_execution(cancel_routine["id"])["trigger"],"scheduled"); self.assertTrue(schedules.get_schedule(cancel_schedule["id"])["enabled"]); router.route=original_route
            doomed=schedules.create_schedule(routine["id"],{"type":"daily","time":"22:00"},parameter_values={"platform":"youtube"}); self.assertTrue(routines.delete_routine(routine["id"])); self.assertFalse(schedules.get_schedule(doomed["id"])["enabled"]); self.assertEqual(schedules.get_schedule(doomed["id"])["status"],"routine_deleted"); self.assertEqual(original["actions"],[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}}]); self.assertTrue(schedules.delete_schedule(daily["id"])); self.assertIsNone(schedules.get_schedule(daily["id"])); self.assertGreater(len(ScheduledRoutineManager(MemoryManager(directory),RoutineManager(MemoryManager(directory),router)).list_schedules()),0)
    def test_command_registry(self):
        registry=CommandRegistry(); registry.register("PING","example")
        self.assertEqual(registry.plugin_for("PING"),"example"); registry.unregister_plugin("example"); self.assertIsNone(registry.plugin_for("PING"))
    def test_confirmation(self):
        router=CommandRouter(CommandRegistry(),None,PermissionManager(),None)
        self.assertTrue(router.route(Command("SHUTDOWN")).confirmation_required)
    def test_memory_and_aliases_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.aliases["telegram_alias"]="telegram"; memory.save_aliases(); self.assertEqual(MemoryManager(directory).aliases["telegram_alias"],"telegram")
    def test_skill_creation_run_edit_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            router=RouterStub(); skills=SkillManager(MemoryManager(directory),router); skills.create("Gaming",[{"intent":"OPEN_APPLICATION","parameters":{"application":"steam"}}])
            self.assertTrue(skills.run("Gaming").ok); self.assertEqual(len(router.calls),1); self.assertEqual(skills.update("Gaming",description="play")["description"],"play"); self.assertTrue(skills.delete("Gaming"))
    def test_skill_persistence_duplicate_and_import_export(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); skills=SkillManager(memory,RouterStub()); skills.create("Stream",[]); self.assertIsNotNone(skills.duplicate("Stream"))
            exported=Path(directory)/"stream.json"; self.assertTrue(skills.export_skill("Stream",exported)); skills.delete("Stream"); imported=skills.import_skill(exported)
            self.assertEqual(imported["name"],"Stream"); self.assertIsNotNone(SkillManager(MemoryManager(directory),RouterStub()).find("Stream"))
    def test_learning_engine_creates_structured_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            skills=SkillManager(MemoryManager(directory),RouterStub()); result=LearningEngine(None,skills).create_from_explanation('Когда я говорю "стрим", открой OBS, Discord и Telegram, потом поставь громкость 60')
            self.assertTrue(result.ok); self.assertEqual([a["intent"] for a in result.data["skill"]["actions"]],["OPEN_APPLICATION","OPEN_APPLICATION","OPEN_APPLICATION","SET_VOLUME"])
    def test_voice_create_dialogue(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); skills=SkillManager(memory,RouterStub()); dialog=DialogueManager(LearningEngine(None,skills),skills,memory)
            dialog.start_create(); dialog.handle("Стрим"); dialog.handle("Открой OBS и Discord, потом громкость 60"); preview=dialog.handle("сохрани"); self.assertTrue(preview.confirmation_required)
            self.assertTrue(dialog.handle("да").ok); self.assertIsNotNone(skills.find("Стрим"))
    def test_dialogue_edit_delete_and_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); skills=SkillManager(memory,RouterStub()); skills.create("Стрим",[{"intent":"OPEN_APPLICATION","parameters":{"application":"telegram"}}]); dialog=DialogueManager(LearningEngine(None,skills),skills,memory)
            dialog.start_edit("Стрим"); dialog.handle("Добавь открытие YouTube"); dialog.handle("сохрани"); self.assertTrue(dialog.handle("да").ok); dialog.start_alias("телега","telegram"); self.assertTrue(dialog.handle("да").ok); self.assertEqual(memory.aliases["телега"],"telegram")
            dialog.start_delete("Стрим"); self.assertTrue(dialog.handle("да").ok); self.assertIsNone(skills.find("Стрим"))
    def test_plugin_loading_disable_reload_and_failure_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            destination=Path(directory)/"plugins"; shutil.copytree(Path(__file__).parents[1]/"plugins",destination); bad=destination/"broken"; bad.mkdir()
            (bad/"manifest.json").write_text(json.dumps({"id":"broken","name":"Broken","version":"1","description":"x","enabled":True}),encoding="utf-8"); (bad/"plugin.py").write_text("raise RuntimeError('boom')",encoding="utf-8")
            registry=CommandRegistry(); manager=PluginManager(registry,{},destination); manager.load_all(); self.assertIn("applications",manager.plugins); self.assertIn("broken",manager.errors)
            self.assertTrue(manager.set_enabled("applications",False)); self.assertIsNone(registry.plugin_for("OPEN_APPLICATION")); self.assertTrue(manager.set_enabled("applications",True)); self.assertEqual(registry.plugin_for("OPEN_APPLICATION"),"applications"); self.assertTrue(manager.reload("applications"))
    def test_assistant_parser_dialogue_and_plugin_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            assistant=self._isolated_assistant(directory,load_plugins=True); self.assertGreaterEqual(len(assistant.plugins.plugins),7); self.assertTrue(assistant.handle("Создай новый режим").ok); assistant.handle("Работа")
            assistant.handle("Открой блокнот и калькулятор"); preview=assistant.handle("сохрани"); self.assertTrue(preview.confirmation_required); self.assertTrue(assistant.handle("да").ok); self.assertIsNotNone(assistant.skills.find("Работа")); assistant.skills.delete("Работа")
    def test_demonstration_recorder_allowlist_and_cancel(self):
        events=EventBus(); recorder=DemonstrationRecorder(events); recorder.start()
        events.emit("command_routed",command=Command("OPEN_APPLICATION",{"application":"obs"}),result=Result(True,"ok")); events.emit("command_routed",command=Command("SHUTDOWN",{}),result=Result(True,"bad"))
        result=recorder.stop(); self.assertTrue(result.ok); self.assertEqual(result.data["actions"],[{"intent":"OPEN_APPLICATION","parameters":{"application":"obs"}}]); self.assertTrue(recorder.cancel().ok); self.assertEqual(recorder.actions,[])
    def test_assistant_demonstration_dialogue_integration(self):
        with tempfile.TemporaryDirectory() as directory:
            assistant=self._isolated_assistant(directory,load_plugins=True); self.assertTrue(assistant.handle("начни обучение").ok)
            assistant.events.emit("command_routed",command=Command("SET_VOLUME",{"level":60}),result=Result(True,"ok"))
            self.assertTrue(assistant.handle("закончи обучение").ok); self.assertTrue(assistant.handle("Демо").ok); self.assertTrue(assistant.handle("по умолчанию").confirmation_required)
            self.assertTrue(assistant.handle("да").ok); self.assertIsNotNone(assistant.skills.find("Демо")); assistant.skills.delete("Демо")
    def test_custom_command_persists_and_routes_to_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            assistant=Assistant(); assistant.memory=MemoryManager(directory)
            # Test dialogue persists the mapping; parser behavior itself is exercised with its memory.
            skills=SkillManager(assistant.memory,RouterStub()); skills.create("Стрим",[]); dialog=DialogueManager(LearningEngine(None,skills),skills,assistant.memory)
            self.assertTrue(dialog.start_custom_command("начать стрим","Стрим").confirmation_required); self.assertTrue(dialog.handle("да").ok)
            self.assertEqual(MemoryManager(directory).custom_commands["начать стрим"],"Стрим")
    def test_file_search_plugin_returns_metadata(self):
        from plugins.files.plugin import Plugin
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); sample=root/"report.pdf"; sample.write_text("data",encoding="utf-8")
            memory=MemoryManager(directory); memory.settings["plugins"]={"files":{"roots":[directory],"max_results":10}}; plugin=Plugin(); plugin.initialize({"memory":memory})
            result=plugin.execute(Command("SEARCH_FILE",{"query":"найди файл report"})); self.assertTrue(result.ok); self.assertEqual(result.data["results"][0]["name"],"report.pdf"); self.assertIn("size",result.data["results"][0])
    def test_autostart_command_is_non_admin_hkcu(self):
        autostart=WindowsAutostart(); self.assertIn("main.py",autostart.command()); self.assertTrue(autostart.available())
    def test_microphone_selection_persistence_without_backend(self):
        settings={"microphone_index":None}; manager=MicrophoneManager(settings); manager.devices=lambda:[{"index":2,"name":"Mic"}]
        manager.select(2); self.assertEqual(manager.selected_index(),2)
        with self.assertRaises(RuntimeError): manager.select(9)
    def test_voice_controller_wake_to_tts_and_shutdown(self):
        class FakeAssistant:
            def __init__(self): self.memory=type("M",(),{"settings":{"speech_timeout":1}})(); self.states=[]
            def set_state(self,state): self.states.append(state)
            def handle(self,text): self.received=text; return Result(True,"done")
        class FakeStt:
            def __init__(self): self.calls=0
            def listen_once(self,*args): self.calls+=1; return "Jarvis open browser" if self.calls==1 else (_ for _ in ()).throw(RuntimeError("stop"))
        class FakeWake:
            def strip(self,text): return "open browser" if text.startswith("Jarvis") else None
        class FakeMic:
            def selected_index(self): return None
        class FakeTts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        assistant=FakeAssistant(); tts=FakeTts(); controller=VoiceController(assistant,FakeStt(),FakeWake(),FakeMic(),tts)
        controller.start(); time.sleep(.05); controller.stop()
        self.assertEqual(assistant.received,"open browser"); self.assertEqual(tts.messages,["done"]); self.assertIn(AssistantState.LISTENING,assistant.states)
    def test_voice_pending_dialogue_accepts_follow_up_without_wake_word(self):
        class FakeAssistant:
            def __init__(self):
                self.memory=type("M",(),{"settings":{"speech_timeout":1}})(); self.dialogue=type("D",(),{"pending":None})(); self.received=[]; self.states=[]
            def set_state(self,state): self.states.append(state)
            def handle(self,text):
                self.received.append(text)
                if text=="начать": self.dialogue.pending={"kind":"routine_parameters"}; return Result(True,"Уточните параметр.")
                if text=="youtube": self.dialogue.pending=None; return Result(True,"Готово.")
                return Result(False,"Неизвестный ответ.")
        class Wake:
            def strip(self,text): return "начать" if text=="Джарвис, начать" else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        assistant=FakeAssistant(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
        self.assertTrue(voice.process_transcript("Джарвис, начать").ok)
        self.assertTrue(voice.process_transcript("youtube").ok)
        self.assertEqual(assistant.received,["начать","youtube"]); self.assertIsNone(assistant.dialogue.pending)
        self.assertIsNone(voice.process_transcript("youtube")); self.assertEqual(tts.messages,["Уточните параметр.","Готово."])
    def test_voice_short_lived_context_lifecycle_and_priorities(self):
        class Clock:
            def __init__(self): self.value=100.0
            def __call__(self): return self.value
        class Parser:
            def __init__(self): self.calls=[]
            def parse(self,text): self.calls.append(text); return Command("OPEN_BROWSER" if "браузер" in text else "CANCEL_ROUTINE_EXECUTION" if text=="стоп" else "UNKNOWN",{})
        class FakeAssistant:
            def __init__(self,memory):
                self.memory=memory; self.dialogue=type("D",(),{"pending":None})(); self.parser=Parser(); self.received=[]; self.router_calls=[]; self.states=[]
            def set_state(self,state): self.states.append(state)
            def handle(self,text):
                self.received.append(text)
                if self.dialogue.pending:
                    self.dialogue.pending=None; return Result(True,"Параметр принят.")
                if "браузер" in text: self.router_calls.append("OPEN_BROWSER"); return Result(True,"Браузер открыт.")
                return Result(True,"Справочная информация.")
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else "" if text=="Джарвис" else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.settings["conversation_timeout"]=5; before=sorted(Path(directory).glob("*.json"))
            assistant=FakeAssistant(memory); clock=Clock(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); voice._clock=clock
            self.assertEqual(voice.process_transcript("Джарвис").message,"Да, слушаю.")
            created=voice.conversation_context; self.assertEqual(created["session_started_at"],100.0); self.assertEqual(created["last_user_text"],"Джарвис")
            clock.value=101; self.assertTrue(voice.process_transcript("открой браузер").ok); updated=voice.conversation_context; self.assertEqual(updated["last_intent"],"OPEN_BROWSER"); self.assertEqual(updated["last_action"],"OPEN_BROWSER"); self.assertEqual(assistant.router_calls,["OPEN_BROWSER"])
            assistant.dialogue.pending={"kind":"routine_parameters","step":"value"}; self.assertEqual(voice.conversation_context["pending_dialogue_state"],{"kind":"routine_parameters","step":"value"}); parser_calls=len(assistant.parser.calls); self.assertTrue(voice.process_transcript("youtube").ok); self.assertEqual(len(assistant.parser.calls),parser_calls); self.assertEqual(assistant.received[-1],"youtube")
            self.assertIn("Не совсем понял",voice.process_transcript("что ты умеешь?").message); self.assertEqual(assistant.router_calls,["OPEN_BROWSER"])
            clock.value=107; self.assertIsNone(voice.process_transcript("открой браузер")); self.assertIsNone(voice.conversation_context); self.assertIsNone(assistant.dialogue.pending)
            self.assertTrue(voice.process_transcript("Джарвис").ok); self.assertEqual(voice.conversation_context["session_started_at"],107.0); self.assertTrue(voice.process_transcript("стоп").ok); self.assertIsNone(voice.conversation_context)
            self.assertTrue(voice.process_transcript("Джарвис").ok); voice.stop(); self.assertIsNone(voice.conversation_context)
            self.assertEqual(before,sorted(Path(directory).glob("*.json"))); self.assertEqual(tts.messages[0],"Да, слушаю.")
    def test_voice_session_reference_resolution_is_safe_and_bounded(self):
        class Clock:
            def __init__(self): self.value=0.0
            def __call__(self): return self.value
        class Parser:
            def parse(self,text):
                value=text.lower()
                if "браузер" in value: return Command("OPEN_BROWSER",{})
                if "настройки" in value: return Command("OPEN_SETTINGS",{})
                if "громкость 50" in value: return Command("SET_VOLUME",{"level":50})
                if value=="громче": return Command("VOLUME_UP",{})
                if value=="стоп": return Command("CANCEL_ROUTINE_EXECUTION",{})
                if "его" in value: return Command("OPEN_APPLICATION",{"application":"его ещё раз"})
                return Command("UNKNOWN",{})
        class AssistantStub:
            def __init__(self):
                self.memory=type("M",(),{"settings":{"conversation_timeout":3,"speech_timeout":1}})(); self.parser=Parser(); self.dialogue=type("D",(),{"pending":None})(); self.received=[]; self.router_calls=[]; self.states=[]
            def set_state(self,state): self.states.append(state)
            def handle(self,text):
                self.received.append(text)
                if self.dialogue.pending: self.dialogue.pending=None; return Result(True,"Уточнение принято.")
                command=self.parser.parse(text)
                if command.intent!="UNKNOWN": self.router_calls.append(command.intent); return Result(True,"Выполнено.")
                return Result(False,"Неизвестная команда.")
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        assistant=AssistantStub(); clock=Clock(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); voice._clock=clock
        self.assertTrue(voice.process_transcript("Джарвис, открой браузер").ok)
        for reference in ("снова","ещё раз","повтори","сделай это ещё раз","это","открой его ещё раз"):
            self.assertTrue(voice.process_transcript(reference).ok,reference)
        self.assertEqual(assistant.router_calls,["OPEN_BROWSER"]*7); self.assertTrue(all(message.startswith("Повторяю.") for message in tts.messages[1:7]))
        self.assertTrue(voice.process_transcript("открой настройки").ok); self.assertEqual(assistant.router_calls[-1],"OPEN_SETTINGS")
        assistant.dialogue.pending={"kind":"routine_parameters","step":"value"}; before=len(assistant.router_calls); self.assertTrue(voice.process_transcript("снова").ok); self.assertEqual(len(assistant.router_calls),before); self.assertEqual(assistant.received[-1],"снова")
        self.assertIn("Не совсем понял",voice.process_transcript("что ты умеешь?").message); self.assertEqual(len(assistant.router_calls),before)
        self.assertFalse(voice.process_transcript("а её увеличь").ok); self.assertEqual(len(assistant.router_calls),before)
        self.assertTrue(voice.process_transcript("поставь громкость 50").ok); self.assertTrue(voice.process_transcript("а её увеличь").ok); self.assertEqual(assistant.router_calls[-2:],["SET_VOLUME","VOLUME_UP"])
        clock.value=4; self.assertIsNone(voice.process_transcript("снова")); self.assertIsNone(voice.conversation_context)
        self.assertTrue(voice.process_transcript("Джарвис, открой браузер").ok); self.assertTrue(voice.process_transcript("стоп").ok); self.assertIsNone(voice.conversation_context); self.assertIsNone(voice.process_transcript("снова"))
        fresh=VoiceController(assistant,None,Wake(),Mic(),tts); self.assertIsNone(fresh.conversation_context); self.assertIsNone(fresh.process_transcript("снова")); self.assertTrue(fresh.process_transcript("Джарвис,").ok); self.assertFalse(fresh.process_transcript("снова").ok); self.assertEqual(len(assistant.router_calls),before+4)
    def test_voice_session_help_is_informational_and_preserves_safe_reference(self):
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"Браузер открыт.")
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
            self.assertIsNone(voice.process_transcript("что ты умеешь?"))
            self.assertTrue(voice.process_transcript("Джарвис, открой браузер").ok); self.assertEqual(len(router.calls),1)
            help_result=voice.process_transcript("что ты умеешь?"); self.assertTrue(help_result.ok); self.assertIn("безопасные команды",help_result.message); self.assertEqual(len(router.calls),1)
            self.assertEqual(voice.conversation_context["last_intent"],"HELP"); self.assertEqual(voice.conversation_context["last_action"],"OPEN_BROWSER")
            replay=voice.process_transcript("снова"); self.assertTrue(replay.ok); self.assertEqual(len(router.calls),2); self.assertTrue(tts.messages[-1].startswith("Повторяю."))
    def test_voice_session_recap_is_ephemeral_and_informational(self):
        class Clock:
            def __init__(self): self.value=0.0
            def __call__(self): return self.value
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"Браузер открыт.")
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.settings["conversation_timeout"]=2; router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            clock=Clock(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); voice._clock=clock
            self.assertIsNone(voice.process_transcript("что ты сделал?"))
            self.assertTrue(voice.process_transcript("Джарвис, открой браузер").ok); self.assertEqual(len(router.calls),1)
            recap=voice.process_transcript("что ты сделал?"); self.assertTrue(recap.ok); self.assertIn("открытие браузера",recap.message); self.assertEqual(len(router.calls),1); self.assertEqual(tts.messages[-1],recap.message)
            self.assertEqual(voice.conversation_context["last_action"],"OPEN_BROWSER")
            clock.value=3; self.assertIsNone(voice.process_transcript("что ты сделал?")); self.assertIsNone(voice.conversation_context)
            fresh=VoiceController(assistant,None,Wake(),Mic(),tts); fresh._clock=clock; empty= fresh.process_transcript("Джарвис, что ты сделал?"); self.assertTrue(empty.ok); self.assertIn("пока не было",empty.message); self.assertEqual(len(router.calls),1)
    def test_voice_session_response_repeat_is_ephemeral_and_preserves_action_repeat(self):
        class Clock:
            def __init__(self): self.value=0.0
            def __call__(self): return self.value
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"Браузер открыт.")
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.settings["conversation_timeout"]=2; router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            clock=Clock(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); voice._clock=clock
            self.assertIsNone(voice.process_transcript("что ты сказал?"))
            greeting=voice.process_transcript("Джарвис, привет"); self.assertTrue(greeting.ok)
            replay=voice.process_transcript("что ты сказал?"); self.assertTrue(replay.ok); self.assertEqual(replay.message,"Повторяю: "+greeting.message); self.assertEqual(router.calls,[]); self.assertEqual(tts.messages[-1],replay.message)
            self.assertTrue(voice.process_transcript("открой браузер").ok); self.assertEqual(len(router.calls),1)
            self.assertTrue(voice.process_transcript("повтори").ok); self.assertEqual(len(router.calls),2)
            clock.value=3; self.assertIsNone(voice.process_transcript("повтори ответ")); self.assertIsNone(voice.conversation_context)
            fresh=VoiceController(assistant,None,Wake(),Mic(),tts); fresh._clock=clock; empty=fresh.process_transcript("Джарвис, повтори ответ"); self.assertTrue(empty.ok); self.assertIn("пока нечего",empty.message); self.assertEqual(len(router.calls),2)
    def test_voice_pending_dialogue_interruption_has_priority_and_safe_lifecycle(self):
        class Clock:
            def __init__(self): self.value=0.0
            def __call__(self): return self.value
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"Выполнено.")
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.settings["conversation_timeout"]=2; router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            clock=Clock(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); voice._clock=clock
            for request in ("открой сайт","установи громкость","открой папку"):
                self.assertTrue(voice.process_transcript("Джарвис, "+request).ok); self.assertIsNotNone(assistant.dialogue.pending)
                cancelled=voice.process_transcript("отмена"); self.assertTrue(cancelled.ok); self.assertEqual(cancelled.message,"Отменено."); self.assertIsNone(assistant.dialogue.pending); self.assertIsNotNone(voice.conversation_context); self.assertEqual(router.calls,[])
            assistant.dialogue._set_pending({"kind":"routine_parameters","step":"value","missing":[],"index":0,"values":{}})
            self.assertEqual(voice.process_transcript("отменить").message,"Отменено."); self.assertIsNone(assistant.dialogue.pending); self.assertEqual(router.calls,[])
            replay=voice.process_transcript("повтори ответ"); self.assertEqual(replay.message,"Повторяю: Отменено."); self.assertEqual(router.calls,[]); self.assertEqual(tts.messages[-1],replay.message)
            self.assertIn("ничего не жду",voice.process_transcript("что ты сейчас ждёшь?").message)
            self.assertTrue(voice.process_transcript("открой браузер").ok); self.assertEqual(len(router.calls),1)
            self.assertTrue(voice.process_transcript("Джарвис, открой сайт").ok); self.assertIsNotNone(assistant.dialogue.pending)
            stopped=voice.process_transcript("стоп"); self.assertTrue(stopped.ok); self.assertEqual(stopped.message,"Хорошо, прекращаю."); self.assertIsNone(assistant.dialogue.pending); self.assertIsNone(voice.conversation_context); self.assertIsNone(voice.process_transcript("открой браузер")); self.assertEqual(len(router.calls),1)
            self.assertTrue(voice.process_transcript("Джарвис, открой сайт").ok); self.assertIsNotNone(assistant.dialogue.pending)
            goodbye=voice.process_transcript("до свидания"); self.assertTrue(goodbye.ok); self.assertEqual(goodbye.message,"Хорошо, прекращаю."); self.assertIsNone(assistant.dialogue.pending); self.assertIsNone(voice.conversation_context); self.assertEqual(len(router.calls),1)
            self.assertTrue(voice.process_transcript("Джарвис, открой сайт").ok); self.assertIsNotNone(assistant.dialogue.pending)
            self.assertTrue(voice.process_transcript("хватит").ok); self.assertIsNone(assistant.dialogue.pending); self.assertIsNone(voice.conversation_context); self.assertEqual(len(router.calls),1)
            clock.value=3; self.assertIsNone(voice.process_transcript("отмена")); self.assertTrue(voice.process_transcript("Джарвис, открой браузер").ok); self.assertEqual(len(router.calls),2)
    def test_voice_session_controls_preserve_or_reset_only_ephemeral_state(self):
        class Clock:
            def __init__(self): self.value=0.0
            def __call__(self): return self.value
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"Браузер открыт.")
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.settings["conversation_timeout"]=2; router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            clock=Clock(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); voice._clock=clock
            self.assertTrue(voice.process_transcript("Джарвис, открой сайт").ok); pending=assistant.dialogue.pending; history=len(memory.history)
            paused=voice.process_transcript("подожди"); self.assertEqual(paused.message,"Хорошо."); self.assertIs(assistant.dialogue.pending,pending); self.assertEqual(len(memory.history),history); self.assertEqual(router.calls,[])
            self.assertIn("на паузе",voice.process_transcript("YouTube").message); self.assertIs(assistant.dialogue.pending,pending); self.assertEqual(router.calls,[])
            resumed=voice.process_transcript("слушай дальше"); self.assertEqual(resumed.message,"Продолжаю слушать."); self.assertIs(assistant.dialogue.pending,pending); self.assertEqual(len(memory.history),history)
            reset=voice.process_transcript("сбрось разговор"); self.assertEqual(reset.message,"Начинаем сначала."); self.assertIsNone(assistant.dialogue.pending); self.assertIsNotNone(voice.conversation_context); self.assertEqual(len(memory.history),history); self.assertEqual(router.calls,[]); self.assertEqual(tts.messages[-1],reset.message)
            self.assertTrue(voice.process_transcript("открой браузер").ok); self.assertEqual(len(router.calls),1)
            self.assertTrue(voice.process_transcript("подожди").ok); self.assertTrue(voice.process_transcript("продолжай").ok); self.assertEqual(len(router.calls),1)
            assistant.dialogue._set_pending({"kind":"browser_clarification","step":"site"}); self.assertTrue(voice.process_transcript("стоп").ok); self.assertIsNone(assistant.dialogue.pending); self.assertIsNone(voice.conversation_context); self.assertIsNone(voice.process_transcript("открой браузер")); self.assertEqual(len(router.calls),1)
            self.assertTrue(voice.process_transcript("Джарвис, открой браузер").ok); self.assertEqual(len(router.calls),2)
            clock.value=3; self.assertIsNone(voice.process_transcript("продолжай")); self.assertIsNone(voice.conversation_context)
    def test_voice_session_status_query_is_read_only_across_pending_and_pause(self):
        class Clock:
            def __init__(self): self.value=0.0
            def __call__(self): return self.value
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"Выполнено.")
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.settings["conversation_timeout"]=2; router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            clock=Clock(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); voice._clock=clock
            assistant.set_state(AssistantState.READY); ready=voice.process_transcript("Джарвис, ты слушаешь?"); self.assertEqual(ready.message,"Да, я готов."); self.assertEqual(router.calls,[]); self.assertEqual(tts.messages[-1],ready.message)
            pending_result=voice.process_transcript("открой сайт"); self.assertTrue(pending_result.ok); pending=assistant.dialogue.pending; context=voice.conversation_context; history=len(memory.history)
            assistant.set_state(AssistantState.LISTENING); listening=voice.process_transcript("ты сейчас слушаешь?"); self.assertEqual(listening.message,"Да, я слушаю."); self.assertIs(assistant.dialogue.pending,pending); self.assertEqual(voice.conversation_context,context); self.assertEqual(len(memory.history),history); self.assertEqual(router.calls,[])
            self.assertTrue(voice.process_transcript("YouTube").ok); self.assertIsNone(assistant.dialogue.pending); self.assertEqual(len(router.calls),1)
            assistant.set_state(AssistantState.PROCESSING); context=voice.conversation_context; self.assertEqual(voice.process_transcript("какой у тебя статус?").message,"Я обрабатываю запрос."); self.assertEqual(voice.conversation_context,context)
            assistant.set_state(AssistantState.SPEAKING); self.assertEqual(voice.process_transcript("ты слушаешь?").message,"Я сейчас говорю.")
            self.assertTrue(voice.process_transcript("подожди").ok); context=voice.conversation_context; paused=voice.process_transcript("ты на паузе?"); self.assertEqual(paused.message,"Я на паузе."); self.assertEqual(voice.conversation_context,context); self.assertIn("на паузе",voice.process_transcript("открой браузер").message); self.assertEqual(len(router.calls),1)
            self.assertTrue(voice.process_transcript("продолжай").ok); self.assertEqual(len(router.calls),1)
            clock.value=3; self.assertIsNone(voice.process_transcript("ты слушаешь?")); self.assertIsNone(voice.conversation_context)
            self.assertTrue(voice.process_transcript("Джарвис, открой сайт").ok); self.assertTrue(voice.process_transcript("пока").ok); self.assertIsNone(voice.conversation_context); self.assertIsNone(assistant.dialogue.pending)
    def test_voice_contextual_help_is_read_only_for_active_pending_and_paused_sessions(self):
        class Clock:
            def __init__(self): self.value=0.0
            def __call__(self): return self.value
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"Выполнено.")
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.settings["conversation_timeout"]=2; router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            clock=Clock(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); voice._clock=clock
            active=voice.process_transcript("Джарвис, что я могу сейчас сказать?"); self.assertIn("открыть браузер",active.message); self.assertEqual(router.calls,[]); self.assertEqual(tts.messages[-1],active.message)
            self.assertTrue(voice.process_transcript("открой сайт").ok); pending=assistant.dialogue.pending; context=voice.conversation_context; history=len(memory.history)
            browser_help=voice.process_transcript("что можно сейчас сделать?"); self.assertIn("YouTube",browser_help.message); self.assertIs(assistant.dialogue.pending,pending); self.assertEqual(voice.conversation_context,context); self.assertEqual(len(memory.history),history); self.assertEqual(router.calls,[])
            self.assertTrue(voice.process_transcript("YouTube").ok); self.assertEqual(len(router.calls),1)
            self.assertTrue(voice.process_transcript("установи громкость").ok); pending=assistant.dialogue.pending; self.assertIn("от 0 до 100",voice.process_transcript("что ты сейчас от меня ждёшь?").message); self.assertIs(assistant.dialogue.pending,pending); self.assertTrue(voice.process_transcript("50").ok); self.assertEqual(router.calls[-1].parameters,{"level":50})
            self.assertTrue(voice.process_transcript("открой папку").ok); pending=assistant.dialogue.pending; self.assertIn("Загрузки",voice.process_transcript("как я могу с тобой сейчас говорить?").message); self.assertIs(assistant.dialogue.pending,pending); self.assertTrue(voice.process_transcript("Документы").ok); self.assertEqual(router.calls[-1].intent,"OPEN_FOLDER")
            assistant.dialogue._set_pending({"kind":"routine_parameters","step":"value","missing":[{"name":"platform","type":"string","required":True}],"index":0,"values":{}}); pending=assistant.dialogue.pending
            self.assertIn("параметр",voice.process_transcript("что я могу сейчас сказать?").message); self.assertIs(assistant.dialogue.pending,pending); self.assertEqual(router.calls[-1].intent,"OPEN_FOLDER")
            self.assertTrue(voice.process_transcript("сбрось разговор").ok); history=len(memory.history); self.assertTrue(voice.process_transcript("подожди").ok); context=voice.conversation_context
            paused=voice.process_transcript("что я могу сейчас сказать?"); self.assertIn("на паузе",paused.message); self.assertEqual(voice.conversation_context,context); self.assertEqual(len(memory.history),history); self.assertIn("на паузе",voice.process_transcript("открой браузер").message); self.assertEqual(router.calls[-1].intent,"OPEN_FOLDER")
            self.assertTrue(voice.process_transcript("продолжай").ok); clock.value=3; self.assertIsNone(voice.process_transcript("что я могу сейчас сказать?")); self.assertIsNone(voice.conversation_context)
            fresh=voice.process_transcript("Джарвис, что я могу сейчас сказать?"); self.assertIn("открыть браузер",fresh.message); self.assertTrue(voice.process_transcript("открой сайт").ok); self.assertTrue(voice.process_transcript("пока").ok); self.assertIsNone(assistant.dialogue.pending); self.assertIsNone(voice.conversation_context)
    def test_voice_unknown_guidance_is_read_only_and_preserves_pending_dialogues(self):
        class Clock:
            def __init__(self): self.value=0.0
            def __call__(self): return self.value
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"Выполнено.")
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.settings["conversation_timeout"]=2; router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            clock=Clock(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); voice._clock=clock
            self.assertTrue(voice.process_transcript("Джарвис, привет").ok); context=voice.conversation_context; history=len(memory.history)
            unknown=voice.process_transcript("asdfghjkl"); self.assertIn("Не совсем понял",unknown.message); self.assertEqual(voice.conversation_context,context); self.assertEqual(len(memory.history),history); self.assertEqual(router.calls,[]); self.assertEqual(tts.messages[-1],unknown.message)
            self.assertTrue(voice.process_transcript("открой сайт").ok); pending=assistant.dialogue.pending; context=voice.conversation_context; history=len(memory.history)
            self.assertIn("название сайта",voice.process_transcript("asdfghjkl").message); self.assertIs(assistant.dialogue.pending,pending); self.assertEqual(voice.conversation_context,context); self.assertEqual(len(memory.history),history); self.assertEqual(router.calls,[]); self.assertTrue(voice.process_transcript("YouTube").ok); self.assertEqual(len(router.calls),1)
            self.assertTrue(voice.process_transcript("установи громкость").ok); pending=assistant.dialogue.pending; self.assertIn("громкость",voice.process_transcript("громко").message); self.assertIs(assistant.dialogue.pending,pending); self.assertTrue(voice.process_transcript("50").ok); self.assertEqual(router.calls[-1].parameters,{"level":50})
            self.assertTrue(voice.process_transcript("открой папку").ok); pending=assistant.dialogue.pending; self.assertIn("Выберите",voice.process_transcript("где-то").message); self.assertIs(assistant.dialogue.pending,pending); self.assertTrue(voice.process_transcript("Документы").ok); self.assertEqual(router.calls[-1].intent,"OPEN_FOLDER")
            assistant.dialogue._set_pending({"kind":"routine_parameters","step":"value","missing":[{"name":"count","type":"integer","required":True}],"index":0,"values":{}}); pending=assistant.dialogue.pending; context=voice.conversation_context; history=len(memory.history)
            routine_unknown=voice.process_transcript("не число")
            self.assertIn("числовое",routine_unknown.message)
            self.assertIs(assistant.dialogue.pending,pending); self.assertEqual(voice.conversation_context,context); self.assertEqual(len(memory.history),history)
            self.assertTrue(voice.process_transcript("сбрось разговор").ok); self.assertTrue(voice.process_transcript("подожди").ok); context=voice.conversation_context; paused=voice.process_transcript("непонятно"); self.assertIn("на паузе",paused.message); self.assertEqual(voice.conversation_context,context); self.assertEqual(router.calls[-1].intent,"OPEN_FOLDER")
            self.assertTrue(voice.process_transcript("продолжай").ok); self.assertTrue(voice.process_transcript("сбрось разговор").ok); context=voice.conversation_context; after_reset=voice.process_transcript("непонятно"); self.assertIn("Не совсем понял",after_reset.message); self.assertEqual(voice.conversation_context,context)
            clock.value=3; self.assertIsNone(voice.process_transcript("непонятно")); self.assertIsNone(voice.conversation_context); self.assertTrue(voice.process_transcript("Джарвис, открой сайт").ok); self.assertTrue(voice.process_transcript("пока").ok); self.assertIsNone(voice.process_transcript("непонятно"))
    def test_voice_session_acknowledgements_are_read_only_and_preserve_dialogues(self):
        class Clock:
            def __init__(self): self.value=0.0
            def __call__(self): return self.value
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"Выполнено.")
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.settings["conversation_timeout"]=2; router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            clock=Clock(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); voice._clock=clock
            self.assertTrue(voice.process_transcript("Джарвис, привет").ok); context=voice.conversation_context; history=len(memory.history)
            active=voice.process_transcript("ясно"); self.assertEqual(active.message,"Хорошо."); self.assertEqual(voice.conversation_context,context); self.assertEqual(len(memory.history),history); self.assertEqual(router.calls,[]); self.assertEqual(tts.messages[-1],active.message)
            info=voice.process_transcript("что я могу сейчас сказать?"); context=voice.conversation_context; self.assertTrue(info.ok); self.assertEqual(voice.process_transcript("понятно").message,"Хорошо."); self.assertEqual(voice.conversation_context,context)
            self.assertTrue(voice.process_transcript("открой сайт").ok); pending=assistant.dialogue.pending; self.assertIn("жду ответ",voice.process_transcript("понял").message); self.assertIs(assistant.dialogue.pending,pending); self.assertTrue(voice.process_transcript("YouTube").ok); self.assertEqual(len(router.calls),1)
            self.assertTrue(voice.process_transcript("установи громкость").ok); pending=assistant.dialogue.pending; self.assertIn("жду ответ",voice.process_transcript("ладно").message); self.assertIs(assistant.dialogue.pending,pending); self.assertTrue(voice.process_transcript("50").ok); self.assertEqual(router.calls[-1].parameters,{"level":50})
            self.assertTrue(voice.process_transcript("открой папку").ok); pending=assistant.dialogue.pending; self.assertIn("жду ответ",voice.process_transcript("принято").message); self.assertIs(assistant.dialogue.pending,pending); self.assertTrue(voice.process_transcript("Документы").ok); self.assertEqual(router.calls[-1].intent,"OPEN_FOLDER")
            assistant.dialogue._set_pending({"kind":"routine_parameters","step":"value","missing":[{"name":"count","type":"integer","required":True}],"index":0,"values":{}}); pending=assistant.dialogue.pending; self.assertIn("жду ответ",voice.process_transcript("хорошо").message); self.assertIs(assistant.dialogue.pending,pending); self.assertEqual(router.calls[-1].intent,"OPEN_FOLDER")
            self.assertTrue(voice.process_transcript("сбрось разговор").ok); self.assertTrue(voice.process_transcript("подожди").ok); context=voice.conversation_context; paused=voice.process_transcript("понял"); self.assertIn("остаюсь на паузе",paused.message); self.assertEqual(voice.conversation_context,context); self.assertIn("на паузе",voice.process_transcript("открой браузер").message); self.assertEqual(router.calls[-1].intent,"OPEN_FOLDER")
            self.assertTrue(voice.process_transcript("продолжай").ok); clock.value=3; self.assertIsNone(voice.process_transcript("понял")); self.assertIsNone(voice.conversation_context)
            self.assertTrue(voice.process_transcript("Джарвис, открой сайт").ok); self.assertTrue(voice.process_transcript("до свидания").ok); self.assertIsNone(assistant.dialogue.pending); self.assertIsNone(voice.conversation_context)
    def test_voice_session_social_turns_are_safe_and_close_context(self):
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"ok")
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
            self.assertIsNone(voice.process_transcript("спасибо"))
            greeting=voice.process_transcript("Джарвис, привет"); self.assertTrue(greeting.ok); self.assertEqual(greeting.message,"Здравствуйте. Чем могу помочь?")
            thanks=voice.process_transcript("спасибо"); self.assertTrue(thanks.ok); self.assertEqual(thanks.message,"Пожалуйста."); self.assertEqual(router.calls,[])
            goodbye=voice.process_transcript("пока"); self.assertTrue(goodbye.ok); self.assertEqual(goodbye.message,"До свидания."); self.assertIsNone(voice.conversation_context); self.assertIsNone(assistant.dialogue.pending); self.assertEqual(tts.messages,[greeting.message,thanks.message,goodbye.message])
            self.assertIsNone(voice.process_transcript("помощь")); self.assertTrue(voice.process_transcript("Джарвис, до свидания").ok); self.assertEqual(router.calls,[])
    def test_voice_pending_dialogue_recap_preserves_existing_state(self):
        class Clock:
            def __init__(self): self.value=0.0
            def __call__(self): return self.value
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,command.intent)
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        class SilentStt:
            def listen_once(self,*_): threading.Event().wait(.05); return ""
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.settings["conversation_timeout"]=2; router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events); assistant.routines.create("Стрим",[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}}],parameters=[{"name":"platform","type":"string","required":True}])
            clock=Clock(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); voice._clock=clock
            self.assertIn("ничего не жду",voice.process_transcript("Джарвис, что тебе нужно?").message); self.assertEqual(router.calls,[])
            prompt=voice.process_transcript("Джарвис, открой сайт"); pending=assistant.dialogue.pending; history=len(memory.history); recap=voice.process_transcript("что ты сейчас ждёшь?"); self.assertIn("адрес сайта",recap.message); self.assertIs(assistant.dialogue.pending,pending); self.assertEqual(len(memory.history),history); self.assertEqual(router.calls,[]); self.assertEqual(tts.messages[-1],recap.message); self.assertTrue(voice.process_transcript("YouTube").ok); self.assertEqual(router.calls[-1].intent,"OPEN_YOUTUBE")
            self.assertTrue(voice.process_transcript("Джарвис, установи громкость").ok); self.assertIn("громкости от 0 до 100",voice.process_transcript("что тебе нужно?").message); self.assertTrue(voice.process_transcript("50").ok); self.assertEqual(router.calls[-1].parameters,{"level":50})
            self.assertTrue(voice.process_transcript("Джарвис, открой папку").ok); self.assertIn("Загрузки, Документы или Рабочий стол",voice.process_transcript("что ты ждёшь?").message); self.assertTrue(voice.process_transcript("Документы").ok); self.assertEqual(router.calls[-1].intent,"OPEN_FOLDER")
            self.assertTrue(voice.process_transcript("Джарвис, стрим").ok); routine_pending=assistant.dialogue.pending; self.assertIn("параметр",voice.process_transcript("что ты сейчас ждёшь?").message); self.assertIs(assistant.dialogue.pending,routine_pending); self.assertTrue(voice.process_transcript("YouTube").ok); self.assertEqual(router.calls[-1].parameters,{"application":"YouTube"})
            self.assertTrue(voice.process_transcript("Джарвис, открой сайт").ok); before=len(router.calls); self.assertIn("адрес сайта",voice.process_transcript("что тебе нужно?").message); self.assertTrue(voice.process_transcript("открой браузер").ok); self.assertIsNone(assistant.dialogue.pending); self.assertEqual(len(router.calls),before+1)
            self.assertTrue(voice.process_transcript("Джарвис, установи громкость").ok); clock.value=3; self.assertIsNone(voice.process_transcript("что тебе нужно?")); self.assertIsNone(assistant.dialogue.pending)
            clock.value=4; self.assertTrue(voice.process_transcript("Джарвис, открой папку").ok); self.assertTrue(voice.process_transcript("стоп").ok); self.assertIsNone(assistant.dialogue.pending); self.assertIsNone(voice.conversation_context)
            assistant.dialogue.start_browser_clarification(); restart=VoiceController(assistant,SilentStt(),Wake(),Mic(),tts); self.assertTrue(restart.start()); self.assertIsNone(assistant.dialogue.pending); restart.stop()
    def test_voice_pending_value_correction_is_validated_before_execution(self):
        class Clock:
            def __init__(self): self.value=0.0
            def __call__(self): return self.value
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,command.intent)
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        class SilentStt:
            def listen_once(self,*_): threading.Event().wait(.05); return ""
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.settings["conversation_timeout"]=2; router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events); assistant.routines.create("Стрим",[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}}],parameters=[{"name":"platform","type":"string","required":True}])
            clock=Clock(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); voice._clock=clock
            self.assertTrue(voice.process_transcript("Джарвис, установи громкость").ok); invalid=voice.process_transcript("Нет, 150"); self.assertFalse(invalid.ok); self.assertIsNotNone(assistant.dialogue.pending); self.assertIn("громкости от 0 до 100",voice.process_transcript("что тебе нужно?").message); self.assertEqual(router.calls,[])
            corrected=voice.process_transcript("не 50, а 70"); self.assertTrue(corrected.ok); self.assertEqual(router.calls[-1].parameters,{"level":70}); self.assertEqual([call.parameters for call in router.calls],[{"level":70}]); self.assertTrue(tts.messages[-1].startswith("Хорошо, исправляю."))
            self.assertTrue(voice.process_transcript("Джарвис, открой сайт").ok); self.assertTrue(voice.process_transcript("исправь на https://example.com").ok); self.assertEqual(router.calls[-1].intent,"OPEN_BROWSER"); self.assertEqual(router.calls[-1].parameters,{"url":"https://example.com"})
            self.assertTrue(voice.process_transcript("Джарвис, открой папку").ok); self.assertTrue(voice.process_transcript("поставь Загрузки вместо Документы").ok); self.assertEqual(router.calls[-1].parameters,{"query":"загрузки"})
            self.assertTrue(voice.process_transcript("Джарвис, стрим").ok); self.assertTrue(voice.process_transcript("исправь на YouTube").ok); self.assertEqual(router.calls[-1].parameters,{"application":"YouTube"}); self.assertTrue(tts.messages[-1].startswith("Хорошо, исправляю."))
            self.assertTrue(voice.process_transcript("Джарвис, установи громкость").ok); self.assertTrue(voice.process_transcript("50").ok); self.assertEqual(router.calls[-1].parameters,{"level":50}); self.assertFalse(tts.messages[-1].startswith("Хорошо, исправляю."))
            self.assertTrue(voice.process_transcript("Джарвис, открой сайт").ok); before=len(router.calls); self.assertTrue(voice.process_transcript("открой браузер").ok); self.assertIsNone(assistant.dialogue.pending); self.assertEqual(len(router.calls),before+1)
            self.assertTrue(voice.process_transcript("Джарвис, установи громкость").ok); clock.value=3; self.assertIsNone(voice.process_transcript("Нет, 70")); self.assertIsNone(assistant.dialogue.pending)
            clock.value=4; self.assertTrue(voice.process_transcript("Джарвис, открой папку").ok); self.assertTrue(voice.process_transcript("стоп").ok); self.assertIsNone(assistant.dialogue.pending); self.assertIsNone(voice.conversation_context)
            assistant.dialogue.start_volume_clarification(); restart=VoiceController(assistant,SilentStt(),Wake(),Mic(),tts); self.assertTrue(restart.start()); self.assertIsNone(assistant.dialogue.pending); restart.stop()
    def test_voice_session_deterministic_clarification_uses_dialogue_and_router(self):
        class Clock:
            def __init__(self): self.value=0.0
            def __call__(self): return self.value
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,command.intent,{"parameters":command.parameters})
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        class SilentStt:
            def listen_once(self,*_): threading.Event().wait(.05); return ""
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.settings["conversation_timeout"]=2; router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events); assistant.schedules=ScheduledRoutineManager(memory,assistant.routines)
            assistant.routines.create("Стрим",[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}}],parameters=[{"name":"platform","type":"string","required":True}])
            clock=Clock(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); voice._clock=clock
            prompt=voice.process_transcript("Джарвис, открой сайт"); self.assertTrue(prompt.ok); self.assertEqual(prompt.message,"Какой сайт открыть?"); self.assertEqual(assistant.dialogue.pending["kind"],"browser_clarification"); self.assertEqual(tts.messages[-1],prompt.message); self.assertEqual(router.calls,[])
            invalid=voice.process_transcript("неизвестный сайт"); self.assertFalse(invalid.ok); self.assertIsNotNone(assistant.dialogue.pending); self.assertEqual(router.calls,[])
            site_result=voice.process_transcript("YouTube"); self.assertTrue(site_result.ok,site_result.message); self.assertEqual(router.calls[-1].intent,"OPEN_YOUTUBE"); self.assertIsNone(assistant.dialogue.pending)
            prompt=voice.process_transcript("Джарвис, установи громкость"); self.assertEqual(prompt.message,"Какую громкость установить?"); self.assertEqual(tts.messages[-1],prompt.message); self.assertFalse(voice.process_transcript("сто один").ok); self.assertIsNotNone(assistant.dialogue.pending); self.assertTrue(voice.process_transcript("50").ok); self.assertEqual(router.calls[-1].parameters,{"level":50})
            routine_prompt=voice.process_transcript("Джарвис, стрим"); self.assertTrue(routine_prompt.ok); self.assertEqual(assistant.dialogue.pending["kind"],"routine_parameters"); self.assertTrue(voice.process_transcript("YouTube").ok); self.assertEqual(router.calls[-1].parameters,{"application":"YouTube"})
            prompt=voice.process_transcript("Джарвис, открой сайт"); self.assertTrue(prompt.ok); before=len(router.calls); self.assertTrue(voice.process_transcript("открой браузер").ok); self.assertIsNone(assistant.dialogue.pending); self.assertEqual(router.calls[-1].intent,"OPEN_BROWSER"); self.assertEqual(len(router.calls),before+1)
            self.assertTrue(voice.process_transcript("Джарвис, открой сайт").ok); clock.value=3; self.assertIsNone(voice.process_transcript("YouTube")); self.assertIsNone(assistant.dialogue.pending); self.assertIsNone(voice.conversation_context)
            clock.value=4; self.assertTrue(voice.process_transcript("Джарвис, установи громкость").ok); self.assertTrue(voice.process_transcript("стоп").ok); self.assertIsNone(assistant.dialogue.pending); self.assertIsNone(voice.conversation_context)
            assistant.dialogue.start_browser_clarification(); restart=VoiceController(assistant,SilentStt(),Wake(),Mic(),tts); self.assertTrue(restart.start()); self.assertIsNone(assistant.dialogue.pending); restart.stop()
            before=len(router.calls); self.assertTrue(voice.process_transcript("Джарвис, помощь").ok); self.assertEqual(len(router.calls),before)
    def test_voice_session_folder_clarification_is_validated_and_bounded(self):
        class Clock:
            def __init__(self): self.value=0.0
            def __call__(self): return self.value
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,command.intent)
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text)
            def is_speaking(self): return False
        class SilentStt:
            def listen_once(self,*_): threading.Event().wait(.05); return ""
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.settings["conversation_timeout"]=2; router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory
            clock=Clock(); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts); voice._clock=clock
            prompt=voice.process_transcript("Джарвис, открой папку"); self.assertTrue(prompt.ok); self.assertEqual(assistant.dialogue.pending["kind"],"folder_clarification"); self.assertIn("Какую папку",tts.messages[-1]); self.assertEqual(router.calls,[])
            invalid=voice.process_transcript("музыку"); self.assertFalse(invalid.ok); self.assertIsNotNone(assistant.dialogue.pending); self.assertEqual(router.calls,[])
            self.assertTrue(voice.process_transcript("Документы").ok); self.assertEqual(router.calls[-1].intent,"OPEN_FOLDER"); self.assertEqual(router.calls[-1].parameters,{"query":"документы"}); self.assertIsNone(assistant.dialogue.pending)
            self.assertTrue(voice.process_transcript("Джарвис, открой папку").ok); before=len(router.calls); self.assertTrue(voice.process_transcript("открой браузер").ok); self.assertIsNone(assistant.dialogue.pending); self.assertEqual(router.calls[-1].intent,"OPEN_BROWSER"); self.assertEqual(len(router.calls),before+1)
            self.assertTrue(voice.process_transcript("Джарвис, открой папку").ok); clock.value=3; self.assertIsNone(voice.process_transcript("Документы")); self.assertIsNone(assistant.dialogue.pending)
            clock.value=4; self.assertTrue(voice.process_transcript("Джарвис, открой папку").ok); self.assertTrue(voice.process_transcript("стоп").ok); self.assertIsNone(assistant.dialogue.pending); self.assertIsNone(voice.conversation_context)
            assistant.dialogue.start_folder_clarification(); restart=VoiceController(assistant,SilentStt(),Wake(),Mic(),tts); self.assertTrue(restart.start()); self.assertIsNone(assistant.dialogue.pending); restart.stop()
    def test_voice_pipeline_routines_and_safe_errors(self):
        class Router:
            def __init__(self): self.calls=[]
            def route(self, command, confirmed=False): self.calls.append(command); return Result(True,"ok")
        class Wake:
            def strip(self,text):
                return text.split(",",1)[1].strip() if text.lower().startswith("джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self,fail=False): self.messages=[]; self.fail=fail
            def say(self,text):
                if self.fail: raise RuntimeError("tts")
                self.messages.append(text); return True
            def is_speaking(self): return False
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events); assistant.schedules=ScheduledRoutineManager(memory,assistant.routines); assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            routine=assistant.routines.create("Подготовка к стриму",[{"intent":"OPEN_APPLICATION","parameters":{"application":"obs"}}],aliases=["подготовь меня к стриму"]); parameter=assistant.routines.create("Подготовь стрим",[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"telegram"}}],parameters=[{"name":"platform","type":"string","required":True}])
            tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
            self.assertTrue(voice.process_transcript("Джарвис, подготовь меня к стриму").ok); self.assertEqual(len(router.calls),1); self.assertEqual(assistant.state,AssistantState.READY)
            self.assertTrue(voice.process_transcript("Джарвис, подготовь стрим для YouTube без Telegram").ok); self.assertEqual(router.calls[-1].parameters,{"application":"youtube"})
            question=voice.process_transcript("Джарвис, подготовь стрим"); self.assertTrue(question.ok); self.assertEqual(assistant.dialogue.pending["kind"],"routine_parameters"); self.assertTrue(voice.process_transcript("Джарвис, YouTube").ok); self.assertEqual(router.calls[-2].parameters,{"application":"YouTube"})
            question=voice.process_transcript("Джарвис, подготовь стрим"); self.assertTrue(question.ok); self.assertTrue(voice.process_transcript("Джарвис, стоп").ok); self.assertIsNone(assistant.dialogue.pending)
            assistant.dialogue.start_routine_candidates([routine,parameter]); self.assertTrue(voice.process_transcript("Джарвис, стоп").ok); self.assertIsNone(assistant.dialogue.pending); self.assertIsNotNone(assistant.routines.get_routine(routine["id"]))
            before=len(router.calls); self.assertTrue(voice.process_transcript("Джарвис, когда я последний раз запускал подготовка к стриму?").ok); self.assertEqual(len(router.calls),before)
            self.assertTrue(voice.process_transcript("Джарвис, покажи мои расписания").ok); self.assertTrue(voice.process_transcript("Джарвис, поставь подготовка к стриму на завтра в 18:00").ok); self.assertEqual(len(assistant.schedules.list_schedules()),1)
            self.assertIsNone(voice.process_transcript("")); self.assertIn("Не совсем понял",voice.process_transcript("без wake word").message); self.assertGreaterEqual(len(tts.messages),8); self.assertEqual(assistant.routines.find("Подготовка к стриму")["id"],routine["id"]); self.assertIsNotNone(parameter)
            bad_tts=VoiceController(assistant,None,Wake(),Mic(),Tts(fail=True)); self.assertTrue(bad_tts.process_transcript("Джарвис, покажи мои расписания").ok); self.assertEqual(assistant.state,AssistantState.READY)
        class ErrorStt:
            def listen_once(self,*args): raise RuntimeError("stt")
        class MiniAssistant:
            def __init__(self): self.memory=type("M",(),{"settings":{"speech_timeout":1}})(); self.states=[]
            def set_state(self,state): self.states.append(state)
            def handle(self,text): return Result(True,"ok")
        mini=MiniAssistant(); errors=[]; controller=VoiceController(mini,ErrorStt(),Wake(),Mic(),Tts(),lambda text,result:errors.append(result)); controller.start(); time.sleep(.06); controller.stop(); self.assertTrue(errors); self.assertEqual(mini.states[-1],AssistantState.READY)
    def test_voice_controller_stop_blocks_duplicate_while_stt_is_finishing(self):
        class AssistantStub:
            def __init__(self): self.memory=type("M",(),{"settings":{"speech_timeout":1}})(); self.states=[]
            def set_state(self,state): self.states.append(state)
            def handle(self,text): return Result(True,"ok")
        class BlockingStt:
            def __init__(self): self.entered=threading.Event(); self.release=threading.Event()
            def listen_once(self,*_): self.entered.set(); self.release.wait(4); return ""
        class Wake:
            def strip(self,text): return None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def say(self,text): return True
            def is_speaking(self): return False
        assistant=AssistantStub(); stt=BlockingStt(); controller=VoiceController(assistant,stt,Wake(),Mic(),Tts()); self.assertTrue(controller.start()); self.assertTrue(stt.entered.wait(1)); controller.stop(); self.assertFalse(controller.listening); self.assertFalse(controller.start()); self.assertEqual(assistant.states[-1],AssistantState.READY); stt.release.set(); controller._thread.join(2); self.assertTrue(controller.start()); controller.stop()
    def test_natural_language_parser_and_multi_action(self):
        with tempfile.TemporaryDirectory() as directory:
            parser=__import__("core.intent_parser",fromlist=["IntentParser"]).IntentParser(MemoryManager(directory))
            self.assertEqual(parser.parse("Джарвис, можешь открыть мне Telegram?").intent,"OPEN_APPLICATION")
            url=parser.parse("открой https://example.com"); self.assertEqual((url.intent,url.parameters["url"]),("OPEN_BROWSER","https://example.com"))
            self.assertEqual(parser.parse("сделай скрин").intent,"TAKE_SCREENSHOT")
            multi=parser.parse("открой OBS, Discord и Telegram"); self.assertEqual(multi.intent,"MULTI_ACTION"); self.assertEqual(len(multi.actions),3)
            self.assertEqual(parser.parse("поставь громкость 75 процентов").parameters["level"],75)
    def test_inline_skill_and_custom_command_dialogue(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); skills=SkillManager(memory,RouterStub()); dialog=DialogueManager(LearningEngine(None,skills),skills,memory)
            dialog.start_create("Стрим","Открой OBS и Discord, потом громкость 60"); preview=dialog.handle("сохрани"); self.assertTrue(preview.confirmation_required); self.assertTrue(dialog.handle("да").ok)
            ask=dialog.start_custom_command("начать стрим",None); self.assertTrue(ask.ok); confirm=dialog.handle("запускать режим Стрим"); self.assertTrue(confirm.confirmation_required); self.assertTrue(dialog.handle("да").ok)
            self.assertEqual(memory.custom_commands["начать стрим"],"стрим")
    def test_alias_conflict_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.aliases["телега"]="telegram"; dialog=DialogueManager(LearningEngine(None,SkillManager(memory,RouterStub())),SkillManager(memory,RouterStub()),memory)
            result=dialog.start_alias("телега","discord"); self.assertTrue(result.confirmation_required); self.assertIn("уже",result.message)
    def test_draft_accumulates_and_cancel_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); skills=SkillManager(memory,RouterStub()); dialog=DialogueManager(LearningEngine(None,skills),skills,memory)
            dialog.start_create("Стрим"); self.assertTrue(dialog.handle("Открой OBS и Discord").ok); self.assertTrue(dialog.handle("поставь громкость 60").ok)
            preview=dialog.handle("сохрани"); self.assertTrue(preview.confirmation_required); self.assertTrue(dialog.handle("отмена").ok); self.assertIsNone(skills.find("Стрим"))
    def test_edit_draft_does_not_mutate_until_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); skills=SkillManager(memory,RouterStub()); original=skills.create("Стрим",[{"intent":"OPEN_APPLICATION","parameters":{"application":"obs"}}])
            dialog=DialogueManager(LearningEngine(None,skills),skills,memory); dialog.start_edit("Стрим"); dialog.handle("Добавь Telegram"); dialog.handle("отмена")
            self.assertEqual(skills.find("Стрим")["actions"],original["actions"])
    def test_russian_volume_numbers_and_invalid_values(self):
        parser=__import__("core.intent_parser",fromlist=["IntentParser"]).IntentParser(MemoryManager(tempfile.mkdtemp()))
        self.assertEqual(parser.parse("поставь громкость семьдесят пять").parameters["level"],75)
        self.assertEqual(parser.parse("сделай громкость сто").parameters["level"],100)
        self.assertEqual(parser.parse("поставь громкость сто один").intent,"INVALID_VOLUME")
    def test_edit_move_swap_remove_and_cancel_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); skills=SkillManager(memory,RouterStub()); original=skills.create("Стрим",[{"intent":"OPEN_APPLICATION","parameters":{"application":"obs"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"discord"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"telegram"}}])
            dialog=DialogueManager(LearningEngine(None,skills),skills,memory); dialog.start_edit("Стрим"); self.assertTrue(dialog.handle("перемести Telegram в начало").ok)
            self.assertTrue(dialog.handle("поменяй первое и второе действие местами").ok); self.assertTrue(dialog.handle("удали третье действие").ok); dialog.handle("отмена")
            self.assertEqual(skills.find("Стрим")["actions"],original["actions"])
    def test_ambiguous_application_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); parser=__import__("core.intent_parser",fromlist=["IntentParser"]).IntentParser(memory)
            command=parser.parse("открой дис"); self.assertEqual(command.intent,"AMBIGUOUS_APPLICATION"); self.assertEqual(command.entities["confidence"],"LOW")
            dialog=DialogueManager(LearningEngine(None,SkillManager(memory,RouterStub())),SkillManager(memory,RouterStub()),memory)
            ask=dialog.start_ambiguous_application(command.parameters["application"]); self.assertTrue(ask.confirmation_required)
            accepted=dialog.handle("да"); self.assertEqual(accepted.data["command"]["parameters"]["application"],"discord")
    def test_edit_index_move_replace_and_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); skills=SkillManager(memory,RouterStub()); skills.create("Стрим",[{"intent":"OPEN_APPLICATION","parameters":{"application":"obs"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"discord"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"telegram"}}])
            dialog=DialogueManager(LearningEngine(None,skills),skills,memory); dialog.start_edit("Стрим"); self.assertTrue(dialog.handle("перемести Telegram в начало").ok)
            self.assertTrue(dialog.handle("замени второе действие на Steam").ok); dialog.pending["updated_at"]=time.monotonic()-DialogueManager.TIMEOUT_SECONDS-1; self.assertFalse(dialog.handle("покажи действия").ok)
    def test_index_move_final_order(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); skills=SkillManager(memory,RouterStub()); skills.create("Стрим",[{"intent":"OPEN_APPLICATION","parameters":{"application":"obs"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"discord"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"telegram"}}])
            dialog=DialogueManager(LearningEngine(None,skills),skills,memory); dialog.start_edit("Стрим"); self.assertTrue(dialog.handle("перемести третье действие на первое место").ok)
            self.assertEqual([a["parameters"]["application"] for a in dialog.pending["actions"]],["telegram","obs","discord"])
    def test_learned_routine_persistence_and_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=RouterStub(); routines=RoutineManager(memory,router)
            routine=routines.create("Подготовка к стриму",[{"intent":"OPEN_APPLICATION","parameters":{"application":"obs"}}],aliases=["подготовь стрим"])
            self.assertTrue(routines.run("подготовь стрим").ok); self.assertEqual(routine["usage_count"],1)
            loaded=RoutineManager(MemoryManager(directory),router); self.assertIsNotNone(loaded.find("подготовь стрим"))
    def test_routine_execution_reliability(self):
        class SequenceRouter:
            def __init__(self,outcomes): self.outcomes,self.calls=list(outcomes),[]
            def route(self,command,confirmed=False): self.calls.append(command); return self.outcomes.pop(0)
        actions=[{"intent":"ONE","parameters":{"value":1}},{"intent":"TWO","parameters":{"value":2}},{"intent":"THREE","parameters":{"value":3}}]
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=SequenceRouter([Result(True,"one"),Result(True,"two"),Result(True,"three")]); manager=RoutineManager(memory,router); routine=manager.create("Последовательность",actions)
            success=manager.run("Последовательность")
            self.assertTrue(success.ok); self.assertEqual([command.intent for command in router.calls],["ONE","TWO","THREE"]); self.assertEqual(routine["usage_count"],1); self.assertEqual(RoutineManager(MemoryManager(directory),router).find("Последовательность")["usage_count"],1)
            self.assertFalse(manager.create("Пустая",[]).get("usage_count")); self.assertFalse(manager.run("Пустая").ok)
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=SequenceRouter([Result(True,"one"),Result(False,"two failed"),Result(True,"three")]); manager=RoutineManager(memory,router); routine=manager.create("Сбой",actions); before=json.loads(json.dumps(routine))
            partial=manager.run("Сбой")
            self.assertFalse(partial.ok); self.assertIn("не полностью",partial.message); self.assertEqual(partial.data["results"],["one","two failed","three"]); self.assertEqual([command.intent for command in router.calls],["ONE","TWO","THREE"]); self.assertEqual(routine["usage_count"],0); self.assertEqual(routine["actions"],before["actions"])
            invalid=manager.create("Некорректная",[{"intent":"UNKNOWN_ACTION"}]); router.outcomes=[Result(False,"Команда UNKNOWN_ACTION недоступна.")]; self.assertFalse(manager.run("Некорректная").ok); self.assertEqual(invalid["usage_count"],0)
            malformed=manager.create("Сломанная",[{}]); self.assertFalse(manager.run("Сломанная").ok); self.assertEqual(malformed["usage_count"],0)
    def test_routine_execution_progress(self):
        class SequenceRouter:
            def __init__(self,outcomes): self.outcomes,self.calls=list(outcomes),[]
            def route(self,command,confirmed=False): self.calls.append(command.intent); return self.outcomes.pop(0)
        actions=[{"intent":"OPEN_APPLICATION","parameters":{"application":"obs"}},{"intent":"SET_VOLUME","parameters":{"level":60}},{"intent":"OPEN_BROWSER","parameters":{}}]
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); bus=EventBus(); emitted=[]; callback=[]; bus.subscribe("routine_progress",lambda **payload: emitted.append(payload)); router=SequenceRouter([Result(True,"obs"),Result(True,"volume"),Result(True,"browser")]); manager=RoutineManager(memory,router,bus); routine=manager.create("Подготовка",actions)
            self.assertTrue(manager.run("Подготовка",callback.append).ok)
            statuses=[item["status"] for item in callback]
            self.assertEqual(statuses,["started","step_succeeded","step_succeeded","step_succeeded","completed"]); self.assertEqual(statuses,[item["status"] for item in emitted]); self.assertEqual(router.calls,["OPEN_APPLICATION","SET_VOLUME","OPEN_BROWSER"])
            for item in callback: self.assertEqual(item["routine_name"],"Подготовка")
            self.assertEqual([(item["current_step"],item["total_steps"]) for item in callback[1:4]],[(1,3),(2,3),(3,3)]); self.assertIn("открываю obs",callback[1]["message"]); self.assertEqual(routine["usage_count"],1)
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); callback=[]; router=SequenceRouter([Result(True,"obs"),Result(False,"нет доступа"),Result(True,"browser")]); manager=RoutineManager(memory,router); routine=manager.create("Ошибка",actions)
            self.assertFalse(manager.run("Ошибка",callback.append).ok); self.assertEqual(callback[2]["status"],"step_failed"); self.assertEqual(callback[2]["current_step"],2); self.assertIn("нет доступа",callback[2]["message"]); self.assertEqual(callback[-1]["status"],"failed"); self.assertNotIn("completed",[item["status"] for item in callback]); self.assertEqual(router.calls,["OPEN_APPLICATION","SET_VOLUME","OPEN_BROWSER"]); self.assertEqual(routine["usage_count"],0)
            empty=[]; manager.create("Пустая",[]); self.assertFalse(manager.run("Пустая",empty.append).ok); self.assertEqual(empty[0]["status"],"empty")
    def test_routine_execution_cancellation(self):
        class BlockingRouter:
            def __init__(self): self.calls=[]; self.started=threading.Event(); self.release=threading.Event()
            def route(self,command,confirmed=False):
                self.calls.append(command.intent); self.started.set(); self.release.wait(1); return Result(True,"done")
        actions=[{"intent":"ONE","parameters":{}},{"intent":"TWO","parameters":{}},{"intent":"THREE","parameters":{}}]
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=BlockingRouter(); manager=RoutineManager(memory,router); assistant=Assistant(); assistant.routines=manager; assistant.parser.memory=memory; routine=manager.create("Отмена",actions); progress=[]; holder={}
            thread=threading.Thread(target=lambda: holder.setdefault("result",manager.run("Отмена",progress.append))); thread.start(); self.assertTrue(router.started.wait(1)); self.assertTrue(manager.get_execution_state()["active"]); self.assertTrue(assistant.handle("стоп").ok); router.release.set(); thread.join(1)
            result=holder["result"]; self.assertFalse(result.ok); self.assertTrue(result.data["cancelled"]); self.assertEqual(router.calls,["ONE"]); self.assertEqual(routine["usage_count"],0); self.assertEqual(routine["actions"],actions); self.assertEqual(progress[-1]["status"],"cancelled"); self.assertTrue(progress[-1]["cancelled"]); self.assertNotIn("completed",[item["status"] for item in progress]); self.assertFalse(manager.get_execution_state()["active"]); self.assertFalse(manager.cancel_active().ok); self.assertFalse(manager.cancel_active().ok)
            race=RoutineManager(memory,BlockingRouter()); completed=race.create("Гонка",[{"intent":"ONE","parameters":{}}]); race.router.release.set(); events=[]; self.assertTrue(race.run("Гонка",events.append).ok); self.assertFalse(race.cancel_active().ok); self.assertEqual(completed["usage_count"],1); self.assertEqual(events[-1]["status"],"completed")
    def test_routine_run_context_safe_step_skipping(self):
        class SequenceRouter:
            def __init__(self,outcomes=None): self.outcomes=list(outcomes or []); self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command.intent); return self.outcomes.pop(0) if self.outcomes else Result(True,"ok")
        actions=[{"intent":"OPEN_APPLICATION","parameters":{"application":"obs"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"telegram"}},{"intent":"OPEN_BROWSER","parameters":{}},{"intent":"SET_VOLUME","parameters":{"level":60}}]
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=SequenceRouter(); assistant=Assistant(); assistant.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events); assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            routine=assistant.routines.create("Стрим",actions); before=json.loads(json.dumps(routine["actions"]))
            self.assertTrue(assistant.handle("стрим без telegram").ok); self.assertEqual(router.calls,["OPEN_APPLICATION","OPEN_BROWSER","SET_VOLUME"]); self.assertEqual(routine["actions"],before); self.assertEqual(RoutineManager(MemoryManager(directory),router).find("Стрим")["actions"],before); self.assertEqual(routine["usage_count"],1)
            router.calls=[]; self.assertTrue(assistant.handle("стрим без третьего шага").ok); self.assertEqual(router.calls,["OPEN_APPLICATION","OPEN_APPLICATION","SET_VOLUME"])
            calls_before=list(router.calls); self.assertFalse(assistant.handle("стрим без десятого шага").ok); self.assertEqual(router.calls,calls_before)
            duplicate=assistant.routines.create("Дубликат",[{"intent":"OPEN_APPLICATION","parameters":{"application":"telegram"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"telegram"}}]); self.assertFalse(assistant.handle("дубликат без telegram").ok); self.assertEqual(duplicate["usage_count"],0); self.assertEqual(router.calls,calls_before)
            progress=[]; router.calls=[]; self.assertTrue(assistant.routines.run("Стрим",progress.append,[2]).ok); self.assertEqual(router.calls,["OPEN_APPLICATION","OPEN_BROWSER","SET_VOLUME"]); skipped=[item for item in progress if item["status"]=="skipped"][0]; self.assertEqual((skipped["original_step"],skipped["total_steps"],skipped["original_total_steps"]),(2,3,4)); self.assertEqual([(item["current_step"],item["original_step"]) for item in progress if item["status"]=="step_succeeded"],[(1,1),(2,3),(3,4)])
        class BlockingRouter:
            def __init__(self): self.calls=[]; self.started=threading.Event(); self.release=threading.Event()
            def route(self,command,confirmed=False): self.calls.append(command.intent); self.started.set(); self.release.wait(1); return Result(True,"ok")
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=BlockingRouter(); manager=RoutineManager(memory,router); routine=manager.create("Отмена",[{"intent":"ONE","parameters":{}},{"intent":"TWO","parameters":{}},{"intent":"THREE","parameters":{}}]); events=[]; holder={}
            thread=threading.Thread(target=lambda: holder.setdefault("result",manager.run("Отмена",events.append,[2]))); thread.start(); self.assertTrue(router.started.wait(1)); self.assertEqual(manager.get_execution_state()["skipped_step_indexes"],[2]); manager.cancel_active(); router.release.set(); thread.join(1); self.assertTrue(holder["result"].data["cancelled"]); self.assertEqual(router.calls,["ONE"]); self.assertEqual(routine["actions"][1]["intent"],"TWO")
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=SequenceRouter([Result(True,"one"),Result(False,"failed")]); manager=RoutineManager(memory,router); routine=manager.create("Сбой",[{"intent":"ONE","parameters":{}},{"intent":"TWO","parameters":{}},{"intent":"THREE","parameters":{}}]); self.assertFalse(manager.run("Сбой",skipped_indexes=[2]).ok); self.assertEqual(router.calls,["ONE","THREE"]); self.assertEqual(routine["usage_count"],0); self.assertEqual(routine["actions"][1]["intent"],"TWO")
    def test_routine_safe_parameters(self):
        class Router:
            def __init__(self,outcomes=None): self.calls=[]; self.outcomes=list(outcomes or [])
            def route(self,command,confirmed=False): self.calls.append(command); return self.outcomes.pop(0) if self.outcomes else Result(True,"ok")
        schemas=[{"name":"platform","type":"string","required":True},{"name":"level","type":"integer","default":40},{"name":"private","type":"boolean","default":True}]
        actions=[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}},{"intent":"SET_VOLUME","parameters":{"level":"{level}"}},{"intent":"OPEN_BROWSER","parameters":{"private":"{private}"}}]
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); manager=RoutineManager(memory,router); routine=manager.create("Параметры",actions,parameters=schemas); before=json.loads(json.dumps(routine))
            self.assertTrue(manager.run("Параметры",parameter_values={"platform":"YouTube"}).ok); self.assertEqual([call.parameters for call in router.calls],[{"application":"YouTube"},{"level":40},{"private":True}]); self.assertEqual(routine["usage_count"],1); self.assertEqual(routine["actions"],before["actions"]); self.assertEqual(RoutineManager(MemoryManager(directory),router).find("Параметры")["actions"],before["actions"])
            router.calls=[]; self.assertTrue(manager.run("Параметры",parameter_values={"platform":"Twitch","level":55,"private":False}).ok); self.assertEqual(router.calls[1].parameters["level"],55); self.assertFalse(router.calls[2].parameters["private"])
            router.calls=[]; self.assertFalse(manager.run("Параметры").ok); self.assertFalse(manager.run("Параметры",parameter_values={"platform":4}).ok); self.assertEqual(router.calls,[])
            unsafe=manager.create("Небезопасная",[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}}],parameters=[{"name":"platform","type":"string"}]); self.assertFalse(manager.run("Небезопасная",parameter_values={"platform":"cmd.exe /c whoami"}).ok); self.assertFalse(manager.run("Небезопасная",parameter_values={"platform":"../secret"}).ok); self.assertEqual(unsafe["usage_count"],0)
            blocked=manager.create("Заблокированная",[{"intent":"OPEN_APPLICATION","parameters":{"blocked":"{platform}"}}],parameters=[{"name":"platform","type":"string"}]); self.assertFalse(manager.run("Заблокированная",parameter_values={"platform":"x"}).ok); dynamic=manager.create("Динамическая",[{"intent":"{platform}","parameters":{}}],parameters=[{"name":"platform","type":"string"}]); self.assertFalse(manager.run("Динамическая",parameter_values={"platform":"SHELL"}).ok); plugin=manager.create("Plugin",[{"intent":"OPEN_APPLICATION","plugin":"{platform}","parameters":{}}],parameters=[{"name":"platform","type":"string"}]); self.assertFalse(manager.run("Plugin",parameter_values={"platform":"x"}).ok); self.assertEqual(router.calls,[])
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events); assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            routine=assistant.routines.create("Стрим",[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"telegram"}},{"intent":"SET_VOLUME","parameters":{"level":10}}],parameters=[{"name":"platform","type":"string"}]); saved=json.loads(json.dumps(routine["actions"]))
            self.assertTrue(assistant.handle("стрим для youtube без telegram").ok); self.assertEqual([call.parameters for call in router.calls],[{"application":"youtube"},{"level":10}]); self.assertEqual(routine["actions"],saved)
        class BlockingRouter(Router):
            def __init__(self): super().__init__(); self.started=threading.Event(); self.release=threading.Event()
            def route(self,command,confirmed=False): self.calls.append(command); self.started.set(); self.release.wait(1); return Result(True,"ok")
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=BlockingRouter(); manager=RoutineManager(memory,router); routine=manager.create("Отмена",[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"telegram"}},{"intent":"SET_VOLUME","parameters":{"level":10}}],parameters=[{"name":"platform","type":"string"}]); holder={}; thread=threading.Thread(target=lambda: holder.setdefault("result",manager.run("Отмена",skipped_indexes=[2],parameter_values={"platform":"youtube"}))); thread.start(); self.assertTrue(router.started.wait(1)); self.assertEqual(manager.get_execution_state()["parameter_names"],["platform"]); manager.cancel_active(); router.release.set(); thread.join(1); self.assertTrue(holder["result"].data["cancelled"]); self.assertEqual(len(router.calls),1); self.assertEqual(routine["usage_count"],0)
            failed_router=Router([Result(True,"ok"),Result(False,"failed")]); failed=RoutineManager(memory,failed_router); failed_routine=failed.create("Сбой",[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"telegram"}},{"intent":"SET_VOLUME","parameters":{"level":10}}],parameters=[{"name":"platform","type":"string"}]); self.assertFalse(failed.run("Сбой",skipped_indexes=[2],parameter_values={"platform":"youtube"}).ok); self.assertEqual([call.intent for call in failed_router.calls],["OPEN_APPLICATION","SET_VOLUME"]); self.assertEqual(failed_routine["usage_count"],0)
    def test_parameterized_routine_learning(self):
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"ok")
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events); assistant.dialogue.memory=memory; assistant.parser.memory=memory; assistant.skills.memory=memory
            self.assertTrue(assistant.handle("научись подготовке стрима с параметром platform типа string").ok); self.assertTrue(assistant.handle("добавь параметр level типа integer по умолчанию 45").ok); self.assertTrue(assistant.handle("добавь параметр private типа boolean необязательный по умолчанию да").ok); self.assertFalse(assistant.handle("добавь параметр platform типа string").ok); self.assertFalse(assistant.handle("добавь параметр  типа string").ok); self.assertFalse(assistant.handle("добавь параметр bad типа money").ok); self.assertFalse(assistant.handle("добавь параметр invalid типа integer по умолчанию нет").ok); self.assertFalse(assistant.handle("добавь параметр shell типа string по умолчанию cmd.exe /c whoami").ok)
            assistant.events.emit("command_routed",command=Command("OPEN_APPLICATION",{"application":"youtube"}),result=Result(True,"ok")); self.assertTrue(assistant.handle("используй параметр platform").ok); self.assertFalse(assistant.handle("используй параметр platform для intent").ok); self.assertEqual(assistant.demonstration.actions[-1]["intent"],"OPEN_APPLICATION"); self.assertEqual(assistant.demonstration.actions[-1]["parameters"]["application"],"{platform}")
            self.assertTrue(assistant.handle("закончил обучение").ok); preview=assistant.handle("Подготовка стрима"); self.assertTrue(preview.confirmation_required); self.assertIn("Параметры",preview.message); self.assertIn("platform: string",preview.message); self.assertTrue(assistant.handle("да").ok)
            routine=assistant.routines.find("Подготовка стрима"); self.assertEqual([item["name"] for item in routine["parameters"]],["platform","level","private"]); self.assertTrue(assistant.routines.run("Подготовка стрима",parameter_values={"platform":"twitch"}).ok); self.assertEqual(router.calls[-1].parameters["application"],"twitch")
            self.assertTrue(assistant.handle("научись подготовке с параметром temp").ok); self.assertTrue(assistant.handle("отмена").ok); self.assertEqual(assistant.routine_parameters,[]); self.assertEqual(len(memory.learned_routines),1)
            ordinary=Assistant(); ordinary.memory=MemoryManager(directory); ordinary.routines.memory=ordinary.memory; ordinary.dialogue.memory=ordinary.memory; ordinary.parser.memory=ordinary.memory; self.assertTrue(ordinary.handle("научись, как я готовлюсь").ok); ordinary.events.emit("command_routed",command=Command("OPEN_APPLICATION",{"application":"obs"}),result=Result(True,"ok")); ordinary.handle("закончил обучение"); ordinary.handle("Обычная"); self.assertTrue(ordinary.handle("да").ok); self.assertEqual(ordinary.routines.find("Обычная").get("parameters"),[])
    def test_routine_parameter_clarification(self):
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"ok")
        def assistant_with_memory(directory):
            memory=MemoryManager(directory); router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events); assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory; return assistant,router,memory
        with tempfile.TemporaryDirectory() as directory:
            assistant,router,memory=assistant_with_memory(directory); stream=assistant.routines.create("Стрим",[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"telegram"}}],parameters=[{"name":"platform","type":"string","required":True}]); before=json.loads(json.dumps(stream))
            question=assistant.handle("стрим"); self.assertTrue(question.ok); self.assertIn("платформ",question.message.lower()); self.assertEqual(router.calls,[]); self.assertEqual(assistant.dialogue.pending["missing"][0]["name"],"platform")
            self.assertTrue(assistant.handle("YouTube").ok); self.assertEqual(router.calls[0].parameters["application"],"YouTube"); self.assertEqual(stream["actions"],before["actions"]); self.assertEqual(RoutineManager(MemoryManager(directory),router).find("Стрим")["parameters"],before["parameters"]); self.assertIsNone(assistant.dialogue.pending)
            router.calls=[]; question=assistant.handle("стрим"); self.assertTrue(question.ok); self.assertTrue(assistant.handle("стоп").ok); self.assertIsNone(assistant.dialogue.pending); self.assertEqual(router.calls,[]); self.assertTrue(assistant.handle("стрим").ok); self.assertTrue(assistant.handle("отмена").ok)
            multi=assistant.routines.create("Мульти",[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}},{"intent":"SET_VOLUME","parameters":{"level":"{level}"}},{"intent":"OPEN_BROWSER","parameters":{"private":"{private}"}}],parameters=[{"name":"platform","type":"string","required":True},{"name":"level","type":"integer","required":True},{"name":"private","type":"boolean","default":True}])
            first=assistant.handle("мульти"); self.assertIn("платформ",first.message.lower()); second=assistant.handle("Twitch"); self.assertIn("громкост",second.message.lower()); invalid=assistant.handle("очень громко"); self.assertFalse(invalid.ok); self.assertIn("числов",invalid.message); self.assertEqual(assistant.dialogue.pending["values"],{"platform":"Twitch"}); self.assertTrue(assistant.handle("70").ok); self.assertEqual([call.parameters for call in router.calls[-3:]],[{"application":"Twitch"},{"level":70},{"private":True}]); self.assertEqual(multi["usage_count"],1)
            router.calls=[]; self.assertIn("громкост",assistant.handle("мульти для YouTube").message.lower()); self.assertTrue(assistant.handle("25").ok); self.assertEqual(router.calls[0].parameters["application"],"youtube")
            boolean=assistant.routines.create("Булево",[{"intent":"OPEN_BROWSER","parameters":{"private":"{enabled}"}}],parameters=[{"name":"enabled","type":"boolean","required":True}]); self.assertTrue(assistant.handle("булево").ok); self.assertFalse(assistant.handle("может быть").ok); self.assertTrue(assistant.handle("да").ok); self.assertTrue(router.calls[-1].parameters["private"]); self.assertEqual(boolean["usage_count"],1)
            router.calls=[]; self.assertTrue(assistant.handle("стрим для youtube без telegram").ok); self.assertEqual([call.parameters for call in router.calls],[{"application":"youtube"}]); self.assertEqual(stream["actions"],before["actions"])
    def test_routine_execution_history(self):
        class Router:
            def __init__(self,outcomes=None): self.calls=[]; self.outcomes=list(outcomes or [])
            def route(self,command,confirmed=False): self.calls.append(command); return self.outcomes.pop(0) if self.outcomes else Result(True,"ok")
        actions=[{"intent":"ONE","parameters":{}},{"intent":"TWO","parameters":{}}]
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router([Result(True,"one"),Result(True,"two")]); manager=RoutineManager(memory,router); routine=manager.create("История",actions); original=json.loads(json.dumps(routine))
            self.assertTrue(manager.run("История").ok); success=manager.get_last_execution(routine["id"]); self.assertEqual(success["status"],"success"); self.assertEqual((success["total_steps"],success["completed_steps"]),(2,2)); self.assertIsNotNone(success["started_at"]); self.assertIsNotNone(success["finished_at"]); expected=dict(original); expected.update(usage_count=1,last_used_at=routine["last_used_at"]); self.assertEqual(routine,expected)
            failed_manager=RoutineManager(memory,Router([Result(True,"one"),Result(False,"boom")])); failed_manager.router=failed_manager.router; self.assertFalse(failed_manager.run("История").ok); failed=failed_manager.get_last_execution(routine["id"]); self.assertEqual((failed["status"],failed["completed_steps"],failed["failed_step"]),("failed",1,2)); self.assertNotIn("boom",failed["error"])
            empty=manager.create("Пустая",[]); self.assertFalse(manager.run("Пустая").ok); self.assertEqual(manager.get_last_execution(empty["id"])["status"],"failed")
            parameterized=manager.create("Параметры",[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}}],parameters=[{"name":"platform","type":"string"}]); self.assertTrue(manager.run("Параметры",parameter_values={"platform":"super-secret-token"}).ok); parameter_record=manager.get_last_execution(parameterized["id"]); self.assertEqual(parameter_record["parameter_names"],["platform"]); self.assertNotIn("super-secret-token",json.dumps(parameter_record))
            manager.update_routine(routine["id"],name="История новая"); self.assertTrue(manager.run("История новая").ok); history=manager.get_execution_history(routine["id"]); self.assertEqual(history[1]["routine_name"],"История"); self.assertEqual(history[0]["routine_name"],"История новая"); execution_id=history[0]["id"]; copy=manager.get_execution(execution_id); copy["status"]="changed"; self.assertNotEqual(manager.get_execution(execution_id)["status"],"changed"); self.assertIsNone(manager.get_execution("missing"))
            self.assertTrue(manager.delete_routine(routine["id"])); self.assertGreater(len(manager.get_execution_history_by_name("История")),0); self.assertGreater(len(manager.get_execution_history_by_name("История новая")),0)
            loaded=RoutineManager(MemoryManager(directory),router); self.assertGreater(len(loaded.get_execution_history_by_name("История новая")),0)
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); memory.settings["routine_history_limit"]=2; manager=RoutineManager(memory,Router()); routine=manager.create("Лимит",[{"intent":"ONE","parameters":{}}]); manager.run("Лимит"); manager.run("Лимит"); manager.run("Лимит"); self.assertEqual(len(manager.get_execution_history(routine["id"],10)),2)
        class BlockingRouter(Router):
            def __init__(self): super().__init__(); self.started=threading.Event(); self.release=threading.Event()
            def route(self,command,confirmed=False): self.calls.append(command); self.started.set(); self.release.wait(1); return Result(True,"ok")
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=BlockingRouter(); manager=RoutineManager(memory,router); routine=manager.create("Отмена",actions); holder={}; thread=threading.Thread(target=lambda: holder.setdefault("result",manager.run("Отмена"))); thread.start(); self.assertTrue(router.started.wait(1)); manager.cancel_active(); router.release.set(); thread.join(1); cancelled=manager.get_last_execution(routine["id"]); self.assertEqual((cancelled["status"],cancelled["completed_steps"],cancelled["cancellation_step"]),("cancelled",1,2)); self.assertFalse(holder["result"].ok)
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router([Result(False,"boom")]); assistant=Assistant(); assistant.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events); assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory; routine=assistant.routines.create("Запрос",[{"intent":"ONE","parameters":{}}]); assistant.routines.run("Запрос"); calls=len(router.calls); self.assertTrue(assistant.handle("когда я последний раз запускал запрос").ok); self.assertTrue(assistant.handle("как прошёл последний запуск запрос").ok); self.assertTrue(assistant.handle("покажи историю запрос").ok); self.assertTrue(assistant.handle("почему последний запуск запрос завершился ошибкой").ok); self.assertEqual(len(router.calls),calls); self.assertIsNotNone(routine)
    def test_routine_recommendation_as_usual(self):
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"ok")
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); manager=RoutineManager(memory,router); self.assertIsNone(manager.recommend_routine()["routine"])
            dominant=manager.create("Главная",[{"intent":"ONE","parameters":{}}]); other=manager.create("Другая",[{"intent":"TWO","parameters":{}}]); manager.run("Главная"); manager.run("Главная"); self.assertIs(manager.recommend_routine()["routine"],dominant); self.assertEqual(manager.recommend_routine()["routine"]["id"],dominant["id"])
            alias=manager.create("Подготовка",[{"intent":"THREE","parameters":{}}],aliases=["стрим"]); other["usage_count"]=20; self.assertIs(manager.recommend_routine("стрим")["routine"],alias); self.assertIs(manager.recommend_routine("подготовка!")["routine"],alias)
            context=manager.create("Стрим",[{"intent":"FOUR","parameters":{}}],"перед стримом"); self.assertIs(manager.recommend_routine("перед стримом")["routine"],context)
            bad=manager.create("Плохая",[{"intent":"FIVE","parameters":{}}]); good=manager.create("Хорошая",[{"intent":"SIX","parameters":{}}]); bad["usage_count"]=50; started="2026-01-01T00:00:00+00:00"; manager.record_execution(bad,started,"failed",1,0,failed_step=1,error="failed"); manager.record_execution(good,started,"success",1,1); manager.record_execution(good,started,"success",1,1); manager.record_execution(good,started,"success",1,1); self.assertIs(manager.recommend_routine()["routine"],good)
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events); assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            first=assistant.routines.create("Первая",[{"intent":"ONE","parameters":{}}]); second=assistant.routines.create("Вторая",[{"intent":"TWO","parameters":{}}]); ambiguous=assistant.handle("сделай как обычно"); self.assertTrue(ambiguous.ok); self.assertIsNotNone(assistant.dialogue.pending); self.assertTrue(assistant.handle("2").ok); self.assertEqual(router.calls[-1].intent,"TWO"); self.assertIsNotNone(first); self.assertIsNotNone(second)
            stream=assistant.routines.create("Подготовка стрима",[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"telegram"}}],"перед стримом",parameters=[{"name":"platform","type":"string","required":True}]); router.calls=[]; info=assistant.handle("что я обычно запускаю перед стримом"); self.assertTrue(info.ok); self.assertEqual(router.calls,[]); self.assertTrue(assistant.handle("сделай как обычно перед стримом").ok); self.assertEqual(assistant.dialogue.pending and assistant.dialogue.pending["missing"][0]["name"],"platform"); self.assertTrue(assistant.handle("YouTube").ok); self.assertEqual([call.parameters for call in router.calls],[{"application":"YouTube"},{"application":"telegram"}]); self.assertEqual(stream["usage_count"],1)
            router.calls=[]; self.assertTrue(assistant.handle("сделай как обычно перед стримом для YouTube без telegram").ok); self.assertEqual([call.parameters for call in router.calls],[{"application":"youtube"}]); self.assertEqual(stream["actions"][1]["parameters"]["application"],"telegram"); self.assertGreater(len(assistant.routines.get_execution_history(stream["id"])),0)
    def test_composite_routine_commands(self):
        class Router:
            def __init__(self,outcomes=None): self.calls=[]; self.outcomes=list(outcomes or [])
            def route(self,command,confirmed=False): self.calls.append(command); return self.outcomes.pop(0) if self.outcomes else Result(True,"ok")
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events); assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            stream=assistant.routines.create("Стрим",[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"telegram"}}],parameters=[{"name":"platform","type":"string","required":True}]); saved=json.loads(json.dumps(stream))
            self.assertTrue(assistant.handle("стрим для YouTube без telegram").ok); self.assertEqual([call.parameters for call in router.calls],[{"application":"youtube"}]); self.assertEqual(stream["actions"],saved["actions"]); self.assertEqual(RoutineManager(MemoryManager(directory),router).find("Стрим")["actions"],saved["actions"])
            router.calls=[]; multi=assistant.routines.create("Мульти",[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}},{"intent":"SET_VOLUME","parameters":{"level":"{level}"}}],parameters=[{"name":"platform","type":"string","required":True},{"name":"level","type":"integer","required":True}]); prompt=assistant.handle("мульти для youtube"); self.assertTrue(prompt.ok); self.assertIsNotNone(assistant.dialogue.pending); self.assertTrue(assistant.handle("60").ok); self.assertEqual([call.parameters for call in router.calls],[{"application":"youtube"},{"level":60}]); self.assertEqual(multi["usage_count"],1)
            router.calls=[]; usual=assistant.routines.create("Обычная",[{"intent":"OPEN_APPLICATION","parameters":{"application":"obs"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"telegram"}},{"intent":"SET_VOLUME","parameters":{"level":"{level}"}}],"перед стримом",parameters=[{"name":"level","type":"integer","required":True}]); parsed=assistant.parser.parse("сделай как обычно перед стримом, но не открывай telegram и поставь громкость 60"); self.assertEqual(parsed.parameters.get("options"),{"level":60}); composite=assistant.handle("сделай как обычно перед стримом, но не открывай telegram и поставь громкость 60"); self.assertTrue(composite.ok,composite.message); self.assertEqual([call.parameters for call in router.calls],[{"application":"obs"},{"level":60}]); self.assertEqual(usual["actions"][1]["parameters"]["application"],"telegram")
            router.calls=[]; self.assertFalse(assistant._run_composite("стрим",options={"unknown":1}).ok); self.assertFalse(assistant.handle("стрим для youtube без отсутствует").ok); self.assertEqual(router.calls,[]); self.assertTrue(assistant.handle("что входит в стрим без telegram?").ok); self.assertEqual(router.calls,[])
            first=assistant.routines.create("Подготовка к стриму",[{"intent":"ONE","parameters":{}}]); second=assistant.routines.create("Подготовка рабочего места",[{"intent":"TWO","parameters":{}}]); choice=assistant._run_composite("подготовка"); self.assertTrue(choice.ok); self.assertTrue(assistant.handle("2").ok); self.assertEqual(router.calls[-1].intent,"TWO"); self.assertIsNotNone(first); self.assertIsNotNone(second)
            router.calls=[]; self.assertTrue(assistant.handle("запусти стрим как обычно для youtube").ok); self.assertEqual(router.calls[-1].parameters["application"],"telegram"); self.assertEqual(stream["usage_count"],2); self.assertGreater(len(assistant.routines.get_execution_history(stream["id"])),1)
    def test_routine_parameter_presets(self):
        class Router:
            def __init__(self,outcomes=None): self.calls=[]; self.outcomes=list(outcomes or [])
            def route(self,command,confirmed=False): self.calls.append(command); return self.outcomes.pop(0) if self.outcomes else Result(True,"ok")
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); manager=RoutineManager(memory,router); actions=[{"intent":"OPEN_APPLICATION","parameters":{"application":"{platform}"}},{"intent":"OPEN_APPLICATION","parameters":{"application":"telegram"}},{"intent":"SET_VOLUME","parameters":{"level":"{volume}"}}]; routine=manager.create("Стрим",actions,"перед стримом",parameters=[{"name":"platform","type":"string","required":True},{"name":"volume","type":"integer","required":True},{"name":"optional","type":"boolean","default":True},{"name":"secret","type":"string","sensitive":True,"required":False}]); before=json.loads(json.dumps(routine))
            preset=manager.create_preset(routine["id"],"YouTube",{"platform":"youtube","volume":60}); self.assertIsNotNone(preset); self.assertEqual(manager.get_preset(preset["id"])["name"],"YouTube"); self.assertEqual(len(manager.list_presets(routine["id"])),1); self.assertIsNone(manager.create_preset(routine["id"],"Bad",{"platform":"youtube","unknown":1})); self.assertIsNone(manager.create_preset(routine["id"],"Type",{"platform":"youtube","volume":"loud"})); self.assertIsNone(manager.create_preset(routine["id"],"Missing",{"platform":"youtube"})); self.assertIsNone(manager.create_preset(routine["id"],"Secret",{"platform":"youtube","volume":60,"secret":"token"})); self.assertIsNone(manager.create_preset("missing","No",{}))
            updated=manager.update_preset(preset["id"],name="YouTube 60",values={"platform":"youtube","volume":60}); self.assertEqual(updated["name"],"YouTube 60"); self.assertEqual(RoutineManager(MemoryManager(directory),router).get_preset(preset["id"])["name"],"YouTube 60"); self.assertEqual(routine,before)
            assistant=Assistant(); assistant.memory=memory; assistant.routines=manager; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory; router.calls=[]; self.assertTrue(assistant._run_with_preset("Стрим","YouTube 60",skip="telegram",options={"level":80}).ok); self.assertEqual([call.parameters for call in router.calls],[{"application":"youtube"},{"level":80}]); router.calls=[]; self.assertTrue(assistant.handle("сделай как обычно перед стримом с настройками youtube 60 без telegram").ok); self.assertEqual([call.parameters for call in router.calls],[{"application":"youtube"},{"level":60}]); self.assertEqual(manager.get_preset(preset["id"])["values"],{"platform":"youtube","volume":60}); self.assertEqual(routine["actions"],before["actions"])
            partial=manager.create_preset(routine["id"],"Partial",{"platform":"twitch","volume":70}); self.assertIsNotNone(partial); self.assertTrue(manager.update_routine(routine["id"],parameters=[{"name":"platform","type":"string","required":True},{"name":"volume","type":"integer","required":True}]))
            self.assertTrue(manager.delete_preset(partial["id"])); self.assertIsNone(manager.get_preset(partial["id"])); self.assertFalse(manager.delete_preset("missing")); manager.update_routine(routine["id"],name="Стрим новый"); self.assertIsNotNone(manager.get_preset(preset["id"])); self.assertTrue(manager.delete_routine(routine["id"])); self.assertIsNotNone(manager.get_preset(preset["id"]))
    def test_voice_unknown_local_routine_candidate_reaches_assistant_and_runs_only_after_selection(self):
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text); return True
            def is_speaking(self): return False
        class Endpoint:
            def __init__(self): self.values=[]; self.current=.5
            def SetMasterVolumeLevelScalar(self,value,_): self.values.append(value); self.current=value
            def GetMasterVolumeLevelScalar(self): return self.current
        with tempfile.TemporaryDirectory() as directory, patch("plugins.browser.plugin.webbrowser.open",return_value=True) as open_url:
            assistant=self._isolated_assistant(directory,load_plugins=True)
            routine=assistant.routines.create("Подготовка к стриму",[
                {"intent":"OPEN_BROWSER","parameters":{}},
                {"intent":"OPEN_YOUTUBE","parameters":{}},
                {"intent":"SET_VOLUME","parameters":{"level":50}},
            ])
            browser=assistant.plugins.plugins["browser"]; volume=assistant.plugins.plugins["volume"]
            launches=[]; endpoint=Endpoint(); browser._launcher=launches.append; volume._endpoint_factory=lambda:endpoint
            routed=[]; assistant.events.subscribe("command_routed",lambda **item:routed.append(item["command"]))
            voice=VoiceController(assistant,None,Wake(),Mic(),Tts())
            initial=voice.process_transcript("Джарвис, подготовь меня к стриму.")
            self.assertTrue(initial.ok); self.assertNotIn("Не совсем понял",initial.message)
            self.assertEqual(assistant.dialogue.pending["kind"],"routine_candidates")
            self.assertEqual(routed,[]); self.assertEqual(routine["usage_count"],0)
            completed=voice.process_transcript("1")
            self.assertTrue(completed.ok); self.assertEqual([command.intent for command in routed],["OPEN_BROWSER","OPEN_YOUTUBE","SET_VOLUME"])
            self.assertEqual([command.parameters for command in routed],[{},{},{"level":50}])
            self.assertEqual(len(launches),1); open_url.assert_called_once_with("https://www.youtube.com")
            self.assertEqual(endpoint.values,[.5]); self.assertEqual(routine["usage_count"],1)
            self.assertEqual(len(assistant.memory.learned_routines),1)

    def test_voice_unknown_local_routine_candidate_preserves_multiple_and_zero_fallbacks(self):
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text); return True
            def is_speaking(self): return False
        def assistant_for(directory):
            assistant=Assistant(); memory=MemoryManager(directory)
            assistant.memory=memory; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            assistant.routines=RoutineManager(memory,RouterStub(),assistant.events)
            return assistant
        with tempfile.TemporaryDirectory() as directory:
            assistant=assistant_for(directory)
            assistant.routines.create("Подготовка к стриму",[]); assistant.routines.create("Стрим рабочий",[])
            voice=VoiceController(assistant,None,Wake(),Mic(),Tts())
            multiple=voice.process_transcript("Джарвис, подготовь меня к стриму.")
            self.assertTrue(multiple.ok); self.assertEqual(assistant.dialogue.pending["kind"],"routine_candidates")
        with tempfile.TemporaryDirectory() as directory:
            assistant=assistant_for(directory); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
            missing=voice.process_transcript("Джарвис, совершенно неизвестная просьба.")
            self.assertTrue(missing.ok); self.assertIn("Не совсем понял",missing.message); self.assertIsNone(assistant.dialogue.pending)

    def test_voice_local_routine_gate_keeps_provider_and_exact_command_priority(self):
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text); return True
            def is_speaking(self): return False
        class Provider(ConversationProvider):
            def __init__(self): self.calls=0
            def respond(self,turn,context): self.calls+=1; return ProviderResponse("Безопасный ответ.")
        class Plugins:
            def __init__(self): self.calls=[]
            def execute(self,plugin_id,command): self.calls.append((plugin_id,command)); return Result(True,"Открываю браузер.")
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); assistant=Assistant(); assistant.memory=memory; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            provider=Provider(); assistant.conversation_providers.register(provider)
            voice=VoiceController(assistant,None,Wake(),Mic(),Tts())
            conversational=voice.process_transcript("Джарвис, совершенно неизвестная просьба.")
            self.assertTrue(conversational.ok); self.assertEqual(conversational.message,"Безопасный ответ."); self.assertEqual(provider.calls,1)
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); assistant=Assistant(); assistant.memory=memory; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            plugins=Plugins(); assistant.plugins=plugins; assistant.registry.register("OPEN_BROWSER","safe"); assistant.router=CommandRouter(assistant.registry,plugins,PermissionManager(),assistant.skills,assistant.events); assistant.skills.router=assistant.router; assistant.routines=RoutineManager(memory,assistant.router,assistant.events)
            provider=Provider(); assistant.conversation_providers.register(provider)
            voice=VoiceController(assistant,None,Wake(),Mic(),Tts())
            command=voice.process_transcript("Джарвис, открой браузер")
            self.assertTrue(command.ok); self.assertEqual(provider.calls,0); self.assertEqual([call[1].intent for call in plugins.calls],["OPEN_BROWSER"])

    def test_voice_natural_conversation_uses_enabled_provider_and_publishes_tts(self):
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if text.startswith("Джарвис,") else None
        class Mic:
            def selected_index(self): return None
        class Tts:
            def __init__(self): self.messages=[]; self.calls=0; self.enabled=False; self.rate=-5; self.volume=1
            def say(self,text): self.calls+=1; self.messages.append(text); return True
            def is_speaking(self): return False
            def diagnostic_status(self): return {"speech_started":True,"speech_finished":True,"last_error_class":None}
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"Браузер открыт.")
        class Provider(ConversationProvider):
            def __init__(self): self.calls=[]
            def respond(self,turn,context):
                self.calls.append((turn,context))
                if turn.to_dict().get("normalized_text")=="кто ты и что ты умеешь":
                    return ProviderResponse("Я JARVIS, голосовой помощник. Я отвечаю на вопросы и выполняю разрешённые команды.",conversational=True)
                return None
        with tempfile.TemporaryDirectory() as directory:
            assistant=self._isolated_assistant(directory); router=Router(); assistant.router=router; assistant.skills.router=router; assistant.routines.router=router
            provider=Provider(); assistant.conversation_providers.register(provider); tts=Tts(); voice=VoiceController(assistant,None,Wake(),Mic(),tts)
            conversational=voice.process_transcript("Джарвис, кто ты и что ты умеешь")
            self.assertTrue(conversational.ok); self.assertEqual(conversational.message,"Я JARVIS, голосовой помощник. Я отвечаю на вопросы и выполняю разрешённые команды.")
            self.assertEqual(tts.messages,[conversational.message]); self.assertEqual(tts.calls,1); self.assertTrue(tts.enabled); self.assertEqual(tts.rate,0); self.assertEqual(tts.volume,100); self.assertEqual(len(provider.calls),1); self.assertEqual(router.calls,[])
            self.assertEqual(voice.tts_diagnostic,{"response_type":"conversational","tts_text_length":len(conversational.message),"tts_enqueue_count":1,"speech_started":True,"speech_finished":True,"last_error_class":None})
            action=voice.process_transcript("открой браузер")
            self.assertTrue(action.ok); self.assertEqual([item.intent for item in router.calls],["OPEN_BROWSER"]); self.assertEqual(len(provider.calls),1)
            self.assertEqual(tts.messages[-1],action.message); self.assertEqual(tts.calls,2)
            self.assertEqual(voice.tts_diagnostic,{"response_type":"action_success","tts_text_length":len(action.message),"tts_enqueue_count":2,"speech_started":True,"speech_finished":True,"last_error_class":None})
            unresolved=voice.process_transcript("неизвестная бессмысленная фраза")
            self.assertFalse(unresolved.ok); self.assertEqual(len(provider.calls),2); self.assertEqual([item.intent for item in router.calls],["OPEN_BROWSER"])
            self.assertEqual(tts.messages[-1],unresolved.message); self.assertEqual(tts.calls,3)
            self.assertEqual(voice.tts_diagnostic["tts_enqueue_count"],3)

    def test_unknown_provider_boundary_keeps_authoritative_text_and_command_router_priority(self):
        """UNKNOWN text may receive text only; only parsed commands reach Router."""
        class Provider(ConversationProvider):
            def __init__(self):
                self.calls=[]; self.responses={
                    "король":ProviderResponse("Король — монарх, правящий государством."),
                    "что такое youtube":ProviderResponse("YouTube — сервис для просмотра и публикации видео.",response_type="informational"),
                    "что такое юриспруденция":ProviderResponse("Юриспруденция — наука и практика права.",response_type="informational"),
                    "свободный текст":ProviderResponse("Открой YouTube",conversational=True),
                }
            def respond(self,turn,context):
                self.calls.append(turn.normalized_text)
                return self.responses.get(turn.normalized_text)
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False):
                self.calls.append(command)
                return Result(True,"Открываю YouTube.")
        with tempfile.TemporaryDirectory() as directory:
            assistant=self._isolated_assistant(directory); router=Router()
            assistant.router=router; assistant.skills.router=router; assistant.routines.router=router
            provider=Provider(); assistant.conversation_providers.register(provider)
            for phrase,expected in (("король","Король — монарх, правящий государством."),("что такое YouTube","YouTube — сервис для просмотра и публикации видео."),("что такое юриспруденция","Юриспруденция — наука и практика права.")):
                with self.subTest(phrase=phrase):
                    result=assistant.handle(phrase)
                    self.assertTrue(result.ok); self.assertEqual(result.message,expected); self.assertNotEqual(result.message,"Готово.")
                    turn=ConversationTurn.create(user_text=phrase,session_state="READY",intent="UNKNOWN",response_type=result.data["provider_response_type"],result=result,timestamp=1)
                    self.assertEqual(ResponseComposer().compose(turn=turn).text,expected)
            text_only=assistant.handle("свободный текст")
            self.assertTrue(text_only.ok); self.assertEqual(text_only.message,"Открой YouTube"); self.assertEqual(router.calls,[])
            action=assistant.handle("открой YouTube")
            self.assertTrue(action.ok); self.assertEqual([call.intent for call in router.calls],["OPEN_YOUTUBE"])
            self.assertEqual(provider.calls,["король","что такое youtube","что такое юриспруденция","свободный текст"])
            assistant.conversation_providers.register(Provider())
            assistant.conversation_providers.provider.responses.clear()
            fallback=assistant.handle("пустой ответ provider")
            self.assertFalse(fallback.ok); self.assertEqual(fallback.message,"Я не понял команду.")

    def test_tts_worker_exposes_only_safe_diagnostics_without_speaking(self):
        """The desktop SAPI worker remains observable without retaining speech text."""
        tts=TextToSpeech(enabled=False)
        try:
            status=tts.diagnostic_status()
            self.assertEqual(status["backend"],"SAPI")
            self.assertTrue(status["tts_enabled"] is False)
            self.assertTrue(status["worker_started"])
            self.assertFalse(status["speech_started"])
            self.assertFalse(status["speech_finished"])
            self.assertEqual(status["spoken_count"],0)
            self.assertNotIn("text",status)
            self.assertIsNone(status["last_error_class"])
            self.assertFalse(tts.say("Не сохраняй эту тестовую фразу."))
            tts.last_error="SAPI_SPEAK_RUNTIMEERROR"
            self.assertEqual(tts.diagnostic_status()["last_error_class"],"SAPI_SPEAK_RUNTIMEERROR")
        finally:
            tts.shutdown()

    def test_tts_stop_never_invokes_worker_owned_com_voice(self):
        class WorkerVoice:
            def Speak(self, *_):
                raise AssertionError("stop must not call a worker-owned COM object")
        class Token:
            def __init__(self, token_id): self.Id=token_id
        class Tokens:
            def __init__(self): self.values=[Token("installed-a"),Token("installed-b")]; self.Count=len(self.values)
            def Item(self, index): return self.values[index]
        class Voice:
            def __init__(self): self.tokens=Tokens(); self.Voice=None
            def GetVoices(self): return self.tokens
        tts=TextToSpeech(enabled=False)
        try:
            voice=Voice(); tts.voice_id="missing"; tts._select_voice(voice)
            self.assertEqual(voice.Voice.Id,"installed-a"); self.assertEqual(tts.diagnostic_status()["selected_voice_id"],"installed-a")
            voice=Voice(); tts.voice_id="installed-b"; tts._select_voice(voice)
            self.assertEqual(voice.Voice.Id,"installed-b")
            tts._voice=WorkerVoice()
            tts.stop()
        finally:
            tts._voice=None
            tts.shutdown()

    def test_tts_selects_installed_voice_from_final_response_language(self):
        class Token:
            def __init__(self, token_id, language, description): self.Id,self.language,self.description=token_id,language,description
            def GetAttribute(self, name): return self.language if name=="Language" else ""
            def GetDescription(self): return self.description
        class Tokens:
            def __init__(self): self.values=[Token("en-zira","409","English"),Token("ru-irina","419","Russian")]; self.Count=len(self.values)
            def Item(self, index): return self.values[index]
        class Voice:
            def __init__(self): self.tokens=Tokens(); self.Voice=None
            def GetVoices(self): return self.tokens
        tts=TextToSpeech(enabled=False,voice_id="missing")
        try:
            voice=Voice(); tts._select_voice(voice,"Привет, чем могу помочь?")
            self.assertEqual(voice.Voice.Id,"ru-irina")
            voice=Voice(); tts._select_voice(voice,"Hello, how can I help you?")
            self.assertEqual(voice.Voice.Id,"en-zira")
            tts.voice_id="en-zira"; voice=Voice(); tts._select_voice(voice,"12345")
            self.assertEqual(voice.Voice.Id,"en-zira")
            tts.voice_id="missing"; voice=Voice(); tts._select_voice(voice,"12345")
            self.assertEqual(voice.Voice.Id,"en-zira")
        finally:
            tts.shutdown()

    def test_tts_routes_russian_text_to_optional_piper_and_falls_back_safely(self):
        class Completed:
            returncode=0
        tts=TextToSpeech(enabled=False)
        try:
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory); python=Path(sys.executable); model=root / "voice.onnx"; model.write_bytes(b"x")
                data=root / "espeak-ng-data"; data.mkdir(); worker=root / "worker.py"; worker.write_text("# test",encoding="utf-8")
                bridge=root / "bridge"; bridge.mkdir(); tts._piper_paths={"python":python,"bridge_root":bridge,"model":model,"espeak_data":data,"worker":worker}
                calls=[]
                def runner(command,**kwargs):
                    calls.append((command,kwargs)); Path(command[-1]).write_bytes(b"R"*64); return Completed()
                with patch("speech.text_to_speech.subprocess.run",side_effect=runner), patch.object(tts,"_play_wav") as player:
                    self.assertTrue(tts._speak_piper("Проверка русского голоса JARVIS")); self.assertEqual(len(calls),1); player.assert_called_once()
                self.assertEqual(tts._text_language("Привет, чем могу помочь?"),"ru")
                self.assertEqual(tts._text_language("Hello, how can I help you?"),"en")
                with patch.object(tts,"_piper_available",return_value=False): self.assertFalse(tts._speak_piper("Русский текст"))
        finally:
            tts.shutdown()

    def test_tts_piper_worker_reports_existing_speaking_lifecycle(self):
        states=[]; tts=TextToSpeech(enabled=True,on_state=states.append)
        try:
            with patch.object(tts,"_speak_piper",return_value=True):
                self.assertTrue(tts.say("Русский ответ"))
                deadline=time.monotonic()+1
                while time.monotonic()<deadline and not tts.diagnostic_status()["speech_finished"]: time.sleep(.01)
            status=tts.diagnostic_status()
            self.assertTrue(status["speech_started"]); self.assertTrue(status["speech_finished"])
            self.assertEqual(status["active_backend"],"PIPER"); self.assertIn("SPEAKING",states); self.assertIn("READY",states)
        finally:
            tts.shutdown()

    def test_stt_resource_normalization_is_bounded_to_registered_high_confidence_targets(self):
        normalizer=STTResourceNormalizer()
        resources=[RegisteredResource("YouTube",("youtube","ютуб")),RegisteredResource("Spotify",("спотифай",))]
        self.assertEqual(normalizer.normalize("открой ютуб",resources),"открой YouTube")
        self.assertEqual(normalizer.normalize("открой спотифй",resources),"открой Spotify")
        self.assertEqual(normalizer.normalize("открой совершеннонеизвестныйресурс",resources),"открой совершеннонеизвестныйресурс")
        self.assertEqual(normalizer.normalize("сегодня хорошая погода",resources),"сегодня хорошая погода")


    def test_desktop_entrypoint_enables_existing_local_conversation_provider_before_ui(self):
        import main as entrypoint
        class DesktopAssistant:
            def __init__(self): self.calls=[]
            def enable_ollama_provider(self): self.calls.append("enable_ollama_provider"); return Result(True,"ok")
            def start(self): self.calls.append("start")
        assistant=DesktopAssistant()
        with patch.object(entrypoint,"Assistant",return_value=assistant):
            self.assertIs(entrypoint.build_assistant(),assistant)
        self.assertEqual(assistant.calls,["enable_ollama_provider","start"])

    def test_routine_execution_audit_keeps_metadata_without_candidate_or_confirmation_transcripts(self):
        class Router:
            def __init__(self,outcomes=None): self.calls=[]; self.outcomes=list(outcomes or [])
            def route(self,command,confirmed=False): self.calls.append(command); return self.outcomes.pop(0) if self.outcomes else Result(True,"ok")
        def wired(directory,router):
            assistant=Assistant(); memory=MemoryManager(directory)
            assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            assistant.routines=RoutineManager(memory,router,assistant.events)
            return assistant
        actions=[{"intent":"OPEN_BROWSER","parameters":{}},{"intent":"OPEN_YOUTUBE","parameters":{}},{"intent":"SET_VOLUME","parameters":{"level":50}}]
        with tempfile.TemporaryDirectory() as directory:
            router=Router(); assistant=wired(directory,router); routine=assistant.routines.create("Подготовка к стриму",actions)
            selected=assistant.handle("Подготовь меня к стриму.")
            self.assertTrue(selected.ok); self.assertEqual(assistant.dialogue.pending["kind"],"routine_candidates")
            executed=assistant.handle("1")
            self.assertTrue(executed.ok); self.assertEqual([call.intent for call in router.calls],["OPEN_BROWSER","OPEN_YOUTUBE","SET_VOLUME"])
            record=assistant.routines.get_last_execution(routine["id"]); self.assertEqual(record["status"],"success"); self.assertEqual((record["total_steps"],record["completed_steps"]),(3,3))
            self.assertEqual(routine["usage_count"],1); self.assertIsNotNone(routine["last_used_at"]); self.assertEqual(routine["actions"],actions)
            history=json.dumps(assistant.memory.history,ensure_ascii=False); self.assertNotIn("Подготовь меня к стриму.",history); self.assertNotIn('"text": "1"',history); self.assertNotIn("provider_payload",history)
        with tempfile.TemporaryDirectory() as directory:
            router=Router(); assistant=wired(directory,router); routine=assistant.routines.create("Подготовка",actions,"действия перед стримом")
            prompt=assistant.handle("сделай подготовку перед стримом")
            self.assertTrue(prompt.confirmation_required); self.assertTrue(assistant.handle("Да.").ok)
            self.assertEqual(assistant.routines.get_last_execution(routine["id"])["status"],"success")
            self.assertNotIn("Да.",json.dumps(assistant.memory.history,ensure_ascii=False))

    def test_routine_failure_and_cancellation_audits_are_transcript_free(self):
        class Router:
            def __init__(self,outcomes=None,cancel=False): self.calls=[]; self.outcomes=list(outcomes or []); self.cancel=cancel; self.manager=None
            def route(self,command,confirmed=False):
                self.calls.append(command)
                if self.cancel and len(self.calls)==1: self.manager.cancel_active()
                return self.outcomes.pop(0) if self.outcomes else Result(True,"ok")
        def wired(directory,router):
            assistant=Assistant(); memory=MemoryManager(directory)
            assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            assistant.routines=RoutineManager(memory,router,assistant.events); router.manager=assistant.routines
            return assistant
        actions=[{"intent":"OPEN_BROWSER","parameters":{}},{"intent":"OPEN_YOUTUBE","parameters":{}}]
        for outcomes,cancel,expected in (([Result(False,"safe failure")],False,"failed"),([],True,"cancelled")):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                router=Router(outcomes,cancel); assistant=wired(directory,router); routine=assistant.routines.create("Подготовка к стриму",actions)
                result=assistant.handle("Подготовка к стриму")
                self.assertFalse(result.ok); record=assistant.routines.get_last_execution(routine["id"])
                self.assertEqual(record["status"],expected); self.assertIn(record["completed_steps"],(0,1)); self.assertEqual(routine["usage_count"],0)
                self.assertNotIn("Подготовка к стриму",json.dumps(assistant.memory.history,ensure_ascii=False))

    def test_routine_privacy_does_not_change_normal_history_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory)
            for index in range(205): memory.add_history(f"normal {index}","ok")
            self.assertEqual(len(memory.history),200); self.assertEqual(memory.history[0]["text"],"normal 204")
            assistant=Assistant(); assistant.memory=memory; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory
            self.assertTrue(assistant.handle("покажи мои процедуры").ok)
            self.assertEqual(assistant.memory.history[0]["text"],"покажи мои процедуры")

    def test_resource_resolver_ranks_bounded_application_game_website_file_and_folder_targets(self):
        catalog={
            "application":lambda target:[{"name":"FIFA 26","aliases":["fifa","фифа"],"source":"shortcut","command":{"intent":"OPEN_APPLICATION","parameters":{"application":"FIFA 26"}}}],
            "game":lambda target:[{"name":"FIFA 26","aliases":["fifa","фифа"],"source":"shortcut","command":{"intent":"OPEN_APPLICATION","parameters":{"application":"FIFA 26"}}}],
            "folder":lambda target:[{"name":"Игры","aliases":["игры"],"source":"allowed_folder","command":{"intent":"OPEN_FOLDER","parameters":{"query":"игры"}}}],
            "file":lambda target:[{"name":"Диплом.pdf","aliases":["диплом"],"source":"safe_file_root","command":{"intent":"SEARCH_FILE","parameters":{"query":"диплом"}}}],
        }
        resolver=ResourceResolver(catalog)
        for kind,target,intent in (("application","FIFA","OPEN_APPLICATION"),("game","фифа","OPEN_APPLICATION"),("website","YouTube","OPEN_YOUTUBE"),("folder","игры","OPEN_FOLDER"),("file","диплом","SEARCH_FILE")):
            found=resolver.resolve(ResourceTarget(kind,target)); self.assertEqual(found["status"],"one"); self.assertEqual(found["candidates"][0].command["intent"],intent)
        catalog["application"]=lambda target:[
            {"name":"Discord","aliases":["discord"],"source":"shortcut","command":{"intent":"OPEN_APPLICATION","parameters":{"application":"Discord"}}},
            {"name":"Discord","aliases":["discord"],"source":"application_registry","command":{"intent":"OPEN_APPLICATION","parameters":{"application":"Discord"}}},
        ]
        resolver.discoverers["application"]=catalog["application"]
        self.assertEqual(resolver.resolve(ResourceTarget("application","discord"))["status"],"multiple")
        self.assertEqual(resolver.resolve(ResourceTarget("game","Unknown"))["status"],"none")
        with self.assertRaises(ValueError): ResourceTarget("application","C:\\Windows\\cmd.exe")
        with self.assertRaises(ValueError): ResourceTarget("application","powershell; whoami")

    def test_resource_resolver_semantic_shortcut_family_ranking_is_bounded(self):
        def shortcut(name):
            return {"name":name,"aliases":[],"source":"desktop_shortcut","command":{"intent":"OPEN_APPLICATION","parameters":{"application":name}}}
        resolver=ResourceResolver({"game":lambda target:[shortcut("EA SPORTS FC 26")]})
        for query in ("FIFA","фифа","FC 26"):
            found=resolver.resolve(ResourceTarget("game",query))
            self.assertEqual(found["status"],"one",query); self.assertEqual(found["candidates"][0].name,"EA SPORTS FC 26")
            self.assertEqual(found["candidates"][0].source,"desktop_shortcut")
        exact=resolver.resolve(ResourceTarget("game","EA SPORTS FC 26"))
        self.assertEqual(exact["confidence"],"HIGH"); self.assertEqual(exact["candidates"][0].score,100)
        resolver.discoverers["game"]=lambda target:[shortcut("EA SPORTS FC 26"),shortcut("FIFA Manager"),shortcut("FIFA 23")]
        ambiguous=resolver.resolve(ResourceTarget("game","FIFA"))
        self.assertEqual(ambiguous["status"],"multiple")
        self.assertEqual([item.name for item in ambiguous["candidates"]],["FIFA 23","FIFA Manager","EA SPORTS FC 26"])
        self.assertEqual(resolver.resolve(ResourceTarget("game","Photoshop"))["status"],"none")
        low=ResourceResolver({"game":lambda target:[shortcut("EA SPORTS FC 26")]}).resolve(ResourceTarget("game","FIFA"))
        self.assertEqual(low["confidence"],"LOW")
        with self.assertRaises(ValueError): ResourceTarget("game","C:\\Windows\\cmd.exe")

    def test_information_request_provider_is_bounded_and_session_scoped(self):
        class Weather(InformationProvider):
            def __init__(self): self.calls=[]
            def query(self,request):
                self.calls.append(request)
                return InformationResult(True,"weather",{"location":request.location,"temperature":18,"condition":"облачно"})
        class Provider(ConversationProvider):
            def __init__(self): self.contexts=[]
            def respond(self,turn,context):
                self.contexts.append(dict(context))
                text=turn.normalized_text
                if "вечером" in text: return ProviderResponse("","INFORMATION_REQUEST",{},information_category="weather",location="Харьков",time_context="evening")
                if "погода" in text: return ProviderResponse("","INFORMATION_REQUEST",{},information_category="weather",location="Харьков",time_context="today")
                if "найди" in text: return ProviderResponse("","INFORMATION_REQUEST",{},information_category="web_search",information_query="безопасный запрос")
                if "сайт" in text: return ProviderResponse("","INFORMATION_REQUEST",{},information_category="site_information",target="example",information_query="раздел")
                return ProviderResponse("Обычный разговор.")
        class Tts:
            def __init__(self): self.messages=[]
            def say(self,text): self.messages.append(text); return True
        class Wake:
            def strip(self,text): return text.split(",",1)[1].strip() if "," in text and text.casefold().startswith("джарвис") else None
        with tempfile.TemporaryDirectory() as directory:
            assistant=self._isolated_assistant(directory); weather=Weather(); assistant.information=InformationService({"weather":weather}); provider=Provider(); assistant.conversation_providers.register(provider)
            tts=Tts(); voice=VoiceController(assistant,None,Wake(),object(),tts)
            first=voice.process_transcript("Джарвис, какая погода сегодня?")
            self.assertTrue(first.ok); self.assertIn("18 градусов",first.message); self.assertEqual(assistant.memory.history,[])
            second=voice.process_transcript("А вечером?")
            self.assertTrue(second.ok); self.assertEqual(weather.calls[-1].time_context,"evening"); self.assertEqual(provider.contexts[-1]["last_information"]["location"],"Харьков")
            unavailable=voice.process_transcript("Найди информацию о проекте")
            self.assertFalse(unavailable.ok); self.assertIn("Источник информации",unavailable.message); self.assertEqual(assistant.memory.history,[])
            self.assertTrue(voice.process_transcript("Обычный вопрос").ok); self.assertEqual(tts.messages[-1],"Обычный разговор.")
        registry=ConversationProviderRegistry(); allowed={"INFORMATION_REQUEST"}
        valid=ProviderResponse("","INFORMATION_REQUEST",{},information_category="weather",location="Харьков")
        self.assertIs(registry.validate(valid,allowed),valid)
        self.assertIsNone(registry.validate(ProviderResponse("","INFORMATION_REQUEST",{},information_category="weather",location="cmd.exe"),allowed))
        with self.assertRaises(ValueError): InformationRequest("site_information","x","C:\\Windows\\cmd.exe")

    def test_assistant_information_request_bridge_precedes_clarification_and_keeps_optional_target(self):
        class Source(InformationProvider):
            def __init__(self): self.requests=[]
            def supports(self,request): return 100
            def query(self,request):
                self.requests.append(request)
                return InformationResult(True,request.category,source="test",summary="Проверенный результат.")
        class Providers:
            state="ENABLED"
            def __init__(self,response): self.response=response
            def respond(self,*_): return self.response
        source=Source(); assistant=Assistant(); assistant.information=InformationService([source])
        response=ProviderResponse("","INFORMATION_REQUEST",{},requires_clarification=True,response_type="informational",information_category="web_search",information_query="Какая столица Франции?")
        assistant.conversation_providers=Providers(response)
        candidate=assistant._provider_candidate("Какая столица Франции?",Command("UNKNOWN",{},""))
        self.assertEqual(candidate[0],"result"); self.assertTrue(candidate[1].ok); self.assertEqual(len(source.requests),1)
        request=source.requests[0]; self.assertEqual((request.operation,request.query,request.target,request.language),("GET_INFORMATION","Какая столица Франции?","","ru"))
        optional=ProviderResponse("","INFORMATION_REQUEST",{},information_category="weather",location="Харьков",time_context="вечером",source_hint="trusted")
        mapped=assistant._information_request(optional); self.assertEqual((mapped.location,mapped.time_context,mapped.source_hint,mapped.language),("Харьков","вечером","trusted","ru"))
        unsafe=ProviderResponse("","INFORMATION_REQUEST",{},information_category="web_search",information_query="C:\\Windows\\cmd.exe")
        self.assertIsNone(assistant._information_request(unsafe))
    def test_assistant_information_bridge_is_not_created_for_actions_conversation_or_goal(self):
        class Source(InformationProvider):
            def __init__(self): self.requests=[]
            def supports(self,request): return 100
            def query(self,request): self.requests.append(request); return InformationResult(True,request.category,summary="result")
        class Providers:
            state="ENABLED"
            def __init__(self,response): self.response=response
            def respond(self,*_): return self.response
        source=Source(); assistant=Assistant(); assistant.information=InformationService([source])
        for response in (ProviderResponse("","OPEN_BROWSER",{}),ProviderResponse("Обычный разговор."),ProviderResponse("","",{},goal="capability")):
            assistant.conversation_providers=Providers(response)
            assistant._provider_candidate("неизвестный текст",Command("UNKNOWN",{},""))
        self.assertEqual(source.requests,[])

    def test_information_source_resolver_uses_generic_adapter_scores(self):
        class Adapter(InformationProvider):
            def __init__(self,name,score): self.name,self.score,self.calls=name,score,0
            def supports(self,request): return self.score if request.target=="news" else 0
            def query(self,request): self.calls+=1; return InformationResult(True,"",summary="Краткий проверенный результат.",source=self.name,facts={"raw":"hidden"})
        low,high=Adapter("low",20),Adapter("high",80)
        service=InformationService([low,high]); request=InformationRequest("web_search","новости",target="news")
        result=service.query(request)
        self.assertTrue(result.ok); self.assertEqual(result.message,"Краткий проверенный результат."); self.assertEqual((low.calls,high.calls),(0,1))
        none=InformationService().query(request); self.assertFalse(none.ok); self.assertIn("Источник информации",none.message)
        self.assertNotIn("raw",result.message); self.assertEqual(result.data["information_request"]["operation"],"GET_INFORMATION")
    def test_wikipedia_source_supports_valid_topic_and_rejects_unsafe_request(self):
        source=WikipediaInformationSource(transport=lambda *_:(200,b'{}'))
        self.assertEqual(source.supports(InformationRequest("web_search","квантовая механика")),80); self.assertTrue(source.endpoint.startswith("https://ru.wikipedia.org/"))
        with self.assertRaises(ValueError): InformationRequest("web_search","https://example.invalid")
        unsafe=type("Request",(),{"operation":"GET_INFORMATION","category":"web_search","target":"C:\\Windows\\cmd.exe","query":""})()
        self.assertEqual(source.supports(unsafe),0)
    def test_wikipedia_source_mock_search_and_extract_returns_normalized_result(self):
        calls=[]
        def transport(params,*_):
            calls.append(dict(params))
            if params.get("list")=="search": return 200,json.dumps({"query":{"search":[{"title":"Искусственный интеллект"}]}}).encode()
            return 200,json.dumps({"query":{"pages":[{"title":"Искусственный интеллект","extract":"Искусственный интеллект: область информатики."}]}}).encode()
        source=WikipediaInformationSource(transport=transport); result=source.query(InformationRequest("web_search","искусственный интеллект"))
        self.assertTrue(result.ok); self.assertEqual((result.source,result.title,result.confidence),("wikipedia","Искусственный интеллект","high")); self.assertIn("область",result.summary); self.assertNotIn("query",result.facts)
        self.assertEqual(calls[0]["srsearch"],"искусственный интеллект"); self.assertEqual(source.last_response_diagnostic["extract_length"],len("Искусственный интеллект: область информатики.")); self.assertEqual(source.last_response_diagnostic["empty_reason"],"")
        formatted=InformationService([source]).query(InformationRequest("web_search","искусственный интеллект"))
        self.assertTrue(formatted.ok); self.assertIn("область",formatted.message); self.assertNotIn(":",formatted.message)
    def test_wikipedia_source_empty_search_is_truthful_not_found(self):
        source=WikipediaInformationSource(transport=lambda *_:(200,b'{"query":{"search":[]}}'))
        result=source.query(InformationRequest("web_search","неизвестная тема")); self.assertFalse(result.ok); self.assertIn("ничего не найдено",result.error)
    def test_wikipedia_source_missing_extract_and_unexpected_search_schema_are_truthful(self):
        def missing_extract(params,*_):
            if params.get("list")=="search": return 200,'{"query":{"search":[{"title":"Тема"}]}}'.encode("utf-8")
            return 200,'{"query":{"pages":[{"title":"Тема"}]}}'.encode("utf-8")
        source=WikipediaInformationSource(transport=missing_extract); result=source.query(InformationRequest("web_search","тема"))
        self.assertFalse(result.ok); self.assertEqual(source.last_result_diagnostic["status"],"EMPTY"); self.assertEqual(source.last_response_diagnostic["empty_reason"],"missing_or_unsafe_extract")
        malformed_shape=WikipediaInformationSource(transport=lambda *_:(200,b'{"query":{"unexpected":[]}}'))
        result=malformed_shape.query(InformationRequest("web_search","тема")); self.assertFalse(result.ok); self.assertEqual(malformed_shape.last_result_diagnostic["status"],"NOT_FOUND"); self.assertEqual(malformed_shape.last_response_diagnostic["empty_reason"],"search_empty_or_unsafe_title")
    def test_wikipedia_source_http_error_is_graceful(self):
        source=WikipediaInformationSource(transport=lambda *_:(500,b""))
        result=source.query(InformationRequest("web_search","тема")); self.assertFalse(result.ok); self.assertEqual(source.last_diagnostic["http_status"],500); self.assertIn("недоступен",result.error)
    def test_wikipedia_source_malformed_json_is_graceful(self):
        source=WikipediaInformationSource(transport=lambda *_:(200,b"not-json"))
        result=source.query(InformationRequest("web_search","тема")); self.assertFalse(result.ok); self.assertEqual(source.last_diagnostic["reason"],"invalid_json")
    def test_wikipedia_source_oversized_body_is_graceful(self):
        source=WikipediaInformationSource(transport=lambda _params,_timeout,limit:(200,b"x"*(limit+1)))
        result=source.query(InformationRequest("web_search","тема")); self.assertFalse(result.ok); self.assertEqual(source.last_diagnostic["reason"],"response_too_large")
    def test_wikipedia_source_timeout_is_graceful(self):
        def timeout(*_): raise TimeoutError()
        source=WikipediaInformationSource(transport=timeout); result=source.query(InformationRequest("web_search","тема")); self.assertFalse(result.ok); self.assertEqual(source.last_diagnostic["reason"],"network_error")
    def test_information_source_resolver_selects_wikipedia_without_higher_trusted_source(self):
        source=WikipediaInformationSource(transport=lambda *_:(200,b'{"query":{"search":[]}}')); resolver=InformationSourceResolver([source])
        request=InformationRequest("web_search","история дипломатии"); self.assertIs(resolver.resolve(request),source); self.assertEqual(resolver.last_score,80)
    def test_wikidata_source_scores_structured_facts_above_wikipedia_without_entity_rules(self):
        wikidata=WikidataInformationSource(transport=lambda *_:(200,b'{}')); wikipedia=WikipediaInformationSource(transport=lambda *_:(200,b'{}'))
        factual=InformationRequest("web_search","Какая столица Франции?"); explanatory=InformationRequest("web_search","Расскажи о квантовой механике")
        self.assertEqual(wikidata.supports(factual),100); self.assertGreater(wikidata.supports(factual),wikipedia.supports(factual)); self.assertEqual(wikidata.supports(explanatory),0); self.assertEqual(wikipedia.supports(explanatory),80)
        unsafe=type("Request",(),{"operation":"GET_INFORMATION","category":"web_search","target":"","query":"Какая столица C:\\cmd.exe"})()
        self.assertEqual(wikidata.supports(unsafe),0)
    def test_wikidata_source_mock_entity_claim_and_value_returns_structured_fact(self):
        calls=[]
        def transport(params,*_):
            calls.append(dict(params)); action=params.get("action"); entity_id=params.get("ids","")
            if action=="wbsearchentities": return 200,json.dumps({"search":[{"id":"Q142","label":"Франция"}]}).encode()
            if action=="wbgetclaims": return 200,json.dumps({"claims":{"P36":[{"mainsnak":{"datavalue":{"value":{"id":"Q90"}}}}]}}).encode()
            if entity_id=="Q142": return 200,json.dumps({"entities":{"Q142":{"labels":{"ru":{"value":"Франция"}}}}}).encode()
            return 200,json.dumps({"entities":{"Q90":{"labels":{"ru":{"value":"Париж"}}}}}).encode()
        source=WikidataInformationSource(transport=transport); request=InformationRequest("web_search","Какая столица Франции?"); result=source.query(request)
        self.assertTrue(result.ok); self.assertEqual((result.source,result.title,result.facts),("wikidata","Франция",{"Столица":"Париж"})); self.assertIn("Париж",result.summary); self.assertEqual(calls[0]["search"],"Франция"); self.assertEqual(source.last_result_diagnostic["facts_count"],1)
        formatted=InformationService([source]).query(request); self.assertTrue(formatted.ok); self.assertIn("Париж",formatted.message)
    def test_wikidata_entity_search_phrase_is_bounded_subject_or_safe_fallback(self):
        source=WikidataInformationSource(transport=lambda *_:(200,b"{}"))
        full=InformationRequest("web_search","Какая столица Франции?")
        self.assertEqual(source._request_fact_spec(full),("Столица","P36","Франция"))
        target=InformationRequest("web_search","Какая столица?",target="Франция")
        self.assertEqual(source._request_fact_spec(target),("Столица","P36","Франция"))
        self.assertEqual(source._entity_search_phrase("Франция"),"Франция")
        self.assertEqual(source._entity_search_phrase("Свободная тема"),"Свободная тема")
        self.assertEqual(source._entity_search_phrase("C:\\cmd.exe"),"")
    def test_wikidata_full_question_uses_normalized_entity_search_before_property_selection(self):
        calls=[]
        def transport(params,*_):
            calls.append(dict(params))
            if params.get("action")=="wbsearchentities": return 200,json.dumps({"search":[{"id":"Q801","label":"Первый"},{"id":"Q802","label":"Второй"}]}).encode()
            if params.get("action")=="wbgetclaims":
                claims={"P36":[{"mainsnak":{"datavalue":{"value":{"id":"Q803"}}}}]} if params.get("entity")=="Q802" else {}
                return 200,json.dumps({"claims":claims}).encode()
            if params.get("ids")=="Q802": return 200,json.dumps({"entities":{"Q802":{"labels":{"ru":{"value":"Второй"}}}}}).encode()
            return 200,json.dumps({"entities":{"Q803":{"labels":{"ru":{"value":"Значение"}}}}}).encode()
        source=WikidataInformationSource(transport=transport)
        result=source.query(InformationRequest("web_search","Какая столица Франции?"))
        self.assertTrue(result.ok); self.assertEqual(calls[0]["search"],"Франция"); self.assertEqual(result.title,"Второй")
        diagnostic=source.last_response_diagnostic
        self.assertEqual((diagnostic["property_requested"],diagnostic["search_query_preview"],diagnostic["candidate_count"],diagnostic["candidate_with_property_count"]),("P36","Франция",2,1))
        self.assertEqual(diagnostic["candidate_diagnostics"][0]["has_required_property"],False); self.assertEqual(diagnostic["candidate_diagnostics"][1]["has_required_property"],True)
    def test_wikidata_property_aware_selection_skips_first_candidate_without_requested_claim(self):
        calls=[]
        def transport(params,*_):
            calls.append(dict(params)); action=params.get("action"); entity_id=params.get("ids","")
            if action=="wbsearchentities": return 200,json.dumps({"search":[{"id":"Q101","label":"Первый"},{"id":"Q202","label":"Второй"}]}).encode()
            if action=="wbgetclaims":
                claims={"P36":[{"mainsnak":{"datavalue":{"value":{"id":"Q303"}}}}]} if params.get("entity")=="Q202" else {}
                return 200,json.dumps({"claims":claims}).encode()
            if entity_id=="Q202": return 200,json.dumps({"entities":{"Q202":{"labels":{"ru":{"value":"Второй"}}}}}).encode()
            return 200,json.dumps({"entities":{"Q303":{"labels":{"ru":{"value":"Значение"}}}}}).encode()
        source=WikidataInformationSource(transport=transport)
        result=source.query(InformationRequest("web_search","Какая столица теста?"))
        self.assertTrue(result.ok); self.assertEqual(result.title,"Второй"); self.assertEqual(result.facts,{"Столица":"Значение"})
        self.assertEqual(calls[0]["limit"],"5"); claims=[call for call in calls if call.get("action")=="wbgetclaims"]
        self.assertEqual([(call["entity"],call["property"]) for call in claims],[("Q101","P36"),("Q202","P36")]); self.assertTrue(all("props" not in call for call in claims))
        self.assertTrue(any(call.get("ids")=="Q202" and call.get("props")=="labels" and call.get("languages")=="ru" for call in calls))
        self.assertEqual(source.last_response_diagnostic["candidate_with_property_count"],1); self.assertTrue(source.last_response_diagnostic["selected_candidate_found"])
        self.assertEqual((source.last_response_diagnostic["claims_request_mode"],source.last_response_diagnostic["candidate_batch_size"]),("property_scoped",2))
    def test_wikidata_property_aware_selection_keeps_search_order_when_candidates_have_claim(self):
        def transport(params,*_):
            if params.get("action")=="wbsearchentities": return 200,json.dumps({"search":[{"id":"Q401","label":"Первый"},{"id":"Q402","label":"Второй"}]}).encode()
            if params.get("action")=="wbgetclaims":
                value="Q403" if params.get("entity")=="Q401" else "Q404"
                return 200,json.dumps({"claims":{"P36":[{"mainsnak":{"datavalue":{"value":{"id":value}}}}]}}).encode()
            if params.get("ids")=="Q401": return 200,json.dumps({"entities":{"Q401":{"labels":{"ru":{"value":"Первый"}}}}}).encode()
            return 200,json.dumps({"entities":{"Q403":{"labels":{"ru":{"value":"Первое значение"}}},"Q404":{"labels":{"ru":{"value":"Второе значение"}}}}}).encode()
        source=WikidataInformationSource(transport=transport)
        result=source.query(InformationRequest("web_search","Какая столица теста?"))
        self.assertTrue(result.ok); self.assertEqual(result.title,"Первый"); self.assertEqual(result.facts,{"Столица":"Первое значение"}); self.assertEqual(source.last_response_diagnostic["candidate_with_property_count"],2)
    def test_wikidata_property_aware_selection_is_generic_for_mapped_property_and_not_found_is_truthful(self):
        calls=[]
        def transport(params,*_):
            calls.append(dict(params))
            if params.get("action")=="wbsearchentities": return 200,json.dumps({"search":[{"id":"Q501","label":"Первый"},{"id":"Q502","label":"Второй"}]}).encode()
            if params.get("action")=="wbgetclaims":
                claims={"P17":[{"mainsnak":{"datavalue":{"value":{"id":"Q503"}}}}]} if params.get("entity")=="Q502" else {}
                return 200,json.dumps({"claims":claims}).encode()
            if params.get("ids")=="Q502": return 200,json.dumps({"entities":{"Q502":{"labels":{"ru":{"value":"Второй"}}}}}).encode()
            return 200,json.dumps({"entities":{"Q503":{"labels":{"ru":{"value":"Страна"}}}}}).encode()
        source=WikidataInformationSource(transport=transport)
        result=source.query(InformationRequest("web_search","Какая страна теста?"))
        self.assertTrue(result.ok); self.assertEqual(result.facts,{"Страна":"Страна"}); self.assertEqual(source.last_response_diagnostic["property_requested"],"P17")
        self.assertEqual([(call["entity"],call["property"]) for call in calls if call.get("action")=="wbgetclaims"],[("Q501","P17"),("Q502","P17")])
        missing=WikidataInformationSource(transport=lambda params,*_:(200,json.dumps({"search":[{"id":"Q601","label":"Нет claims"}]} if params.get("action")=="wbsearchentities" else {}).encode()))
        absent=missing.query(InformationRequest("web_search","Какая столица теста?"))
        self.assertFalse(absent.ok); self.assertEqual(missing.last_response_diagnostic["empty_reason"],"claim_not_found"); self.assertEqual(missing.last_response_diagnostic["candidate_with_property_count"],0)
    def test_wikidata_property_aware_candidate_response_is_bounded_and_malformed_is_safe(self):
        source=WikidataInformationSource(transport=lambda params,*_:(200,json.dumps({"search":[{"id":"Q701"},{"id":"bad"},{"id":"Q702"},{"id":"Q703"},{"id":"Q704"},{"id":"Q705"},{"id":"Q706"}]} if params.get("action")=="wbsearchentities" else {"entities":[]}).encode()))
        result=source.query(InformationRequest("web_search","Какая столица теста?"))
        self.assertFalse(result.ok); self.assertEqual(source.last_response_diagnostic["candidate_count"],4); self.assertEqual(source.last_response_diagnostic["empty_reason"],"claim_not_found")
    def test_wikidata_source_not_found_and_transport_failures_are_truthful(self):
        source=WikidataInformationSource(transport=lambda *_:(200,b'{"search":[]}'))
        result=source.query(InformationRequest("web_search","Какая столица Франции?")); self.assertFalse(result.ok); self.assertEqual(source.last_result_diagnostic["status"],"NOT_FOUND")
        malformed=WikidataInformationSource(transport=lambda *_:(200,b"not-json")); self.assertFalse(malformed.query(InformationRequest("web_search","Какая столица Франции?")).ok); self.assertEqual(malformed.last_diagnostic["reason"],"invalid_json")
        http=WikidataInformationSource(transport=lambda *_:(500,b"")); self.assertFalse(http.query(InformationRequest("web_search","Какая столица Франции?")).ok); self.assertEqual(http.last_diagnostic["http_status"],500)
        oversized=WikidataInformationSource(transport=lambda _p,_t,limit:(200,b"x"*(limit+1))); self.assertFalse(oversized.query(InformationRequest("web_search","Какая столица Франции?")).ok); self.assertEqual(oversized.last_diagnostic["reason"],"response_too_large")
    def test_information_source_resolver_selects_wikidata_for_facts_and_wikipedia_for_explanations(self):
        wikidata=WikidataInformationSource(transport=lambda *_:(200,b'{}')); wikipedia=WikipediaInformationSource(transport=lambda *_:(200,b'{}')); resolver=InformationSourceResolver([wikipedia,wikidata])
        self.assertIs(resolver.resolve(InformationRequest("web_search","Какая столица Франции?")),wikidata); self.assertEqual(resolver.last_score,100)
        self.assertIs(resolver.resolve(InformationRequest("web_search","Расскажи о квантовой механике")),wikipedia); self.assertEqual(resolver.last_score,80)

    def test_brave_web_search_config_state_request_contract_and_bounded_results(self):
        calls=[]
        def transport(params,*_):
            calls.append(dict(params)); return 200,json.dumps({"web":{"results":[
                {"title":"Первый результат","url":"https://example.invalid/one","description":"Краткое безопасное описание."},
                {"title":"Второй результат","url":"https://example.invalid/two","description":"Второе безопасное описание."},
                {"title":"Третий результат","url":"https://example.invalid/three","description":"Третье безопасное описание."},
                {"title":"Лишний результат","url":"https://example.invalid/four","description":"Не должен попасть в ответ."}]}}).encode()
        source=BraveWebSearchInformationSource(api_key="unit-test-token",transport=transport)
        request=InformationRequest("web_search","актуальная нейтральная тема",source_hint="web")
        self.assertGreater(source.supports(request),0); result=source.query(request)
        self.assertTrue(result.ok); self.assertEqual((result.source,result.facts["result_count"]),("web_search",3)); self.assertNotIn("https",result.summary); self.assertNotIn("unit-test-token",str(source.last_response_diagnostic))
        self.assertEqual(calls,[{"q":"актуальная нейтральная тема","count":"3","search_lang":"ru","safesearch":"moderate"}])
        self.assertEqual(source.last_result_diagnostic["confidence"],"medium")

    def test_brave_web_search_unconfigured_and_failures_are_safe(self):
        called=[]; request=InformationRequest("web_search","актуальная нейтральная тема",source_hint="web")
        unconfigured=BraveWebSearchInformationSource(api_key="",transport=lambda *_:called.append(True) or (200,b"{}"))
        self.assertEqual(unconfigured.status(),{"state":"UNCONFIGURED","api_key_present":False}); self.assertEqual(unconfigured.supports(request),0)
        self.assertFalse(unconfigured.query(request).ok); self.assertEqual(called,[])
        for status,reason in ((401,"authentication_error"),(429,"rate_limited"),(500,"http_error")):
            source=BraveWebSearchInformationSource(api_key="unit-test-token",transport=lambda *_ , code=status:(code,b""))
            self.assertFalse(source.query(request).ok); self.assertEqual(source.last_diagnostic["reason"],reason)
        malformed=BraveWebSearchInformationSource(api_key="unit-test-token",transport=lambda *_:(200,b"not-json"))
        self.assertFalse(malformed.query(request).ok); self.assertEqual(malformed.last_diagnostic["reason"],"invalid_json")
        oversized=BraveWebSearchInformationSource(api_key="unit-test-token",transport=lambda _p,_t,limit:(200,b"x"*(limit+1)))
        self.assertFalse(oversized.query(request).ok); self.assertEqual(oversized.last_diagnostic["reason"],"response_too_large")
        self.assertRaises(ValueError,InformationRequest,"web_search","https://unsafe.example",source_hint="web")

    def test_brave_web_search_ranking_preserves_wikipedia_and_wikidata(self):
        web=BraveWebSearchInformationSource(api_key="unit-test-token",transport=lambda *_:(200,b"{}")); wikipedia=WikipediaInformationSource(transport=lambda *_:(200,b"{}")); wikidata=WikidataInformationSource(transport=lambda *_:(200,b"{}"))
        resolver=InformationSourceResolver([wikipedia,wikidata,web])
        explanatory=InformationRequest("web_search","объясни нейтральную тему")
        factual=InformationRequest("web_search","Какая столица теста?",time_context="current")
        fresh=InformationRequest("web_search","актуальная нейтральная тема",source_hint="web",time_context="current")
        self.assertIs(resolver.resolve(explanatory),wikipedia); self.assertIs(resolver.resolve(factual),wikidata); self.assertIs(resolver.resolve(fresh),web); self.assertEqual(resolver.last_score,120)
        unconfigured=BraveWebSearchInformationSource(api_key="",transport=lambda *_:(200,b"{}")); self.assertIs(InformationSourceResolver([wikipedia,unconfigured]).resolve(explanatory),wikipedia)

    def test_unconfigured_brave_never_calls_transport_and_existing_sources_remain_resolvable(self):
        brave_calls=[]
        brave=BraveWebSearchInformationSource(api_key="",transport=lambda *_:brave_calls.append(True) or self.fail("unconfigured Brave must not use transport"))
        wikipedia=WikipediaInformationSource(transport=lambda *_:(200,b"{}")); wikidata=WikidataInformationSource(transport=lambda *_:(200,b"{}"))
        resolver=InformationSourceResolver([wikipedia,wikidata,brave])
        self.assertIs(resolver.resolve(InformationRequest("web_search","объясни нейтральную тему")),wikipedia)
        self.assertIs(resolver.resolve(InformationRequest("web_search","Какая столица теста?")),wikidata)
        self.assertEqual(brave.status()["state"],"UNCONFIGURED"); self.assertEqual(brave_calls,[])

    def test_browser_chatgpt_provider_states_and_text_only_response(self):
        class Transport:
            def __init__(self,state="READY",response="Короткий видимый ответ."): self.state=state; self.last_state=state; self.response=response; self.calls=[]
            def status(self): return self.state
            def send_message(self,text,timeout): self.calls.append((text,timeout)); return self.response
        ready=Transport(); provider=BrowserChatGPTProvider(transport=ready)
        response=provider.respond_bounded({"normalized_text":"Объясни тему"},{})
        self.assertIsInstance(response,ProviderResponse); self.assertEqual(response.intent,""); self.assertEqual(ready.calls[0][0],"Объясни тему"); self.assertEqual(provider.last_state,"READY")
        for state in ("UNAVAILABLE","LOGIN_REQUIRED","BROWSER_CLOSED","BUSY"):
            source=BrowserChatGPTProvider(transport=Transport(state)); self.assertIsNone(source.respond_bounded({"normalized_text":"тема"},{})); self.assertEqual(source.last_state,state)
        empty=BrowserChatGPTProvider(transport=Transport(response="")); self.assertIsNone(empty.respond_bounded({"normalized_text":"тема"},{})); self.assertEqual(empty.last_state,"EMPTY_RESPONSE")
        sensitive=BrowserChatGPTProvider(transport=ready); self.assertIsNone(sensitive.respond_bounded({"normalized_text":"мой password"},{})); self.assertEqual(sensitive.last_state,"ERROR")

    def test_chatgpt_browser_preflight_reports_startup_prerequisites_without_launch(self):
        class Transport(PlaywrightChatGPTBrowserTransport):
            def __init__(self,package=True,binary=True,profile=True,imported=True,engine=True):
                self.package=package; self.binary=binary; self.profile=profile; self.imported=imported; self.engine=engine; self.open_calls=0
            def _playwright_package_available(self): return self.package
            def _browser_binary_available(self): return self.binary
            def _profile_directory_ready(self): return self.profile
            def _transport_import_available(self): return self.imported
            def _engine_configuration_available(self): return self.engine
            def _open(self): self.open_calls+=1; self.fail("preflight must never launch a browser")
            def fail(self,message): raise AssertionError(message)
        missing_package=Transport(package=False)
        self.assertEqual(missing_package.preflight()["playwright_package"],"NOT_INSTALLED")
        self.assertEqual(missing_package.preflight()["preflight_status"],"UNAVAILABLE")
        missing_binary=Transport(binary=False)
        self.assertEqual(missing_binary.preflight()["browser_binary"],"MISSING")
        missing_profile=Transport(profile=False)
        self.assertEqual(missing_profile.preflight()["profile_directory"],"UNAVAILABLE")
        ready=Transport()
        self.assertEqual(ready.preflight()["preflight_status"],"READY"); self.assertEqual(ready.open_calls,0)
        provider=BrowserChatGPTProvider(transport=ready)
        self.assertEqual(provider.preflight()["preflight_status"],"READY")
        self.assertFalse(hasattr(provider,"router")); self.assertFalse(hasattr(provider,"memory")); self.assertEqual(ready.open_calls,0)

    def test_chatgpt_browser_dom_send_controls_are_bounded_and_observable(self):
        class List:
            def __init__(self,items=()): self.items=list(items)
            def count(self): return len(self.items)
            def nth(self,index): return self.items[index]
            @property
            def first(self): return self.items[0]
            @property
            def last(self): return self.items[-1]
        class Composer:
            def __init__(self,send=()): self.send=List(send)
            def count(self): return 1
            def is_visible(self): return True
            def locator(self,selector): return self.send if selector in PlaywrightChatGPTBrowserTransport.send_selectors else List()
        class Field:
            def __init__(self,kind,composer): self.kind=kind; self.composer=composer; self.filled=False
            def is_visible(self): return True
            def is_editable(self): return True
            def evaluate(self,_): return "TEXTAREA" if self.kind=="TEXTAREA" else "DIV"
            def get_attribute(self,name): return "true" if name=="contenteditable" and self.kind=="CONTENTEDITABLE" else None
            def fill(self,_): self.filled=True
            def locator(self,_): return self.composer
        class Send:
            def __init__(self,enabled=True): self.enabled=enabled; self.clicks=0
            def is_visible(self): return True
            def is_enabled(self): return self.enabled
            def click(self): self.clicks+=1
        class Response:
            def inner_text(self,**_): return "Короткий ответ."
        class Page:
            def __init__(self,field=None,send=(),response=True): self.field=field; self.send=send; self.response=response
            def locator(self,selector):
                if selector in PlaywrightChatGPTBrowserTransport.input_selectors:
                    valid=(selector=="textarea" and self.field and self.field.kind=="TEXTAREA") or ("contenteditable" in selector and self.field and self.field.kind=="CONTENTEDITABLE")
                    return List([self.field] if valid else [])
                if selector in PlaywrightChatGPTBrowserTransport.assistant_selectors: return List([Response()] if self.response else [])
                return List()
            def evaluate(self,_): return "BODY"
            def wait_for_function(self,*_,**__): return None
        class Transport(PlaywrightChatGPTBrowserTransport):
            def __init__(self,page,transition=True): self._page=page; self.transition=transition; self.last_state="READY"; self.last_diagnostic=self._new_dom_diagnostic()
            def status(self): return "READY"
            def _submit_transition_observed(self,*_): return self.transition
            def _wait_for_response(self,*_):
                self.last_diagnostic["response_transition_observed"]=True
                return "Короткий ответ."
        for kind in ("TEXTAREA","CONTENTEDITABLE"):
            send=Send(); composer=Composer([send]); field=Field(kind,composer); transport=Transport(Page(field,[send]))
            self.assertEqual(transport.send_message("safe",1),"Короткий ответ.")
            self.assertTrue(field.filled); self.assertEqual(send.clicks,1); self.assertTrue(transport.last_diagnostic["request_sent"])
        disabled=Send(False); field=Field("TEXTAREA",Composer([disabled])); transport=Transport(Page(field,[disabled]))
        self.assertIsNone(transport.send_message("safe",1)); self.assertEqual(transport.last_state,"SEND_CONTROL_DISABLED"); self.assertEqual(disabled.clicks,0)
        transport=Transport(Page())
        self.assertIsNone(transport.send_message("safe",1)); self.assertEqual(transport.last_state,"INPUT_NOT_FOUND")
        field=Field("TEXTAREA",Composer()); transport=Transport(Page(field))
        self.assertIsNone(transport.send_message("safe",1)); self.assertEqual(transport.last_state,"SEND_CONTROL_NOT_FOUND")
        arbitrary=Send(); field=Field("TEXTAREA",Composer()); transport=Transport(Page(field,[arbitrary]))
        self.assertIsNone(transport.send_message("safe",1)); self.assertEqual(arbitrary.clicks,0)
        send=Send(); field=Field("TEXTAREA",Composer([send])); transport=Transport(Page(field,[send]),transition=False)
        self.assertIsNone(transport.send_message("safe",1)); self.assertEqual(transport.last_state,"DOM_SUBMIT_TRANSITION_NOT_DETECTED"); self.assertFalse(transport.last_diagnostic["request_sent"])

    def test_chatgpt_browser_response_detection_uses_new_visible_stable_assistant_text(self):
        class List:
            def __init__(self,items=()): self.items=list(items)
            def count(self): return len(self.items)
            def nth(self,index): return self.items[index]
            @property
            def first(self): return self.items[0]
        class Message:
            def __init__(self,text,visible=True,role="assistant",markdown=False): self.text=text; self.visible=visible; self.role=role; self.markdown=markdown
            def is_visible(self): return self.visible
            def inner_text(self,**_): return self.text
            def get_attribute(self,name):
                if name=="data-message-author-role": return self.role if not self.markdown else None
                if name=="class": return "markdown" if self.markdown else ""
                return None
            def locator(self,expression):
                if "author-role='user'" in expression and self.role=="user": return List([self])
                return List()
        class StreamingMessage(Message):
            def __init__(self,texts): super().__init__(texts[0]); self.texts=texts; self.position=0
            def set_frame(self,index): self.position=min(max(0,index-1),len(self.texts)-1)
            def inner_text(self,**_): return self.texts[self.position]
        class Page:
            def __init__(self,frames,selector=None): self.frames=frames; self.index=0; self.selector=selector
            def locator(self,selector):
                if selector in PlaywrightChatGPTBrowserTransport.assistant_selectors and (self.selector is None or selector==self.selector): return List(self.frames[self.index])
                return List()
            def wait_for_timeout(self,_):
                self.index=min(self.index+1,len(self.frames)-1)
                for message in self.frames[self.index]:
                    if hasattr(message,"set_frame"): message.set_frame(self.index)
        def response_for(frames,timeout=250,selector=None):
            transport=PlaywrightChatGPTBrowserTransport(); transport._page=Page(frames,selector); transport.last_diagnostic=transport._new_dom_diagnostic()
            before=transport._assistant_counts()
            transport._page.wait_for_timeout(1)
            return transport,transport._wait_for_response(before,timeout)
        old=Message("Старый ответ")
        transport,response=response_for([[old],[old]])
        self.assertEqual(response,""); self.assertFalse(transport.last_diagnostic["response_transition_observed"])
        user_only=[]
        transport,response=response_for([user_only,user_only])
        self.assertEqual(response,""); self.assertFalse(transport.last_diagnostic["latest_message_detected"])
        streaming=StreamingMessage(["Искусственный","Искусственный интеллект помогает решать задачи.","Искусственный интеллект помогает решать задачи."])
        transport,response=response_for([[],[streaming],[streaming],[streaming],[streaming],[streaming]],timeout=2000)
        self.assertEqual(response,"Искусственный интеллект помогает решать задачи.")
        self.assertTrue(transport.last_diagnostic["response_transition_observed"]); self.assertEqual(transport.last_diagnostic["latest_message_role"],"ASSISTANT")
        empty=Message(""); filled=Message("Готовый ответ")
        transport,response=response_for([[],[empty],[filled],[filled],[filled],[filled]],timeout=2000)
        self.assertEqual(response,"Готовый ответ")
        hidden=Message("hidden full page content",visible=False); newest=Message("Новый безопасный ответ")
        transport,response=response_for([[old],[old,hidden,newest],[old,hidden,newest]],timeout=2000)
        self.assertEqual(response,"Новый безопасный ответ"); self.assertNotIn("hidden",response)
        markdown=Message("Видимый ответ",markdown=True)
        transport,response=response_for([[],[markdown],[markdown]],timeout=2000,selector="main .markdown")
        self.assertEqual(response,"Видимый ответ"); self.assertEqual(transport.last_diagnostic["latest_message_role"],"ASSISTANT")
        user_markdown=Message("Пользовательский текст",role="user",markdown=True)
        transport,response=response_for([[],[user_markdown],[user_markdown]],timeout=250,selector="main .markdown")
        self.assertEqual(response,""); self.assertFalse(transport.last_diagnostic["response_transition_observed"])
        many_old=[Message(f"Старый {index}") for index in range(9)]
        tail=Message("Новый после девяти старых")
        transport,response=response_for([many_old,many_old+[tail],many_old+[tail]],timeout=2000,selector="main .markdown")
        self.assertEqual(response,"Новый после девяти старых")

    def test_chatgpt_transcript_response_detection_requires_new_completed_assistant_turn(self):
        class List:
            def __init__(self,items=()): self.items=list(items)
            def count(self): return len(self.items)
            def nth(self,index): return self.items[index]
        class Turn:
            def __init__(self,text,assistant=True,complete=True,visible=True):
                self.text,self.assistant,self.complete,self.visible=text,assistant,complete,visible
            def is_visible(self): return self.visible
            def inner_text(self,**_): return self.text
            def get_attribute(self,name):
                if name=="data-assistant-content-started": return "" if self.assistant else None
                if name=="data-message-complete": return "" if self.complete else None
                if name=="data-message-author-role": return None
                if name=="class": return ""
                return None
            def locator(self,_): return List()
        class Page:
            def __init__(self,frames): self.frames,self.index=frames,0
            def locator(self,selector):
                turns=self.frames[self.index]
                if selector==PlaywrightChatGPTBrowserTransport.assistant_turn_selector:
                    return List([turn for turn in turns if turn.assistant])
                if selector in PlaywrightChatGPTBrowserTransport.assistant_selectors: return List()
                return List()
            def wait_for_timeout(self,_): self.index=min(self.index+1,len(self.frames)-1)
        def detect(frames,timeout=1500):
            transport=PlaywrightChatGPTBrowserTransport(); transport._page=Page(frames); transport.last_diagnostic=transport._new_dom_diagnostic()
            before=transport._assistant_counts(); transport._page.wait_for_timeout(1)
            return transport,transport._wait_for_response(before,timeout)
        old=Turn("Старый завершённый ответ")
        transport,response=detect([[old],[old]])
        self.assertEqual(response,"")  # pre-send transcript turns are ignored
        user=Turn("Пользовательский текст",assistant=False)
        transport,response=detect([[],[user],[user]])
        self.assertEqual(response,"")
        incomplete=Turn("ещё печатается",complete=False)
        transport,response=detect([[],[incomplete],[incomplete]],timeout=250)
        self.assertEqual(response,"")  # completion attribute is mandatory
        completed=Turn("Готовый ответ")
        transport,response=detect([[],[completed],[completed],[completed],[completed]])
        self.assertEqual(response,"Готовый ответ")
        self.assertTrue(transport.last_diagnostic["response_transition_observed"])
        streaming=Turn("частичный ответ",complete=False); complete=Turn("полный ответ",complete=True)
        transport,response=detect([[],[streaming],[complete],[complete],[complete],[complete]])
        self.assertEqual(response,"полный ответ")
        transport,response=detect([[],[],[]],timeout=250)
        self.assertEqual(response,"")

    def test_browser_chatgpt_explicit_trigger_is_read_only_and_ollama_remains_default(self):
        class Transport:
            last_state="READY"
            def __init__(self): self.calls=[]
            def status(self): return "READY"
            def send_message(self,text,timeout): self.calls.append(text); return "Безопасный внешний ответ."
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,*_): self.calls.append(command); return Result(True,"unexpected")
        with tempfile.TemporaryDirectory() as directory:
            assistant=self._isolated_assistant(directory); transport=Transport(); assistant.browser_chatgpt_provider=BrowserChatGPTProvider(transport=transport)
            router=Router(); assistant.router=router; assistant.skills.router=router; assistant.routines.router=router
            result=assistant.handle("Джарвис, спроси ChatGPT: объясни тему")
            self.assertTrue(result.ok); self.assertEqual(transport.calls,["объясни тему"]); self.assertEqual(router.calls,[]); self.assertEqual(assistant.memory.history,[]); self.assertEqual(result.data["provider_id"],"browser_chatgpt")
            self.assertEqual(assistant._browser_chatgpt_prompt("обычный запрос"),"")

    def test_chatgpt_browser_smoke_launcher_imports_core_from_tools_directory(self):
        root=Path(__file__).resolve().parents[1]
        completed=subprocess.run([sys.executable,"real_chatgpt_browser_smoke.py","--import-check"],cwd=str(root/"tools"),capture_output=True,text=True,timeout=15,check=False)
        self.assertEqual(completed.returncode,0); self.assertIn("launcher_status=PASS core_import=PASS",completed.stdout)

    def test_chatgpt_browser_smoke_artifact_is_safe_and_atomic(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location("chatgpt_smoke_tool",root/"tools"/"real_chatgpt_browser_smoke.py")
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            artifact=Path(directory)/"missing"/"diagnostics"/"result.json"
            successful=module.new_result()
            successful.update({"launcher_status":"PASS","browser_status":"READY","login_status":"PASS","chatgpt_page":"PASS","request_sent":True,"response_received":True,"response_length":42,"provider_status":"READY","router_calls":0,"plugin_calls":0,"memory_delta":0,"tts_calls":1,"final_status":"PASS","error_code":"","response_text":"must not persist","cookie":"must not persist","api_key":"must not persist"})
            module.write_result_artifact(successful,artifact)
            stored=json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(set(stored),set(module.SAFE_RESULT_FIELDS)); self.assertEqual(stored["response_length"],42)
            self.assertTrue({"candidate_count","visible_count","latest_role","latest_length","transition_status"}.issubset(stored))
            self.assertNotIn("assistant_message_candidates",stored); self.assertNotIn("response_transition_observed",stored)
            self.assertNotIn("response_text",stored); self.assertNotIn("cookie",stored); self.assertNotIn("api_key",stored)
            self.assertEqual(list(artifact.parent.glob("*.tmp")),[])
            failed=module.new_result(); failed.update({"final_status":"RUNTIME_FAILURE","error_code":"provider token secret","provider_status":"password"})
            module.write_result_artifact(failed,artifact)
            stored=json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(stored["final_status"],"RUNTIME_FAILURE")
            self.assertEqual(stored["error_code"],"NOT_AVAILABLE"); self.assertEqual(stored["provider_status"],"NOT_AVAILABLE")
            self.assertEqual(list(artifact.parent.glob("*.tmp")),[])

    def test_chatgpt_dom_diagnostic_parser_keeps_only_bounded_structure(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location("chatgpt_dom_diagnostic",root/"tools"/"chatgpt_response_dom_diagnostic.py")
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        raw={"page":{"ready":True,"main_visible":True},"main_structure":{"tag":"MAIN","role":"main","aria_label":"secret account name","class_names":["container"],"visible":True,"text_length":0,"parent_tag":"DIV","parent_class_names":[],"data_attributes":["data-testid"],"data_author_role":""},"selector_counts":{"main":1,"article":2,"markdown":4,"cookies":100},
             "candidate_structures":[{"tag":"DIV","role":"article","aria_label":"secret account name","class_names":["markdown","password-token"],
                                     "visible":True,"text_length":28,"parent_tag":"ARTICLE","parent_class_names":["message"],
                                     "data_attributes":["data-testid","data-token"],"data_author_role":"assistant",
                                     "response_text":"must never persist","cookie":"must never persist"} for _ in range(25)],
             "response_text":"must never persist"}
        parsed=module.parse_diagnostic_structure(raw)
        self.assertEqual(parsed["page_status"],"READY"); self.assertEqual(parsed["candidate_count"],20)
        self.assertEqual(parsed["main_structure"]["tag"],"MAIN"); self.assertEqual(parsed["main_structure"]["aria_label"],"REDACTED")
        self.assertEqual(parsed["selector_counts"]["markdown"],4)
        self.assertNotIn("cookies",parsed["selector_counts"]); self.assertNotIn("response_text",parsed)
        candidate=parsed["candidate_structures"][0]
        self.assertEqual(candidate["aria_label"],"REDACTED")
        self.assertEqual(candidate["class_names"],["markdown"])
        self.assertEqual(candidate["data_attributes"],["data-testid"])
        self.assertNotIn("response_text",candidate); self.assertNotIn("cookie",candidate)
        with tempfile.TemporaryDirectory() as directory:
            artifact=Path(directory)/"missing"/"structure.json"
            module.write_diagnostic_artifact(parsed,artifact)
            stored=json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(stored["page_status"],"READY"); self.assertEqual(len(stored["candidate_structures"]),20)
            self.assertEqual(list(artifact.parent.glob("*.tmp")),[])

    def test_chatgpt_live_dom_probe_persists_structure_without_response_text(self):
        from tools.chatgpt_response_live_dom_probe import parse_live_structure, write_live_artifact
        node={"tag":"DIV","role":"article","aria_label":"private prompt","class_names":["markdown","cookie-token"],
              "visible":True,"text_length":6,"parent_tag":"SECTION","parent_class_names":["message"],
              "data_attributes":["data-testid","data-secret"],"data_author_role":"assistant","response_text":"готов"}
        raw={"live":{"status":"READY","request_sent":True,"assistant_marked":True,"main_descendants":50,
                     "structural_candidate_count":25},"candidate_structures":[node for _ in range(25)],
             "response_text":"готов","cookie":"hidden"}
        parsed=parse_live_structure(raw)
        self.assertEqual((parsed["probe_status"],parsed["request_sent"],parsed["structure"]["candidate_count"]),
                         ("READY",True,20))
        with tempfile.TemporaryDirectory() as directory:
            artifact=Path(directory)/"missing"/"live.json"
            write_live_artifact(raw,artifact)
            stored=json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(stored["structure"]["candidate_structures"][0]["text_length"],6)
            serialized=artifact.read_text(encoding="utf-8")
            for secret in ("готов","hidden","private prompt","cookie-token","data-secret"):
                self.assertNotIn(secret,serialized)
            self.assertEqual(list(artifact.parent.glob("*.tmp")),[])

    def test_chatgpt_a11y_probe_keeps_only_bounded_structural_fields(self):
        from tools.chatgpt_response_a11y_probe import parse_a11y_probe, write_a11y_artifact
        node={"tag":"DIV","role":"article","aria_label":"private assistant answer","class_names":["message","cookie-token"],
              "visible":True,"text_length":12,"parent_tag":"SECTION","parent_class_names":["thread"],
              "data_attributes":["data-testid","data-secret"],"data_author_role":"assistant","response_text":"готов"}
        raw={"live":{"status":"READY","request_sent":True,"main_visible":True,"main_descendant_count":30,
                     "iframe_count":2,"iframe_origins":["https://chatgpt.com/conversation/abc","file:///private"],
                     "shadow_root_count":1,"accessibility_candidate_count":4},
             "candidate_structures":[node for _ in range(25)],"response_text":"готов","cookies":"hidden"}
        parsed=parse_a11y_probe(raw)
        self.assertEqual((parsed["probe_status"],parsed["likely_assistant_candidate_count"],parsed["iframe_count"]),
                         ("READY",20,2))
        self.assertEqual(parsed["iframe_origins"],["https://chatgpt.com","NOT_AVAILABLE"])
        with tempfile.TemporaryDirectory() as directory:
            artifact=Path(directory)/"new"/"a11y.json"
            write_a11y_artifact(raw,artifact)
            serialized=artifact.read_text(encoding="utf-8")
            for secret in ("готов","hidden","private assistant answer","cookie-token","data-secret","conversation/abc"):
                self.assertNotIn(secret,serialized)
            self.assertEqual(list(artifact.parent.glob("*.tmp")),[])

    def test_application_discovery_exposes_trusted_desktop_url_display_name_only(self):
        with tempfile.TemporaryDirectory() as directory:
            desktop=Path(directory)/"Desktop"; desktop.mkdir(); (desktop/"EA SPORTS FC 26.url").write_text("[InternetShortcut]\nURL=steam://rungameid/0\n",encoding="utf-8")
            with patch("plugins.applications.plugin.Path.home",return_value=Path(directory)):
                entries=ApplicationsPlugin().discover_resources(ResourceTarget("game","FIFA"))
            candidate=next(item for item in entries if item["name"]=="EA SPORTS FC 26")
            self.assertEqual(candidate["source"],"desktop_shortcut")
            self.assertEqual(candidate["command"],{"intent":"OPEN_APPLICATION","parameters":{"application":"EA SPORTS FC 26"}})
            self.assertNotIn("URL",str(candidate)); self.assertNotIn(".url",str(candidate))

    def test_open_application_direct_miss_uses_resource_confirmation_without_history(self):
        class Router:
            def __init__(self,miss=True): self.calls=[]; self.miss=miss
            def route(self,command,confirmed=False):
                self.calls.append(command)
                return Result(False,"Не найдено.",{"resource_not_found":True}) if self.miss and len(self.calls)==1 else Result(True,"Открываю.")
        def candidate(name): return {"name":name,"aliases":[],"source":"desktop_shortcut","command":{"intent":"OPEN_APPLICATION","parameters":{"application":name}}}
        with tempfile.TemporaryDirectory() as directory:
            assistant=self._isolated_assistant(directory); router=Router(); assistant.router=router; assistant.skills.router=router; assistant.routines.router=router
            assistant.resources=ResourceResolver({"application":lambda target:[candidate("Arcade Pro")],"game":lambda target:[]})
            prompt=assistant.handle("открой Arcade")
            self.assertTrue(prompt.confirmation_required); self.assertEqual(assistant.dialogue.pending["kind"],"resource_confirmation")
            self.assertEqual([call.parameters for call in router.calls],[{"application":"arcade"}]); self.assertEqual(assistant.memory.history,[])
            confirmed=assistant.handle("да")
            self.assertTrue(confirmed.ok); self.assertEqual([call.parameters for call in router.calls],[{"application":"arcade"},{"application":"Arcade Pro"}]); self.assertEqual(assistant.memory.history,[])
        with tempfile.TemporaryDirectory() as directory:
            assistant=self._isolated_assistant(directory); router=Router(); assistant.router=router; assistant.skills.router=router; assistant.routines.router=router
            calls=[]; assistant.resources=ResourceResolver({"application":lambda target:calls.append(target) or [],"game":lambda target:[]})
            missing=assistant.handle("открой Photoshop")
            self.assertFalse(missing.ok); self.assertEqual(missing.message,"Я не нашёл подходящее приложение."); self.assertEqual(len(calls),1); self.assertEqual(assistant.memory.history,[])
        with tempfile.TemporaryDirectory() as directory:
            assistant=self._isolated_assistant(directory); router=Router(miss=False); assistant.router=router; assistant.skills.router=router; assistant.routines.router=router
            assistant.resources=ResourceResolver({"application":lambda target:self.fail("fallback must not run after direct success")})
            self.assertTrue(assistant.handle("открой Discord").ok); self.assertEqual(len(router.calls),1)

    def test_provider_resource_target_uses_dialogue_and_router_without_execution_in_resolver(self):
        class Router:
            def __init__(self): self.calls=[]
            def route(self,command,confirmed=False): self.calls.append(command); return Result(True,"opened")
        class Provider(ConversationProvider):
            def __init__(self,response): self.response=response; self.calls=0
            def respond(self,turn,context): self.calls+=1; return self.response
        def candidate(name): return {"name":name,"aliases":["fifa"],"source":"shortcut","command":{"intent":"OPEN_APPLICATION","parameters":{"application":name}}}
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events)
            assistant.resources=ResourceResolver({"game":lambda target:[candidate("FIFA 26")]})
            provider=Provider(ProviderResponse("Нашёл FIFA.","OPEN_RESOURCE",{},resource_type="game",target="FIFA")); assistant.conversation_providers.register(provider)
            prompt=assistant.handle("Мне нужна FIFA")
            self.assertTrue(prompt.confirmation_required); self.assertEqual(assistant.dialogue.pending["kind"],"resource_confirmation"); self.assertEqual(router.calls,[])
            self.assertTrue(assistant.handle("Да.").ok); self.assertEqual([call.intent for call in router.calls],["OPEN_APPLICATION"]); self.assertEqual(provider.calls,1)
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); router=Router(); assistant=Assistant(); assistant.memory=memory; assistant.router=router; assistant.skills.router=router; assistant.parser.memory=memory; assistant.dialogue.memory=memory; assistant.skills.memory=memory; assistant.routines=RoutineManager(memory,router,assistant.events)
            assistant.resources=ResourceResolver({"game":lambda target:[candidate("FIFA 26"),candidate("FIFA Legacy")]})
            provider=Provider(ProviderResponse("Нашёл варианты.","OPEN_RESOURCE",{},resource_type="game",target="FIFA")); assistant.conversation_providers.register(provider)
            choices=assistant.handle("Мне нужна FIFA"); self.assertTrue(choices.ok); self.assertEqual(assistant.dialogue.pending["kind"],"resource_candidates")
            self.assertTrue(assistant.handle("2").ok); self.assertEqual(router.calls[-1].parameters,{"application":"FIFA Legacy"})

    def test_resource_target_validation_keeps_legacy_parser_and_provider_safety(self):
        registry=ConversationProviderRegistry(); allowed={"OPEN_RESOURCE"}
        valid=ProviderResponse("", "OPEN_RESOURCE",{},resource_type="application",target="FIFA")
        self.assertIs(registry.validate(valid,allowed),valid)
        self.assertIsNone(registry.validate(ProviderResponse("", "OPEN_RESOURCE",{},resource_type="application",target="C:\\Windows\\cmd.exe"),allowed))
        self.assertIsNone(registry.validate(ProviderResponse("", "OPEN_RESOURCE",{"path":"x"},resource_type="application",target="FIFA"),allowed))
        memory=type("M",(),{"aliases":{},"custom_commands":{},"learned_routines":[],"skills":[]})()
        parser=IntentParser(memory)
        self.assertEqual(parser.parse("открой https://example.com").intent,"OPEN_BROWSER")
        self.assertEqual(parser.parse("открой YouTube").intent,"OPEN_YOUTUBE")
        self.assertEqual(parser.parse("поставь громкость 50").intent,"SET_VOLUME")

    def test_learn_routine_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            assistant=self._isolated_assistant(directory,load_plugins=True); self.assertTrue(assistant.handle("научись, как я готовлюсь к стриму").ok); self.assertTrue(assistant.routine_learning)
            assistant.events.emit("command_routed",command=Command("OPEN_APPLICATION",{"application":"obs"}),result=Result(True,"ok")); assistant.events.emit("command_routed",command=Command("SET_VOLUME",{"level":60}),result=Result(True,"ok"))
            self.assertTrue(assistant.handle("закончил обучение").ok); self.assertEqual(assistant.dialogue.pending["kind"],"routine"); self.assertTrue(assistant.handle("Подготовка к стриму").confirmation_required); self.assertTrue(assistant.handle("да").ok)
            routine=assistant.routines.find("Подготовка к стриму"); self.assertIsNotNone(routine); assistant.memory.learned_routines.remove(routine); assistant.memory.save_learned_routines()
    def test_previously_destructive_assistant_tests_preserve_persistent_like_sentinel(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=MemoryManager(directory); sentinel=RoutineManager(memory,RouterStub()).create("TEST_SENTINEL_ROUTINE",[{"intent":"OPEN_BROWSER","parameters":{}}])
            for name in ("test_assistant_parser_dialogue_and_plugin_discovery","test_assistant_demonstration_dialogue_integration","test_learn_routine_end_to_end"):
                case=CoreTests(name); getattr(case,name)()
            reloaded=RoutineManager(MemoryManager(directory),RouterStub()).get_routine(sentinel["id"])
            self.assertIsNotNone(reloaded); self.assertEqual(reloaded["name"],"TEST_SENTINEL_ROUTINE")
    def test_find_routine_discovery_confidence(self):
        with tempfile.TemporaryDirectory() as directory:
            m=MemoryManager(directory); r=RoutineManager(m,RouterStub()); stream=r.create("Подготовка к стриму",[],"действия перед стримом",["подготовь меня к стриму"]); r.create("Подготовка рабочего места",[])
            self.assertEqual(r.find_routine("  подготовка К стриму ")["confidence"],"HIGH")
            self.assertEqual(r.find_routine("подготовь меня к стриму")["matched_by"],"alias")
            self.assertEqual(r.find_routine("сделай подготовку перед стримом")["confidence"],"MEDIUM")
            self.assertIsNone(r.find_routine("подготовка")["routine"]); self.assertIsNone(r.find_routine("совсем другое")["routine"]); self.assertEqual(stream["usage_count"],0)
    def test_medium_routine_confirmation_dialogue(self):
        with tempfile.TemporaryDirectory() as directory:
            m=MemoryManager(directory); router=RouterStub(); manager=RoutineManager(m,router); routine=manager.create("Подготовка к стриму",[{"intent":"OPEN_APPLICATION","parameters":{"application":"obs"}}],"действия перед стримом")
            dialog=DialogueManager(LearningEngine(None,SkillManager(m,router)),SkillManager(m,router),m); self.assertTrue(dialog.start_routine_confirmation(routine).confirmation_required); confirmed=dialog.handle("да"); self.assertEqual(confirmed.data["routine"],routine["name"]); self.assertEqual(routine["usage_count"],0)
    def test_medium_confirmation_yes_no_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            assistant=Assistant(); assistant.memory=MemoryManager(directory); assistant.routines.memory=assistant.memory
            calls=[]; assistant.routines.router=type("R",(),{"route":lambda s,c: calls.append(c) or Result(True,"ok")})()
            routine=assistant.routines.create("Подготовка к стриму",[{"intent":"OPEN_APPLICATION","parameters":{"application":"obs"}}],"действия перед стримом")
            prompt=assistant.handle("сделай подготовку перед стримом"); self.assertTrue(prompt.confirmation_required); self.assertEqual(routine["usage_count"],0)
            self.assertTrue(assistant.handle("да").ok); self.assertEqual(len(calls),1); self.assertEqual(routine["usage_count"],1); self.assertIsNone(assistant.dialogue.pending)
            self.assertFalse(assistant.handle("да").ok); self.assertEqual(len(calls),1)
            assistant.handle("сделай подготовку перед стримом"); self.assertFalse(assistant.handle("нет").ok); self.assertEqual(len(calls),1); self.assertEqual(routine["usage_count"],1); self.assertIsNone(assistant.dialogue.pending)
    def test_routine_candidate_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            m=MemoryManager(directory); router=RouterStub(); manager=RoutineManager(m,router); first=manager.create("Подготовка к стриму",[{"intent":"OPEN_APPLICATION","parameters":{"application":"obs"}}]); second=manager.create("Подготовка рабочего места",[{"intent":"OPEN_APPLICATION","parameters":{"application":"discord"}}])
            dialog=DialogueManager(LearningEngine(None,SkillManager(m,router)),SkillManager(m,router),m); self.assertTrue(dialog.start_routine_candidates([first,second]).ok)
            selected=dialog.handle("2"); self.assertEqual(selected.data["routine"],second["name"]); self.assertIsNone(dialog.pending); self.assertEqual(first["usage_count"],0); self.assertEqual(second["usage_count"],0)
    def test_routine_candidate_invalid_and_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            m=MemoryManager(directory); router=RouterStub(); manager=RoutineManager(m,router); first=manager.create("Подготовка к стриму",[]); second=manager.create("Подготовка рабочего места",[])
            dialog=DialogueManager(LearningEngine(None,SkillManager(m,router)),SkillManager(m,router),m); dialog.start_routine_candidates([first,second]); self.assertFalse(dialog.handle("3").ok); self.assertIsNotNone(dialog.pending); self.assertTrue(dialog.handle("нет").ok); self.assertIsNone(dialog.pending)
    def test_routine_management_and_alias_api(self):
        with tempfile.TemporaryDirectory() as directory:
            m=MemoryManager(directory); manager=RoutineManager(m,RouterStub()); self.assertEqual(manager.list_routines(),[])
            item=manager.create_routine("Routine",[{"intent":"OPEN_APPLICATION","parameters":{"application":"obs"}}],"old"); item["usage_count"]=4
            self.assertIs(manager.get_routine(item["id"]),item); self.assertIsNone(manager.get_routine("missing")); self.assertTrue(manager.add_alias(item["id"],"run routine")); self.assertFalse(manager.add_alias(item["id"],"run routine")); self.assertFalse(manager.add_alias(item["id"],"")); self.assertEqual(manager.find_routine("run routine")["routine"],item)
            updated=manager.update_routine(item["id"],name="Renamed",description="new",actions=[]); self.assertEqual(updated["id"],item["id"]); self.assertEqual(updated["usage_count"],4); self.assertTrue(manager.remove_alias(item["id"],"run routine")); self.assertIsNone(manager.find_routine("run routine")["routine"])
            self.assertTrue(manager.delete_routine(item["id"])); self.assertEqual(RoutineManager(MemoryManager(directory),RouterStub()).list_routines(),[])
    def test_routine_text_management_list_rename_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            a=Assistant(); a.memory=MemoryManager(directory); a.routines.memory=a.memory; a.dialogue.memory=a.memory; a.parser.memory=a.memory
            self.assertEqual(a.handle("покажи мои процедуры").message,"Процедур пока нет.")
            item=a.routines.create("Старая",[]); self.assertTrue(a.handle("переименуй процедуру старая в новая").ok); self.assertIsNotNone(a.routines.find("новая")); self.assertFalse(a.handle("переименуй процедуру нет в новая").ok)
            prompt=a.handle("удали процедуру новая"); self.assertTrue(prompt.confirmation_required); self.assertFalse(a.handle("нет").ok); self.assertIsNotNone(a.routines.find("новая")); a.handle("удали процедуру новая"); self.assertTrue(a.handle("да").ok); self.assertIsNone(a.routines.get_routine(item["id"]))
    def test_routine_details_and_alias_text_management(self):
        with tempfile.TemporaryDirectory() as directory:
            a=Assistant(); a.memory=MemoryManager(directory); a.routines.memory=a.memory; a.dialogue.memory=a.memory; a.parser.memory=a.memory; a.skills.memory=a.memory
            router=RouterStub(); a.routines.router=router
            routine=a.routines.create("Подготовка к стриму",[{"intent":"OPEN_APPLICATION","parameters":{"application":"obs"}}],"Перед трансляцией")
            details=a.handle("расскажи про подготовку к стриму")
            self.assertTrue(details.ok); self.assertIn("Подготовка к стриму",details.message); self.assertIn("Перед трансляцией",details.message); self.assertIn("OPEN_APPLICATION",details.message); self.assertEqual(router.calls,[])
            self.assertFalse(a.handle("что входит в отсутствующую процедуру").ok)
            self.assertTrue(a.handle('добавь алиас "перед стримом" для процедуры подготовка к стриму').ok)
            self.assertIs(a.routines.find_routine("перед стримом")["routine"],routine); self.assertFalse(a.handle('добавь алиас "перед стримом" для процедуры подготовка к стриму').ok); self.assertFalse(a.handle('добавь алиас "" для процедуры подготовка к стриму').ok)
            other=a.routines.create("Другая",[]); self.assertFalse(a.handle('добавь алиас "перед стримом" для процедуры другая').ok)
            self.assertTrue(a.handle('удали алиас "перед стримом" у процедуры подготовка к стриму').ok); self.assertIsNone(a.routines.find_routine("перед стримом")["routine"]); self.assertFalse(a.handle('удали алиас "перед стримом" у процедуры подготовка к стриму').ok); self.assertFalse(a.handle('удали алиас "x" у процедуры отсутствует').ok)
            self.assertIs(a.routines.find_routine("подготовка к стриму")["routine"],routine); self.assertTrue(a.routines.run(routine["name"]).ok); self.assertEqual(len(router.calls),1); self.assertIsNotNone(other)
