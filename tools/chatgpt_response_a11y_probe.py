"""One-request structural accessibility probe for ChatGPT's dedicated profile.

This diagnostic is intentionally separate from BrowserChatGPTProvider. It sends
one fixed message, records only bounded node structure and lengths, then closes
the browser. It never persists page/response text, HTML, cookies, tokens,
credentials, screenshots, or browser storage.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.chatgpt_response_dom_diagnostic import (
    PROFILE_PATH, PROJECT_ROOT, _safe_structure,
)
from tools.chatgpt_response_live_dom_probe import (
    INPUT_SELECTORS, SEND_SELECTORS, TEST_MESSAGE, _unique_send,
    _unique_visible_editable,
)


ARTIFACT_RELATIVE_PATH = Path("data/diagnostics/chatgpt_response_a11y_probe.json")
ARTIFACT_PATH = PROJECT_ROOT / ARTIFACT_RELATIVE_PATH
MAX_CANDIDATES = 20
_ALLOWED_STATUSES = {"READY", "LOGIN_REQUIRED", "INPUT_NOT_FOUND", "SEND_NOT_FOUND",
                     "SUBMIT_NOT_CONFIRMED", "RESPONSE_NOT_OBSERVED", "RUNTIME_FAILURE"}

# This returns only DOM structure; it never serializes innerText/textContent.
_DOM_STRUCTURE_JS = """() => {
  const main = document.querySelector('main, [role="main"]');
  const visible = node => {
    const rect = node.getBoundingClientRect(), style = getComputedStyle(node);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' &&
      style.visibility !== 'hidden';
  };
  const structure = (node, source) => {
    const parent = node.parentElement;
    return {tag: node.tagName, role: node.getAttribute('role') || '',
      aria_label: node.getAttribute('aria-label') || '',
      class_names: [...node.classList].slice(0, 8), visible: visible(node),
      text_length: (node.innerText || '').trim().length,
      parent_tag: parent ? parent.tagName : '',
      parent_class_names: parent ? [...parent.classList].slice(0, 8) : [],
      data_attributes: node.getAttributeNames().filter(name => name.startsWith('data-')).slice(0, 8),
      data_author_role: node.getAttribute('data-message-author-role') || '', source: source};
  };
  const roots = [document]; let shadowRoots = 0;
  for (let index = 0; index < roots.length && index < 1000; index++) {
    for (const node of roots[index].querySelectorAll('*')) {
      if (node.shadowRoot) { shadowRoots++; roots.push(node.shadowRoot); }
    }
  }
  if (!main) return {main_visible: false, main_descendant_count: 0, shadow_root_count: shadowRoots,
                      dom_candidates: []};
  const candidates = [], seen = new Set();
  for (const node of [...main.querySelectorAll('*')].slice(-1000)) {
    if (!visible(node) || node.closest('form, [contenteditable="true"]')) continue;
    const length = (node.innerText || '').trim().length;
    if (!length || seen.has(node)) continue;
    seen.add(node);
    const role = node.getAttribute('role') || '';
    const classes = [...node.classList];
    const attributes = node.getAttributeNames();
    const testid = node.getAttribute('data-testid') || '';
    const author = node.getAttribute('data-message-author-role') || '';
    if (author === 'user' || node.closest('[data-message-author-role="user"]')) continue;
    let score = 0;
    if (author === 'assistant') score += 200;
    if (role === 'article') score += 150;
    if (role === 'listitem' || role === 'group') score += 80;
    if (node.tagName === 'ARTICLE') score += 120;
    if (classes.includes('markdown')) score += 100;
    if (classes.some(value => /message|response|assistant|prose/.test(value))) score += 60;
    if (attributes.some(value => /message|conversation|turn|response/.test(value))) score += 40;
    if (/message|conversation|turn|response/.test(testid)) score += 40;
    if (node.children.length <= 3) score += 10;
    if (score > 0) candidates.push({score: score, structure: structure(node, 'dom')});
  }
  candidates.sort((a, b) => b.score - a.score);
  return {main_visible: visible(main), main_descendant_count: Math.min(main.querySelectorAll('*').length, 1000),
          shadow_root_count: shadowRoots, dom_candidates: candidates.slice(0, 20).map(item => item.structure)};
}"""


def _bounded_number(value, limit=1000):
    return min(max(value, 0), limit) if isinstance(value, int) and not isinstance(value, bool) else 0


def _safe_origin(value):
    try:
        parsed = urlparse(str(value or ""))
        return "%s://%s" % (parsed.scheme, parsed.netloc) if parsed.scheme in {"http", "https"} and parsed.netloc else "NOT_AVAILABLE"
    except Exception:
        return "NOT_AVAILABLE"


def _safe_ax_node(raw):
    """Convert CDP accessibility node to the same no-text structural form."""
    raw = dict(raw or {})
    role = str(dict(raw.get("role") or {}).get("value") or "")[:40]
    name = str(dict(raw.get("name") or {}).get("value") or "")
    # Name may contain assistant text. Preserve length only; never persist content.
    return {
        "tag": "AXNODE", "role": role if role.replace("_", "").replace("-", "").isalnum() else "NOT_AVAILABLE",
        "aria_label": "REDACTED" if name else "NOT_AVAILABLE", "class_names": [],
        "visible": not bool(raw.get("ignored")), "text_length": min(len(name), 100000),
        "parent_tag": "NOT_AVAILABLE", "parent_class_names": [], "data_attributes": [],
        "data_author_role": "NOT_AVAILABLE",
    }


def _a11y_candidates(cdp):
    """Read a bounded AX tree and retain role/name-length structure only."""
    try:
        nodes = list((cdp.send("Accessibility.getFullAXTree") or {}).get("nodes") or [])[:1500]
    except Exception:
        return []
    candidates = []
    for node in nodes:
        role = str(dict(node.get("role") or {}).get("value") or "").lower()
        name = str(dict(node.get("name") or {}).get("value") or "")
        if role not in {"article", "listitem", "group", "textbox", "statictext", "paragraph"}:
            continue
        if not name and role not in {"article", "listitem", "group"}:
            continue
        score = {"article": 140, "listitem": 100, "group": 70, "paragraph": 50,
                 "statictext": 30, "textbox": -100}.get(role, 0)
        if score > 0:
            candidates.append((score, _safe_ax_node(node)))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in candidates[:MAX_CANDIDATES]]


def parse_a11y_probe(raw):
    """Whitelist a bounded, text-free artifact suitable for durable diagnostics."""
    raw = dict(raw or {})
    live = dict(raw.get("live") or {})
    status = str(live.get("status") or "RUNTIME_FAILURE")
    structures = []
    for node in list(raw.get("candidate_structures") or [])[:MAX_CANDIDATES]:
        safe = _safe_structure(node)
        if safe is not None:
            structures.append(safe)
    return {
        "probe_status": status if status in _ALLOWED_STATUSES else "RUNTIME_FAILURE",
        "request_sent": live.get("request_sent") is True,
        "main_visible": live.get("main_visible") is True,
        "visible_main_descendant_count": _bounded_number(live.get("main_descendant_count")),
        "iframe_count": _bounded_number(live.get("iframe_count"), 100),
        "iframe_origins": [_safe_origin(value) for value in list(live.get("iframe_origins") or [])[:20]],
        "shadow_root_count": _bounded_number(live.get("shadow_root_count")),
        "accessibility_candidate_count": _bounded_number(live.get("accessibility_candidate_count")),
        "likely_assistant_candidate_count": len(structures),
        "candidate_structures": structures,
    }


def write_a11y_artifact(raw, path=ARTIFACT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(parse_a11y_probe(raw), ensure_ascii=False, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent),
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return path


def _frame_origins(page):
    return [_safe_origin(frame.url) for frame in page.frames]


def probe_once():
    """Submit exactly one diagnostic message and always close its browser context."""
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
            send.click(timeout=3000)  # The sole submission for this diagnostic run.
            handle = None
            try:
                handle = field.element_handle(timeout=2000)
                page.wait_for_function("element => element.tagName === 'TEXTAREA' ? element.value.length === 0 : element.textContent.trim().length === 0",
                                       arg=handle, timeout=5000)
                result["live"]["request_sent"] = True
            except Exception:
                result["live"]["status"] = "SUBMIT_NOT_CONFIRMED"
                return result
            finally:
                if handle is not None:
                    handle.dispose()
            cdp = context.new_cdp_session(page)
            deadline = time.monotonic() + 45
            previous = None
            stable = 0
            while time.monotonic() < deadline:
                dom = page.evaluate(_DOM_STRUCTURE_JS)
                ax = _a11y_candidates(cdp)
                signature = (len(dom.get("dom_candidates", [])),
                             tuple(node.get("text_length", 0) for node in dom.get("dom_candidates", [])),
                             len(ax), tuple(node.get("text_length", 0) for node in ax))
                stable = stable + 1 if signature == previous and (signature[0] or signature[2]) else 0
                previous = signature
                if stable >= 2:
                    break
                page.wait_for_timeout(1000)
            dom = page.evaluate(_DOM_STRUCTURE_JS)
            ax = _a11y_candidates(cdp)
            combined = list(dom.get("dom_candidates", [])) + ax
            result["candidate_structures"] = combined[:MAX_CANDIDATES]
            result["live"].update({
                "main_visible": dom.get("main_visible") is True,
                "main_descendant_count": dom.get("main_descendant_count", 0),
                "iframe_count": len(page.frames), "iframe_origins": _frame_origins(page),
                "shadow_root_count": dom.get("shadow_root_count", 0),
                "accessibility_candidate_count": len(ax),
                "status": "READY" if combined else "RESPONSE_NOT_OBSERVED",
            })
            return result
        finally:
            context.close()


def main():
    if sys.argv[1:] != ["--live"]:
        print("usage: python tools/chatgpt_response_a11y_probe.py --live")
        return 20
    result = {"live": {"status": "RUNTIME_FAILURE", "request_sent": False}}
    try:
        result = probe_once()
    except Exception:
        pass
    finally:
        safe = parse_a11y_probe(result)
        write_a11y_artifact(result)
        print("=== CHATGPT_RESPONSE_A11Y_PROBE ===")
        print("artifact_path=%s" % ARTIFACT_RELATIVE_PATH.as_posix())
        print("probe_status=%s" % safe["probe_status"])
        print("request_sent=%s" % str(safe["request_sent"]).lower())
        print("accessibility_candidate_count=%s" % safe["accessibility_candidate_count"])
        print("iframe_count=%s" % safe["iframe_count"])
        print("shadow_root_count=%s" % safe["shadow_root_count"])
        print("likely_assistant_candidate_count=%s" % safe["likely_assistant_candidate_count"])
        print("=== END ===")
    return 0 if safe["probe_status"] == "READY" else 20


if __name__ == "__main__":
    sys.exit(main())
