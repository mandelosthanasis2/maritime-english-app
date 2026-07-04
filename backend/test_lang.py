"""Tests for the multi-language content model (lang.py + migration + read path).

Self-contained: runs against a throwaway SQLite file, no external services and
no test framework needed —

    python test_lang.py          (or: pytest test_lang.py)

Covers: resolve_lang/wrap_el/normalize_item_data semantics, migration
idempotency over real-shaped data for every item type, and the learner/admin
read-path contract (learners get plain strings, admin gets the raw keyed
objects, Greek payloads are byte-identical to before).
"""

import copy
import json
import os
import sys
import tempfile

_DB_FILE = os.path.join(tempfile.gettempdir(), "marlingo_test_lang.db")
if os.path.exists(_DB_FILE):
    os.remove(_DB_FILE)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB_FILE}")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "x" * 32)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lang import normalize_item_data, resolve_item_data, resolve_lang, wrap_el  # noqa: E402

# Real-shaped fixtures, one per item type (email_compose in the PRE-migration
# legacy flat shape on purpose — the migration must wrap it, and only it).
FIXTURES = {
    "teaching": {
        "type": "teaching", "skill_type": "teaching", "level": "A2",
        "english": {"text": "Engine Order Telegraph"},
        "explanations": {"el": {"translation": "Τηλέγραφος", "note": "Εξήγηση…",
                                "examples": [{"en": "Stand by engine.", "el": "Ετοιμότητα."}]}},
    },
    "vocabulary": {
        "type": "vocabulary", "skill_type": "vocabulary", "level": "A2",
        "english": {"text": "astern", "phonetic": "əˈstɜːn", "answer": "προς τα πίσω",
                    "options": ["προς τα πίσω", "εμπρός", "δεξιά", "αριστερά"]},
        "explanations": {"el": {"translation": "προς τα πίσω", "note": "…"}},
    },
    "fill_gap": {
        "type": "fill_gap", "skill_type": "fill_gap", "level": "B1",
        "english": {"text": "Reduce speed now.", "gap_text": "Reduce ___ now.",
                    "answer": "speed", "options": ["speed", "course"]},
        "explanations": {"el": {"translation": "Μείωσε ταχύτητα.", "note": "…"}},
    },
    "word_order": {
        "type": "word_order", "skill_type": "word_order", "level": "B1",
        "english": {"text": "Stop engine now.", "scrambled": ["now", "Stop", "engine"]},
        "explanations": {"el": {"translation": "Σταμάτα τώρα.", "note": "…"}},
    },
    "listening": {
        "type": "listening", "skill_type": "listening", "level": "B1",
        "english": {"text": "The engineer reported low pressure in the main line.",
                    "gap_text": "The engineer reported ___ pressure in the ___ line.",
                    "blanks": [{"answer": "low", "options": ["low", "high", "no"]},
                               {"answer": "main", "options": ["main", "fuel", "steam"]}]},
        "explanations": {"el": {"translation": "Ο μηχανικός ανέφερε…"}},
    },
    "speaking": {
        "type": "speaking", "skill_type": "speaking", "level": "A2",
        "english": {"text": "Full ahead.", "phonetic": "fʊl əˈhɛd"},
        "explanations": {"el": {"translation": "Πρόσω ολοταχώς.", "note": "…"}},
    },
    "translation": {
        "type": "translation", "skill_type": "speaking", "level": "A2", "direction": "el_to_en",
        "english": {"text": "Stop engine."},
        "explanations": {"el": {"translation": "Σταμάτα.", "prompt": "Πώς λες…;", "note": "…"}},
    },
    "dialogue": {
        "type": "dialogue", "skill_type": "roleplay", "level": "B1",
        "english": {"scenario": "Bridge places the telegraph to Stand By.",
                    "lines": [{"speaker": "Bridge", "text": "Stand by engine."}],
                    "user_role": "Engine"},
        "explanations": {"el": {"translation": "Η γέφυρα ζητά ετοιμότητα.", "note": "…"}},
    },
    "email_compose": {
        "type": "email_compose", "skill_type": "email_compose", "level": "B1",
        "english": {"scenario": "Η γεννήτρια σταμάτησε. Γράψε email.",
                    "instructions": "Ανάφερε τι συνέβη."},
    },
}


def test_resolve_lang():
    assert resolve_lang("Κράτα") == "Κράτα"          # legacy flat passes through
    assert resolve_lang(None) is None
    assert resolve_lang({"el": "Κράτα"}) == "Κράτα"
    assert resolve_lang({"el": "a", "uk": "b"}, "uk") == "b"
    assert resolve_lang({"el": "a", "uk": "b"}, "tl") == "a"   # el fallback
    assert resolve_lang({"uk": "b"}, "tl") == "b"              # any fallback
    assert resolve_lang({}, "el") is None


def test_wrap_el():
    assert wrap_el("γεια") == {"el": "γεια"}
    assert wrap_el({"el": "γεια"}) == {"el": "γεια"}   # idempotent
    assert wrap_el("") == ""                            # nothing to key
    assert wrap_el(None) is None


def test_normalize_item_data():
    for kind, fixture in FIXTURES.items():
        snapshot = copy.deepcopy(fixture)
        out, changed = normalize_item_data(fixture)
        # Only email_compose has language-bearing flat strings to wrap.
        assert changed == (kind == "email_compose"), kind
        assert fixture == snapshot, f"{kind}: input mutated"
        if changed:
            assert out["english"]["scenario"] == {"el": snapshot["english"]["scenario"]}
            assert out["english"]["instructions"] == {"el": snapshot["english"]["instructions"]}
            again, changed_again = normalize_item_data(out)
            assert not changed_again and again is out  # second pass: no-op


def test_resolve_item_data():
    wrapped, _ = normalize_item_data(FIXTURES["email_compose"])
    resolved = resolve_item_data(wrapped, "el")
    assert resolved["english"]["scenario"] == "Η γεννήτρια σταμάτησε. Γράψε email."
    # dialogue scenario is ENGLISH content — never wrapped, never resolved away.
    assert resolve_item_data(FIXTURES["dialogue"], "el") is FIXTURES["dialogue"]
    # A future second language: its group is served under the fixed ".el"
    # accessor; other groups never leak to the client.
    multi = {"type": "teaching", "english": {"text": "EOT"},
             "explanations": {"el": {"note": "ελληνικά"}, "uk": {"note": "українська"}}}
    assert resolve_item_data(multi, "uk")["explanations"] == {"el": {"note": "українська"}}
    assert resolve_item_data(multi, "el")["explanations"] == {"el": {"note": "ελληνικά"}}


def test_migration_and_read_path():
    from db import SessionLocal, init_db

    init_db()
    from models import Item, Lesson, UserProgress

    session = SessionLocal()
    if session.query(Lesson).filter_by(lesson_id="tl_mix").count() == 0:
        session.add(Lesson(lesson_id="tl_mix", track="maritime", title="Mixed",
                           status="approved", description="Ελληνική περιγραφή",
                           cefr_level="A2", skill_area="vocabulary", order_index=0))
        session.add(Lesson(lesson_id="tl_email", track="email", title="Σενάριο",
                           status="approved"))
        for i, (kind, fixture) in enumerate(FIXTURES.items()):
            session.add(Item(
                item_id=f"tl_{kind}",
                lesson_id="tl_email" if kind == "email_compose" else "tl_mix",
                type=fixture["type"], level=fixture.get("level"), order_index=i,
                skill_type=fixture["skill_type"], status="approved",
                data=copy.deepcopy(fixture),
            ))
        session.add(UserProgress(user_id="tl_u1", email="u1@x.gr",
                                 total_xp=0, current_streak=0))
        session.commit()
    session.close()

    import migrate

    migrate.run()
    session = SessionLocal()
    rows = {r.item_id: r.data for r in session.query(Item).all() if r.item_id.startswith("tl_")}
    assert rows["tl_email_compose"]["english"]["scenario"] == {"el": "Η γεννήτρια σταμάτησε. Γράψε email."}
    for kind in FIXTURES:
        if kind != "email_compose":
            assert rows[f"tl_{kind}"] == FIXTURES[kind], f"{kind} touched by migration"
    lesson = session.query(Lesson).filter_by(lesson_id="tl_mix").one()
    assert lesson.description_i18n == {"el": "Ελληνική περιγραφή"}
    assert session.query(UserProgress).filter_by(user_id="tl_u1").one().explanation_language == "el"
    session.close()

    snapshot = json.dumps(rows, sort_keys=True, ensure_ascii=False)
    migrate.run()  # idempotency: second run changes nothing
    session = SessionLocal()
    rows2 = {r.item_id: r.data for r in session.query(Item).all() if r.item_id.startswith("tl_")}
    assert json.dumps(rows2, sort_keys=True, ensure_ascii=False) == snapshot
    session.close()

    # Read-path contract.
    from app import app

    client = app.test_client()
    mix = client.get("/api/lessons/tl_mix").get_json()
    assert mix["description"] == "Ελληνική περιγραφή"  # plain string, resolved
    by_id = {i["item_id"]: i["data"] for i in mix["items"]}
    for kind in FIXTURES:
        if kind != "email_compose":
            # Zero visible change for Greek users: payload identical to before.
            assert by_id[f"tl_{kind}"] == FIXTURES[kind], kind

    email = client.get("/api/lessons/tl_email").get_json()["items"][0]["data"]
    assert email["english"]["scenario"] == "Η γεννήτρια σταμάτησε. Γράψε email."
    assert email["english"]["instructions"] == "Ανάφερε τι συνέβη."

    raw = client.get(
        "/api/admin/items?status=all&lesson_id=tl_email",
        headers={"X-Admin-Key": os.environ["ADMIN_API_KEY"]},
    ).get_json()["items"][0]["data"]
    assert raw["english"]["scenario"] == {"el": "Η γεννήτρια σταμάτησε. Γράψε email."}


if __name__ == "__main__":
    test_resolve_lang()
    test_wrap_el()
    test_normalize_item_data()
    test_resolve_item_data()
    test_migration_and_read_path()
    print("test_lang.py: all tests passed")
