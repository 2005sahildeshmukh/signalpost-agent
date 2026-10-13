"""Normalization helpers shared by identity resolution and extraction."""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

LEGAL_SUFFIXES = {
    "as", "asa", "ans", "da", "enk", "iks", "sa", "sam", "sti", "nuf", "ks", "bbl", "brl", "fli", "esek",
    "ab", "aps", "ltd", "limited", "inc", "gmbh", "bv", "oy", "plc", "llc", "a/s",
}
STOPWORDS = {"og", "and", "the", "i", "for", "av", "pa", "på", "til", "de", "&", "co", "v", "ved", "stiftelsen", "stiftelse"}
GENERIC_TOKENS = {
    "holding", "holdings", "invest", "investment", "investering", "eiendom", "eiendommer", "eiendomsselskap",
    "consult", "consulting", "service", "services", "norge", "norway", "norsk", "nordic", "group", "gruppen",
    "selskap", "selskapet", "drift", "utvikling", "management", "partner", "partners", "capital", "finans",
}
NORDIC = str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE", "ö": "o", "ä": "a", "ü": "u", "é": "e"})


def fold(text: Any) -> str:
    value = str(text or "").translate(NORDIC)
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()


def name_tokens(name: Any, *, keep_generic: bool = True) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", fold(str(name or "").replace("&", " og ")))
    out = [t for t in tokens if t not in LEGAL_SUFFIXES and t not in STOPWORDS]
    if not keep_generic:
        out = [t for t in out if t not in GENERIC_TOKENS]
    return out


def distinctive_tokens(name: Any) -> list[str]:
    tokens = name_tokens(name, keep_generic=False)
    return [t for t in tokens if len(t) >= 3 and not t.isdigit()] or name_tokens(name)


def org_checksum_ok(digits: str) -> bool:
    if not re.fullmatch(r"\d{9}", digits or ""):
        return False
    weights = (3, 2, 7, 6, 5, 4, 3, 2)
    total = sum(int(d) * w for d, w in zip(digits[:8], weights))
    remainder = 11 - total % 11
    check = 0 if remainder == 11 else remainder
    return check != 10 and check == int(digits[8])


ORG_PATTERN = re.compile(r"(?<![\d])(\d{3})[\s. \-]?(\d{3})[\s. \-]?(\d{3})(?![\d])")
ORG_CONTEXT = re.compile(r"(org\.?\s*-?\s*n(?:r|o|ummer)|organisasjonsn|foretaksregisteret|mva|vat|business\s*id|company\s*(?:reg|no|number))", re.I)


def org_numbers_in(text: str) -> tuple[set[str], set[str]]:
    """Return (all valid org numbers, those with explicit org-number context nearby)."""
    found: set[str] = set()
    contextual: set[str] = set()
    for match in ORG_PATTERN.finditer(text or ""):
        digits = "".join(match.groups())
        if not org_checksum_ok(digits):
            continue
        found.add(digits)
        window = text[max(0, match.start() - 40): match.end() + 8]
        if ORG_CONTEXT.search(window):
            contextual.add(digits)
    return found, contextual


def norwegian_phone(value: Any) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("0047"):
        digits = digits[4:]
    elif digits.startswith("47") and len(digits) == 10:
        digits = digits[2:]
    return digits if len(digits) == 8 else None


PHONE_PATTERN = re.compile(r"(?:\+47|0047)?[\s ]*(\d[\d\s .\-]{6,12}\d)")


def phones_in(text: str) -> set[str]:
    out = set()
    for match in PHONE_PATTERN.finditer(text or ""):
        phone = norwegian_phone(match.group(0))
        if phone and phone[0] in "2345679":
            out.add(phone)
    return out


def clean_space(text: Any, limit: int | None = None) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:limit] if limit else value


def unique(values: Iterable[Any]) -> list[Any]:
    seen = set()
    out = []
    for value in values:
        key = repr(value)
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def domain_label_variants(name: str) -> list[str]:
    """Plausible domain labels derived from a legal name (candidates only, never evidence)."""
    raw_tokens = re.findall(r"[a-zA-Z0-9æøåÆØÅäöüé]+", str(name or "").replace("&", " og "))
    lowered = [t.lower() for t in raw_tokens]
    core = [t for t in lowered if fold(t) not in LEGAL_SUFFIXES]
    if not core:
        return []
    variants: list[list[str]] = []

    def translit(tokens: list[str], style: int) -> list[str]:
        table = [
            {"ø": "o", "å": "a", "æ": "ae", "ä": "a", "ö": "o", "ü": "u", "é": "e"},
            {"ø": "oe", "å": "aa", "æ": "ae", "ä": "ae", "ö": "oe", "ü": "u", "é": "e"},
        ][style]
        return ["".join(table.get(ch, ch) for ch in token) for token in tokens]

    no_stop = [t for t in core if fold(t) not in STOPWORDS]
    for style in (0, 1):
        variants.append(translit(no_stop, style))
        if no_stop != core:
            variants.append(translit(core, style))
    labels: list[str] = []
    for tokens in variants:
        tokens = [re.sub(r"[^a-z0-9]", "", t) for t in tokens]
        tokens = [t for t in tokens if t]
        if not tokens:
            continue
        labels.append("".join(tokens))
        if len(tokens) > 1:
            labels.append("-".join(tokens))
        if len(tokens) >= 3:
            labels.append("".join(tokens[:2]))
        stripped = [t for t in tokens if t not in GENERIC_TOKENS]
        if stripped and stripped != tokens and len("".join(stripped)) >= 5:
            labels.append("".join(stripped))
    out = []
    for label in labels:
        if 3 <= len(label) <= 63 and label not in out and not label.startswith("-"):
            out.append(label)
    return out
