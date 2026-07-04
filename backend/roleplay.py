"""AI role-play conversation powered by the Anthropic Claude API.

Claude plays the counterpart role in a maritime English dialogue scenario (e.g.
if the learner plays "Engine", Claude plays "Bridge") and returns a short,
radio-style reply plus an optional Greek correction note about the learner's
English.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

# Use the latest, most capable Claude model.
MODEL = "claude-opus-4-8"

# Size caps: every token in the history/scenario is billed on each turn, so
# oversized input is rejected (413) instead of being forwarded to the API.
MAX_HISTORY_TURNS = 40
MAX_MESSAGE_CHARS = 4000
MAX_SCENARIO_CHARS = 4000
MAX_ROLE_CHARS = 200

# Structured-output schema: reply + correction are always strings. An empty
# correction string means "no notable mistake" and is converted to null before
# returning to the client.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "correction": {"type": "string"},
    },
    "required": ["reply", "correction"],
    "additionalProperties": False,
}

SYSTEM_TEMPLATE = """You are a maritime English conversation partner for a Greek seafarer who is practising spoken English for their job (e.g. engine room duties).

ROLE-PLAY SETUP
- Scenario: {scenario}
- The learner is playing the role of "{user_role}".
- You play the OTHER party in this scenario. For bridge-engine room communications, if the learner is the Engine / engine room, you are the Bridge; if the learner is the Bridge, you are the Engine. Infer the counterpart role from the scenario and stay in it consistently. NEVER play the learner's role for them.

HOW TO RESPOND
- Stay in character and steer the conversation toward the scenario's goal, but allow natural variation and let the learner improvise.
- Use realistic, correct maritime English and standard marine communication phrases (IMO SMCP style where appropriate).
- Keep every reply SHORT and radio-like - one or two sentences, the way real bridge-engine communications sound. No essays, no narration.
- If the learner makes a serious English mistake, gently model the correct phrasing inside your in-character reply, without breaking character to lecture them.

OUTPUT
Return a JSON object with exactly two fields:
- "reply": your short, in-character spoken response, in English.
- "correction": a brief, friendly note IN GREEK about any notable English mistake in the learner's last message (wrong word, grammar, or non-standard phrasing). One short sentence at most. If there is no notable mistake, use an empty string."""


class RoleplayError(Exception):
    """An expected, client-presentable failure with an HTTP status code."""

    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code


def _clean_history(history):
    """Keep only well-formed user/assistant turns with non-empty string content."""
    cleaned = []
    if not isinstance(history, list):
        return cleaned
    for message in history:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            cleaned.append({"role": role, "content": content})
    return cleaned


def chat(scenario, user_role, history, user_message):
    """Run one role-play turn and return {"reply": str, "correction": str|None}."""
    user_message = (user_message or "").strip()
    scenario = (scenario or "").strip()
    user_role = (user_role or "").strip()

    # Validate input FIRST (cheap, config-independent), then check configuration.
    # Reject oversized input instead of billing it (see the caps above).
    if isinstance(history, list) and len(history) > MAX_HISTORY_TURNS:
        raise RoleplayError(
            "Η συνομιλία έγινε πολύ μεγάλη — πάτα «Τέλος / Επανεκκίνηση» για νέο role-play.",
            413,
        )
    if len(user_message) > MAX_MESSAGE_CHARS:
        raise RoleplayError(
            f"Το μήνυμα είναι πολύ μεγάλο (όριο {MAX_MESSAGE_CHARS} χαρακτήρες).", 413
        )
    if len(scenario) > MAX_SCENARIO_CHARS or len(user_role) > MAX_ROLE_CHARS:
        raise RoleplayError("Το σενάριο είναι πολύ μεγάλο.", 413)

    messages = _clean_history(history)
    if any(len(m["content"]) > MAX_MESSAGE_CHARS for m in messages):
        raise RoleplayError(
            f"Ένα μήνυμα της συνομιλίας είναι πολύ μεγάλο (όριο {MAX_MESSAGE_CHARS} χαρακτήρες).",
            413,
        )

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        logger.error("ANTHROPIC_API_KEY is not set; role-play is unavailable.")
        raise RoleplayError("AI role-play is not configured on the server.", 503)

    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency missing
        logger.exception("anthropic SDK failed to import.")
        raise RoleplayError("AI role-play is not available on the server.", 503) from exc

    if user_message:
        messages.append({"role": "user", "content": user_message})
    elif not messages:
        # Opening turn: nothing said yet, so prompt Claude to start in character.
        messages.append(
            {"role": "user", "content": "[Begin the role-play now. You speak first, in character.]"}
        )
    else:
        raise RoleplayError("user_message is required.", 400)

    # The Messages API requires the first message to be from the user.
    if messages[0]["role"] != "user":
        messages.insert(0, {"role": "user", "content": "[Begin the role-play.]"})

    system = SYSTEM_TEMPLATE.format(
        scenario=(scenario or "A maritime bridge-engine room communication.").strip(),
        user_role=(user_role or "the learner").strip(),
    )

    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system,
            messages=messages,
            output_config={
                "effort": "low",  # keep radio-style replies fast and cheap
                "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
            },
        )
    except anthropic.APIError as exc:
        logger.exception("Anthropic API call failed.")
        raise RoleplayError("The AI tutor is unavailable right now. Please try again.", 502) from exc

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        logger.error("Could not parse model output as JSON: %r", text)
        raise RoleplayError("The AI tutor returned an unexpected response.", 502) from exc

    reply = (data.get("reply") or "").strip()
    correction = (data.get("correction") or "").strip() or None
    if not reply:
        raise RoleplayError("The AI tutor returned an empty reply.", 502)

    return {"reply": reply, "correction": correction}
