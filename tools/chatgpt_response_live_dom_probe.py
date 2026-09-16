"""One-request, structural-only probe of the dedicated ChatGPT browser profile.

This is a diagnostic tool, not the BrowserChatGPTProvider or its smoke test.
It never persists page text, response text, HTML, screenshots, or browser storage.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.chatgpt_response_dom_diagnostic import (
    PROFILE_PATH, PROJECT_ROOT, _STRUCTURAL_JS, parse_diagnostic_structure,
)


ARTIFACT_RELATIVE_PATH = Path("data/diagnostics/chatgpt_response_live_dom.json")
ARTIFACT_PATH = PROJECT_ROOT / ARTIFACT_RELATIVE_PATH
TEST_MESSAGE = "Ответь одним словом: готов."
INPUT_SELECTORS = ("textarea", '[contenteditable="true"][role="textbox"]',
                   '[role="textbox"][contenteditable="true"]', '[contenteditable="true"]')
SEND_SELECTORS = ('button[data-testid="send-button"]', 'button[aria-label="Send"]',
                  'button[aria-label="Отправить"]', 'button[type="submit"]')

# Read only structural properties. No text value is returned to Python.
_LIVE_CANDIDATES_JS = """() => {
  const main = document.querySelector('main, [role="main"]');
  if (!main) return {candidate_structures: [], assistant_marked: false, counts: {}};
  const visible = node => {
    const rect = node.getBoundingClientRect(), style = getComputedStyle(node);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' &&
           style.visibility !== 'hidden';
  };
  const structure = node => {
    const parent = node.parentElement;
    return {tag: node.tagName, role: node.getAttribute('role') || '',
      aria_label: node.getAttribute('aria-label') || '',
      class_names: [...node.classList].slice(0, 8), visible: visible(node),
      text_length: (node.innerText || '').trim().length,
      parent_tag: parent ? parent.tagName : '',
      parent_class_names: parent ? [...parent.classList].slice(0, 8) : [],
      data_attributes: node.getAttributeNames().filter(name => name.startsWith('data-')).slice(0, 8),
      data_author_role: node.getAttribute('data-message-author-role') || ''};
  };
  const nodes = [...main.querySelectorAll('*')].slice(-800);
  const candidates = [];
  let assistantMarked = false;
  for (const node of nodes) {
    if (!visible(node) || node.closest('form, [contenteditable="true"]')) continue;
    const length = (node.innerText || '').trim().length;
    if (!length) continue;
    const author = node.getAttribute('data-message-author-role') || '';
    if (author === 'user' || node.closest('[data-message-author-role="user"]')) continue;
    const classes = [...node.classList];
    const testid = node.getAttribute('data-testid') || '';
    let score = 0;
    if (author === 'assistant') {score += 150; assistantMarked = true;}
    if (node.getAttribute('role') === 'article') score += 100;
    if (node.tagName === 'ARTICLE') score += 90;
    if (classes.includes('markdown')) score += 80;
    if (classes.some(value => /message|response|assistant|prose/.test(value))) score += 50;
    if (/message|conversation-turn/.test(testid)) score += 40;
    if (node.children.length <= 2) score += 20;
    if (length <= 500) score += 10;
    if (score > 0) candidates.push({node, score});
  }
  candidates.sort((a, b) => b.score - a.score);
  return {candidate_structures: candidates.slice(0, 20).map(item => structure(item.node)),
          assistant_marked: assistantMarked, counts: {main_descendants: nodes.length,
          structural_candidates: candidates.length}};
}"""


def parse_live_structure(raw):
    """Whitelist only bounded structural diagnostics and fixed status values."""
    raw = dict(raw or {})
    structure = parse_diagnostic_structure(raw)
    live = dict(raw.get("live") or {})
    status = str(live.get("status") or "UNAVAILABLE")
    if status not in {"READY", "LOGIN_REQUIRED", "INPUT_NOT_FOUND", "SEND_NOT_FOUND",
                      "SUBMIT_NOT_CONFIRMED", "RESPONSE_NOT_OBSERVED", "RUNTIME_FAILURE"}:
        status = "UNAVAILABLE"
    return {
        "probe_status": status,
        "request_sent": live.get("request_sent") is True,
        "assistant_marked": live.get("assistant_marked") is True,
        "main_descendants": min(max(live.get("main_descendants"), 0), 800)
                            if isinstance(live.get("main_descendants"), int) and not isinstance(live.get("main_descendants"), bool) else 0,
        "structural_candidate_count": min(max(live.get("structural_candidate_count"), 0), 800)
                                      if isinstance(live.get("structural_candidate_count"), int) and not isinstance(live.get("structural_candidate_count"), bool) else 0,
        "structure": structure,
    }


def write_live_artifact(raw, path=ARTIFACT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(parse_live_structure(raw), ensure_ascii=False, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent),
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return path


def _unique_visible_editable(page):
    for selector in INPUT_SELECTORS:
        locator = page.locator(selector)
        eligible = [locator.nth(index) for index in range(min(locator.count(), 8))
                    if locator.nth(index).is_visible() and locator.nth(index).is_editable()]
        if len(eligible) == 1:
            return eligible[0]
        if len(eligible) > 1:
            return None
    return None


def _unique_send(form):
    for selector in SEND_SELECTORS:
        locator = form.locator(selector)
        eligible = [locator.nth(index) for index in range(min(locator.count(), 8))
                    if locator.nth(index).is_visible() and locator.nth(index).is_enabled()]
        if len(eligible) == 1:
            return eligible[0]
        if len(eligible) > 1:
            return None
    return None


def probe_once():
    """Exactly one UI send; browser context always closes."""
    from playwright.sync_api import sync_playwright
    result = {"live": {"status": "RUNTIME_FAILURE", "request_sent": False}}
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(str(PROFILE_PATH), headless=False)
        try:
            pages = [page for page in context.pages if page.url.startswith("https://chatgpt.com/")]
            if len(pages) > 1:
                return result
            page = pages[0] if pages else context.new_page()
            if not pages:
                page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=20000)
            page.locator("main, [role='main']").first.wait_for(state="visible", timeout=20000)
            result.update(page.evaluate(_STRUCTURAL_JS))
            field = _unique_visible_editable(page)
            if field is None:
                result["live"]["status"] = "LOGIN_REQUIRED" if page.locator("textarea, [contenteditable='true']").count() == 0 else "INPUT_NOT_FOUND"
                return result
            form = field.locator("xpath=ancestor::form[1]")
            if form.count() != 1 or not form.is_visible():
                result["live"]["status"] = "SEND_NOT_FOUND"
                return result
            field.fill(TEST_MESSAGE)
            send = _unique_send(form)
            if send is None:
                result["live"]["status"] = "SEND_NOT_FOUND"
                return result
            send.click(timeout=3000)  # The only message submission in this probe.
            try:
                handle = field.element_handle(timeout=2000)
                page.wait_for_function("element => element.tagName === 'TEXTAREA' ? element.value.length === 0 : element.textContent.trim().length === 0",
                                       arg=handle, timeout=5000)
                result["live"]["request_sent"] = True
            except Exception:
                result["live"]["status"] = "SUBMIT_NOT_CONFIRMED"
                return result
            finally:
                if 'handle' in locals() and handle is not None:
                    handle.dispose()
            deadline = time.monotonic() + 45
            last = None
            stable = 0
            while time.monotonic() < deadline:
                snapshot = page.evaluate(_LIVE_CANDIDATES_JS)
                signature = (snapshot.get("counts", {}).get("structural_candidates", 0),
                             tuple(item.get("text_length", 0) for item in snapshot.get("candidate_structures", [])))
                if signature == last and signature[0] > 0:
                    stable += 1
                else:
                    stable = 0
                last = signature
                if snapshot.get("assistant_marked") and stable >= 2:
                    break
                page.wait_for_timeout(1000)
            result.update(page.evaluate(_STRUCTURAL_JS))
            snapshot = page.evaluate(_LIVE_CANDIDATES_JS)
            result["candidate_structures"] = snapshot.get("candidate_structures", [])
            result["live"].update({"assistant_marked": snapshot.get("assistant_marked") is True,
                                   "main_descendants": snapshot.get("counts", {}).get("main_descendants", 0),
                                   "structural_candidate_count": snapshot.get("counts", {}).get("structural_candidates", 0),
                                   "status": "READY" if snapshot.get("assistant_marked") else "RESPONSE_NOT_OBSERVED"})
            return result
        finally:
            context.close()


def main():
    if sys.argv[1:] != ["--live"]:
        print("usage: python tools/chatgpt_response_live_dom_probe.py --live")
        return 20
    result = {"live": {"status": "RUNTIME_FAILURE", "request_sent": False}}
    try:
        result = probe_once()
    except Exception:
        pass  # Preserve a safe failure artifact; never print exception/page content.
    finally:
        safe = parse_live_structure(result)
        write_live_artifact(result)
        print("=== CHATGPT_RESPONSE_LIVE_DOM_PROBE ===")
        print(f"artifact_path={ARTIFACT_RELATIVE_PATH.as_posix()}")
        print(f"probe_status={safe['probe_status']}")
        print(f"request_sent={str(safe['request_sent']).lower()}")
        print(f"candidate_count={safe['structure']['candidate_count']}")
        print(f"assistant_marked={str(safe['assistant_marked']).lower()}")
        print("=== END ===")
    return 0 if safe["probe_status"] == "READY" else 20


if __name__ == "__main__":
    sys.exit(main())
