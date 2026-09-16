"""Read-only structural DOM diagnostic for the dedicated ChatGPT profile.

This tool does not submit messages or persist page text, HTML, screenshots,
credentials, cookies, tokens, or browser storage.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = PROJECT_ROOT / "data" / "browser_profiles" / "chatgpt"
ARTIFACT_RELATIVE_PATH = Path("data/diagnostics/chatgpt_response_dom_structure.json")
ARTIFACT_PATH = PROJECT_ROOT / ARTIFACT_RELATIVE_PATH
MAX_CANDIDATES = 20
_SENSITIVE = re.compile(r"token|secret|password|credential|authorization|cookie|api[_-]?key|email|account", re.I)
_SAFE_LABELS = {"CHATGPT", "SEND", "ОТПРАВИТЬ", "NEW CHAT", "НОВЫЙ ЧАТ", "ASSISTANT", "USER", "MAIN"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_./:\-\[\]%(),=+]{1,90}$")
_SAFE_ROLE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_SAFE_DATA_NAME = re.compile(r"^data-[a-z0-9-]{1,50}$")
_SELECTOR_KEYS = ("main", "role_main", "article", "role_article", "markdown", "message", "conversation_turn", "author_role")


def _safe_name(value):
    value = str(value or "")
    return value if _SAFE_NAME.fullmatch(value) and not _SENSITIVE.search(value) else "REDACTED"


def _safe_role(value):
    value = str(value or "")
    return value if _SAFE_ROLE.fullmatch(value) and not _SENSITIVE.search(value) else "NOT_AVAILABLE"


def _safe_classes(values):
    if not isinstance(values, list):
        return []
    return [item for item in (_safe_name(value) for value in values[:8]) if item != "REDACTED"]


def _safe_label(value):
    value = str(value or "").strip()
    return value if value.upper() in _SAFE_LABELS else "REDACTED" if value else "NOT_AVAILABLE"


def _safe_number(value, ceiling=100000):
    return min(value, ceiling) if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _safe_structure(node):
    if not isinstance(node, dict):
        return None
    data_names = node.get("data_attributes")
    if not isinstance(data_names, list):
        data_names = []
    return {
        "tag": _safe_role(node.get("tag")),
        "role": _safe_role(node.get("role")),
        "aria_label": _safe_label(node.get("aria_label")),
        "class_names": _safe_classes(node.get("class_names")),
        "visible": node.get("visible") is True,
        "text_length": _safe_number(node.get("text_length")),
        "parent_tag": _safe_role(node.get("parent_tag")),
        "parent_class_names": _safe_classes(node.get("parent_class_names")),
        "data_attributes": [name for name in data_names[:8]
                            if isinstance(name, str) and _SAFE_DATA_NAME.fullmatch(name)
                            and not _SENSITIVE.search(name)],
        "data_author_role": _safe_role(node.get("data_author_role")),
    }


def parse_diagnostic_structure(raw):
    """Whitelist structural data so DOM text/attributes cannot enter artifact."""
    raw = dict(raw or {})
    page = dict(raw.get("page") or {})
    page_ready = page.get("ready") is True or raw.get("page_status") == "READY"
    main_visible = page.get("main_visible") is True or raw.get("main_visible") is True
    result = {
        "page_status": "READY" if page_ready else "UNAVAILABLE",
        "main_visible": main_visible,
        "main_structure": _safe_structure(raw.get("main_structure")),
        "selector_counts": {},
        "candidate_count": 0,
        "candidate_structures": [],
    }
    counts = dict(raw.get("selector_counts") or {})
    for key in _SELECTOR_KEYS:
        result["selector_counts"][key] = _safe_number(counts.get(key), 10000)
    nodes = raw.get("candidate_structures")
    if not isinstance(nodes, list):
        nodes = []
    for node in nodes[:MAX_CANDIDATES]:
        structure = _safe_structure(node)
        if structure is not None:
            result["candidate_structures"].append(structure)
    result["candidate_count"] = len(result["candidate_structures"])
    return result


def write_diagnostic_artifact(result, path=ARTIFACT_PATH):
    """Atomically persist the whitelisted structure in project diagnostics."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(parse_diagnostic_structure(result), ensure_ascii=False, indent=2) + "\n"
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


_STRUCTURAL_JS = """() => {
  const selectors = {
    main: 'main', role_main: '[role="main"]', article: 'main article',
    role_article: 'main [role="article"]', markdown: 'main .markdown',
    message: 'main [class*="message"], main [data-testid*="message"]',
    conversation_turn: 'main [data-testid*="conversation-turn"]',
    author_role: 'main [data-message-author-role]'
  };
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
  const counts = {}, seen = new Set(), candidates = [];
  for (const [key, selector] of Object.entries(selectors)) {
    const nodes = document.querySelectorAll(selector);
    counts[key] = nodes.length;
    if (key === 'main' || key === 'role_main') continue;
    for (const node of [...nodes].slice(-30)) {
      if (seen.has(node)) continue;
      seen.add(node);
      const classes = [...node.classList];
      const author = node.getAttribute('data-message-author-role') || '';
      let score = visible(node) ? 10 : 0;
      if (author === 'assistant') score += 100;
      if (node.getAttribute('role') === 'article') score += 60;
      if (node.tagName === 'ARTICLE') score += 50;
      if (classes.includes('markdown')) score += 50;
      if (classes.some(value => value.includes('message'))) score += 30;
      if ((node.getAttribute('data-testid') || '').includes('conversation-turn')) score += 40;
      if (author === 'user' || node.closest('form, [contenteditable="true"]')) score -= 100;
      candidates.push({...structure(node), score});
    }
  }
  candidates.sort((a, b) => b.score - a.score);
  const main = document.querySelector('main, [role="main"]');
  return {page: {ready: document.readyState === 'complete' || document.readyState === 'interactive',
                 main_visible: !!main && visible(main)},
          main_structure: main ? structure(main) : null,
          selector_counts: counts, candidate_structures: candidates.slice(0, 20)};
}"""


def inspect_dedicated_profile():
    """Launch only the dedicated profile; no message interaction is possible."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(str(PROFILE_PATH), headless=False)
        try:
            pages = [page for page in context.pages if page.url.startswith("https://chatgpt.com/")]
            if len(pages) > 1:
                return parse_diagnostic_structure({})
            page = pages[0] if pages else context.new_page()
            if not pages:
                page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=20000)
            page.locator("main, [role='main']").first.wait_for(state="visible", timeout=20000)
            page.wait_for_timeout(3000)
            return parse_diagnostic_structure(page.evaluate(_STRUCTURAL_JS))
        finally:
            context.close()


def main():
    if sys.argv[1:] != ["--inspect"]:
        print("usage: python tools/chatgpt_response_dom_diagnostic.py --inspect")
        return 20
    result = parse_diagnostic_structure({})
    try:
        result = inspect_dedicated_profile()
    except Exception:
        result["page_status"] = "UNAVAILABLE"
    finally:
        write_diagnostic_artifact(result)
        print("=== CHATGPT_RESPONSE_DOM_DIAGNOSTIC ===")
        print(f"artifact_path={ARTIFACT_RELATIVE_PATH.as_posix()}")
        print(f"page_status={result['page_status']}")
        print(f"main_visible={str(result['main_visible']).lower()}")
        print(f"candidate_count={result['candidate_count']}")
        for key in _SELECTOR_KEYS:
            print(f"{key}_count={result['selector_counts'][key]}")
        print("=== END ===")
    return 0 if result["page_status"] == "READY" else 20


if __name__ == "__main__":
    sys.exit(main())
