"""Admin-only interview-prep chat powered by the Anthropic Claude API.

Claude acts as an interview coach / mock interviewer for a specific job
interview, grounded in a prep document that lives server-side
(content/interview_prep_nakilat.md) and only ever travels inside the system
prompt to Claude — it is never exposed through any endpoint or bundled into
the frontend.

The server is stateless: the client sends the full conversation history on
every turn (same pattern as roleplay.py) and gets back one plain-text reply.
"""

import logging
import os

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


def chat(messages, user_id=None):
    """Run one coaching turn and return {"reply": str}.

    `messages` is the full conversation so far ([{role, content}, ...],
    ending with the user's latest message).
    """
    # Validate input FIRST (cheap, config-independent), then check configuration.
    if isinstance(messages, list) and len(messages) > MAX_MESSAGES:
        raise InterviewPrepError(
            "Η συνομιλία έγινε πολύ μεγάλη — ξεκίνα νέα συζήτηση.", 413
        )

    cleaned = _clean_messages(messages)
    if not cleaned or cleaned[-1]["role"] != "user":
        raise InterviewPrepError(
            "messages must end with a non-empty user message.", 400
        )
    if any(len(m["content"]) > MAX_MESSAGE_CHARS for m in cleaned):
        raise InterviewPrepError(
            f"Ένα μήνυμα είναι πολύ μεγάλο (όριο {MAX_MESSAGE_CHARS} χαρακτήρες).", 413
        )

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

    # The Messages API requires the first message to be from the user.
    if cleaned[0]["role"] != "user":
        cleaned.insert(0, {"role": "user", "content": "[Continue the session.]"})

    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_REPLY_TOKENS,
            system=SYSTEM_PROMPT,
            messages=cleaned,
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
        endpoint="admin_interview_prep",
        units=input_tokens + output_tokens,
        user_id=user_id,
        details={"input_tokens": input_tokens, "output_tokens": output_tokens, "model": MODEL},
    )

    reply = "".join(b.text for b in response.content if b.type == "text").strip()
    if not reply:
        raise InterviewPrepError("The interview coach returned an empty reply.", 502)

    return {"reply": reply}
