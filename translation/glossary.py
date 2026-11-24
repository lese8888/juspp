"""加载和校验游戏新闻术语表。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_GLOSSARY_PATH = Path(__file__).with_name("glossary.json")


@dataclass(frozen=True)
class GlossaryEntry:
    term: str
    mode: str
    aliases: tuple[str, ...] = ()
    translations: dict[str, str] | None = None
    forbidden: tuple[str, ...] = ()

    def source_patterns(self) -> tuple[str, ...]:
        return (self.term, *self.aliases)


@dataclass(frozen=True)
class Glossary:
    version: str
    entries: tuple[GlossaryEntry, ...]

    def terms_in(self, text: str) -> tuple[GlossaryEntry, ...]:
        source = str(text or "")
        matches = []
        for entry in self.entries:
            if any(
                re.search(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", source, re.IGNORECASE)
                for value in entry.source_patterns()
            ):
                matches.append(entry)
        return tuple(matches)

    def prompt_lines(self, text: str, language: str) -> list[str]:
        lines = []
        for entry in self.terms_in(text):
            if entry.mode == "keep":
                lines.append(f"- {entry.term} => 原样保留英文")
                continue
            target = (entry.translations or {}).get(language)
            if target:
                lines.append(f"- {entry.term} => {target}")
        return lines

    def validate(self, text: str, source: str, language: str) -> tuple[list[str], list[str]]:
        failures: list[str] = []
        warnings: list[str] = []
        normalized = str(text or "")
        for entry in self.terms_in(source):
            if entry.mode == "keep":
                alternatives = [entry.term, *entry.aliases]
                if not any(value.lower() in normalized.lower() for value in alternatives):
                    warnings.append(f"应有保留专名：{entry.term}")
            elif entry.mode == "translate" and language.startswith("zh"):
                expected = (entry.translations or {}).get(language)
                if expected and expected not in normalized:
                    failures.append(f"术语 {entry.term} 应使用“{expected}”")
            for forbidden in entry.forbidden:
                if forbidden and forbidden in normalized:
                    failures.append(f"术语 {entry.term} 出现禁用译法“{forbidden}”")
        return failures, warnings


def load_glossary(path: Path | None = None) -> Glossary:
    glossary_path = path or DEFAULT_GLOSSARY_PATH
    payload = json.loads(glossary_path.read_text(encoding="utf-8"))
    entries = []
    for raw in payload.get("terms", []):
        term = str(raw.get("term") or "").strip()
        mode = str(raw.get("mode") or "").strip()
        if not term or mode not in {"keep", "translate"}:
            raise ValueError(f"术语表条目无效：{raw}")
        entries.append(
            GlossaryEntry(
                term=term,
                mode=mode,
                aliases=tuple(str(value) for value in raw.get("aliases", [])),
                translations={
                    str(key): str(value)
                    for key, value in raw.get("translations", {}).items()
                },
                forbidden=tuple(str(value) for value in raw.get("forbid", [])),
            )
        )
    return Glossary(
        version=str(payload.get("version") or ""),
        entries=tuple(entries),
    )


def source_text_for_glossary(title: str, summary: str) -> str:
    return f"{title}\n{summary}"


def glossary_prompt_lines(
    glossary: Glossary,
    title: str,
    summary: str,
    language: str,
) -> Iterable[str]:
    return glossary.prompt_lines(source_text_for_glossary(title, summary), language)
