# Hermes item-JSON format — multi-language readiness (PR D)

Summary of what changed in the item JSON shape so the external Hermes
initialization prompt can be updated. **Only the explanation/instruction layer
is per-language; ENGLISH learning content is identical for every nationality
and stays flat.** The single source of truth for which field is which lives in
`backend/lang.py`.

## The rule

- `explanations` is a **language-keyed map**. Greek lives under `"el"`; a
  future language (e.g. Ukrainian) adds a sibling group under its own key
  (`"uk": {...}`) — no structural change.
- The only Greek that historically lived **outside** `explanations` as flat
  strings — `english.scenario` / `english.instructions` on `email_compose`
  items — is now a per-field language object: `{"el": "<Greek>"}`.
- Everything else under `english` is English learning content and stays a
  flat string/array: `text`, `phonetic`, `gap_text`, `answer`, `options`,
  `blanks[].answer`, `blanks[].options`, `scrambled`, `lines[].speaker`,
  `lines[].text`, `user_role` — and `scenario` on **dialogue** items, which
  is English scene-setting (unlike the email one).
- **Documented exception:** multiple-choice `vocabulary` items keep Greek
  meanings in `english.answer` / `english.options[]` as flat strings. They are
  graded answer data (compared verbatim by the scorer); per-language option
  sets are a content-design task for the first real second language.
- The backend accepts BOTH shapes on every write path and normalizes flat
  strings to `{"el": ...}` on store, so an older Hermes prompt keeps working —
  but new prompts should emit the keyed shape directly.

## One example per item type

### teaching
```json
{
  "type": "teaching", "skill_type": "teaching", "level": "A2", "difficulty": "A2",
  "ship_types": ["all"],
  "english": { "text": "Engine Order Telegraph" },
  "explanations": { "el": {
      "translation": "Τηλέγραφος μηχανής",
      "note": "Ο τηλέγραφος μεταφέρει τις εντολές ταχύτητας από τη γέφυρα στο μηχανοστάσιο…",
      "examples": [ { "en": "Stand by engine.", "el": "Ετοιμότητα μηχανής." } ]
  } },
  "pronunciation_focus": [], "tags": ["telegraph"]
}
```
(`examples` live INSIDE their language group as `{"en", "<lang>"}` pairs — a
`"uk"` group would carry its own examples `[{"en": ..., "uk": ...}]`.)

### vocabulary (multiple choice)
```json
{
  "type": "vocabulary", "skill_type": "vocabulary", "level": "A2", "difficulty": "A2",
  "ship_types": ["all"],
  "english": {
    "text": "astern", "phonetic": "əˈstɜːn",
    "answer": "προς τα πίσω",
    "options": ["προς τα πίσω", "προς τα εμπρός", "δεξιά", "αριστερά"]
  },
  "explanations": { "el": { "translation": "προς τα πίσω", "note": "…" } }
}
```
(`answer`/`options` stay flat — see the documented exception above.)

### fill_gap
```json
{
  "type": "fill_gap", "skill_type": "fill_gap", "level": "B1", "difficulty": "B1",
  "english": {
    "text": "Reduce speed to dead slow ahead.",
    "gap_text": "Reduce ___ to dead slow ahead.",
    "answer": "speed", "options": ["speed", "course", "power", "draft"]
  },
  "explanations": { "el": { "translation": "Μείωσε ταχύτητα…", "note": "…" } }
}
```

### word_order
```json
{
  "type": "word_order", "skill_type": "word_order", "level": "B1", "difficulty": "B1",
  "english": { "text": "Stop engine immediately.", "scrambled": ["immediately", "Stop", "engine"] },
  "explanations": { "el": { "translation": "Σταμάτα τη μηχανή αμέσως.", "note": "…" } }
}
```

### listening (interactive cloze)
```json
{
  "type": "listening", "skill_type": "listening", "level": "B1", "difficulty": "B1",
  "english": {
    "text": "The chief engineer reported low pressure in the main line.",
    "phonetic": "…",
    "gap_text": "The chief engineer reported ___ pressure in the ___ line.",
    "blanks": [
      { "answer": "low",  "options": ["low", "high", "steady"] },
      { "answer": "main", "options": ["main", "fuel", "steam"] }
    ]
  },
  "explanations": { "el": { "translation": "Ο πρώτος μηχανικός ανέφερε…" } }
}
```

### speaking
```json
{
  "type": "speaking", "skill_type": "speaking", "level": "A2", "difficulty": "A2",
  "english": { "text": "Full ahead.", "phonetic": "fʊl əˈhɛd" },
  "explanations": { "el": { "translation": "Πρόσω ολοταχώς.", "note": "…" } }
}
```

### translation (spoken production; Greek prompt)
```json
{
  "type": "translation", "skill_type": "speaking", "level": "A2", "difficulty": "A2",
  "direction": "el_to_en",
  "english": { "text": "Stop engine." },
  "explanations": { "el": {
      "translation": "Σταμάτα τη μηχανή.",
      "prompt": "Πώς λες στα Αγγλικά: «Σταμάτα τη μηχανή.»;",
      "note": "…"
  } }
}
```

### roleplay (dialogue) — `scenario` here is ENGLISH and stays flat
```json
{
  "type": "dialogue", "skill_type": "roleplay", "level": "B1", "difficulty": "B1",
  "english": {
    "scenario": "Bridge places the telegraph to Stand By before departure.",
    "lines": [
      { "speaker": "Bridge", "text": "Engine room, bridge. Stand by engine." },
      { "speaker": "Engine", "text": "Bridge, engine room. Standing by." }
    ],
    "user_role": "Engine"
  },
  "explanations": { "el": { "translation": "Η γέφυρα ζητά ετοιμότητα…", "note": "…" } }
}
```

### email_compose — **CHANGED**: scenario/instructions are language-keyed now
```json
{
  "type": "email_compose", "skill_type": "email_compose", "level": "B1", "difficulty": "B1",
  "english": {
    "scenario":     { "el": "Η γεννήτρια Νο.2 σταμάτησε λόγω υπερθέρμανσης. Γράψε email στην εταιρεία να αναφέρεις το πρόβλημα." },
    "instructions": { "el": "Ανάφερε: τι συνέβη, πότε, τι ενέργειες έγιναν, τι ζητάς." }
  }
}
```

## Lesson-level fields (unchanged in the Hermes output)

The lesson envelope keeps `lesson_title_en` (flat English), `lesson_title_el`
and `lesson_description_el` (Greek): the backend stores the description both
flat (`description`, back-compat) and language-keyed
(`description_i18n = {"el": ...}`). A future language adds keys server-side;
the Hermes prompt fields do not change.
