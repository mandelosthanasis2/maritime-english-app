"""Admin: generate maritime English items with the Claude API.

Produces items in the exact existing item JSON schema, based on IMO SMCP and an
optional source_text used as a STRUCTURAL reference (original wording, never a
verbatim copy). Returns a list of item dicts; the caller stores them as drafts.
"""

import json
import logging
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

MAX_ITEMS = 20

SYSTEM_PROMPT = """You are an expert maritime English curriculum designer. You create practice items for Greek seafarers learning English for their job, grounded in IMO Standard Marine Communication Phrases (SMCP) and standard shipboard practice.

You output items in EXACTLY this JSON schema (an array of item objects). Each item:
{
  "type": one of "vocabulary" | "listening" | "fill_gap" | "word_order" | "speaking" | "dialogue" | "translation",
  "level": CEFR band "A1" | "A2" | "B1" | "B2" | "C1",
  "difficulty": same CEFR band as level,
  "skill_type": one of "vocabulary" | "listening" | "fill_gap" | "word_order" | "speaking" | "roleplay",
  "ship_types": array of strings (e.g. ["all"]),
  "english": { ... shape depends on skill_type, see below ... },
  "explanations": { "el": { "translation": "<Greek translation>", "note": "<short Greek teaching note>", "prompt": "<optional Greek prompt, for translation items>" } },
  "pronunciation_focus": array of short strings (IPA hints or focus sounds), may be empty,
  "tags": array of short lowercase strings
}

english shape by skill_type:
- vocabulary / speaking / listening: { "text": "<English>", "phonetic": "<IPA>" }
- fill_gap: { "text": "<full English sentence>", "gap_text": "<same sentence with ___ for the blank>", "answer": "<the missing word>", "options": ["<answer>", "<distractor>", "<distractor>", "<distractor>"] }
- word_order: { "text": "<full correct English sentence>", "scrambled": ["<word/chunk>", ...] }  (the scrambled chunks must reconstruct text exactly; multi-word chunks are allowed)
- roleplay (use "type": "dialogue", "skill_type": "roleplay"): { "scenario": "<short English scenario>", "lines": [{"speaker": "Bridge"|"Engine"|..., "text": "<English line>"}], "user_role": "<which speaker the learner plays>" }
- translation (use "type": "translation", "skill_type": "speaking"): { "text": "<target English>" } and put the Greek source in explanations.el.prompt

Rules:
- Map skill_type to type: roleplay -> type "dialogue"; everything else uses the matching type ("vocabulary","listening","fill_gap","word_order","speaking"); translations use type "translation" with skill_type "speaking".
- Use realistic, correct maritime English and SMCP phrasing where relevant.
- Use the provided source_text ONLY as a structural/topical reference — write original wording, never copy sentences verbatim.
- Do NOT invent an "audio_url" or "id" field; omit them.
- explanations.el text (translation/note/prompt) must be in Greek.
- Output ONLY a valid JSON array of items. No markdown, no code fences, no prose before or after."""


class AdminGenError(Exception):
    """A client-presentable item-generation failure with an HTTP status code."""

    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code


def _build_user_prompt(topic, role, source_text, num_items, difficulty):
    parts = [
        f"Generate exactly {num_items} maritime English practice item(s).",
        f"Topic: {topic}.",
        f"Target learner role: {role}.",
        f"Target CEFR difficulty (use for both level and difficulty): {difficulty}.",
        "Vary the skill_type across the items (a mix of vocabulary, fill_gap, "
        "word_order, speaking, listening, and roleplay where it fits the topic).",
    ]
    if source_text and source_text.strip():
        parts.append(
            "Use the following source text as a STRUCTURAL and topical reference "
            "only — base the items' structure and terminology on it, but write "
            "completely original wording (never copy sentences verbatim):\n\n"
            f"<source_text>\n{source_text.strip()}\n</source_text>"
        )
    parts.append("Return ONLY the JSON array.")
    return "\n\n".join(parts)


def _parse_json_array(text):
    """Robustly extract a JSON array of objects from the model's text output."""
    if not text:
        raise AdminGenError("The generator returned an empty response.", 502)

    cleaned = text.strip()
    # Strip ```json ... ``` fences if present.
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

    # Fall back to slicing from the first '[' to the last ']'.
    if not cleaned.startswith("["):
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1 or end <= start:
            logger.error("Generator output is not a JSON array: %r", text[:300])
            raise AdminGenError("The generator did not return a JSON array.", 502)
        cleaned = cleaned[start : end + 1]

    try:
        data = json.loads(cleaned)
    except ValueError as exc:
        logger.error("Could not parse generator JSON: %s | %r", exc, cleaned[:300])
        raise AdminGenError("The generator returned invalid JSON.", 502) from exc

    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        raise AdminGenError("The generator did not return a list of items.", 502)
    return data


def generate_items(topic, role, source_text, num_items, difficulty):
    """Call Claude and return a list of generated item dicts."""
    topic = (topic or "").strip()
    role = (role or "").strip()
    if not topic:
        raise AdminGenError("topic is required.", 400)
    if not role:
        raise AdminGenError("role is required.", 400)

    difficulty = (difficulty or "B1").strip().upper()
    if difficulty not in ALLOWED_DIFFICULTY:
        raise AdminGenError("difficulty must be one of A1, A2, B1, B2, C1.", 400)

    try:
        num_items = int(num_items)
    except (TypeError, ValueError):
        raise AdminGenError("num_items must be an integer.", 400)
    if num_items < 1 or num_items > MAX_ITEMS:
        raise AdminGenError(f"num_items must be between 1 and {MAX_ITEMS}.", 400)

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
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": _build_user_prompt(
                        topic, role, source_text, num_items, difficulty
                    ),
                }
            ],
            output_config={"effort": "medium"},
        )
    except anthropic.APIError as exc:
        logger.exception("Anthropic API call failed during item generation.")
        raise AdminGenError("The item generator is unavailable right now. Please try again.", 502) from exc

    text = "".join(b.text for b in response.content if b.type == "text")
    items = _parse_json_array(text)
    return items
