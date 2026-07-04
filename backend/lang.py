"""Multi-language readiness for the content model — the single source of truth.

The app's ENGLISH learning content is identical for every nationality; only
the explanation/instruction layer is per-language. This module defines, in one
place, which fields belong to which layer, plus the helpers every read/write
path uses. No second language exists yet — everything resolves to Greek ("el")
— but with this in place, adding Tagalog/Ukrainian/etc. is a translation task
(add a key), not a migration.

THE CANONICAL SHAPES
  1. `data.explanations` is a language-keyed map — the primary container:
         "explanations": {"el": {"translation": ..., "note": ...,
                                 "prompt": ..., "examples": [...]}}
     Each language group is self-contained; teaching `examples` live inside
     their group as [{"en": <English>, "<lang>": <gloss>}] pairs (today
     {"en", "el"}). A future language adds a sibling group ("uk": {...}).
  2. Per-field language objects for the Greek text that historically lived
     as flat strings OUTSIDE explanations:
         "english": {"scenario": {"el": "<Greek writing task>"},
                     "instructions": {"el": "<Greek guidance>"}}
     (email_compose items only — see LANG_FIELDS_IN_ENGLISH.)
  3. Lesson metadata: `title` stays a flat ENGLISH string; `title_el` remains
     the dedicated Greek-title column; `description` (Greek-bearing) gains a
     language-keyed sibling column `description_i18n` = {"el": ...} while the
     flat column keeps the Greek text for back-compat.

ENGLISH LEARNING-CONTENT FIELDS — NEVER language-keyed, always flat:
  english.text, english.phonetic, english.gap_text, english.answer,
  english.options[], english.blanks[].answer, english.blanks[].options[],
  english.scrambled[], english.lines[].{speaker,text}, english.user_role,
  english.audio_url, english.scenario (dialogue/roleplay — English there!),
  explanations.<lang>.examples[].en, pronunciation_focus[], tags[].

DOCUMENTED EXCEPTION — vocabulary multiple-choice `english.answer` and
`english.options[]` contain GREEK meanings (the exercise is "see the English
word, pick its meaning"). They stay FLAT in this PR because they are graded
answer data (compared verbatim by the scorer); per-language option sets are a
content-design task for the first real second language, not a wrap.

READ PATH: user-facing serializers call resolve_item_data(data, lang) so the
client keeps receiving exactly today's shapes (plain strings; explanations
under the fixed accessor key "el", which carries the RESOLVED language — the
learner frontend never needs to know about other languages until a real one
ships). Admin endpoints return the RAW keyed objects.

WRITE PATH: normalize_item_data(data) wraps any flat language-bearing string
into {"el": ...}, so both legacy-flat and lang-keyed input are accepted
forever and everything stored is keyed. The startup migration runs the same
normalizer over existing rows once (re-runs are no-ops).
"""

DEFAULT_LANG = "el"

# data.english fields that carry LEARNER-language text, per item kind
# (skill_type, falling back to type). Everything not listed here is either
# English learning content (see the module docstring) or lives under
# `explanations`, which is language-keyed as a whole.
LANG_FIELDS_IN_ENGLISH = {
    "email_compose": ("scenario", "instructions"),
}


def _item_kind(data):
    """The kind used to look up per-field rules (skill_type, then type)."""
    if not isinstance(data, dict):
        return ""
    return data.get("skill_type") or data.get("type") or ""


def resolve_lang(value, lang=DEFAULT_LANG):
    """One value in the requested language, tolerant of both shapes forever.

    dict  -> value[lang], falling back to "el", then any non-None entry
    other -> returned as-is (legacy flat string, None, lists...)
    """
    if isinstance(value, dict):
        picked = value.get(lang)
        if picked is not None:
            return picked
        picked = value.get(DEFAULT_LANG)
        if picked is not None:
            return picked
        for candidate in value.values():
            if candidate is not None:
                return candidate
        return None
    return value


def wrap_el(value):
    """A flat string becomes {"el": value}; keyed objects pass through.

    Empty strings and None stay as they are (nothing to key), which is what
    makes the migration and write-path normalization idempotent.
    """
    if isinstance(value, str) and value.strip():
        return {DEFAULT_LANG: value}
    return value


def normalize_item_data(data):
    """Wrap every language-bearing flat string in `data` into {"el": ...}.

    Returns (data, changed). Never mutates the input; when nothing needs
    wrapping the original object is returned with changed=False — so callers
    (write endpoints, the migration) can skip no-op saves.
    """
    if not isinstance(data, dict):
        return data, False

    fields = LANG_FIELDS_IN_ENGLISH.get(_item_kind(data), ())
    english = data.get("english")
    if not fields or not isinstance(english, dict):
        return data, False

    changed = {}
    for field in fields:
        value = english.get(field)
        wrapped = wrap_el(value)
        if wrapped is not value:
            changed[field] = wrapped
    if not changed:
        return data, False

    out = dict(data)
    out["english"] = {**english, **changed}
    return out, True


def resolve_item_data(data, lang=DEFAULT_LANG):
    """A user-facing copy of `data` with the language layer resolved.

    - explanations: the group for `lang` (el fallback) is emitted under the
      FIXED client key "el" — the learner frontend's accessor. For Greek
      users the output is identical to the stored object.
    - per-field keyed values (see LANG_FIELDS_IN_ENGLISH) become plain
      strings again, exactly as the client always received them.
    Only the touched containers are copied; untouched nested values are
    shared (the result is for serialization, not mutation).
    """
    if not isinstance(data, dict):
        return data

    out = data

    explanations = data.get("explanations")
    if isinstance(explanations, dict) and explanations:
        group = resolve_lang(explanations, lang)
        if group is not explanations.get(DEFAULT_LANG) or len(explanations) > 1:
            out = dict(out)
            out["explanations"] = {DEFAULT_LANG: group}

    fields = LANG_FIELDS_IN_ENGLISH.get(_item_kind(data), ())
    english = data.get("english")
    if fields and isinstance(english, dict):
        resolved = {}
        for field in fields:
            value = english.get(field)
            picked = resolve_lang(value, lang)
            if picked is not value:
                resolved[field] = picked
        if resolved:
            if out is data:
                out = dict(out)
            out["english"] = {**english, **resolved}

    return out
