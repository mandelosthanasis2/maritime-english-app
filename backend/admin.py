"""Admin: generate maritime / grammar English items with the Claude API.

Accepts pasted text or PDF-extracted text (with an optional page range),
auto-chunks large input (max 8 chunks per generation), and asks Claude to
produce items in the exact existing item JSON schema as drafts. The content
"kind" (auto | grammar | maritime) drives the `track` of each item:
  - maritime → track "maritime" (IMO SMCP / shipboard terminology)
  - grammar  → track "grammar" (general English; each item carries a clear
               Greek explanation of the grammar rule in explanations.el.note)
  - auto     → Claude decides per passage and sets the track itself.
"""

import io
import json
import logging
import math
import os
import re

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-8"

ALLOWED_DIFFICULTY = {"A1", "A2", "B1", "B2", "C1"}
ALLOWED_SKILL_TYPES = {
    "teaching",
    "vocabulary",
    "listening",
    "fill_gap",
    "word_order",
    "speaking",
    "roleplay",
}
ALLOWED_KINDS = {"auto", "grammar", "maritime"}
ALLOWED_TRACKS = {"grammar", "maritime"}

MAX_CHUNKS = 8
TARGET_CHUNK_CHARS = 6000
MAX_ITEMS_PER_CHUNK = 6

SYSTEM_PROMPT = """You are an expert English curriculum designer who creates practice items for Greek learners. You handle two kinds of content:
  - "maritime": maritime/nautical English grounded in IMO Standard Marine Communication Phrases (SMCP) and shipboard practice (engine room, bridge, deck, cargo, safety).
  - "grammar": general English grammar and vocabulary for everyday/learner use.

You receive a passage of source material. You GROUP the practice items you create into one or more coherent LESSONS (e.g. "The Bridge", "Steering & Helm Orders", "Radar Basics", or for grammar "Present Perfect", "Comparatives"). A passage may yield several lessons; each lesson bundles related items.

DECIDING THE TRACK
- If told the kind is "maritime" or "grammar", use that for every lesson.
- If told "auto", decide per lesson: nautical/shipboard content -> "maritime"; general English grammar/usage -> "grammar".

TEACHING FIRST (concept cards)
- Every NEW lesson MUST START with 1-2 "teaching" items — the concept explanation a teacher would give BEFORE the exercises. They must be the FIRST entries of the lesson's "items" array; all exercises come after them.
- A teaching item is reading material, not an exercise: it has NO answer. Its explanations.el.note is the actual mini-lesson the learner reads — a DETAILED Greek explanation of the concept: what it is, when and how it is used, written simply for a Greek learner. Include 2-3 examples in explanations.el.examples (each an English sentence with its Greek translation).
- Track-aware content: for "grammar" lessons the teaching item explains the grammar rule; for "maritime" lessons it explains the terminology/procedure and how it is used on board.
- When you reuse an EXISTING lesson title (merging items into it), do NOT add teaching items unless the passage introduces a clearly different concept.

AVOIDING DUPLICATE LESSONS
- You will be given a list of EXISTING LESSON TITLES. If the content you are creating fits one of them, reuse that EXACT title in "lesson_title_en" (so it is merged, not duplicated). Only invent a new title when none fits.

OUTPUT: a JSON array of LESSON objects:
{
  "lesson_title_en": "<English lesson title>",
  "lesson_title_el": "<Greek lesson title>",
  "lesson_description_el": "<one short Greek sentence describing the lesson>",
  "track": "maritime" | "grammar",
  "items": [ <item objects, see schema below> ]
}

Each ITEM object:
{
  "type": "teaching" | "vocabulary" | "listening" | "fill_gap" | "word_order" | "speaking" | "dialogue" | "translation",
  "level": CEFR band "A1|A2|B1|B2|C1",
  "difficulty": same CEFR band,
  "skill_type": "teaching" | "vocabulary" | "listening" | "fill_gap" | "word_order" | "speaking" | "roleplay",
  "ship_types": array of strings (use ["all"] for general grammar),
  "english": { ... shape depends on skill_type, see below ... },
  "explanations": { "el": { "translation": "<Greek>", "note": "<Greek note, see rules>", "prompt": "<optional Greek prompt for translation items>", "examples": [<teaching items only, see below>] } },
  "pronunciation_focus": array of short strings (may be empty),
  "tags": array of short lowercase strings
}

english shape by skill_type:
- teaching: { "text": "<short English title of the concept>" }. The lesson body lives in explanations.el: "translation" = short Greek title, "note" = the detailed Greek explanation (the mini-lesson the learner reads), "examples" = [{"en": "<English example>", "el": "<Greek translation>"}, ...] with 2-3 entries.
- vocabulary / speaking / listening: { "text": "<English>", "phonetic": "<IPA>" }
- fill_gap: { "text": "<full English sentence>", "gap_text": "<same sentence with ___ for the blank>", "answer": "<missing word>", "options": ["<answer>", "<distractor>", "<distractor>", "<distractor>"] }
- word_order: { "text": "<full correct English sentence>", "scrambled": ["<word/chunk>", ...] }  (chunks must reconstruct text exactly; multi-word chunks allowed)
- roleplay (use "type":"dialogue", "skill_type":"roleplay"): { "scenario": "<English>", "lines": [{"speaker": "...", "text": "<English>"}], "user_role": "<which speaker the learner plays>" }
- translation (use "type":"translation", "skill_type":"speaking"): { "text": "<target English>" }, with the Greek source in explanations.el.prompt

CRITICAL RULES
- GRAMMAR items: explanations.el.note MUST contain a clear Greek explanation of the grammar rule the item practises — what the rule is, and how/when it is used — in simple words for a Greek beginner. This is mandatory for every grammar item.
- MARITIME items: use realistic, correct maritime English and SMCP phrasing; the Greek note should briefly explain the term/usage in Greek.
- If the passage is from a WORKBOOK and already contains exercises with answers, CONVERT those existing exercises into this schema (keep their intent and answers) and ADD the Greek explanation — do not invent unrelated new exercises.
- Use the source ONLY as a structural/topical reference — write original wording, never copy long sentences verbatim.
- explanations.el text (translation/note/prompt) must be in Greek.
- Do NOT invent an "audio_url" or "id" field; omit them.
- Map skill_type to type: roleplay -> type "dialogue"; translation -> type "translation" (skill_type "speaking"); otherwise type matches skill_type (teaching -> type "teaching").
- Output ONLY a valid JSON array of lesson objects. No markdown, no code fences, no prose."""


class AdminGenError(Exception):
    """A client-presentable item-generation failure with an HTTP status code."""

    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code


# --- PDF + chunking ----------------------------------------------------------


def parse_page_range(spec, total_pages):
    """Parse a 1-based page spec like '5-48' or '5,9,12-14' into 0-based indices.

    Empty/None -> all pages. Raises AdminGenError(400) on a malformed spec.
    """
    spec = (spec or "").strip()
    if not spec:
        return list(range(total_pages))

    pages = set()
    try:
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                start, end = int(a), int(b)
                if start > end:
                    start, end = end, start
                for p in range(start, end + 1):
                    pages.add(p)
            else:
                pages.add(int(part))
    except ValueError as exc:
        raise AdminGenError("Μη έγκυρο εύρος σελίδων (π.χ. 5-48).", 400) from exc

    idx = sorted(p - 1 for p in pages if 1 <= p <= total_pages)
    return idx or list(range(total_pages))


def extract_text_from_pdf(file_bytes, page_range):
    """Extract text from the given (1-based) page range of a PDF."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency missing
        logger.exception("pypdf failed to import.")
        raise AdminGenError("PDF processing is not available on the server.", 503) from exc

    try:
        reader = PdfReader(io.BytesIO(file_bytes))
    except Exception as exc:
        logger.warning("Could not read PDF: %s", exc)
        raise AdminGenError("Δεν ήταν δυνατή η ανάγνωση του PDF.", 400) from exc

    indices = parse_page_range(page_range, len(reader.pages))
    parts = []
    for i in indices:
        try:
            parts.append(reader.pages[i].extract_text() or "")
        except Exception:  # pragma: no cover - per-page extraction hiccup
            continue

    text = "\n\n".join(p for p in parts if p.strip())
    if not text.strip():
        raise AdminGenError(
            "Δεν βρέθηκε κείμενο στις επιλεγμένες σελίδες (μήπως είναι σκαναρισμένο PDF;).",
            400,
        )
    return text


def chunk_text(text, max_chunks=MAX_CHUNKS, target_chars=TARGET_CHUNK_CHARS):
    """Split text into at most `max_chunks` chunks at paragraph boundaries."""
    text = (text or "").strip()
    if not text:
        return []

    # Size chunks so the whole text fits within max_chunks.
    size = max(target_chars, math.ceil(len(text) / max_chunks))
    paragraphs = re.split(r"\n\s*\n", text)

    chunks, current = [], ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if current and len(current) + len(para) + 2 > size:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)

    # Safety: never exceed max_chunks (merge the overflow into the last chunk).
    if len(chunks) > max_chunks:
        head = chunks[: max_chunks - 1]
        tail = "\n\n".join(chunks[max_chunks - 1 :])
        chunks = head + [tail]
    return chunks


# --- Generation --------------------------------------------------------------


def _parse_json_array(text):
    if not text:
        raise AdminGenError("The generator returned an empty response.", 502)
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
    if not cleaned.startswith("["):
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise AdminGenError("The generator did not return a JSON array.", 502)
        cleaned = cleaned[start : end + 1]
    try:
        data = json.loads(cleaned)
    except ValueError as exc:
        raise AdminGenError("The generator returned invalid JSON.", 502) from exc
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        raise AdminGenError("The generator did not return a list of items.", 502)
    return data


def _norm_title(title):
    """Normalise a lesson title for semantic-ish dedup (lower, strip punctuation)."""
    return (
        (title or "")
        .strip()
        .lower()
        .replace("—", " ")
        .replace("-", " ")
        .replace("&", " and ")
    )
    # (whitespace collapsed below)


def _norm(title):
    return re.sub(r"\s+", " ", _norm_title(title)).strip()


def _resolve_track(value, kind):
    if kind in ("grammar", "maritime"):
        return kind
    track = (value or "").strip().lower()
    return track if track in ALLOWED_TRACKS else "maritime"


def _chunk_user_prompt(chunk, kind, known_titles):
    if kind == "grammar":
        kind_line = 'The content kind is "grammar" (general English): set track="grammar".'
    elif kind == "maritime":
        kind_line = 'The content kind is "maritime": set track="maritime".'
    else:
        kind_line = (
            'The content kind is "auto": decide per lesson whether it is grammar or '
            "maritime and set the track accordingly."
        )
    if known_titles:
        titles_block = (
            "EXISTING LESSON TITLES (reuse one EXACTLY in lesson_title_en if your "
            "content fits it, to avoid duplicates):\n- "
            + "\n- ".join(known_titles)
            + "\n\n"
        )
    else:
        titles_block = ""
    return (
        f"{kind_line}\n\n"
        f"{titles_block}"
        "Group the practice items you create from the passage below into one or more "
        "lessons. Every NEW lesson must START with 1-2 Greek 'teaching' concept items "
        "(detailed Greek explanation + 2-3 examples) BEFORE the exercises. If the "
        "passage already contains exercises with answers, convert those. Grammar items "
        "must include a clear Greek rule explanation in explanations.el.note.\n\n"
        f"<source_passage>\n{chunk}\n</source_passage>\n\n"
        "Return ONLY the JSON array of lesson objects."
    )


def _generate_chunk_lessons(client, anthropic, chunk, kind, known_titles):
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": _chunk_user_prompt(chunk, kind, known_titles)}
            ],
            output_config={"effort": "medium"},
        )
    except anthropic.APIError as exc:
        logger.warning("Anthropic call failed for a chunk: %s", exc)
        raise AdminGenError("The generator is unavailable right now.", 502) from exc

    text = "".join(b.text for b in response.content if b.type == "text")
    return _parse_json_array(text)


def generate_lessons(source_text, kind, existing_lessons=None):
    """Chunk the text and produce de-duplicated suggested lessons (each with items).

    `existing_lessons` is a list of {"lesson_id", "title", "track"} already in the
    DB; a chunk that matches one of their titles attaches its items there instead
    of creating a new draft lesson.

    Returns a list of dicts:
      {title_en, title_el, description_el, track, existing_lesson_id|None, items:[...]}
    """
    kind = (kind or "auto").strip().lower()
    if kind not in ALLOWED_KINDS:
        kind = "auto"

    text = (source_text or "").strip()
    if not text:
        raise AdminGenError("Χρειάζεται κείμενο ή PDF.", 400)

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        logger.error("ANTHROPIC_API_KEY is not set; generation unavailable.")
        raise AdminGenError("Item generation is not configured on the server.", 503)

    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency missing
        logger.exception("anthropic SDK failed to import.")
        raise AdminGenError("Item generation is not available on the server.", 503) from exc

    existing_lessons = existing_lessons or []
    existing_by_norm = {_norm(l["title"]): l for l in existing_lessons if l.get("title")}

    client = anthropic.Anthropic()
    chunks = chunk_text(text)
    logger.info("Generating lessons from %d chunk(s), kind=%s", len(chunks), kind)

    proposed = []  # ordered list of lesson dicts
    by_norm = {}  # norm title -> proposed dict (this run)
    failures = 0

    for i, chunk in enumerate(chunks):
        known_titles = [p["title_en"] for p in proposed] + [
            l["title"] for l in existing_lessons if l.get("title")
        ]
        try:
            lessons = _generate_chunk_lessons(client, anthropic, chunk, kind, known_titles)
        except AdminGenError as exc:
            failures += 1
            logger.warning("Chunk %d/%d failed: %s", i + 1, len(chunks), exc)
            continue

        for lesson in lessons:
            title_en = (lesson.get("lesson_title_en") or "").strip()
            items = lesson.get("items")
            if not title_en or not isinstance(items, list) or not items:
                continue
            norm = _norm(title_en)
            track = _resolve_track(lesson.get("track"), kind)

            if norm in by_norm:
                by_norm[norm]["items"].extend(items)
            elif norm in existing_by_norm:
                match = existing_by_norm[norm]
                entry = {
                    "title_en": match["title"],
                    "title_el": lesson.get("lesson_title_el"),
                    "description_el": lesson.get("lesson_description_el"),
                    "track": match.get("track") or track,
                    "existing_lesson_id": match["lesson_id"],
                    "items": list(items),
                }
                by_norm[norm] = entry
                proposed.append(entry)
            else:
                entry = {
                    "title_en": title_en,
                    "title_el": lesson.get("lesson_title_el"),
                    "description_el": lesson.get("lesson_description_el"),
                    "track": track,
                    "existing_lesson_id": None,
                    "items": list(items),
                }
                by_norm[norm] = entry
                proposed.append(entry)

    if not proposed:
        raise AdminGenError("Η παραγωγή απέτυχε για όλα τα τμήματα. Δοκίμασε ξανά.", 502)

    total_items = sum(len(p["items"]) for p in proposed)
    logger.info(
        "Proposed %d lesson(s), %d item(s) (%d chunk failure(s)).",
        len(proposed),
        total_items,
        failures,
    )
    return proposed


# --- Teaching backfill for existing lessons -----------------------------------
#
# Older lessons were created before the "teaching" type existed. The admin can
# ask for 1-2 teaching concept cards to be generated FROM the lesson's own
# items, reviewed as drafts, and approved like any other generated content.

MAX_TEACHING_ITEMS = 2
DIGEST_MAX_ITEMS = 40
DIGEST_NOTE_CHARS = 200

TEACHING_SYSTEM_PROMPT = """You write Greek "teaching" concept cards for an EXISTING English lesson for Greek learners. A teaching card is the explanation a teacher gives BEFORE the exercises: reading material, no answer.

You receive the lesson's title, its track, and a digest of its existing items. From THAT content (not invented topics), produce 1-2 teaching items that explain the lesson's concept:
- track "grammar": explain the grammar rule the lesson practises — what it is, when and how it is used.
- track "maritime": explain the terminology/procedure and how it is used on board (IMO SMCP / shipboard practice).

OUTPUT: ONLY a valid JSON array of 1-2 item objects, no markdown or prose:
{
  "type": "teaching",
  "skill_type": "teaching",
  "level": CEFR band "A1|A2|B1|B2|C1" (match the lesson's level),
  "difficulty": same band,
  "ship_types": ["all"],
  "english": { "text": "<short English title of the concept>" },
  "explanations": { "el": {
      "translation": "<short Greek title>",
      "note": "<the mini-lesson the learner reads: a DETAILED Greek explanation — τι είναι, πότε και πώς χρησιμοποιείται — written simply for a Greek learner>",
      "examples": [ { "en": "<English example>", "el": "<Greek translation>" } ]  // 2-3 entries, drawn from or consistent with the lesson's content
  } },
  "pronunciation_focus": [],
  "tags": [<short lowercase strings>]
}

RULES
- All explanations.el text MUST be in Greek; english.text is a short English title.
- Use the lesson's actual phrases/terms in the examples where possible.
- Do NOT invent "audio_url" or "id" fields.
- Output ONLY the JSON array."""


def lesson_digest(items, max_items=DIGEST_MAX_ITEMS):
    """A compact text digest of a lesson's items for the teaching generator."""
    lines = []
    for item in items[:max_items]:
        data = item.data or {}
        english = data.get("english") or {}
        el = (data.get("explanations") or {}).get("el") or {}
        text = english.get("text") or english.get("scenario") or ""
        line = f"- [{item.skill_type or item.type} | {item.difficulty}] {text}"
        if el.get("translation"):
            line += f" | μετάφραση: {el['translation']}"
        if el.get("note"):
            line += f" | σημείωση: {el['note'][:DIGEST_NOTE_CHARS]}"
        lines.append(line)
    return "\n".join(lines)


def generate_teaching_for_lesson(title, track, digest):
    """Ask Claude for 1-2 teaching items for an existing lesson; returns raw dicts."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        logger.error("ANTHROPIC_API_KEY is not set; generation unavailable.")
        raise AdminGenError("Item generation is not configured on the server.", 503)
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency missing
        logger.exception("anthropic SDK failed to import.")
        raise AdminGenError("Item generation is not available on the server.", 503) from exc

    user_prompt = (
        f'LESSON TITLE: "{title}"\n'
        f"TRACK: {track}\n\n"
        "EXISTING ITEMS (digest):\n"
        f"{digest}\n\n"
        "Produce the 1-2 teaching items as a JSON array."
    )
    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=TEACHING_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            output_config={"effort": "medium"},
        )
    except anthropic.APIError as exc:
        logger.warning("Anthropic call failed for teaching backfill: %s", exc)
        raise AdminGenError("The generator is unavailable right now.", 502) from exc

    text = "".join(b.text for b in response.content if b.type == "text")
    raws = _parse_json_array(text)

    teaching = []
    for raw in raws[:MAX_TEACHING_ITEMS]:
        # Force the type fields so a drifting model response can't store a
        # non-teaching item through this endpoint.
        raw["type"] = "teaching"
        raw["skill_type"] = "teaching"
        el = (raw.get("explanations") or {}).get("el") or {}
        if raw.get("english", {}).get("text") and el.get("note"):
            teaching.append(raw)
    if not teaching:
        raise AdminGenError("Ο generator δεν επέστρεψε έγκυρα teaching items. Δοκίμασε ξανά.", 502)
    return teaching
