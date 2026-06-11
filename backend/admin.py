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

You receive a passage of source material and produce practice items derived from it.

DECIDING THE TRACK
- If told the kind is "maritime" or "grammar", use that.
- If told "auto", decide yourself per passage: nautical/shipboard terminology and procedures -> "maritime"; general English grammar/usage -> "grammar".
- Put the chosen value in each item's "track" field ("maritime" or "grammar").

YOU ALSO DECIDE, per item: "difficulty" (CEFR A1|A2|B1|B2|C1), "level" (same as difficulty), "skill_type", "type", and the target "ship_types" (use ["all"] for general grammar).

ITEM JSON SCHEMA (output an array of these objects):
{
  "track": "maritime" | "grammar",
  "type": "vocabulary" | "listening" | "fill_gap" | "word_order" | "speaking" | "dialogue" | "translation",
  "level": CEFR band,
  "difficulty": same CEFR band,
  "skill_type": "vocabulary" | "listening" | "fill_gap" | "word_order" | "speaking" | "roleplay",
  "ship_types": array of strings,
  "english": { ... shape depends on skill_type, see below ... },
  "explanations": { "el": { "translation": "<Greek>", "note": "<Greek note, see rules>", "prompt": "<optional Greek prompt for translation items>" } },
  "pronunciation_focus": array of short strings (may be empty),
  "tags": array of short lowercase strings
}

english shape by skill_type:
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
- Map skill_type to type: roleplay -> type "dialogue"; translation -> type "translation" (skill_type "speaking"); otherwise type matches skill_type.
- Output ONLY a valid JSON array. No markdown, no code fences, no prose."""


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


def _chunk_user_prompt(chunk, kind):
    if kind == "grammar":
        kind_line = 'The content kind is "grammar" (general English). Set track="grammar" on every item.'
    elif kind == "maritime":
        kind_line = 'The content kind is "maritime". Set track="maritime" on every item.'
    else:
        kind_line = (
            'The content kind is "auto": decide per item whether it is grammar or '
            "maritime and set the track field accordingly."
        )
    return (
        f"{kind_line}\n\n"
        f"Create up to {MAX_ITEMS_PER_CHUNK} practice items derived from the passage "
        "below. If it already contains exercises with answers, convert those. "
        "Remember: grammar items must include a clear Greek rule explanation in "
        "explanations.el.note.\n\n"
        f"<source_passage>\n{chunk}\n</source_passage>\n\n"
        "Return ONLY the JSON array."
    )


def _generate_chunk(client, anthropic, chunk, kind):
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _chunk_user_prompt(chunk, kind)}],
            output_config={"effort": "medium"},
        )
    except anthropic.APIError as exc:
        logger.warning("Anthropic call failed for a chunk: %s", exc)
        raise AdminGenError("The item generator is unavailable right now.", 502) from exc

    text = "".join(b.text for b in response.content if b.type == "text")
    items = _parse_json_array(text)

    # Normalise the track field according to the requested kind.
    for item in items:
        if kind in ("grammar", "maritime"):
            item["track"] = kind
        else:
            track = (item.get("track") or "").strip().lower()
            item["track"] = track if track in ALLOWED_TRACKS else "maritime"
    return items


def generate_items(source_text, kind):
    """Chunk the source text and generate items for each chunk; return the list."""
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

    client = anthropic.Anthropic()
    chunks = chunk_text(text)
    logger.info("Generating items from %d chunk(s), kind=%s", len(chunks), kind)

    items, failures = [], 0
    for i, chunk in enumerate(chunks):
        try:
            items.extend(_generate_chunk(client, anthropic, chunk, kind))
        except AdminGenError as exc:
            failures += 1
            logger.warning("Chunk %d/%d failed: %s", i + 1, len(chunks), exc)

    if not items:
        raise AdminGenError("Η παραγωγή απέτυχε για όλα τα τμήματα. Δοκίμασε ξανά.", 502)

    logger.info("Generated %d item(s) (%d chunk failure(s)).", len(items), failures)
    return items
