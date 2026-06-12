"""Adaptive exercise selection — a simple, transparent BirdBrain-style scorer.

Given the user's placement results (cefr_level, maritime_level) and their
answer history (UserItemStat rows), `choose_next` picks the single best next
item from the approved pool. The flow is:

  1. Decide which track to serve (grammar vs maritime) with a weighted coin
     flip driven by maritime familiarity — always a mix, never 100/0.
  2. Compute the user's "working level" per track: start from placement and
     climb one CEFR band at a time while they keep scoring > 80% there.
  3. Score every candidate item in the chosen track and return the best one.

Each scoring factor is an additive, independently tunable weight:

  - level fit          items at the working level score highest; one band
                       away is fine; far-away bands fade out (never excluded,
                       so a thin pool still always yields something).
  - weakness boost     skill_types where the user's success rate is low get
                       priority — we drill what they get wrong.
  - spaced repetition  items they answered WRONG come back after a day; items
                       answered recently (either way) are pushed away so the
                       session doesn't repeat itself.
  - freshness          never-seen items get a small head start over already-
                       mastered ones.
  - jitter             a touch of randomness so equal candidates rotate.

Everything degrades gracefully: no placement -> sensible defaults, no history
-> level fit + freshness alone, empty chosen track -> the other track, empty
pool -> None (the route turns that into a friendly response, not an error).
"""

import random
from datetime import datetime, timezone

CEFR_LEVELS = ("A1", "A2", "B1", "B2", "C1")

# Where each maritime familiarity level starts on the CEFR-style difficulty
# scale used by items. Grammar starts directly at the placement cefr_level.
MARITIME_BASE_LEVEL = {"none": "A1", "basic": "B1", "proficient": "B2"}
DEFAULT_LEVEL = "A1"  # no placement taken -> start from the bottom

# Probability of serving a MARITIME item, by maritime familiarity. A user who
# knows no terminology mostly builds general English first; a proficient one
# mostly drills maritime — but both always keep a mix (rule b).
MARITIME_SHARE = {"none": 0.3, "basic": 0.5, "proficient": 0.7}
DEFAULT_MARITIME_SHARE = 0.4

# Level-up rule (rule d): climb one band when the user has answered at least
# this many items at the working level with a success rate above the bar.
LEVEL_UP_MIN_ATTEMPTS = 5
LEVEL_UP_RATIO = 0.8

# Spaced repetition (rule e): an item answered wrong becomes "due" again after
# this many days; anything answered in the cooldown window is pushed away.
REVIEW_WRONG_AFTER_DAYS = 1
RECENT_COOLDOWN_HOURS = 12

# Weakness boost (rule c) only kicks in once a skill has enough attempts to
# make its success rate meaningful.
WEAKNESS_MIN_ATTEMPTS = 3

# --- Score weights (additive; tune freely) -----------------------------------
W_LEVEL_EXACT = 3.0  # item difficulty == working level
W_LEVEL_NEAR = 1.5  # one band away
W_REVIEW_DUE = 5.0  # wrong a while ago — strongest pull
W_RECENT_PENALTY = -6.0  # answered within the cooldown window
W_MASTERED_PENALTY = -1.0  # last answer was correct (already knows it)
W_WEAKNESS = 2.0  # scaled by (1 - success rate) of the item's skill_type
W_FRESH = 1.0  # never attempted
W_JITTER = 0.5  # random tie-breaker / variety


def section_for_track(track):
    """Lesson track -> adaptive track (legacy tracks like 'engine' = maritime)."""
    return "grammar" if track == "grammar" else "maritime"


def _level_index(level):
    return CEFR_LEVELS.index(level) if level in CEFR_LEVELS else CEFR_LEVELS.index("B1")


def _age(now, answered_at):
    """Time since an attempt; naive timestamps (e.g. SQLite) are taken as UTC."""
    if answered_at is None:
        return None
    if answered_at.tzinfo is None:
        answered_at = answered_at.replace(tzinfo=timezone.utc)
    return now - answered_at


def base_level(progress, track):
    """The placement-derived starting level for a track (rule a)."""
    if track == "grammar":
        level = getattr(progress, "cefr_level", None)
        return level if level in CEFR_LEVELS else DEFAULT_LEVEL
    return MARITIME_BASE_LEVEL.get(getattr(progress, "maritime_level", None), DEFAULT_LEVEL)


def working_level(start, level_stats):
    """Climb from `start` one band at a time while performance stays >80%.

    `level_stats` maps difficulty -> (correct, total) for one track. The climb
    stops at the first band without enough good attempts, so difficulty rises
    gradually as the user proves themselves (rule d).
    """
    index = _level_index(start)
    while index < len(CEFR_LEVELS) - 1:
        correct, total = level_stats.get(CEFR_LEVELS[index], (0, 0))
        if total >= LEVEL_UP_MIN_ATTEMPTS and correct / total > LEVEL_UP_RATIO:
            index += 1
        else:
            break
    return CEFR_LEVELS[index]


def pick_track(maritime_level, available_tracks, rng):
    """Weighted grammar/maritime choice (rule b), constrained to what exists."""
    if available_tracks == {"grammar"}:
        return "grammar"
    if available_tracks == {"maritime"}:
        return "maritime"
    share = MARITIME_SHARE.get(maritime_level, DEFAULT_MARITIME_SHARE)
    return "maritime" if rng.random() < share else "grammar"


def _aggregate(stats):
    """Fold UserItemStat rows into the lookups scoring needs.

    Returns (by_item, level_stats, skill_stats):
      by_item      item_id -> stat row (spaced repetition / recency)
      level_stats  track -> {difficulty: (correct, total)}   (level-up rule)
      skill_stats  track -> {skill_type: (correct, total)}   (weakness rule)
    """
    by_item = {}
    level_stats = {"grammar": {}, "maritime": {}}
    skill_stats = {"grammar": {}, "maritime": {}}
    for stat in stats:
        by_item[stat.item_id] = stat
        track = section_for_track(stat.track)
        total = (stat.correct_count or 0) + (stat.wrong_count or 0)
        if total == 0:
            continue
        for bucket, key in (
            (level_stats[track], stat.difficulty),
            (skill_stats[track], stat.skill_type),
        ):
            correct, seen = bucket.get(key, (0, 0))
            bucket[key] = (correct + (stat.correct_count or 0), seen + total)
    return by_item, level_stats, skill_stats


def score_item(item, stat, target_level, skill_rates, now, rng):
    """One candidate's score; the factors are documented in the module header."""
    score = 0.0

    # Level fit: full points at the working level, partial one band away.
    distance = abs(_level_index(item.difficulty) - _level_index(target_level))
    if distance == 0:
        score += W_LEVEL_EXACT
    elif distance == 1:
        score += W_LEVEL_NEAR
    # further bands add nothing but stay eligible (graceful thin-pool fallback)

    # Weakness: drill skill_types the user keeps getting wrong.
    rate = skill_rates.get(item.skill_type)
    if rate is not None:
        score += W_WEAKNESS * (1.0 - rate)

    # History: spaced repetition, cooldown, freshness.
    if stat is None:
        score += W_FRESH
    else:
        age = _age(now, stat.last_answered_at)
        if age is not None and age.total_seconds() < RECENT_COOLDOWN_HOURS * 3600:
            score += W_RECENT_PENALTY
        elif stat.last_correct is False and age is not None and age.days >= REVIEW_WRONG_AFTER_DAYS:
            score += W_REVIEW_DUE
        elif stat.last_correct:
            score += W_MASTERED_PENALTY

    return score + rng.uniform(0, W_JITTER)


def choose_next(progress, rows, stats, now=None, rng=None):
    """Pick the next exercise for a user.

    progress  UserProgress (placement levels; may have nulls)
    rows      (Item, lesson_track) pairs — the approved pool
    stats     the user's UserItemStat rows

    Returns (item, track, meta) or None when the pool is empty. `meta` explains
    the choice (chosen track, working level, why this item) so the frontend or
    a debugging admin can see what the engine was thinking.
    """
    now = now or datetime.now(timezone.utc)
    rng = rng or random.Random()

    candidates = {"grammar": [], "maritime": []}
    for item, lesson_track in rows:
        candidates[section_for_track(lesson_track)].append(item)
    available = {track for track, items in candidates.items() if items}
    if not available:
        return None  # nothing approved at all — caller responds gracefully

    by_item, level_stats, skill_stats = _aggregate(stats)

    track = pick_track(getattr(progress, "maritime_level", None), available, rng)
    if track not in available:  # chosen mix has no items — fall back (rule f)
        track = next(iter(available))

    target_level = working_level(base_level(progress, track), level_stats[track])
    skill_rates = {
        skill: correct / total
        for skill, (correct, total) in skill_stats[track].items()
        if total >= WEAKNESS_MIN_ATTEMPTS
    }

    best, best_score = None, None
    for item in candidates[track]:
        score = score_item(item, by_item.get(item.item_id), target_level, skill_rates, now, rng)
        if best_score is None or score > best_score:
            best, best_score = item, score

    stat = by_item.get(best.item_id)
    if stat is None:
        reason = "new"
    elif stat.last_correct is False:
        reason = "review"
    else:
        reason = "practice"
    meta = {
        "track": track,
        "target_level": target_level,
        "reason": reason,
        "score": round(best_score, 3),
    }
    return best, track, meta


# --- Lesson-level recommendation ----------------------------------------------
#
# `choose_next_lesson` applies the same philosophy one level up: instead of the
# next ITEM, it picks the next whole LESSON and explains why in Greek. The
# track choice and working level are shared with the item scorer; the lesson
# score adds completion history (avoid what was just finished, resurface what
# needs review) and how well the lesson's content matches the user's weak
# skills. reason_el is built from plain templates — no API calls.

# A lesson completed within this window is excluded outright (just finished);
# one completed longer than the review window ago earns a small "review" bonus.
LESSON_JUST_DONE_DAYS = 3
LESSON_REVIEW_AFTER_DAYS = 7

W_LESSON_LEVEL_EXACT = 3.0  # lesson difficulty == working level
W_LESSON_LEVEL_NEAR = 1.5  # one band away
W_LESSON_NEW = 2.0  # never completed
W_LESSON_REVIEW = 1.5  # completed long ago — worth revisiting
W_LESSON_MISTAKES = 2.5  # contains items the user last got WRONG
W_LESSON_WEAKNESS = 2.0  # scaled by how weak its skill_types are

SKILL_LABEL_EL = {
    "vocabulary": "το λεξιλόγιο",
    "listening": "την ακουστική κατανόηση",
    "fill_gap": "τη συμπλήρωση κενών",
    "word_order": "τη σύνταξη προτάσεων",
    "speaking": "την προφορική έκφραση",
    "roleplay": "τους διαλόγους",
}
TRACK_GOAL_EL = {
    "grammar": "τη γραμματική σου",
    "maritime": "τη ναυτική σου ορολογία",
}


def _lesson_level(items):
    """A lesson's CEFR band = the rounded mean band of its items."""
    average = sum(_level_index(item.difficulty) for item in items) / len(items)
    return CEFR_LEVELS[round(average)]


def _reason_el(code, track, lesson_level, weak_skill=None):
    """Plain Greek templates explaining the pick — no API calls (rule: zero cost)."""
    goal = TRACK_GOAL_EL.get(track, "τα Αγγλικά σου")
    if code == "mistakes":
        return "Περιέχει σημεία που σε δυσκόλεψαν — ώρα να τα ξαναδούμε και να τα δυναμώσουμε."
    if code == "review":
        return "Το ολοκλήρωσες πριν καιρό — μια επανάληψη θα σταθεροποιήσει όσα έμαθες."
    if code == "weakness" and weak_skill in SKILL_LABEL_EL:
        return (
            f"Δουλεύει {SKILL_LABEL_EL[weak_skill]}, εκεί που χρειάζεσαι εξάσκηση — "
            f"και είναι στο επίπεδό σου ({lesson_level})."
        )
    return f"Είναι στο επίπεδό σου ({lesson_level}) και θα δυναμώσει {goal}."


def choose_next_lesson(progress, lessons, stats, completions, now=None, rng=None):
    """Pick the next whole lesson for a user, with a Greek explanation.

    progress     UserProgress (placement levels; may have nulls)
    lessons      (Lesson, [approved Items]) pairs — the approved pool
    stats        the user's UserItemStat rows
    completions  the user's UserLessonCompletion rows

    Returns (lesson, reason_el, meta) or None when nothing is suitable —
    i.e. no approved lessons with items, or everything was completed within
    the last LESSON_JUST_DONE_DAYS days.
    """
    now = now or datetime.now(timezone.utc)
    rng = rng or random.Random()

    last_completed = {}  # lesson_id -> most recent completion time
    for completion in completions:
        seen = last_completed.get(completion.lesson_id)
        if seen is None or (completion.completed_at and completion.completed_at > seen):
            last_completed[completion.lesson_id] = completion.completed_at

    def days_since_completion(lesson_id):
        age = _age(now, last_completed.get(lesson_id))
        return None if age is None else age.days

    # Candidates: approved lessons that still have items, not finished just now.
    candidates = {"grammar": [], "maritime": []}
    for lesson, items in lessons:
        if not items:
            continue
        days = days_since_completion(lesson.lesson_id)
        if days is not None and days < LESSON_JUST_DONE_DAYS:
            continue  # just completed — never re-suggest immediately
        candidates[section_for_track(lesson.track)].append((lesson, items))
    available = {track for track, entries in candidates.items() if entries}
    if not available:
        return None

    by_item, level_stats, skill_stats = _aggregate(stats)

    track = pick_track(getattr(progress, "maritime_level", None), available, rng)
    if track not in available:  # chosen mix has no lessons — fall back
        track = next(iter(available))

    target_level = working_level(base_level(progress, track), level_stats[track])
    skill_rates = {
        skill: correct / total
        for skill, (correct, total) in skill_stats[track].items()
        if total >= WEAKNESS_MIN_ATTEMPTS
    }

    best = None  # (score, lesson, factors)
    for lesson, items in candidates[track]:
        score = 0.0
        factors = {"level": _lesson_level(items)}

        # Level fit, like the item scorer but on the lesson's dominant band.
        distance = abs(_level_index(factors["level"]) - _level_index(target_level))
        if distance == 0:
            score += W_LESSON_LEVEL_EXACT
        elif distance == 1:
            score += W_LESSON_LEVEL_NEAR

        # Mistakes to revisit: items in this lesson the user last got wrong.
        mistakes = sum(
            1
            for item in items
            if by_item.get(item.item_id) is not None
            and by_item[item.item_id].last_correct is False
        )
        if mistakes:
            score += W_LESSON_MISTAKES
            factors["mistakes"] = mistakes

        # Completion history: prefer new lessons; old completions invite review.
        days = days_since_completion(lesson.lesson_id)
        if days is None:
            score += W_LESSON_NEW
            factors["new"] = True
        elif days >= LESSON_REVIEW_AFTER_DAYS:
            score += W_LESSON_REVIEW
            factors["review"] = True

        # Weakness: how much of the lesson exercises the user's weak skills.
        weak = [
            (skill_rates[item.skill_type], item.skill_type)
            for item in items
            if item.skill_type in skill_rates
        ]
        if weak:
            worst_rate, worst_skill = min(weak)
            score += W_LESSON_WEAKNESS * (1.0 - worst_rate)
            if worst_rate < 0.7:
                factors["weak_skill"] = worst_skill

        score += rng.uniform(0, W_JITTER)
        if best is None or score > best[0]:
            best = (score, lesson, factors)

    score, lesson, factors = best
    # Reason priority mirrors what matters most pedagogically: fix mistakes,
    # then refresh old material, then attack weaknesses, else level/track fit.
    if factors.get("mistakes"):
        code = "mistakes"
    elif factors.get("review"):
        code = "review"
    elif factors.get("weak_skill"):
        code = "weakness"
    else:
        code = "level"
    reason_el = _reason_el(code, track, factors["level"], factors.get("weak_skill"))
    meta = {
        "track": track,
        "target_level": target_level,
        "lesson_level": factors["level"],
        "reason_code": code,
        "score": round(score, 3),
    }
    return lesson, reason_el, meta
