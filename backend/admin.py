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
    "email_compose",
}
ALLOWED_KINDS = {"auto", "grammar", "maritime", "email"}
ALLOWED_TRACKS = {"grammar", "maritime", "email"}
ALLOWED_ROLE_CATEGORIES = {"engineer", "deck", "common"}
# New lesson architecture: the CEFR band a whole lesson sits at (A2-C2) and the
# single skill it trains. Distinct from items.difficulty (A1-C1) — see models.py.
ALLOWED_CEFR_LEVELS = {"A2", "B1", "B2", "C1", "C2"}
ALLOWED_SKILL_AREAS = {"vocabulary", "grammar", "listening", "speaking"}
# Item skill_type/type -> lesson skill area, for the heuristic fallback when the
# model omits skill_area. grammar-track lessons are forced to "grammar" instead.
_SKILL_AREA_FROM_ITEM = {
    "vocabulary": "vocabulary",
    "fill_gap": "vocabulary",
    "word_order": "vocabulary",
    "listening": "listening",
    "speaking": "speaking",
    "roleplay": "speaking",
}

MAX_CHUNKS = 8
TARGET_CHUNK_CHARS = 6000
# Per-lesson sizing. IDEAL_* guides the model; MAX_ITEMS_PER_LESSON is a HARD
# backstop enforced in code (after de-duplication) so a lesson can never balloon
# — e.g. when several chunks contribute items to the same lesson title.
IDEAL_ITEMS_MIN = 12
IDEAL_ITEMS_MAX = 18
MAX_ITEMS_PER_LESSON = 20

# Explicit per-request timeout for the long generation/enrichment Claude calls
# (large max_tokens). The Anthropic SDK's default is ~600s; we set this a bit
# higher so a single big chunk has room to finish instead of erroring out and
# making the caller retry. (gunicorn's --timeout is already 600s.)
GENERATION_TIMEOUT_SECONDS = 900

SYSTEM_PROMPT = """You are an expert English curriculum designer who creates practice items for Greek learners. You handle three kinds of content:
  - "maritime": maritime/nautical English grounded in IMO Standard Marine Communication Phrases (SMCP) and shipboard practice (engine room, bridge, deck, cargo, safety).
  - "grammar": general English grammar and vocabulary for everyday/learner use.
  - "email": how seafarers write professional emails in English (e.g. emails to the company/office, reports, requests) — greetings, structure, set phrases, tone.

You receive a passage of source material. You GROUP the practice items you create into one or more coherent LESSONS (e.g. "The Bridge", "Steering & Helm Orders", "Radar Basics", or for grammar "Present Perfect", "Comparatives", or for email "Reporting to the Office", "Requesting Spare Parts"). A passage may yield several lessons; each lesson bundles related items.

DECIDING THE TRACK
- If told the kind is "maritime", "grammar" or "email", use that for every lesson.
- If told "auto", decide per lesson: nautical/shipboard content -> "maritime"; general English grammar/usage -> "grammar". (Do NOT pick "email" in auto mode — email lessons are only produced when the kind is explicitly "email".)

TEACHING FIRST (concept cards)
- Every NEW lesson MUST START with 1-2 "teaching" items — the concept explanation a teacher would give BEFORE the exercises. They must be the FIRST entries of the lesson's "items" array; all exercises come after them.
- A teaching item is reading material, not an exercise: it has NO answer. Its explanations.el.note is the actual mini-lesson the learner reads — a DETAILED Greek explanation of the concept: what it is, when and how it is used, written simply for a Greek learner. Include 2-3 examples in explanations.el.examples (each an English sentence with its Greek translation).
- Track-aware content: for "grammar" lessons the teaching item explains the grammar rule; for "maritime" lessons it explains the terminology/procedure and how it is used on board; for "email" lessons it explains what the email is, when you write it, and its structure (see EMAIL WRITING LESSONS below).
- When you reuse an EXISTING lesson title (merging items into it), do NOT add teaching items unless the passage introduces a clearly different concept.

AVOIDING DUPLICATE LESSONS
- You will be given a list of EXISTING LESSON TITLES. If the content you are creating fits one of them, reuse that EXACT title in "lesson_title_en" (so it is merged, not duplicated). Only invent a new title when none fits.

DECIDING THE ROLE CATEGORY
Each lesson also gets a "role_category" — who on board it is for:
- "engineer": engine room, machinery, engine orders, fuel/lubrication, technical maintenance.
- "deck": bridge, navigation, radar, helm/steering, mooring, cargo handling on deck.
- "common": for everyone — safety, emergencies, general communication, SMCP basics, and ALL grammar lessons (track "grammar" is ALWAYS "common"). Email lessons (track "email") are ALWAYS "common" too.

DECIDING THE LESSON LEVEL (cefr_level)
Each lesson gets a single CEFR band describing the level of the WHOLE lesson: "A2" | "B1" | "B2" | "C1" | "C2". Judge it from the difficulty of the language the lesson teaches (vocabulary range, sentence complexity, how specialised the content is): basic everyday/entry maritime -> A2/B1; confident professional use -> B2; demanding, nuanced or highly technical -> C1/C2. This is the lesson's organizing level, separate from each item's own difficulty band.

DECIDING THE SKILL AREA (skill_area)
Each lesson trains ONE primary skill: "vocabulary" | "grammar" | "listening" | "speaking".
- "grammar": the lesson's point is an English grammar rule/structure (ALL track "grammar" lessons are "grammar").
- "vocabulary": the lesson's point is learning words/terms/phrases and their meaning (most maritime terminology lessons).
- "listening": the lesson is built around understanding spoken English (listening items dominate). A LISTENING lesson must contain ONLY "teaching" + "listening" items — NO separate fill_gap (each listening item already contains its own sentence + blanks). Its ONE teaching item gives short USAGE INSTRUCTIONS for the exercise (e.g. "Άκουσε την πρόταση, διάλεξε τις λέξεις που λείπουν· πάτα 🐢 για αργή επανάληψη ή 👁 για τη μετάφραση αν δυσκολευτείς."), NOT a vocabulary/grammar mini-lesson — the learner already knows the words.
- "speaking": the lesson is built around producing speech — pronunciation, saying phrases aloud, roleplay dialogues dominate.
Pick the skill the lesson MOSTLY trains, even though a lesson mixes item types. (Do NOT set skill_area for "email" lessons — omit it.)

DECIDING THE ORDER (order_index)
Lessons in the same section (same cefr_level + skill_area) are studied IN SEQUENCE, and the next is locked until the previous is passed. Give each lesson an integer "order_index" (0, 1, 2, …) reflecting where it belongs in that sequence: the most fundamental / prerequisite / easiest lesson gets the LOWEST number, more advanced ones higher. When you produce several lessons for the same section, number them 0,1,2,… in teaching order. (Omit for email lessons.)

OUTPUT: a JSON array of LESSON objects:
{
  "lesson_title_en": "<English lesson title>",
  "lesson_title_el": "<Greek lesson title>",
  "lesson_description_el": "<one short Greek sentence describing the lesson>",
  "track": "maritime" | "grammar" | "email",
  "role_category": "engineer" | "deck" | "common",
  "cefr_level": "A2" | "B1" | "B2" | "C1" | "C2",
  "skill_area": "vocabulary" | "grammar" | "listening" | "speaking",  // omit for email lessons
  "order_index": <integer, 0 = first/most fundamental in its section>,  // omit for email lessons
  "items": [ <item objects, see schema below> ]
}

Each ITEM object:
{
  "type": "teaching" | "vocabulary" | "listening" | "fill_gap" | "word_order" | "speaking" | "dialogue" | "translation" | "email_compose",
  "level": CEFR band "A1|A2|B1|B2|C1",
  "difficulty": same CEFR band,
  "skill_type": "teaching" | "vocabulary" | "listening" | "fill_gap" | "word_order" | "speaking" | "roleplay" | "email_compose",
  "ship_types": array of strings (use ["all"] for general grammar),
  "english": { ... shape depends on skill_type, see below ... },
  "explanations": { "el": { "translation": "<Greek>", "note": "<Greek note, see rules>", "prompt": "<optional Greek prompt for translation items>", "examples": [<teaching items only, see below>] } },
  "pronunciation_focus": array of short strings (may be empty),
  "tags": array of short lowercase strings
}

english shape by skill_type:
- teaching: { "text": "<short English title of the concept>" }. The lesson body lives in explanations.el: "translation" = short Greek title, "note" = the detailed Greek explanation (the mini-lesson the learner reads), "examples" = [{"en": "<English example>", "el": "<Greek translation>"}, ...] with 2-3 entries.
- vocabulary: { "text": "<English word or short phrase>", "phonetic": "<IPA>", "answer": "<the correct Greek meaning>", "options": ["<correct Greek meaning>", "<plausible wrong meaning>", "<plausible wrong meaning>", "<plausible wrong meaning>"] }. This is a MULTIPLE-CHOICE exercise: the learner SEES the English word and PICKS its Greek meaning. Provide 3-4 options total with EXACTLY ONE correct; "answer" MUST appear verbatim among "options". Put the SAME correct Greek meaning in explanations.el.translation. The wrong options must be PLAUSIBLE — same domain and word class (e.g. other nautical terms for maritime, related everyday words for grammar) — never obviously unrelated or silly.
- speaking: { "text": "<English>", "phonetic": "<IPA>" }
- listening: { "text": "<full English sentence, 7+ words>", "phonetic": "<IPA>", "gap_text": "<the SAME sentence with EXACTLY TWO ___ blanks>", "blanks": [ {"answer": "<missing word 1>", "options": ["<answer 1>", "<distractor>", "<distractor>"]}, {"answer": "<missing word 2>", "options": ["<answer 2>", "<distractor>", "<distractor>"]} ] }. The learner HEARS the whole sentence (Azure TTS) and fills the two missing words. Rules: gap_text is text with two blanks; EXACTLY 2 blanks; each blank has EXACTLY 3 options with ONE correct, and "answer" MUST appear verbatim among its options; the two missing words are meaningful content words (not "the"/"a"); distractors are plausible (same word class/domain). Put the Greek translation of the sentence in explanations.el.translation. A listening item is self-contained — do NOT add a separate fill_gap for the same heard sentence.
- fill_gap: { "text": "<full English sentence>", "gap_text": "<same sentence with ___ for the blank>", "answer": "<missing word>", "options": ["<answer>", "<distractor>", "<distractor>", "<distractor>"] }
- word_order: { "text": "<full correct English sentence>", "scrambled": ["<word/chunk>", ...] }  (chunks must reconstruct text exactly; multi-word chunks allowed)
- roleplay (use "type":"dialogue", "skill_type":"roleplay"): { "scenario": "<English>", "lines": [{"speaker": "...", "text": "<English>"}], "user_role": "<which speaker the learner plays>" }
- translation (use "type":"translation", "skill_type":"speaking"): { "text": "<target English>" }, with the Greek source in explanations.el.prompt
- email_compose (EMAIL lessons only; use "type":"email_compose","skill_type":"email_compose"): { "scenario": "<the writing task, described IN GREEK — e.g. 'Η γεννήτρια Νο.2 σταμάτησε λόγω υπερθέρμανσης. Γράψε email στην εταιρεία να αναφέρεις το πρόβλημα.'>", "instructions": "<optional GREEK guidance: the key points the email should include>" }. NO answer, NO options — the learner writes a free-text email that is assessed by AI feedback, not auto-checked.

LESSON STRUCTURE, SIZE & PACING (mandatory for every lesson)
- SIZE: aim for 12-18 items per lesson. NEVER produce more than 20 items in a single lesson — this is a HARD limit. If the source material is large, SPLIT it into MULTIPLE separate lessons (each a coherent sub-topic) instead of one oversized lesson.
- QUALITY OVER QUANTITY: only create an item if it teaches something distinct. If the material genuinely supports just 10 strong items, produce 10 — do NOT pad to hit a number. Twelve sharp, distinct items beat twenty with filler.
- NO REPETITION (critical): every item MUST teach something DIFFERENT. Do NOT reuse the same word/phrase/concept across multiple items, and do NOT produce duplicate or near-duplicate exercises (e.g. the same sentence as both a fill_gap and a word_order, or several vocabulary items for the same word). Each item is unique.
- ORDER the "items" array as a pedagogical build-up (this becomes the learner's sequence). Keep this structure, with a few DISTINCT items per phase. (EMAIL lessons follow the EMAIL WRITING LESSONS section instead of steps 4-5 below.)
  1. 1-2 "teaching" concept items (the explanation, as described above).
  2. RECOGNITION: a few distinct vocabulary and listening items (understand the material).
  3. PRODUCTION: a few distinct fill_gap and word_order items (use the material).
  4. (maritime/grammar only) At least ONE speaking item (skill_type "speaking") — MANDATORY in those tracks, so the learner practises pronunciation.
  5. (maritime/grammar only) At least ONE roleplay item (type "dialogue", skill_type "roleplay") at the END — applying the material in a realistic dialogue — whenever it fits the topic.
- VARIETY: do not place many items of the same type back-to-back; alternate types so the lesson has rhythm.

EMAIL WRITING LESSONS (track "email") — STRUCTURE (allowed item types: teaching, vocabulary, fill_gap; NO speaking, NO roleplay, NO listening, NO word_order, NO email_compose):
- email is WRITTEN, not spoken, so an email lesson must NOT contain any speaking, dialogue/roleplay or listening items.
- 1-2 "teaching" items first: explain in Greek what this email is and WHEN you write it, and lay out its STRUCTURE — greeting -> context -> main point -> request -> closing. The teaching note must include a FULL example email (in English) annotated by section, with a clear Greek explanation. Use explanations.el.examples for 2-3 short bilingual phrase examples.
- then "vocabulary" items for the SET EMAIL PHRASES as multiple choice (English fixed phrase -> pick the Greek meaning), e.g. "I am writing to inform you that...", "Please find attached...", "We kindly request...", "I look forward to your reply.". Same vocabulary shape (text/phonetic/answer/options) as below; distractors are other plausible email phrases' meanings.
- then "fill_gap" items: a HALF/partial email with blanks where the learner picks the correct set phrase from the options (the gap_text shows the email with ___; "answer" is the correct phrase; "options" are 3-4 plausible email phrases).
- close with more fill_gap or vocabulary. Keep the same 12-18 item sizing and NO-REPETITION rules. Do NOT add an email_compose item — free-writing practice lives in a separate "writing scenarios" area, not inside these lessons.

CRITICAL RULES
- GRAMMAR items: explanations.el.note MUST contain a clear Greek explanation of the grammar rule the item practises — what the rule is, and how/when it is used — in simple words for a Greek beginner. This is mandatory for every grammar item.
- MARITIME items: use realistic, correct maritime English and SMCP phrasing; the Greek note should briefly explain the term/usage in Greek.
- EMAIL items: use real, professional email English and set phrases; the Greek note explains the phrase and when to use it. Never emit speaking/listening/roleplay/word_order items in an email lesson.
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


def _item_signature(raw):
    """A normalized fingerprint of an item's core content, for de-duplication.

    Uses the English text (or a roleplay scenario) plus the gap answer, so two
    items that drill the SAME word/sentence collapse to one signature even if
    the model dressed them up slightly differently. Returns "" when there is no
    usable text (those items are never treated as duplicates).
    """
    if not isinstance(raw, dict):
        return ""
    english = raw.get("english") or {}
    parts = [
        english.get("text") or english.get("scenario") or "",
        english.get("answer") or "",
    ]
    sig = " ".join(p for p in parts if p)
    return re.sub(r"\s+", " ", sig.strip().lower())


def _dedup_items(items):
    """Drop later items whose content signature repeats an earlier one."""
    seen = set()
    out = []
    for raw in items:
        sig = _item_signature(raw)
        if sig and sig in seen:
            continue
        if sig:
            seen.add(sig)
        out.append(raw)
    return out


def item_signature(data):
    """Public alias for the content signature of a stored item's `data` dict.

    Lets the dedup endpoint reuse the exact same fingerprint as generation /
    enrichment when cleaning duplicates out of an existing lesson.
    """
    return _item_signature(data)


def _resolve_track(value, kind):
    if kind in ("grammar", "maritime", "email"):
        return kind
    track = (value or "").strip().lower()
    return track if track in ALLOWED_TRACKS else "maritime"


def _resolve_role_category(value, track):
    """Validate the model's role_category; grammar/email lessons are always common."""
    if track in ("grammar", "email"):
        return "common"
    category = (value or "").strip().lower()
    return category if category in ALLOWED_ROLE_CATEGORIES else "common"


def _resolve_cefr_level(value, items):
    """Validate the model's lesson cefr_level (A2-C2); fall back to the items' mean."""
    level = (value or "").strip().upper()
    if level in ALLOWED_CEFR_LEVELS:
        return level
    bands = ("A1", "A2", "B1", "B2", "C1")
    out = ("A2", "B1", "B2", "C1", "C2")  # lesson scale, parallel to bands (A1->A2)
    indices = [
        bands.index((i.get("difficulty") or i.get("level") or "").strip().upper())
        for i in items
        if (i.get("difficulty") or i.get("level") or "").strip().upper() in bands
    ]
    if not indices:
        return "A2"
    return out[round(sum(indices) / len(indices))]


def _resolve_skill_area(value, track, items):
    """Validate the model's skill_area; grammar by track, else majority item vote."""
    if track == "email":
        return None  # email path doesn't use skill_area
    area = (value or "").strip().lower()
    if area in ALLOWED_SKILL_AREAS:
        return area
    if track == "grammar":
        return "grammar"
    votes = {"vocabulary": 0, "listening": 0, "speaking": 0}
    for item in items:
        skill = (item.get("skill_type") or item.get("type") or "").strip().lower()
        mapped = _SKILL_AREA_FROM_ITEM.get(skill)
        if mapped in votes:
            votes[mapped] += 1
    best = max(votes, key=lambda k: votes[k])
    return best if votes[best] else "vocabulary"


def _resolve_order_index(value):
    """Coerce the model's order_index to a non-negative int; None when absent/invalid."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _chunk_user_prompt(chunk, kind, known_titles):
    if kind == "grammar":
        kind_line = 'The content kind is "grammar" (general English): set track="grammar".'
    elif kind == "maritime":
        kind_line = 'The content kind is "maritime": set track="maritime".'
    elif kind == "email":
        kind_line = (
            'The content kind is "email" (professional email writing for seafarers): set '
            'track="email" for every lesson and follow the EMAIL WRITING LESSONS structure '
            "(teaching with a full example email, then set-phrase vocabulary, then fill_gap "
            "on a partial email). Do NOT emit any speaking, roleplay/dialogue, listening or "
            "word_order items."
        )
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
        "lessons. Aim for 12-18 items per lesson and NEVER more than 20 — if the passage "
        "is large, split it into MULTIPLE lessons rather than one oversized lesson. Order "
        "each lesson as a pedagogical build-up: 1-2 Greek 'teaching' concept items first, "
        "then a few distinct recognition items (vocabulary, listening), then a few distinct "
        "production items (fill_gap, word_order), then at least one speaking item, and a "
        "roleplay dialogue at the end where it fits. Vocabulary items are MULTIPLE-CHOICE "
        "(English word -> pick the Greek meaning) with 3-4 plausible options. NO REPETITION: "
        "every item must teach something DIFFERENT — no duplicate or near-duplicate "
        "exercises, and never reuse the same word/phrase/concept across items. Quality over "
        "quantity: if the material only supports ~10 strong distinct items, produce 10 — do "
        "not pad. If the passage already contains exercises with answers, convert those. "
        "Grammar items must include a clear Greek rule explanation in explanations.el.note.\n\n"
        f"<source_passage>\n{chunk}\n</source_passage>\n\n"
        "Return ONLY the JSON array of lesson objects."
    )


def _generate_chunk_lessons(client, anthropic, chunk, kind, known_titles):
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=20000,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": _chunk_user_prompt(chunk, kind, known_titles)}
            ],
            output_config={"effort": "medium"},
            timeout=GENERATION_TIMEOUT_SECONDS,
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
      {title_en, title_el, description_el, track, role_category, cefr_level,
       skill_area, order_index, existing_lesson_id|None, items:[...]}
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
            role_category = _resolve_role_category(lesson.get("role_category"), track)

            if norm in by_norm:
                by_norm[norm]["items"].extend(items)
            elif norm in existing_by_norm:
                match = existing_by_norm[norm]
                entry = {
                    "title_en": match["title"],
                    "title_el": lesson.get("lesson_title_el"),
                    "description_el": lesson.get("lesson_description_el"),
                    "track": match.get("track") or track,
                    # Existing lessons keep their curated category/level/skill/order untouched.
                    "role_category": None,
                    "cefr_level": None,
                    "skill_area": None,
                    "order_index": None,
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
                    "role_category": role_category,
                    "cefr_level": _resolve_cefr_level(lesson.get("cefr_level"), items),
                    "skill_area": _resolve_skill_area(
                        lesson.get("skill_area"), track, items
                    ),
                    "order_index": _resolve_order_index(lesson.get("order_index")),
                    "existing_lesson_id": None,
                    "items": list(items),
                }
                by_norm[norm] = entry
                proposed.append(entry)

    if not proposed:
        raise AdminGenError("Η παραγωγή απέτυχε για όλα τα τμήματα. Δοκίμασε ξανά.", 502)

    # De-duplicate within each lesson and enforce the HARD per-lesson cap. This
    # is the backstop for cross-chunk merges (several chunks adding items to the
    # same lesson title) and any repetitive output the prompt didn't prevent.
    for entry in proposed:
        deduped = _dedup_items(entry["items"])
        if len(deduped) != len(entry["items"]):
            logger.info(
                "Lesson %r: dropped %d duplicate item(s).",
                entry["title_en"],
                len(entry["items"]) - len(deduped),
            )
        entry["items"] = deduped[:MAX_ITEMS_PER_LESSON]

    total_items = sum(len(p["items"]) for p in proposed)
    logger.info(
        "Proposed %d lesson(s), %d item(s) (%d chunk failure(s)).",
        len(proposed),
        total_items,
        failures,
    )
    return proposed


# --- Auto-categorization of existing lessons -----------------------------------
#
# One batched Claude call (chunked for very large pools) classifies many
# lessons at once into engineer / deck / common, from their title plus a small
# sample of their items. Grammar-track lessons never reach this code path —
# the route keeps them out of the candidates, and they stay "common".

CATEGORIZE_BATCH_SIZE = 25
CATEGORIZE_SAMPLE_ITEMS = 6

AUTO_CATEGORIZE_SYSTEM_PROMPT = """You classify English lessons for Greek seafarers by who on board they are for.

Categories:
- "engineer": engine room, machinery, engine orders, fuel/lubrication, technical maintenance.
- "deck": bridge, navigation, radar, helm/steering, mooring, cargo work on deck.
- "common": for everyone — safety, emergencies, general communication, SMCP basics, general vocabulary.

You receive a JSON array of lessons: {"lesson_id": "...", "title": "...", "samples": ["<item text>", ...]}.

OUTPUT: ONLY a valid JSON array, one entry per input lesson, no prose:
[{"lesson_id": "...", "role_category": "engineer" | "deck" | "common"}]"""


def lesson_category_samples(items, max_items=CATEGORIZE_SAMPLE_ITEMS):
    """A few representative item texts for the categorizer."""
    samples = []
    for item in items:
        english = (item.data or {}).get("english") or {}
        text = english.get("text") or english.get("scenario") or ""
        if text:
            samples.append(text[:120])
        if len(samples) >= max_items:
            break
    return samples


def auto_categorize_lessons(lessons_payload):
    """Classify lessons in batches; returns {lesson_id: role_category}.

    `lessons_payload` is a list of {"lesson_id", "title", "samples"} dicts.
    Entries the model skips or answers invalidly are simply absent from the
    result (the caller leaves those lessons unchanged).
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        logger.error("ANTHROPIC_API_KEY is not set; categorization unavailable.")
        raise AdminGenError("Item generation is not configured on the server.", 503)
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency missing
        logger.exception("anthropic SDK failed to import.")
        raise AdminGenError("Item generation is not available on the server.", 503) from exc

    client = anthropic.Anthropic()
    result = {}
    for start in range(0, len(lessons_payload), CATEGORIZE_BATCH_SIZE):
        batch = lessons_payload[start : start + CATEGORIZE_BATCH_SIZE]
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=2000,
                system=AUTO_CATEGORIZE_SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": json.dumps(batch, ensure_ascii=False)}
                ],
                output_config={"effort": "medium"},
            )
        except anthropic.APIError as exc:
            logger.warning("Anthropic call failed for a categorize batch: %s", exc)
            continue  # other batches may still succeed

        text = "".join(b.text for b in response.content if b.type == "text")
        try:
            rows = _parse_json_array(text)
        except AdminGenError:
            logger.warning("Categorize batch returned unparseable output — skipping.")
            continue
        for row in rows:
            category = (row.get("role_category") or "").strip().lower()
            if row.get("lesson_id") and category in ALLOWED_ROLE_CATEGORIES:
                result[row["lesson_id"]] = category

    if not result:
        raise AdminGenError("Η αυτόματη ταξινόμηση απέτυχε. Δοκίμασε ξανά.", 502)
    return result


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


# --- Enrichment of small / incomplete existing lessons ------------------------
#
# Brings an existing lesson up to the pedagogical standard (8-12 items, with a
# speaking item and a closing roleplay) by generating ONLY the items it is
# missing, grounded in the lesson's own content. Existing items are never
# touched — the new items are stored as drafts for review.

ENRICH_TARGET_MIN = 12
ENRICH_TARGET_MAX = 18
# Varied exercise types used to fill a lesson up to the minimum size.
ENRICH_EXERCISE_CYCLE = ("vocabulary", "fill_gap", "word_order", "listening")
# Pedagogical order: recognition -> production -> speaking -> roleplay (last).
ENRICH_RANK = {
    "vocabulary": 1,
    "listening": 1,
    "fill_gap": 2,
    "word_order": 2,
    "speaking": 3,
    "roleplay": 4,
}

ENRICH_SYSTEM_PROMPT = """You extend an EXISTING English lesson for Greek learners by writing the practice items it is missing, grounded in the lesson's own topic and vocabulary. You do NOT rewrite or repeat existing items.

You receive the lesson title, track, and a digest of its current items, plus the EXACT list of new items to produce. Produce exactly those items, in the given order.

Each ITEM object:
{
  "type": "vocabulary" | "listening" | "fill_gap" | "word_order" | "speaking" | "dialogue",
  "skill_type": "vocabulary" | "listening" | "fill_gap" | "word_order" | "speaking" | "roleplay",
  "level": CEFR band "A1|A2|B1|B2|C1" (match the lesson),
  "difficulty": same band,
  "ship_types": array of strings (["all"] for general grammar),
  "english": { ... shape depends on skill_type ... },
  "explanations": { "el": { "translation": "<Greek>", "note": "<Greek note>", "prompt": "<optional Greek prompt for translation>" } },
  "pronunciation_focus": array of short strings (may be empty),
  "tags": array of short lowercase strings
}

english shape by skill_type:
- vocabulary: { "text": "<English word or short phrase>", "phonetic": "<IPA>", "answer": "<correct Greek meaning>", "options": ["<correct Greek meaning>", "<plausible wrong>", "<plausible wrong>", "<plausible wrong>"] }  (MULTIPLE-CHOICE: English word -> pick the Greek meaning; exactly ONE correct, "answer" appears verbatim in "options", same meaning also in explanations.el.translation; wrong options PLAUSIBLE, same domain/word class)
- listening: { "text": "<full English sentence, 7+ words>", "phonetic": "<IPA>", "gap_text": "<the SAME sentence with EXACTLY TWO ___ blanks>", "blanks": [ {"answer": "<missing word 1>", "options": ["<answer 1>", "<distractor>", "<distractor>"]}, {"answer": "<missing word 2>", "options": ["<answer 2>", "<distractor>", "<distractor>"]} ] }  (the learner HEARS the sentence and fills the two missing content words; EXACTLY 2 blanks, EXACTLY 3 options each with ONE correct and "answer" verbatim among them; Greek translation in explanations.el.translation; self-contained — no separate fill_gap for the same sentence)
- fill_gap: { "text": "<full English sentence>", "gap_text": "<same sentence with ___ for the blank>", "answer": "<missing word>", "options": ["<answer>", "<distractor>", "<distractor>", "<distractor>"] }
- word_order: { "text": "<full correct English sentence>", "scrambled": ["<word/chunk>", ...] }  (chunks must reconstruct text exactly)
- speaking: { "text": "<short English phrase to say aloud>", "phonetic": "<IPA>" }
- roleplay (use "type":"dialogue", "skill_type":"roleplay"): { "scenario": "<English>", "lines": [{"speaker": "...", "text": "<English>"}], "user_role": "<which speaker the learner plays>" }

RULES
- Ground every item in the lesson's existing topic/terms — no unrelated content.
- NO REPETITION (critical): each new item MUST teach something DIFFERENT from the lesson's existing items AND from each other. No duplicates or near-duplicates, and never reuse the same word/phrase/concept that an existing item already covers.
- QUALITY OVER QUANTITY: prefer fewer, genuinely distinct items over repetitive filler — never pad just to reach the count.
- GRAMMAR items: explanations.el.note MUST give a clear Greek rule explanation.
- MARITIME items: realistic SMCP / shipboard English; Greek note explains the term/usage.
- All explanations.el text is in Greek. Do NOT invent "audio_url" or "id".
- Output ONLY a valid JSON array of the requested item objects. No prose, no code fences."""


def _item_skill(item):
    return (item.skill_type or item.type or "").lower()


def analyze_lesson_gaps(items, skill_area=None):
    """Decide which items an existing lesson needs to reach the standard.

    Returns {count, have_speaking, have_roleplay, needed} where `needed` is an
    ordered list of skill_types to generate (may be empty when the lesson is
    already complete).

    A LISTENING lesson holds only teaching + listening items (the listening item
    is self-contained since #78), so it is filled with listening items only — no
    speaking/roleplay/fill_gap. Other skill areas keep the existing behaviour.
    """
    count = len(items)
    skills = [_item_skill(i) for i in items]
    have_speaking = "speaking" in skills
    have_roleplay = "roleplay" in skills

    if skill_area == "listening":
        needed = []
        while count + len(needed) < ENRICH_TARGET_MIN and count + len(needed) < ENRICH_TARGET_MAX:
            needed.append("listening")
        return {
            "count": count,
            "have_speaking": have_speaking,
            "have_roleplay": have_roleplay,
            "needed": needed,
        }

    needed = []
    if not have_speaking:
        needed.append("speaking")
    if not have_roleplay:
        needed.append("roleplay")
    # Fill with varied exercises until the lesson reaches the minimum size,
    # without pushing the total past the maximum.
    cycle_i = 0
    while count + len(needed) < ENRICH_TARGET_MIN and count + len(needed) < ENRICH_TARGET_MAX:
        needed.append(ENRICH_EXERCISE_CYCLE[cycle_i % len(ENRICH_EXERCISE_CYCLE)])
        cycle_i += 1

    needed.sort(key=lambda s: ENRICH_RANK.get(s, 2))
    return {
        "count": count,
        "have_speaking": have_speaking,
        "have_roleplay": have_roleplay,
        "needed": needed,
    }


def generate_enrichment_items(title, track, role_category, digest, needed):
    """Ask Claude for exactly the `needed` items for an existing lesson."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        logger.error("ANTHROPIC_API_KEY is not set; generation unavailable.")
        raise AdminGenError("Item generation is not configured on the server.", 503)
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency missing
        logger.exception("anthropic SDK failed to import.")
        raise AdminGenError("Item generation is not available on the server.", 503) from exc

    # A readable, numbered shopping list of what to produce, in order.
    lines = "\n".join(f"{i + 1}. skill_type = {s}" for i, s in enumerate(needed))
    user_prompt = (
        f'LESSON TITLE: "{title}"\n'
        f"TRACK: {track}\nROLE: {role_category}\n\n"
        "EXISTING ITEMS (digest — do NOT repeat these):\n"
        f"{digest}\n\n"
        f"PRODUCE EXACTLY THESE {len(needed)} NEW ITEMS, IN THIS ORDER:\n{lines}\n\n"
        "Return ONLY the JSON array."
    )
    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=12000,
            system=ENRICH_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            output_config={"effort": "medium"},
            timeout=GENERATION_TIMEOUT_SECONDS,
        )
    except anthropic.APIError as exc:
        logger.warning("Anthropic call failed for enrichment: %s", exc)
        raise AdminGenError("The generator is unavailable right now.", 502) from exc

    text = "".join(b.text for b in response.content if b.type == "text")
    raws = _parse_json_array(text)

    items = []
    for raw in raws:
        english = raw.get("english")
        if not isinstance(english, dict) or not english:
            continue  # malformed — skip
        # Never let enrichment introduce a teaching card (that's a separate flow).
        skill = (raw.get("skill_type") or raw.get("type") or "").lower()
        if skill == "teaching":
            continue
        items.append(raw)
    # Drop any duplicate items the model produced among the new ones.
    items = _dedup_items(items)
    if not items:
        raise AdminGenError("Ο generator δεν επέστρεψε έγκυρα items. Δοκίμασε ξανά.", 502)
    return items


# --- Email writing scenarios ---------------------------------------------------
#
# Free-writing practice prompts for the email track. Each scenario becomes a
# standalone email-track lesson holding a single email_compose item (assessed by
# AI feedback). The admin can write them by hand or generate a batch here.

MAX_EMAIL_SCENARIOS = 12

EMAIL_SCENARIO_SYSTEM_PROMPT = """You design WRITING-PRACTICE scenarios for Greek seafarers learning to write professional emails in English. Each scenario asks the learner to WRITE a complete email for a realistic shipboard situation; their email is then assessed by an AI tutor.

You receive a TOPIC and a COUNT. Produce exactly COUNT DISTINCT scenarios.

OUTPUT: ONLY a JSON array of objects, no prose, no code fences:
{
  "title": "<short Greek label, e.g. 'Αναφορά βλάβης γεννήτριας'>",
  "scenario": "<the situation + task, IN GREEK, 1-3 sentences, e.g. 'Η γεννήτρια Νο.2 σταμάτησε λόγω υπερθέρμανσης. Γράψε email στην εταιρεία να αναφέρεις το πρόβλημα.'>",
  "instructions": "<GREEK guidance: the key points the email should include>"
}

RULES
- Realistic maritime situations (engine room, deck, cargo, safety, port, crew, company office).
- ALL text in Greek. Vary the situations; no duplicates.
- Output ONLY the JSON array."""


def generate_email_scenarios(topic, count):
    """Generate writing-practice scenarios via Claude; returns list of dicts.

    Each dict: {"title", "scenario", "instructions"} (all Greek). Reuses the same
    Anthropic setup as the other generators.
    """
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 5
    count = max(1, min(MAX_EMAIL_SCENARIOS, count))

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        logger.error("ANTHROPIC_API_KEY is not set; scenario generation unavailable.")
        raise AdminGenError("Item generation is not configured on the server.", 503)
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency missing
        logger.exception("anthropic SDK failed to import.")
        raise AdminGenError("Item generation is not available on the server.", 503) from exc

    topic = (topic or "").strip() or "γενικά επαγγελματικά email προς την εταιρεία"
    user_prompt = (
        f"TOPIC: {topic}\nCOUNT: {count}\n\nProduce {count} distinct scenarios as a JSON array."
    )
    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=EMAIL_SCENARIO_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            output_config={"effort": "medium"},
        )
    except anthropic.APIError as exc:
        logger.warning("Anthropic call failed for email scenarios: %s", exc)
        raise AdminGenError("The generator is unavailable right now.", 502) from exc

    text = "".join(b.text for b in response.content if b.type == "text")
    raws = _parse_json_array(text)

    scenarios = []
    for raw in raws[:count]:
        scenario = (raw.get("scenario") or "").strip()
        if not scenario:
            continue
        scenarios.append(
            {
                "title": (raw.get("title") or "").strip() or "Σενάριο γραψίματος",
                "scenario": scenario,
                "instructions": (raw.get("instructions") or "").strip(),
            }
        )
    if not scenarios:
        raise AdminGenError("Ο generator δεν επέστρεψε έγκυρα σενάρια. Δοκίμασε ξανά.", 502)
    return scenarios
