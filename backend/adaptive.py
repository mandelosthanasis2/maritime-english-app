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
