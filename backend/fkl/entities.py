"""L4c — resolving the many names of one entity.

Documents do not agree on what to call things. Across this corpus the same
company is ``Delhivery Limited``, ``Delhivery Ltd.``, ``Delhivery`` and ``the
Company``; the same director is ``Suvir Suren Sujan`` in the prospectus and
``Suvir Sujan`` in the annual report. Two claims about "the same entity" are
only comparable once those collapse.

**Identifiers come first, and they are not a tie-breaker — they are the
answer.** A CIN or a DIN is a registration number issued by a regulator, and it
survives every rewording, abbreviation and typographical difference that defeats
string comparison. The corpus proves the point twice over:

- Delhivery's CIN is ``U63090DL2011PLC221234`` in the 2022 prospectus and
  ``L63090DL2011PLC221234`` in the FY24 annual report. The prefix flips from
  unlisted to listed at IPO. A string comparison sees a different identifier; a
  human sees the same company; the registration number ``221234`` sees it too.
- ``U63090DL2011PTC409002`` shares the industry code, the state and the
  incorporation year with both of those, and is a *different* company. So the
  invariant cannot be "the CIN minus its first letter" — it has to be built
  around the registration number, which is what actually identifies the
  registrant.

**Names merge only on exact normalised equality.** Legal suffixes and
punctuation are stripped, because ``Delhivery Limited`` and ``Delhivery Ltd.``
differ in no way that matters. Nothing fuzzier happens here: two people can
share a first and last name, and a wrong entity merge silently fuses two
different subjects into one, which produces confident contradictions between
claims that were never about the same thing. When names alone are not enough,
the honest answer is a separate entity, and the identifier will merge them later
if the documents ever supply one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Entity, EntityAlias, EntityIdentifier

# Corporate Identification Number, as issued in India:
#   L        listing status - L listed, U unlisted   <- flips at IPO
#   63090    industry code
#   DL       state
#   2011     year of incorporation
#   PLC      company class - PLC public, PTC private <- can change
#   221234   registration number                     <- the invariant
_CIN = re.compile(r"\b([LU])(\d{5})([A-Z]{2})(\d{4})([A-Z]{3})(\d{6})\b")
_DIN = re.compile(r"\bDIN[:\s#-]*(\d{8})\b", re.I)
_ISIN = re.compile(r"\b(IN[EF][0-9A-Z]{9})\b")

# Legal forms carry no identity: `Delhivery Limited` and `Delhivery Ltd.` are one
# company. Ordered longest-first so `private limited` is removed before `limited`.
_LEGAL_SUFFIXES = [
    "private limited",
    "public limited company",
    "limited liability partnership",
    "incorporated",
    "corporation",
    "company",
    "limited",
    "pvt ltd",
    "pvt. ltd.",
    "pvt",
    "ltd",
    "llp",
    "plc",
    "inc",
    "corp",
    "co",
]

_HONORIFICS = re.compile(
    r"^(mr|mrs|ms|miss|dr|prof|shri|smt|sri|sh)\.?\s+", re.I
)
_PUNCT = re.compile(r"[^\w\s]")
_SPACE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """The comparison key for a name: lowercase, unpunctuated, legal form removed.

    Deliberately conservative. This key is used for *merging*, and a key that is
    too generous fuses two entities permanently.
    """
    text = _HONORIFICS.sub("", (name or "").strip())
    text = _PUNCT.sub(" ", text.lower())
    text = _SPACE.sub(" ", text).strip()
    for suffix in _LEGAL_SUFFIXES:
        if text.endswith(" " + suffix):
            text = text[: -len(suffix) - 1].strip()
            break
    return text


def cin_key(cin: str) -> str | None:
    """The part of a CIN that identifies the registrant, not its listing status.

    Returns ``state + year + registration number``. The listing prefix and the
    company class are dropped because both change over a company's life without
    the company changing; the industry code is dropped because it is a
    classification rather than an identity.
    """
    m = _CIN.fullmatch(cin.strip()) or _CIN.search(cin)
    if not m:
        return None
    _listing, _industry, state, year, _cls, registration = m.groups()
    return f"{state}{year}{registration}"


@dataclass(frozen=True)
class Identifier:
    kind: str
    value: str
    normalized: str


def find_identifiers(text: str) -> list[Identifier]:
    """Every registration number in a piece of text, normalised for comparison."""
    found: list[Identifier] = []
    seen: set[tuple[str, str]] = set()

    for m in _CIN.finditer(text or ""):
        value = m.group(0)
        key = ("cin", value)
        if key not in seen:
            seen.add(key)
            found.append(Identifier("cin", value, cin_key(value) or value))
    for m in _DIN.finditer(text or ""):
        value = m.group(1)
        if ("din", value) not in seen:
            seen.add(("din", value))
            found.append(Identifier("din", value, value))
    for m in _ISIN.finditer(text or ""):
        value = m.group(1)
        if ("isin", value) not in seen:
            seen.add(("isin", value))
            found.append(Identifier("isin", value, value))
    return found


@dataclass
class Resolution:
    entity_id: int
    canonical_name: str
    method: str  # identifier | alias | exact_name | new
    confidence: float
    created: bool = False
    merged_from: int | None = None


def resolve_entity(
    session: Session,
    name: str,
    *,
    entity_type: str = "organisation",
    identifiers: list[Identifier] | None = None,
) -> Resolution:
    """Find or create the entity a name refers to.

    Resolution order is strict, strongest evidence first:

    1. a registration number already on file
    2. an alias already recorded for some entity
    3. nothing — a new entity, with its name and identifiers recorded

    An identifier match wins even when the names look nothing alike, which is
    the whole point. It also *teaches* the registry: the new spelling is stored
    as an alias, so the next document using it resolves at step 2 without
    needing the identifier again.
    """
    identifiers = identifiers or []
    key = normalize_name(name)
    if not key:
        raise ValueError("cannot resolve an empty entity name")

    # 1. Identifiers.
    for ident in identifiers:
        row = session.scalars(
            select(EntityIdentifier).where(
                EntityIdentifier.kind == ident.kind,
                EntityIdentifier.normalized == ident.normalized,
            )
        ).first()
        if row is not None:
            entity = session.get(Entity, row.entity_id)
            _learn_alias(session, entity, name, key, "identifier", 1.0)
            _learn_identifiers(session, entity, identifiers)
            return Resolution(entity.id, entity.canonical_name, "identifier", 1.0)

    # 2. A name already seen.
    alias = session.scalars(
        select(EntityAlias).where(EntityAlias.alias_key == key)
    ).first()
    if alias is not None:
        entity = session.get(Entity, alias.entity_id)
        _learn_identifiers(session, entity, identifiers)
        return Resolution(entity.id, entity.canonical_name, alias.method, alias.confidence)

    # 3. New.
    entity = Entity(canonical_name=name.strip(), entity_type=entity_type)
    session.add(entity)
    session.flush()
    _learn_alias(session, entity, name, key, "exact_name", 1.0)
    _learn_identifiers(session, entity, identifiers)
    return Resolution(entity.id, entity.canonical_name, "new", 1.0, created=True)


def _learn_alias(
    session: Session, entity: Entity, alias: str, key: str, method: str, confidence: float
) -> None:
    existing = session.scalars(
        select(EntityAlias).where(EntityAlias.alias_key == key)
    ).first()
    if existing is not None:
        return
    session.add(
        EntityAlias(
            entity_id=entity.id,
            alias=alias.strip(),
            alias_key=key,
            method=method,
            confidence=confidence,
        )
    )
    session.flush()


def _learn_identifiers(
    session: Session, entity: Entity, identifiers: list[Identifier]
) -> None:
    """Record identifiers against an entity, ignoring ones already known.

    An identifier seen on a *different* entity is left alone rather than being
    moved or duplicated. That case means either a genuine collision or an
    earlier bad merge, and quietly reassigning it would destroy the evidence
    needed to tell which.
    """
    for ident in identifiers:
        existing = session.scalars(
            select(EntityIdentifier).where(
                EntityIdentifier.kind == ident.kind,
                EntityIdentifier.value == ident.value,
            )
        ).first()
        if existing is not None:
            continue
        session.add(
            EntityIdentifier(
                entity_id=entity.id,
                kind=ident.kind,
                value=ident.value,
                normalized=ident.normalized,
            )
        )
    session.flush()
