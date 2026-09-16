"""Optional user-controlled ChatGPT browser provider; never reads browser credentials."""
from dataclasses import dataclass
import importlib.util
import os
import re
import time
from pathlib import Path

from .conversation_provider import ProviderAdapter, ProviderCapabilities, ProviderResponse

_SENSITIVE=re.compile(r"(?:token|secret|password|credential|authorization|cookie|api[_-]?key)",re.I)


@dataclass(frozen=True)
class BrowserChatGPTConfig:
    profile_dir: str="data/browser_profiles/chatgpt"
    timeout_seconds: float=30.0
    max_response_length: int=500

    def valid(self):
        return (isinstance(self.profile_dir,str) and self.profile_dir and len(self.profile_dir)<=240
                and isinstance(self.timeout_seconds,(int,float)) and 5<=self.timeout_seconds<=60
                and isinstance(self.max_response_length,int) and 1<=self.max_response_length<=500)


class ChatGPTBrowserTransport:
    """DOM-only transport contract. It owns no provider semantics or JARVIS state."""
    def status(self): raise NotImplementedError
    def send_message(self, text, timeout): raise NotImplementedError


class PlaywrightChatGPTBrowserTransport(ChatGPTBrowserTransport):
    """Dedicated persistent profile; credentials stay inside the user-controlled browser."""
    page_url="https://chatgpt.com/"
    input_selectors=("textarea",'[contenteditable="true"][role="textbox"]','[role="textbox"][contenteditable="true"]','[contenteditable="true"]')
    send_selectors=('button[data-testid="send-button"]','button[aria-label="Send"]','button[aria-label="Отправить"]','button[type="submit"]')
    # Some ChatGPT layouts no longer expose data-message-author-role on the
    # assistant turn. A visible Markdown block in the conversation area is a
    # narrow fallback; composer and explicit user turns are excluded below.
    assistant_selectors=('main [data-message-author-role="assistant"]','main [role="article"][data-message-author-role="assistant"]','main .markdown')
    # Current ChatGPT transcript contract, established by the bounded live DOM
    # probe. Only a newly added, completed assistant LI is authoritative.
    assistant_turn_selector='main ol[data-conversation-transcript] > li[data-message-role][data-assistant-content-started]'
    generation_selectors=('button[data-testid="stop-button"]','button[aria-label*="Stop"]','button[aria-label*="Остановить"]')
    new_chat_selectors=('a[href="/"]','button[aria-label*="New chat"]','button[data-testid="new-chat-button"]')

    def __init__(self, config=None):
        self.config=config or BrowserChatGPTConfig(); self._playwright=None; self._context=None; self._page=None; self.last_state="UNAVAILABLE"; self.last_diagnostic=self._new_dom_diagnostic()

    @staticmethod
    def _new_dom_diagnostic():
        return {"input_candidates_count":0,"contenteditable_candidates_count":0,"textarea_candidates_count":0,"visible_input_candidates":0,"enabled_input_candidates":0,"send_candidates_count":0,"visible_send_candidates":0,"enabled_send_candidates":0,"active_element_type":"NOT_AVAILABLE","page_ready":False,"input_detected":False,"input_type":"NOT_AVAILABLE","input_fill_status":"NOT_ATTEMPTED","send_control_detected":False,"send_control_type":"NOT_AVAILABLE","send_action_status":"NOT_ATTEMPTED","request_sent":False,"assistant_message_candidates":0,"visible_assistant_messages":0,"assistant_text_candidates":0,"latest_message_detected":False,"latest_message_role":"NOT_AVAILABLE","latest_message_length":0,"generation_indicator_present":False,"composer_value_after_submit":"NOT_AVAILABLE","response_transition_observed":False}

    @staticmethod
    def _playwright_cache_root():
        configured=os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        if configured and configured != "0": return Path(configured)
        local_app_data=os.environ.get("LOCALAPPDATA","")
        return Path(local_app_data)/"ms-playwright" if local_app_data else Path("ms-playwright")

    def _playwright_package_available(self):
        try: return importlib.util.find_spec("playwright.sync_api") is not None
        except (ImportError, AttributeError, ValueError): return False

    def _transport_import_available(self):
        try:
            from playwright.sync_api import sync_playwright
            return callable(sync_playwright)
        except ImportError: return False

    def _browser_binary_available(self):
        root=self._playwright_cache_root()
        try:
            for candidate in root.glob("chromium-*"):
                if ((candidate/"chrome-win"/"chrome.exe").is_file()
                        or (candidate/"chrome-win64"/"chrome.exe").is_file()): return True
        except OSError: pass
        return False

    def _profile_directory_ready(self):
        """Check existence or creatability without reading profile contents."""
        profile=Path(self.config.profile_dir)
        try:
            if profile.exists(): return profile.is_dir() and os.access(str(profile),os.R_OK|os.W_OK)
            ancestor=profile.parent
            while not ancestor.exists() and ancestor != ancestor.parent: ancestor=ancestor.parent
            return ancestor.is_dir() and os.access(str(ancestor),os.R_OK|os.W_OK)
        except OSError: return False

    def _engine_configuration_available(self):
        return self.config.valid()

    def preflight(self):
        """Read-only startup prerequisites; this never launches a browser."""
        package=self._playwright_package_available()
        imported=self._transport_import_available() if package else False
        binary=self._browser_binary_available()
        profile=self._profile_directory_ready()
        engine=self._engine_configuration_available()
        ready=package and imported and binary and profile and engine
        return {
            "browser_dependency":"READY" if package and binary else "UNAVAILABLE",
            "playwright_package":"READY" if package else "NOT_INSTALLED",
            "browser_binary":"READY" if binary else "MISSING",
            "profile_directory":"READY" if profile else "UNAVAILABLE",
            "transport_import":"PASS" if imported else "FAIL",
            "engine_configuration":"PASS" if engine else "FAIL",
            "preflight_status":"READY" if ready else "UNAVAILABLE",
        }

    def _open(self):
        if self._page is not None and not self._page.is_closed(): return True
        try:
            from playwright.sync_api import sync_playwright
            profile=Path(self.config.profile_dir); profile.mkdir(parents=True,exist_ok=True)
            self._playwright=sync_playwright().start()
            self._context=self._playwright.chromium.launch_persistent_context(str(profile),headless=False)
            self._page=self._context.pages[0] if self._context.pages else self._context.new_page()
            self._page.goto(self.page_url,wait_until="domcontentloaded",timeout=int(self.config.timeout_seconds*1000))
            return True
        except Exception:
            self.last_state="UNAVAILABLE"; return False

    def _first(self, selectors):
        for selector in selectors:
            locator=self._page.locator(selector)
            try:
                if locator.count()>0 and locator.first.is_visible(): return locator.first
            except Exception: continue
        return None

    @staticmethod
    def _is_visible(locator):
        try: return locator.is_visible()
        except Exception: return False

    @staticmethod
    def _is_enabled(locator):
        try: return locator.is_enabled()
        except Exception: return False

    def _active_element_type(self):
        try:
            value=self._page.evaluate("() => { const element=document.activeElement; return element ? element.tagName : 'NONE'; }")
            return str(value or "NOT_AVAILABLE").upper()[:40]
        except Exception: return "NOT_AVAILABLE"

    def _candidates(self, root, selectors):
        candidates=[]
        for selector in selectors:
            locator=root.locator(selector)
            try:
                candidates.extend(locator.nth(index) for index in range(min(locator.count(),8)))
            except Exception: continue
        return candidates

    def _find_input(self):
        diagnostic=self.last_diagnostic
        all_candidates=self._candidates(self._page,self.input_selectors)
        diagnostic["input_candidates_count"]=len(all_candidates)
        diagnostic["textarea_candidates_count"]=sum(1 for item in all_candidates if self._locator_type(item)=="TEXTAREA")
        diagnostic["contenteditable_candidates_count"]=sum(1 for item in all_candidates if self._locator_type(item)=="CONTENTEDITABLE")
        visible=[item for item in all_candidates if self._is_visible(item)]
        editable=[]
        for item in visible:
            try:
                if item.is_editable(): editable.append(item)
            except Exception: continue
        diagnostic["visible_input_candidates"]=len(visible); diagnostic["enabled_input_candidates"]=len(editable)
        # Selector order is deliberate; within any selector a non-unique composer
        # is unsafe, so no arbitrary textbox is chosen.
        for selector in self.input_selectors:
            eligible=[item for item in self._candidates(self._page,(selector,)) if self._is_visible(item) and self._is_editable(item)]
            if len(eligible)==1:
                field=eligible[0]; diagnostic["input_detected"]=True; diagnostic["input_type"]=self._locator_type(field); return field
            if len(eligible)>1: break
        self.last_state="INPUT_NOT_FOUND"; return None

    @staticmethod
    def _is_editable(locator):
        try: return locator.is_editable()
        except Exception: return False

    @staticmethod
    def _locator_type(locator):
        try:
            tag=str(locator.evaluate("element => element.tagName")).upper()
            return "TEXTAREA" if tag=="TEXTAREA" else "CONTENTEDITABLE" if str(locator.get_attribute("contenteditable")).lower()=="true" else tag[:40]
        except Exception: return "NOT_AVAILABLE"

    def _composer_context(self, field):
        try:
            form=field.locator("xpath=ancestor::form[1]")
            if form.count()==1 and self._is_visible(form): return form
        except Exception: pass
        return None

    def _find_send_control(self, composer):
        diagnostic=self.last_diagnostic
        if composer is None:
            self.last_state="SEND_CONTROL_NOT_FOUND"; return None
        candidates=self._candidates(composer,self.send_selectors)
        diagnostic["send_candidates_count"]=len(candidates)
        visible=[item for item in candidates if self._is_visible(item)]
        enabled=[item for item in visible if self._is_enabled(item)]
        diagnostic["visible_send_candidates"]=len(visible); diagnostic["enabled_send_candidates"]=len(enabled)
        for selector in self.send_selectors:
            eligible=[item for item in self._candidates(composer,(selector,)) if self._is_visible(item) and self._is_enabled(item)]
            if len(eligible)==1:
                send=eligible[0]; diagnostic["send_control_detected"]=True; diagnostic["send_control_type"]="BUTTON"; return send
            if len(eligible)>1: break
        self.last_state="SEND_CONTROL_DISABLED" if visible else "SEND_CONTROL_NOT_FOUND"; return None

    def _submit_transition_observed(self, field, timeout):
        """Observe only a boolean DOM transition; no composer text is retained."""
        try:
            handle=field.element_handle(timeout=timeout)
            if handle is not None:
                try:
                    self._page.wait_for_function("element => element.tagName === 'TEXTAREA' ? element.value.length === 0 : element.textContent.trim().length === 0",arg=handle,timeout=timeout)
                    self.last_diagnostic["composer_value_after_submit"]="CLEARED"
                    return True
                finally: handle.dispose()
        except Exception: pass
        return self._first(self.generation_selectors) is not None

    def _assistant_counts(self):
        counts={}
        try: counts[self.assistant_turn_selector]=self._page.locator(self.assistant_turn_selector).count()
        except Exception: counts[self.assistant_turn_selector]=0
        for selector in self.assistant_selectors:
            try: counts[selector]=self._page.locator(selector).count()
            except Exception: counts[selector]=0
        return counts

    @staticmethod
    def _has_ancestor(locator, expression):
        try: return locator.locator(expression).count()>0
        except Exception: return False

    def _assistant_role(self, locator):
        if self._has_ancestor(locator,"xpath=ancestor::*[@data-message-author-role='user'][1]"):
            return "USER"
        if self._has_ancestor(locator,"xpath=ancestor::form[1]") or self._has_ancestor(locator,"xpath=ancestor::*[@contenteditable='true'][1]"):
            return "COMPOSER"
        try:
            if locator.get_attribute("data-assistant-content-started") is not None: return "ASSISTANT"
            if str(locator.get_attribute("data-message-author-role") or "").lower()=="assistant": return "ASSISTANT"
            classes=str(locator.get_attribute("class") or "").split()
            if "markdown" in classes: return "ASSISTANT"
        except Exception: pass
        if self._has_ancestor(locator,"xpath=ancestor::*[@data-message-author-role='assistant'][1]"):
            return "ASSISTANT"
        return "UNKNOWN"

    @staticmethod
    def _completed_assistant_turn(locator):
        try:
            return (locator.get_attribute("data-assistant-content-started") is not None
                    and locator.get_attribute("data-message-complete") is not None)
        except Exception: return False

    def _new_transcript_assistant_candidates(self, before_counts):
        """Return only completed assistant LIs added after the send snapshot."""
        try:
            locator=self._page.locator(self.assistant_turn_selector)
            total=locator.count(); start=before_counts.get(self.assistant_turn_selector,0)
            if total<=start: return [],False
            candidates=[]
            for index in range(max(start,total-8),total):
                item=locator.nth(index)
                if self._is_visible(item) and self._completed_assistant_turn(item): candidates.append(item)
            # A new but incomplete turn must remain on this path and be waited
            # for; a legacy fallback must not race it.
            return candidates,True
        except Exception:
            return [],False

    def _new_assistant_candidates(self, before_counts):
        """Inspect only the bounded tail added after the pre-send snapshot."""
        transcript,transcript_seen=self._new_transcript_assistant_candidates(before_counts)
        if transcript or transcript_seen: return transcript
        candidates=[]
        for selector in self.assistant_selectors:
            try:
                locator=self._page.locator(selector)
                total=locator.count(); start=before_counts.get(selector,0)
                if total<=start: continue
                for index in range(max(start,total-8),total):
                    item=locator.nth(index)
                    if self._is_visible(item) and self._assistant_role(item)=="ASSISTANT":
                        candidates.append(item)
            except Exception: continue
        return candidates

    def _visible_assistant_candidates(self):
        turns=[]
        try:
            locator=self._page.locator(self.assistant_turn_selector)
            turns.extend(locator.nth(index) for index in range(min(locator.count(),8)))
        except Exception: pass
        fallback=self._candidates(self._page,self.assistant_selectors)
        candidates=turns+fallback
        visible=([item for item in turns if self._is_visible(item) and self._completed_assistant_turn(item)]
                 +[item for item in fallback if self._is_visible(item) and self._assistant_role(item)=="ASSISTANT"])
        diagnostic=self.last_diagnostic
        diagnostic["assistant_message_candidates"]=len(candidates)
        diagnostic["visible_assistant_messages"]=len(visible)
        diagnostic["generation_indicator_present"]=self._first(self.generation_selectors) is not None
        return visible

    @staticmethod
    def _visible_message_text(locator):
        try: return " ".join(str(locator.inner_text(timeout=2000) or "").split())
        except Exception: return ""

    def _stable_response_text(self, locator, initial, timeout):
        """Return only a bounded, stable visible assistant message string."""
        if not initial: return ""
        deadline=time.monotonic()+max(0.05,timeout/1000)
        previous=initial; stable_samples=0
        while time.monotonic()<deadline:
            try: self._page.wait_for_timeout(min(250,max(1,int((deadline-time.monotonic())*1000))))
            except Exception: time.sleep(.05)
            current=self._visible_message_text(locator)
            if current and current==previous:
                stable_samples+=1
                if stable_samples>=3 and self._first(self.generation_selectors) is None:
                    return current
            elif current:
                previous=current; stable_samples=0
        return ""

    def _wait_for_response(self, before_counts, timeout):
        """Wait for a new visible assistant message, then require text stability."""
        deadline=time.monotonic()+max(0.05,timeout/1000)
        while time.monotonic()<deadline:
            visible=self._visible_assistant_candidates()
            new_visible=self._new_assistant_candidates(before_counts)
            if new_visible:
                latest=new_visible[-1]
                text=self._visible_message_text(latest)
                diagnostic=self.last_diagnostic
                diagnostic["latest_message_detected"]=True; diagnostic["latest_message_role"]="ASSISTANT"; diagnostic["latest_message_length"]=len(text)
                if text:
                    diagnostic["assistant_text_candidates"]=sum(1 for item in visible if self._visible_message_text(item))
                    stable=self._stable_response_text(latest,text,min(1500,max(50,int((deadline-time.monotonic())*1000))))
                    if stable:
                        diagnostic["latest_message_length"]=len(stable); diagnostic["response_transition_observed"]=True
                        return stable
            try: self._page.wait_for_timeout(min(100,max(1,int((deadline-time.monotonic())*1000))))
            except Exception: time.sleep(.05)
        return ""

    def status(self):
        if not self.config.valid(): self.last_state="ERROR"; return self.last_state
        if self.preflight()["preflight_status"]!="READY": self.last_state="UNAVAILABLE"; return self.last_state
        if not self._open(): return self.last_state
        if self._page is None or self._page.is_closed(): self.last_state="BROWSER_CLOSED"; return self.last_state
        if self._first(self.input_selectors) is None: self.last_state="LOGIN_REQUIRED"; return self.last_state
        self.last_state="READY"; return self.last_state

    def send_message(self, text, timeout):
        if self.status()!="READY": return None
        self.last_diagnostic=self._new_dom_diagnostic(); self.last_diagnostic["page_ready"]=True; self.last_diagnostic["active_element_type"]=self._active_element_type()
        try:
            # Isolate JARVIS turns from personal conversations when the standard
            # new-chat control is present; no unrelated tab is inspected.
            new_chat=self._first(self.new_chat_selectors)
            if new_chat is not None: new_chat.click(timeout=2000)
            field=self._find_input()
            if field is None: return None
            before=self._assistant_counts()
            field.fill(text); self.last_diagnostic["input_fill_status"]="PASS"
            composer=self._composer_context(field); send=self._find_send_control(composer)
            if send is None: return None
            send.click()
            self.last_diagnostic["send_action_status"]="CLICKED"
            deadline=int(timeout*1000)
            if not self._submit_transition_observed(field,min(deadline,3000)):
                self.last_diagnostic["send_action_status"]="TRANSITION_NOT_DETECTED"; self.last_state="DOM_SUBMIT_TRANSITION_NOT_DETECTED"; return None
            self.last_diagnostic["request_sent"]=True
            response=self._wait_for_response(before,deadline)
            if response: self.last_state="READY"
            elif self.last_diagnostic["latest_message_detected"]: self.last_state="EMPTY_RESPONSE"
            else: self.last_state="RESPONSE_TIMEOUT"
            return response or None
        except Exception:
            if self.last_diagnostic["input_fill_status"]!="PASS": self.last_diagnostic["input_fill_status"]="FAIL"
            self.last_state="RESPONSE_TIMEOUT"; return None


class BrowserChatGPTProvider(ProviderAdapter):
    """Explicit text-only optional provider; it cannot produce executable intents."""
    provider_id="browser_chatgpt"
    capabilities=ProviderCapabilities(True,False,False,False,False)

    def __init__(self, transport=None, config=None):
        self.config=config or BrowserChatGPTConfig()
        self.transport=transport or PlaywrightChatGPTBrowserTransport(self.config)
        self.last_state="UNAVAILABLE"; self.last_diagnostic={}

    def status(self):
        try: state=self.transport.status()
        except Exception: state="ERROR"
        self.last_state=state if state in {"UNAVAILABLE","READY","LOGIN_REQUIRED","BUSY","ERROR","BROWSER_CLOSED","SEND_CONTROL_NOT_FOUND","RESPONSE_TIMEOUT","EMPTY_RESPONSE","MULTIPLE_TABS_AMBIGUOUS"} else "ERROR"
        return self.last_state

    def diagnostic_status(self):
        """Read-only state: diagnostics must never open a browser or profile."""
        preflight=getattr(self.transport,"preflight",None)
        if callable(preflight):
            self.last_state="READY" if preflight().get("preflight_status")=="READY" else "UNAVAILABLE"
        return self.last_state

    def preflight(self):
        """Expose native startup checks without giving the provider execution access."""
        check=getattr(self.transport,"preflight",None)
        if callable(check): return check()
        return {"browser_dependency":"NOT_AVAILABLE","playwright_package":"NOT_AVAILABLE","browser_binary":"NOT_AVAILABLE","profile_directory":"NOT_AVAILABLE","transport_import":"NOT_AVAILABLE","engine_configuration":"NOT_AVAILABLE","preflight_status":"UNAVAILABLE"}

    @staticmethod
    def _safe_text(value, limit):
        value=" ".join(str(value or "").split())
        if not value or len(value)>limit or _SENSITIVE.search(value): return ""
        return value

    def respond_bounded(self, turn, context):
        if self.status()!="READY": return None
        text=self._safe_text(dict(turn or {}).get("normalized_text",""),500)
        if not text: self.last_state="ERROR"; return None
        self.last_state="BUSY"
        try: response=self.transport.send_message(text,self.config.timeout_seconds)
        except Exception: self.last_state="ERROR"; return None
        self.last_state=getattr(self.transport,"last_state",None) or "ERROR"
        self.last_diagnostic=dict(getattr(self.transport,"last_diagnostic",{}) or {})
        response=self._safe_text(response,self.config.max_response_length)
        if not response:
            if self.last_state not in {"RESPONSE_TIMEOUT","BROWSER_CLOSED","EMPTY_RESPONSE"}: self.last_state="EMPTY_RESPONSE"
            return None
        self.last_state="READY"
        return ProviderResponse(text=response,intent="",parameters={},conversational=True,response_type="conversational")
