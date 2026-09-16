"""Bounded correction of a resource target in a recognized voice command."""
from dataclasses import dataclass
from difflib import SequenceMatcher
import re


_RESOURCE_CONTEXT = re.compile(
    r"\b(?:открой|открыть|запусти|запустить|включи|включить|перейди|перейти|найди|найти)\s+"
    r"(?P<target>[\wа-яёіїєґ .-]{2,80})",
    re.IGNORECASE,
)


def _normal(value):
    return re.sub(r"\s+", " ", str(value).casefold()).strip(" .,!?")


@dataclass(frozen=True)
class RegisteredResource:
    name: str
    aliases: tuple = ()


class STTResourceNormalizer:
    """Correct only an explicit resource target against trusted local names."""
    MIN_CONFIDENCE = 0.88
    MIN_MARGIN = 0.07

    def normalize(self, text, resources):
        if not isinstance(text, str) or not text.strip():
            return text
        match = _RESOURCE_CONTEXT.search(text)
        if not match:
            return text
        target = _normal(match.group("target"))
        if not target:
            return text
        scored = []
        for resource in resources or ():
            name = getattr(resource, "name", "")
            aliases = getattr(resource, "aliases", ())
            if not isinstance(name, str) or not name.strip():
                continue
            values = (name,) + (aliases if isinstance(aliases, tuple) else tuple(aliases or ()))
            for alias in values:
                alias = _normal(alias)
                if not alias:
                    continue
                score = SequenceMatcher(None, target, alias).ratio()
                scored.append((score, name))
        if not scored:
            return text
        scored.sort(key=lambda item: (-item[0], item[1].casefold()))
        score, replacement = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if score < self.MIN_CONFIDENCE or score - runner_up < self.MIN_MARGIN:
            return text
        return text[:match.start("target")] + replacement + text[match.end("target"):]
