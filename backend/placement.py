"""Placement test: question selection, grading, and scoring.

The placement measures two independent things from the approved item pool:
  - General grammar/English level (CEFR A1-C1), from grammar-track items.
  - Maritime-terminology familiarity (none | basic | proficient), from
    maritime-track items (any non-grammar track — e.g. the legacy "engine"
    track — counts as maritime content).

Only auto-gradable item types are used:
  - vocabulary  -> multiple choice: Greek translation prompt, English options
  - fill_gap    -> pick/type the missing word
  - word_order  -> reorder the scrambled chunks into the full sentence
Speaking/roleplay/listening items need audio or human judgement, so they are
excluded. All grading happens server-side on submit; the question payloads
never include the correct answer.
"""

import random
import re

CEFR_LEVELS = ("A1", "A2", "B1", "B2", "C1")

# Item types we can grade automatically from stored data.
TESTABLE_SKILL_TYPES = {"vocabulary", "fill_gap", "word_order"}

GRAMMAR_QUESTIONS_PER_LEVEL = 1  # one per CEFR band -> up to 5 grammar questions
MARITIME_QUESTION_COUNT = 5
VOCAB_OPTION_COUNT = 4

GRAMMAR_PASS_RATIO = 0.7  # >=70% correct at a band counts as passing it
MARITIME_BASIC_RATIO = 0.4  # <40% none, 40-75% basic, >75% proficient
MARITIME_PROFICIENT_RATIO = 0.75


def section_for_track(track):
    """Map a lesson track onto a placement section."""
    return "grammar" if track == "grammar" else "maritime"


def _english(item):
    data = item.data or {}
    english = data.get("english")
    return english if isinstance(english, dict) else {}


def _translation_el(item):
    data = item.data or {}
    el = (data.get("explanations") or {}).get("el") or {}
    return el.get("translation") or ""


def _question_kind(item):
    kind = item.skill_type or item.type
    return kind if kind in TESTABLE_SKILL_TYPES else None


def _difficulty(item):
    return item.difficulty if item.difficulty in CEFR_LEVELS else "B1"


def is_testable(item):
    """True when the item's type is gradable AND its data has what we need."""
    kind = _question_kind(item)
    english = _english(item)
    if kind == "fill_gap":
        options = english.get("options")
        return bool(
            english.get("gap_text")
            and english.get("answer")
            and isinstance(options, list)
            and len(options) >= 2
        )
    if kind == "word_order":
        scrambled = english.get("scrambled")
        return bool(
            english.get("text") and isinstance(scrambled, list) and len(scrambled) >= 2
        )
    if kind == "vocabulary":
        return bool(english.get("text") and _translation_el(item))
    return False


def _build_question(item, section, vocab_pool, rng):
    """One question payload, without the correct answer."""
    kind = _question_kind(item)
    english = _english(item)
    question = {
        "item_id": item.item_id,
        "section": section,
        "skill_type": kind,
        "difficulty": _difficulty(item),
    }
    if kind == "fill_gap":
        options = english["options"]
        question["question"] = {
            "gap_text": english["gap_text"],
            "options": rng.sample(options, len(options)),
        }
    elif kind == "word_order":
        chips = english["scrambled"]
        shuffled = rng.sample(chips, len(chips))
        # Avoid presenting the sentence already in the correct order.
        target = _normalize(english.get("text", ""))
        attempts = 0
        while len(chips) > 1 and _normalize(" ".join(shuffled)) == target and attempts < 12:
            shuffled = rng.sample(chips, len(chips))
            attempts += 1
        question["question"] = {"scrambled": shuffled}
    else:  # vocabulary: Greek prompt, choose the English term
        correct = english["text"]
        distractors = [t for t in dict.fromkeys(vocab_pool) if t != correct]
        options = [correct] + rng.sample(
            distractors, min(VOCAB_OPTION_COUNT - 1, len(distractors))
        )
        rng.shuffle(options)
        question["question"] = {"prompt_el": _translation_el(item), "options": options}
    return question


def _by_level(items):
    grouped = {}
    for item in items:
        grouped.setdefault(_difficulty(item), []).append(item)
    return grouped


def select_questions(rows, rng=None):
    """Pick a balanced ~10-question test from (item, lesson_track) pairs.

    Grammar: up to GRAMMAR_QUESTIONS_PER_LEVEL random items per CEFR band, so
    every band is probed. Maritime: up to MARITIME_QUESTION_COUNT items spread
    round-robin across bands. Bands with no items are simply skipped, so a thin
    pool returns fewer questions instead of failing. The result is ordered by
    increasing difficulty.
    """
    rng = rng or random.Random()

    grammar, maritime = [], []
    for item, track in rows:
        if not is_testable(item):
            continue
        # Email-writing items aren't part of CEFR/SMCP leveling — keep them out
        # of the placement test.
        if track == "email":
            continue
        (grammar if section_for_track(track) == "grammar" else maritime).append(item)

    chosen = []

    grammar_by_level = _by_level(grammar)
    for level in CEFR_LEVELS:
        pool = grammar_by_level.get(level, [])
        for item in rng.sample(pool, min(GRAMMAR_QUESTIONS_PER_LEVEL, len(pool))):
            chosen.append((item, "grammar"))

    maritime_by_level = _by_level(maritime)
    pools = [
        rng.sample(maritime_by_level[level], len(maritime_by_level[level]))
        for level in CEFR_LEVELS
        if level in maritime_by_level
    ]
    picked = []
    while len(picked) < MARITIME_QUESTION_COUNT and pools:
        for pool in pools:
            if len(picked) >= MARITIME_QUESTION_COUNT:
                break
            picked.append(pool.pop(0))
        pools = [p for p in pools if p]
    chosen.extend((item, "maritime") for item in picked)

    chosen.sort(key=lambda pair: CEFR_LEVELS.index(_difficulty(pair[0])))

    # Distractor pools for vocabulary questions, kept per section so grammar
    # questions don't offer maritime terms as options (and vice versa).
    vocab_texts = {
        "grammar": [_english(i)["text"] for i in grammar if _question_kind(i) == "vocabulary"],
        "maritime": [_english(i)["text"] for i in maritime if _question_kind(i) == "vocabulary"],
    }
    return [
        _build_question(item, section, vocab_texts[section], rng)
        for item, section in chosen
    ]


_PUNCT_RE = re.compile(r"[^\w\s']", re.UNICODE)


def _normalize(text):
    text = _PUNCT_RE.sub(" ", str(text).lower())
    return re.sub(r"\s+", " ", text).strip()


def grade_answer(item, answer):
    """Return (is_correct, correct_answer_text) for one submitted answer.

    Comparison is case/punctuation/whitespace-insensitive. word_order answers
    may arrive as a list of chunks (joined with spaces) or as a sentence.
    """
    kind = _question_kind(item)
    english = _english(item)
    if kind == "fill_gap":
        correct = english.get("answer") or ""
    else:  # vocabulary and word_order both grade against the full English text
        correct = english.get("text") or ""
    if isinstance(answer, list):
        answer = " ".join(str(part) for part in answer)
    if not isinstance(answer, str) or not answer.strip():
        return False, correct
    return _normalize(answer) == _normalize(correct), correct


def score_grammar(results):
    """Place the user at the highest CEFR band they pass.

    `results` is a list of (difficulty, is_correct). Walking A1 -> C1, a band
    with >=70% correct is passed; the first failed band stops the climb. Bands
    with no questions are skipped (they neither pass nor block). Someone who
    answered grammar questions but passed nothing is placed at A1; with no
    grammar answers at all the result is None (placement not measured).

    Returns (cefr_level | None, {level: {"asked": n, "correct": n}}).
    """
    by_level = {}
    for level, ok in results:
        by_level.setdefault(level if level in CEFR_LEVELS else "B1", []).append(ok)

    breakdown = {
        level: {"asked": len(answers), "correct": sum(answers)}
        for level, answers in by_level.items()
    }

    placed = None
    for level in CEFR_LEVELS:
        answers = by_level.get(level)
        if not answers:
            continue
        if sum(answers) / len(answers) >= GRAMMAR_PASS_RATIO:
            placed = level
        else:
            break
    if placed is None and by_level:
        placed = CEFR_LEVELS[0]
    return placed, breakdown


def score_maritime(results):
    """Map the share of correct maritime answers onto a familiarity level.

    `results` is a list of booleans. Returns (level | None, percent | None) —
    None when no maritime questions were answered.
    """
    if not results:
        return None, None
    ratio = sum(results) / len(results)
    if ratio > MARITIME_PROFICIENT_RATIO:
        level = "proficient"
    elif ratio >= MARITIME_BASIC_RATIO:
        level = "basic"
    else:
        level = "none"
    return level, round(ratio * 100, 1)
