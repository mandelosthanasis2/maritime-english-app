"""Best-effort logging of paid external API usage (feeds the admin 💰 tab).

One row per external API call lands in ``api_usage_log`` (models.ApiUsageLog)
with the units consumed and an estimated USD cost computed at write time from
the PRICING rates below. The service modules themselves call log_usage()
(tts.py, transcription.py, pronunciation.py, roleplay.py, email_feedback.py,
ai_text.py) so logging happens at the point of each external call — this is
the ONE shared helper; no call site duplicates the write logic.

Design rules (same contract as record_activity in app.py):
  - Logging is BEST-EFFORT: log_usage() never raises. A broken or missing
    database must never fail a user-facing request.
  - Costs are ESTIMATES from published list prices, not billing data — the
    exact amounts live in the provider consoles (Azure portal, DeepSeek
    platform, Anthropic console). Every rate below can be overridden with an
    env var, so a price change never needs a code change.

Units per provider:
  azure_tts            characters synthesized
  azure_stt            audio seconds (from the converted 16 kHz mono WAV)
  azure_pronunciation  audio seconds (same approximation)
  deepseek / claude    input_tokens + output_tokens (split kept in `details`)
"""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _env_price(name, default):
    """A float rate from the environment, falling back to the list price."""
    raw = (os.environ.get(name) or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.warning("Ignoring non-numeric %s=%r; using default %s.", name, raw, default)
    return default


# Published pay-as-you-go list prices, USD (defaults checked 2026-07 while
# building this; re-verify in each provider's console — env overrides win):
#   - Azure Speech (azure.microsoft.com/pricing → Speech Services): neural
#     text-to-speech $16 per 1M characters; standard real-time speech-to-text
#     and pronunciation assessment $1 per audio hour.
#   - DeepSeek (api-docs.deepseek.com → pricing): $0.28 per 1M input tokens
#     (cache miss) / $0.42 per 1M output tokens. Cache-hit input is cheaper,
#     so this slightly over-estimates.
#   - Claude claude-opus-4-8 (docs.claude.com → pricing): $5 per 1M input
#     tokens / $25 per 1M output tokens.
PRICING = {
    "azure_tts_per_1m_chars": _env_price("USAGE_PRICE_AZURE_TTS_PER_1M_CHARS", 16.0),
    "azure_stt_per_hour": _env_price("USAGE_PRICE_AZURE_STT_PER_HOUR", 1.0),
    "azure_pronunciation_per_hour": _env_price("USAGE_PRICE_AZURE_PRONUNCIATION_PER_HOUR", 1.0),
    "deepseek_input_per_1m": _env_price("USAGE_PRICE_DEEPSEEK_INPUT_PER_1M", 0.28),
    "deepseek_output_per_1m": _env_price("USAGE_PRICE_DEEPSEEK_OUTPUT_PER_1M", 0.42),
    "claude_input_per_1m": _env_price("USAGE_PRICE_CLAUDE_INPUT_PER_1M", 5.0),
    "claude_output_per_1m": _env_price("USAGE_PRICE_CLAUDE_OUTPUT_PER_1M", 25.0),
}

# Uploads are converted to 16 kHz mono 16-bit PCM before hitting Azure, so the
# WAV body is a fixed 32,000 bytes per second of audio.
_WAV_BYTES_PER_SECOND = 16000 * 2
_WAV_HEADER_BYTES = 44


def wav_seconds(wav_path):
    """Approximate duration (float seconds) of a converted 16 kHz mono WAV."""
    try:
        return max(os.path.getsize(wav_path) - _WAV_HEADER_BYTES, 0) / _WAV_BYTES_PER_SECOND
    except OSError:
        return 0.0


def estimate_cost(provider, units, details=None):
    """Estimated USD cost of one call; 0.0 for unknown providers."""
    details = details or {}
    if provider == "azure_tts":
        return units * PRICING["azure_tts_per_1m_chars"] / 1_000_000
    if provider == "azure_stt":
        return units * PRICING["azure_stt_per_hour"] / 3600
    if provider == "azure_pronunciation":
        return units * PRICING["azure_pronunciation_per_hour"] / 3600
    if provider in ("deepseek", "claude"):
        in_rate = PRICING[f"{provider}_input_per_1m"] / 1_000_000
        out_rate = PRICING[f"{provider}_output_per_1m"] / 1_000_000
        input_tokens = details.get("input_tokens")
        output_tokens = details.get("output_tokens")
        if input_tokens is None and output_tokens is None:
            # No split available — price everything at the (higher) output rate.
            return units * out_rate
        return (input_tokens or 0) * in_rate + (output_tokens or 0) * out_rate
    return 0.0


def token_usage(response):
    """(input_tokens, output_tokens) from an Anthropic-shaped response, 0s if absent."""
    usage = getattr(response, "usage", None)
    return (
        getattr(usage, "input_tokens", 0) or 0,
        getattr(usage, "output_tokens", 0) or 0,
    )


def log_usage(*, provider, endpoint, units, user_id=None, details=None):
    """Record one external API call. Best-effort: NEVER raises.

    `units` may be a float (audio seconds); it is stored rounded to an int
    but the cost estimate uses the exact value. `user_id` is None for
    admin/system calls (Hermes generation) — the 💰 tab shows those as a
    separate "system" row.
    """
    try:
        # Imported lazily so this module stays importable without a database
        # (e.g. local tooling) and adds no import-time coupling to callers.
        from db import SessionLocal
        from models import ApiUsageLog

        if SessionLocal is None:
            return

        est_cost = round(estimate_cost(provider, units, details), 8)
        units_int = int(round(units))
        if units > 0 and units_int == 0:
            units_int = 1  # sub-second audio still counts as one billable unit

        session = SessionLocal()
        try:
            session.add(
                ApiUsageLog(
                    ts=datetime.now(timezone.utc),
                    user_id=user_id,
                    provider=provider,
                    endpoint=endpoint,
                    units=units_int,
                    est_cost_usd=est_cost,
                    details=details or None,
                )
            )
            session.commit()
        finally:
            session.close()
    except Exception:  # noqa: BLE001 - by contract this can never break a request
        logger.warning(
            "Usage logging failed (provider=%s, endpoint=%s) — ignored.",
            provider,
            endpoint,
            exc_info=True,
        )
