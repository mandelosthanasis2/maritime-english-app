"""Admin-only interview-prep chat powered by the Anthropic Claude API.

Claude acts as an interview coach / mock interviewer for a specific job
interview, grounded in a prep document that lives server-side
(content/interview_prep_nakilat.md) and only ever travels inside the system
prompt to Claude — it is never exposed through any endpoint or bundled into
the frontend.

The server is stateless: the client sends the full conversation history on
every turn (same pattern as roleplay.py) and gets back one plain-text reply.
"""

import json
import logging
import os

from pronunciation import assess_pronunciation_unscripted
from usage import log_usage, token_usage

logger = logging.getLogger(__name__)

# Same model as the other Claude chat features (roleplay / email feedback).
MODEL = "claude-opus-4-8"

# Size caps: the prep document already makes every call token-heavy, so the
# conversation itself is bounded — oversized input is rejected (413) instead
# of being forwarded to the API. Coaching turns run longer than radio-style
# roleplay lines, hence the larger per-message cap.
MAX_MESSAGES = 80
MAX_MESSAGE_CHARS = 8000
MAX_REPLY_TOKENS = 2048

# Voice turns: reject over-long recordings BEFORE any paid Azure call, and
# keep the pronunciation summary sent to Claude compact — only the genuinely
# problematic words (worst first), never the whole word list.
MAX_AUDIO_SECONDS = 180
WORD_ACCURACY_THRESHOLD = 80
MAX_PROBLEM_WORDS = 8

# The prep document is loaded once at import time. Resolved relative to this
# file (like seed.py does) so it works regardless of the working directory.
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PREP_DOCUMENT_PATH = os.path.join(_BACKEND_DIR, "content", "interview_prep_nakilat.md")


def _load_prep_document():
    try:
        with open(PREP_DOCUMENT_PATH, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        logger.exception("Could not load the interview prep document.")
        return ""


PREP_DOCUMENT = _load_prep_document()

SYSTEM_PROMPT = """You are an interview coach and mock interviewer helping Thanasis, a Greek marine engineer, prepare for a specific job interview. The interview details:

- Position: Marine Personnel Recruitment Specialist (Engine), Req. 22890, at Nakilat
- Format: Initial HR interview, ~30 minutes, web meeting, conducted in English
- Interviewer persona: "Simone", an HR recruiter

Your capabilities, depending on what Thanasis asks:
1. MOCK INTERVIEW MODE: Play the role of Simone (HR). Ask realistic questions one at a time, in English. Wait for his answer before continuing. After each answer, briefly step out of character to give feedback (2-4 sentences): what was strong, what to improve, whether he hit the key points from the prep material. Then continue the interview.
2. COACHING MODE: Answer his questions about strategy, help him refine answers, drill specific questions (especially the "hard questions" Q6-Q9), or explain what the interviewer is really testing.
3. LANGUAGE HELP: He is a Greek speaker at B2 English level. Keep the interview itself in English (that is the point — the real interview is in English), but you may give feedback or explanations in Greek if he writes to you in Greek or asks for it. Suggest simpler phrasings when his sentences get too long or complex — the prep material emphasizes short, clear sentences.

Core principles from the prep material you must coach toward:
- Answers should be 60-90 seconds spoken (roughly 100-180 words)
- Never negative about sailing or life at sea; always frame the move ashore as moving TOWARD something
- On gaps (no HR experience, 3rd Engineer rank, 32 vs 36 months sea time): acknowledge honestly, then pivot to strength, never over-apologise
- His strongest card: he can assess engineering competence credibly because he has been an engineer himself
- He should end answers on the role/company, not on himself
- Encourage him to prepare 2-3 questions to ask them; discourage salary questions in the first HR interview

The full prep document follows. Treat it as the source of truth for facts about the candidate and the role:

<prep_document>
{prep_document}
</prep_document>

Be encouraging but honest. The goal is that he walks into the real interview calm, clear, and confident.""".format(prep_document=PREP_DOCUMENT)

# Appended to the system prompt on voice turns only. The placeholder receives
# the compact JSON summary built by _pronunciation_summary().
VOICE_TURN_INSTRUCTIONS = """

VOICE TURN INSTRUCTIONS
The user's last message was spoken aloud and transcribed. Attached is Azure's pronunciation assessment for it:

<pronunciation_assessment>
{assessment_json}
</pronunciation_assessment>

After his spoken answer, before continuing the interview, give structured feedback in this exact order, using these exact section headers:

**🗣 Προφορά**
For each problem word from the assessment (worst first, max 5): the word, what likely went wrong, and how to say it correctly — explained in Greek, in practical terms a non-linguist understands (e.g. "competency → ο τόνος πέφτει στην πρώτη συλλαβή: KOM-pe-ten-see, όχι kom-pe-TEN-see" or "ship → το 'i' εδώ είναι κοντό, όχι 'σιπ' με μακρύ ι"). Use the phoneme-level detail to pinpoint WHICH sound went wrong when it is available. If fluency was low, say so and give one concrete tip (e.g. slow down, pause at commas). If pronunciation was overall good, say so in one line and move on — do not invent problems.

**✏️ Γλώσσα**
Go through the transcript and find real grammar/vocabulary/phrasing mistakes (transcription artifacts are not mistakes — use judgement; if a "mistake" is plausibly the transcriber's fault, skip it or note the uncertainty). For each (max 5, most important first):
- Είπες: "..." (quote the exact phrase)
- Σωστό: "..." (the correction)
- Γιατί: one-sentence explanation in Greek of the rule or the natural phrasing
If a sentence was too long or complex, suggest a shorter version — the prep material emphasizes short, clear sentences.

**🎯 Περιεχόμενο**
2-4 sentences, as in text mode: what was strong, which key points from the prep he hit or missed, one concrete improvement.

Then continue the interview in character (next question), unless he asked for coaching instead.
Keep the feedback honest but encouraging. Never overwhelm: max 5 items per section, most impactful first."""


class InterviewPrepError(Exception):
    """An expected, client-presentable failure with an HTTP status code."""

    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code


def _clean_messages(messages):
    """Keep only well-formed user/assistant turns with non-empty string content."""
    cleaned = []
    if not isinstance(messages, list):
        return cleaned
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            cleaned.append({"role": role, "content": content})
    return cleaned


def _validate_history(messages, max_messages=MAX_MESSAGES):
    """Shared caps + cleanup for a conversation history. Returns the cleaned list."""
    if isinstance(messages, list) and len(messages) > max_messages:
        raise InterviewPrepError(
            "Η συνομιλία έγινε πολύ μεγάλη — ξεκίνα νέα συζήτηση.", 413
        )
    cleaned = _clean_messages(messages)
    if any(len(m["content"]) > MAX_MESSAGE_CHARS for m in cleaned):
        raise InterviewPrepError(
            f"Ένα μήνυμα είναι πολύ μεγάλο (όριο {MAX_MESSAGE_CHARS} χαρακτήρες).", 413
        )
    return cleaned


def _anthropic_or_raise():
    """Config checks shared by both turn kinds; returns the anthropic module."""
    if not PREP_DOCUMENT:
        logger.error("Interview prep document is missing; chat is unavailable.")
        raise InterviewPrepError("Interview prep is not configured on the server.", 503)

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        logger.error("ANTHROPIC_API_KEY is not set; interview prep is unavailable.")
        raise InterviewPrepError("Interview prep is not configured on the server.", 503)

    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency missing
        logger.exception("anthropic SDK failed to import.")
        raise InterviewPrepError(
            "Interview prep is not available on the server.", 503
        ) from exc
    return anthropic


def _call_claude(anthropic, system, messages, user_id, usage_endpoint):
    """One coach turn against the Claude API; returns the reply text."""
    # The Messages API requires the first message to be from the user.
    if messages[0]["role"] != "user":
        messages.insert(0, {"role": "user", "content": "[Continue the session.]"})

    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_REPLY_TOKENS,
            system=system,
            messages=messages,
        )
    except anthropic.APIError as exc:
        logger.exception("Anthropic API call failed.")
        raise InterviewPrepError(
            "The interview coach is unavailable right now. Please try again.", 502
        ) from exc

    # Log right after the API call: the tokens are billed even if the reply
    # turns out to be empty. Distinct endpoint label so the Costs tab shows
    # this feature separately.
    input_tokens, output_tokens = token_usage(response)
    log_usage(
        provider="claude",
        endpoint=usage_endpoint,
        units=input_tokens + output_tokens,
        user_id=user_id,
        details={"input_tokens": input_tokens, "output_tokens": output_tokens, "model": MODEL},
    )

    reply = "".join(b.text for b in response.content if b.type == "text").strip()
    if not reply:
        raise InterviewPrepError("The interview coach returned an empty reply.", 502)
    return reply


def chat(messages, user_id=None):
    """Run one coaching turn and return {"reply": str}.

    `messages` is the full conversation so far ([{role, content}, ...],
    ending with the user's latest message).
    """
    # Validate input FIRST (cheap, config-independent), then check configuration.
    cleaned = _validate_history(messages)
    if not cleaned or cleaned[-1]["role"] != "user":
        raise InterviewPrepError(
            "messages must end with a non-empty user message.", 400
        )

    anthropic = _anthropic_or_raise()
    reply = _call_claude(anthropic, SYSTEM_PROMPT, cleaned, user_id, "admin_interview_prep")
    return {"reply": reply}


def _pronunciation_summary(assessment):
    """The compact summary shared by the client AND the Claude prompt.

    Overall scores plus only the problem words: below the accuracy threshold
    or flagged with an error type, worst first, capped. Phoneme detail is
    trimmed to the sounds that actually scored badly so Claude can pinpoint
    them without wading through the full phoneme stream.
    """
    problems = []
    for w in assessment["words"]:
        accuracy = w.get("accuracy_score")
        error_type = w.get("error_type")
        flagged = error_type and error_type != "None"
        if accuracy is None:
            continue
        if accuracy >= WORD_ACCURACY_THRESHOLD and not flagged:
            continue
        problems.append(
            {
                "word": w["word"],
                "accuracy": round(accuracy),
                "error_type": error_type if flagged else None,
                "phonemes": [
                    {"phoneme": p["phoneme"], "accuracy": round(p["accuracy_score"])}
                    for p in (w.get("phonemes") or [])
                    if p.get("accuracy_score") is not None
                    and p["accuracy_score"] < WORD_ACCURACY_THRESHOLD
                ],
            }
        )
    problems.sort(key=lambda p: p["accuracy"])

    prosody = assessment.get("prosody_score")
    return {
        "accuracy": round(assessment["accuracy_score"]),
        "fluency": round(assessment["fluency_score"]),
        "prosody": round(prosody) if prosody is not None else None,
        "words": problems[:MAX_PROBLEM_WORDS],
    }


def voice_turn(messages, audio_bytes, user_id=None):
    """Run one SPOKEN coaching turn.

    `messages` is the conversation so far WITHOUT the new answer (it may be
    empty or end with the coach's question); the spoken answer arrives as
    `audio_bytes`, is transcribed + scored by Azure in unscripted mode, and
    the transcript is appended server-side as the user's message.

    Returns {"transcript", "pronunciation": <summary>, "reply"}.
    """
    # Leave room for the transcript message that is appended below.
    cleaned = _validate_history(messages, max_messages=MAX_MESSAGES - 1)
    if not audio_bytes:
        raise InterviewPrepError("Uploaded audio is empty.", 400)

    anthropic = _anthropic_or_raise()

    # Azure leg: transcribe + score. Rejects over-long audio (413) before any
    # paid call, and raises PronunciationError for the route to surface.
    assessment = assess_pronunciation_unscripted(
        audio_bytes,
        user_id=user_id,
        usage_endpoint="admin_interview_prep_azure",
        max_seconds=MAX_AUDIO_SECONDS,
    )

    transcript = (assessment.get("transcript") or "").strip()
    if not transcript:
        raise InterviewPrepError(
            "Δεν αναγνωρίστηκε ομιλία. Μίλα πιο καθαρά και δοκίμασε ξανά.", 422
        )
    if len(transcript) > MAX_MESSAGE_CHARS:
        transcript = transcript[:MAX_MESSAGE_CHARS]

    summary = _pronunciation_summary(assessment)
    system = SYSTEM_PROMPT + VOICE_TURN_INSTRUCTIONS.format(
        assessment_json=json.dumps(summary, ensure_ascii=False)
    )

    reply = _call_claude(
        anthropic,
        system,
        cleaned + [{"role": "user", "content": transcript}],
        user_id,
        "admin_interview_prep_voice",
    )
    return {"transcript": transcript, "pronunciation": summary, "reply": reply}
