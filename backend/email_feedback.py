"""AI feedback on a learner's written email, via the Anthropic Claude API.

Mirrors roleplay.py: same SDK and the same structured-output pattern — this is
NOT a new integration, just the email-writing use of the existing Claude setup.
The learner is given a (Greek) scenario and writes an English email; Claude
returns encouraging, specific feedback in Greek plus an improved English
version of the email.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

# Use the latest, most capable Claude model (same as roleplay/generation).
MODEL = "claude-opus-4-8"

# Size caps: the learner's email and the (client-supplied) task text are billed
# as prompt tokens, so oversized input is rejected (413) instead of forwarded.
MAX_EMAIL_CHARS = 10_000
MAX_TASK_CHARS = 4000

# Structured output: three strings. "good" and "improve" are Greek feedback;
# "suggestion" is the improved email itself, in English.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "good": {"type": "string"},
        "improve": {"type": "string"},
        "suggestion": {"type": "string"},
    },
    "required": ["good", "improve", "suggestion"],
    "additionalProperties": False,
}

SYSTEM_TEMPLATE = """You are an encouraging English writing tutor for a Greek seafarer who is learning to write professional emails in English (e.g. emails to the company/office).

THE WRITING TASK the learner was given (in Greek):
{scenario}
{instructions_block}
Evaluate the learner's email against the principles of a good professional email:
- STRUCTURE: greeting -> context -> main point -> request -> closing.
- CLEAR, ACTIVE, CONCISE language (no rambling, no overly complex sentences).
- Correct SET PHRASES ("I am writing to inform you that...", "Please find attached...", "We kindly request...", "I look forward to your reply.").
- Correct MARITIME terminology where relevant.
- Appropriate PROFESSIONAL TONE.

TONE OF YOUR FEEDBACK: encouraging but with clear, specific corrections — never harsh, never empty flattery. Always point to concrete things, with examples.

OUTPUT: a JSON object with exactly three fields. All feedback text is IN GREEK, except the suggested email which is IN ENGLISH:
- "good": what the learner did well — specific, in Greek (positive reinforcement).
- "improve": concrete, specific improvements in Greek — structure, phrasing, style, mistakes. Use short, clear points; reference what to change and why.
- "suggestion": a better version of the email in correct, professional English (the improved email itself). If the learner wrote almost nothing, provide a solid model email for the scenario."""


class EmailFeedbackError(Exception):
    """An expected, client-presentable failure with an HTTP status code."""

    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code


def generate_feedback(scenario, instructions, email_text):
    """Return {"good": str, "improve": str, "suggestion": str} for one email.

    `scenario` and `instructions` are the (Greek) task the learner was given;
    `email_text` is what the learner wrote.
    """
    email_text = (email_text or "").strip()
    if not email_text:
        raise EmailFeedbackError("Γράψε πρώτα το email σου.", 400)
    if len(email_text) > MAX_EMAIL_CHARS:
        raise EmailFeedbackError(
            f"Το email είναι πολύ μεγάλο (όριο {MAX_EMAIL_CHARS} χαρακτήρες).", 413
        )
    if len((scenario or "")) > MAX_TASK_CHARS or len((instructions or "")) > MAX_TASK_CHARS:
        raise EmailFeedbackError("Το σενάριο είναι πολύ μεγάλο.", 413)

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        logger.error("ANTHROPIC_API_KEY is not set; email feedback is unavailable.")
        raise EmailFeedbackError("AI feedback is not configured on the server.", 503)

    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency missing
        logger.exception("anthropic SDK failed to import.")
        raise EmailFeedbackError("AI feedback is not available on the server.", 503) from exc

    instructions = (instructions or "").strip()
    instructions_block = (
        f"\nGUIDANCE the learner was given (in Greek):\n{instructions}\n"
        if instructions
        else ""
    )
    system = SYSTEM_TEMPLATE.format(
        scenario=(scenario or "Γράψε ένα επαγγελματικό email στην εταιρεία.").strip(),
        instructions_block=instructions_block,
    )

    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": email_text}],
            output_config={
                "effort": "medium",  # quality matters more than latency here
                "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
            },
        )
    except anthropic.APIError as exc:
        logger.exception("Anthropic API call failed.")
        raise EmailFeedbackError(
            "Ο AI βοηθός δεν είναι διαθέσιμος αυτή τη στιγμή. Δοκίμασε ξανά.", 502
        ) from exc

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        logger.error("Could not parse model output as JSON: %r", text)
        raise EmailFeedbackError("Ο AI βοηθός επέστρεψε μη αναμενόμενη απάντηση.", 502) from exc

    good = (data.get("good") or "").strip()
    improve = (data.get("improve") or "").strip()
    suggestion = (data.get("suggestion") or "").strip()
    if not (good or improve or suggestion):
        raise EmailFeedbackError("Ο AI βοηθός επέστρεψε κενή απάντηση.", 502)

    return {"good": good, "improve": improve, "suggestion": suggestion}
