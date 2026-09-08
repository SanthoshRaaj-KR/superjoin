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

**Names merge on exact normalised equality, or on an adjudicated match.** Legal
suffixes and punctuation are stripped, because ``Delhivery Limited`` and
``Delhivery Ltd.`` differ in no way that matters. Beyond that, embeddings
*propose* and a model *decides*, exactly as the metric registry does — and for
the same measured reason: no similarity threshold separates the pairs that
should merge from the pairs that must not. There is no auto-link band here
either. Every non-identifier merge is adjudicated.

**Two guards, because a wrong entity merge is the worst failure in this
system.** It silently fuses two different subjects and then reports confident
contradictions between claims that were never about the same thing.

- *People are never merged by name.* Two people can share a first and last name,
  and no amount of context in a prompt makes that safe. A person merges on a DIN
  or not at all — which is what the corpus needs anyway: ``Suvir Suren Sujan``
  and ``Suvir Sujan`` are joined by DIN 01173669, not by looking alike.
- *Only entities of the same kind are even candidates.* A person is never
  compared against an organisation.

The case this exists for is ``Indian economy`` against ``India``: one claim in
the RBI annual report, forty in the IMF report, and until they resolve together
the corpus's one genuine contradiction — the FY26 GDP projection — cannot form,
because the gate blocks on entity before it looks at anything else.

**The prompt was measured, not written once.** The first version listed the
traps and nothing else, and refused all nine test pairs including the one that
matters: it read "the Indian economy" as a concept distinct from the country.
That is a defensible reading of the words and the wrong answer to the question
being asked, which is whether a measurement recorded against one belongs in the
same row as a measurement recorded against the other. Naming that test, and
separating *an entity referred to through one of its aspects* from *a distinct
body associated with it*, is what fixed it:

    MERGE  Indian economy                   / India              aspect
    MERGE  India's economy                  / India              aspect
    keep   Reserve Bank of India            / India              institution
    keep   Government of India              / India              institution
    keep   Delhivery Express Parcel …       / Delhivery Limited  subsidiary
    keep   South Asia                       / India              region
    keep   emerging market economies        / India              group
    keep   the Indian logistics market      / Delhivery Limited  sector
    keep   Spoton Logistics Private Limited / Delhivery Limited  another company

    9/9
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .llm.embed import cosine, embed_texts
from .models import Entity, EntityAlias, EntityIdentifier

log = logging.getLogger(__name__)

# Below this the names are not worth a model call. Above it they are worth
# *asking* about and nothing more — see the metric registry for the measurement
# that removed the auto-link band there, and note that entities are the more
# dangerous of the two: a wrong metric merge produces a nonsense comparison, a
# wrong entity merge produces a plausible one.
CANDIDATE_FLOOR = 0.62
TOP_K = 5

# Kinds whose members may be proposed for merging by name. `person` is
# deliberately absent.
MERGEABLE_TYPES = {"organisation", "place", "country", "institution"}

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


class EntityJudgement(BaseModel):
    """The adjudicator's answer. Structured so it cannot hedge in prose."""

    same_entity: bool = Field(
        description="True only if both names denote the same real-world entity"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(description="One sentence, naming the distinction if any")


SYSTEM = """You decide whether two names refer to the same real-world entity.

The test is not whether the two phrases mean the same thing. It is whether a measurement recorded against one belongs in the same row as a measurement recorded against the other.

SAME entity — different wording, or one of its aspects:
- abbreviations and legal forms: "Delhivery Limited" / "Delhivery Ltd."
- a country or company named through an aspect of itself: "the Indian economy" IS India, "India's economy" IS India, "the Company's business" IS the company. GDP growth of the Indian economy and GDP growth of India are one measurement; the economy is not a separate thing that has its own GDP.
- a descriptive reference to a named subject: "the Company", "the Group", "the nation", when it clearly refers to the other name.

NOT the same entity — a distinct body, and these are the mistakes that matter:
- a country against an institution based in it, or that governs it ("India" is not "the Reserve Bank of India", and not "the Government of India")
- a parent against a subsidiary, a segment, or a joint venture ("Delhivery Limited" is not "Delhivery Express Parcel Transport Limited")
- a company against the industry or market it operates in ("Delhivery" is not "the Indian logistics market")
- a country against a region, a bloc, or a group containing it ("India" is not "South Asia" and not "emerging markets")
- two people, however similar the names
- a place against an organisation named after it

The asymmetry to keep in mind: refusing a true match costs one comparison that does not happen, and a reader can still see both claims. A wrong match fuses two different subjects permanently, and every later comparison between them is reported with full confidence and is wrong. When unsure, answer false."""


def _adjudicate(name: str, candidate: str, entity_type: str) -> EntityJudgement:
    from .llm.client import structured

    return structured(
        response_model=EntityJudgement,
        system=SYSTEM,
        user="\n".join([
            f"Kind: {entity_type}",
            f"Name A: {name}",
            f"Name B: {candidate}",
            "",
            f"Do A and B denote the same {entity_type}?",
        ]),
    )


@dataclass
class Resolution:
    entity_id: int
    canonical_name: str
    method: str  # identifier | alias | exact_name | adjudicated | new
    confidence: float
    created: bool = False
    merged_from: int | None = None
    similarity: float | None = None
    reason: str | None = None


def resolve_entity(
    session: Session,
    name: str,
    *,
    entity_type: str = "organisation",
    identifiers: list[Identifier] | None = None,
    adjudicate: bool = True,
    adjudicator=None,
) -> Resolution:
    """Find or create the entity a name refers to.

    Resolution order is strict, strongest evidence first:

    1. a registration number already on file
    2. an alias already recorded for some entity
    3. a name close enough to one on file that a model is asked, and says yes
    4. nothing — a new entity, with its name and identifiers recorded

    ``adjudicate=False`` disables step 3, which is what an offline run uses. It
    does not fall back to "merge anyway above some threshold" — it falls back to
    creating a new entity, because a similarity score is not evidence of
    identity.

    ``adjudicator`` is injectable so the guards around step 3 — which is the
    part that can do real damage — are testable without a key, and so the
    judgement itself can be swapped without touching the rules that constrain
    it.

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

    # 3. A name that looks like one already on file. Embeddings nominate, the
    #    adjudicator decides, and people are never nominated.
    if adjudicate and entity_type in MERGEABLE_TYPES:
        best, score = _nearest(session, name, entity_type)
        if best is not None:
            verdict = (adjudicator or _adjudicate)(
                name, best.canonical_name, entity_type)
            if verdict.same_entity:
                _learn_alias(session, best, name, key, "adjudicated",
                             verdict.confidence, score)
                _learn_identifiers(session, best, identifiers)
                return Resolution(
                    best.id, best.canonical_name, "adjudicated",
                    verdict.confidence, similarity=score, reason=verdict.reason,
                )
            log.info("kept apart: %r vs %r (%.3f) — %s",
                     name, best.canonical_name, score, verdict.reason)

    # 4. New.
    entity = Entity(canonical_name=name.strip(), entity_type=entity_type)
    session.add(entity)
    session.flush()
    _learn_alias(session, entity, name, key, "exact_name", 1.0)
    _learn_identifiers(session, entity, identifiers)
    return Resolution(entity.id, entity.canonical_name, "new", 1.0, created=True)


def merge_entities(session: Session, keep_id: int, absorb_id: int) -> int:
    """Fold one entity into another, moving everything that pointed at it.

    Needed because resolution is a decision made once, at the moment a surface
    form is first seen, and recorded as an alias. That is the right design —
    it is what makes the second document using a name free — but it means a
    registry that learned a split before it had the evidence to avoid one can
    never unlearn it. ``Indian economy`` and ``India`` were recorded apart by a
    version of this module that could only merge on exact names; every later
    claim then resolved to the split, correctly and unhelpfully.

    Aliases move with the claims rather than being dropped, so the merge is
    also learned: the absorbed name keeps resolving, now to the survivor.
    Returns the number of claims repointed.
    """
    from .models import Claim

    if keep_id == absorb_id:
        return 0
    keep = session.get(Entity, keep_id)
    absorbed = session.get(Entity, absorb_id)
    if keep is None or absorbed is None:
        raise ValueError(f"no such entity: {keep_id if keep is None else absorb_id}")

    moved = session.query(Claim).filter(Claim.entity_id == absorb_id).update(
        {"entity_id": keep_id}, synchronize_session=False
    )
    session.query(EntityAlias).filter(EntityAlias.entity_id == absorb_id).update(
        {"entity_id": keep_id, "method": "merged"}, synchronize_session=False
    )
    session.query(EntityIdentifier).filter(
        EntityIdentifier.entity_id == absorb_id
    ).update({"entity_id": keep_id}, synchronize_session=False)
    session.delete(absorbed)
    session.flush()
    log.info("merged entity %s (%r) into %s (%r): %s claim(s) moved",
             absorb_id, absorbed.canonical_name, keep_id, keep.canonical_name, moved)
    return moved


def _nearest(session: Session, name: str, entity_type: str):
    """The closest entity of the same kind, if it is close enough to ask about.

    Filtering by ``entity_type`` is a hard filter, not a hint. A person is never
    a candidate for an organisation however similar the strings, which removes
    the entire class of failure where a company named after its founder absorbs
    the founder.
    """
    pool = [
        e for e in session.scalars(
            select(Entity).where(Entity.entity_type == entity_type)
        )
        if normalize_name(e.canonical_name) != normalize_name(name)
    ]
    if not pool:
        return None, 0.0

    names = [e.canonical_name for e in pool]
    try:
        vectors = embed_texts(session, names + [name])
    except Exception as exc:
        # No key, or the embeddings endpoint is unreachable. Proposing nothing
        # is the safe degradation: the name becomes its own entity, which is
        # what happens without this step anyway. Failing the claim instead
        # would make a network problem look like an extraction problem.
        log.warning("entity candidates unavailable for %r: %s", name, exc)
        return None, 0.0
    target = vectors.get(name.strip())
    if not target:
        return None, 0.0
    scored = sorted(
        ((cosine(target, vectors[e.canonical_name]), e)
         for e in pool if e.canonical_name in vectors),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not scored or scored[0][0] < CANDIDATE_FLOOR:
        return None, 0.0
    return scored[0][1], scored[0][0]


def _learn_alias(
    session: Session, entity: Entity, alias: str, key: str, method: str,
    confidence: float, similarity: float | None = None,
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
            similarity=similarity,
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
