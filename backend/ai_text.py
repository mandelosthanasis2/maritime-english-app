"""Thin, provider-agnostic wrapper for AI text (content) generation.

Only the Hermes *content-generation* calls in admin.py go through this module
(generate_lessons, enrich, backfill teaching, auto-categorize, email scenarios).
Roleplay and email feedback stay on Claude directly — they are NOT routed here.

Provider selection (env-driven, idempotent):
  - AI_PROVIDER      : "deepseek" (default) | "claude"
  - DEEPSEEK_API_KEY : enables DeepSeek; if absent we silently fall back to Claude
  - DEEPSEEK_MODEL   : default "deepseek-v4-pro" (configurable — e.g. deepseek-v4-flash)
  - DEEPSEEK_BASE_URL: default DeepSeek's Anthropic-compatible endpoint
  - ANTHROPIC_API_KEY: Claude, kept for fallback + the calls that stay on Claude

Behaviour:
  - AI_PROVIDER=deepseek -> try DeepSeek first, fall back to Claude on failure.
  - AI_PROVIDER=claude   -> Claude ONLY (exactly the previous behaviour; DeepSeek
    is never consulted, even if DEEPSEEK_API_KEY is set).
  - If the chosen primary has no key, it is simply skipped (idempotent) so a
    missing DEEPSEEK_API_KEY never breaks generation — it just uses Claude.

DeepSeek is reached through its Anthropic-compatible endpoint, so only the
base_url + api_key + model change; the request/response shape is identical and
the existing prompts / JSON parsing are reused unchanged.
"""

import logging
import os

from usage import log_usage, token_usage

logger = logging.getLogger(__name__)

# Claude side (unchanged from the previous hard-coded value in admin.py).
CLAUDE_MODEL = "claude-opus-4-8"

# DeepSeek side. We use the explicit model name (NOT the deepseek-chat /
# deepseek-reasoner aliases, which retire 2026-07-24).
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-pro"
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"


class AITextError(Exception):
    """Content generation failed (all configured providers errored)."""


class AITextNotConfigured(AITextError):
    """No AI text provider is configured (no usable API key)."""


def _provider_order():
    """Ordered list of providers to try, per AI_PROVIDER.

    deepseek -> DeepSeek primary, Claude fallback.
    claude   -> Claude only (preserves the exact previous behaviour).
    """
    provider = (os.environ.get("AI_PROVIDER") or "deepseek").strip().lower()
    if provider == "claude":
        return ["claude"]
    return ["deepseek", "claude"]


def _has_key(provider):
    if provider == "deepseek":
        return bool(os.environ.get("DEEPSEEK_API_KEY"))
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _configured_providers():
    """Providers from the order that actually have a usable key."""
    return [p for p in _provider_order() if _has_key(p)]


def require_configured():
    """Raise AITextNotConfigured if no provider can be used.

    Mirrors the old per-call `if not ANTHROPIC_API_KEY: raise 503` guard, but
    provider-aware. Call this once before a loop of generate_text() calls.
    """
    if not _configured_providers():
        raise AITextNotConfigured("No AI text provider is configured on the server.")


def _thinking_disabled():
    """Whether to disable thinking on DeepSeek (reasoning models bill reasoning
    tokens by default). On for generation unless explicitly turned off."""
    val = (os.environ.get("DEEPSEEK_DISABLE_THINKING") or "true").strip().lower()
    return val not in ("0", "false", "no", "off")


def _client_and_request(anthropic, provider, effort):
    """Build the SDK client + the provider-specific request kwargs."""
    if provider == "deepseek":
        client = anthropic.Anthropic(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=(os.environ.get("DEEPSEEK_BASE_URL") or DEEPSEEK_DEFAULT_BASE_URL),
        )
        model = os.environ.get("DEEPSEEK_MODEL") or DEEPSEEK_DEFAULT_MODEL
        # `output_config.effort` is an Anthropic-only knob — omit it for DeepSeek.
        # Disable thinking so a reasoning model doesn't silently bill reasoning
        # tokens during (approved-before-live) content generation.
        extra = {}
        if _thinking_disabled():
            extra["thinking"] = {"type": "disabled"}
        return client, model, extra

    # Claude — identical to the previous inline calls (reads ANTHROPIC_API_KEY
    # from the env, passes output_config.effort, no thinking override).
    client = anthropic.Anthropic()
    extra = {"output_config": {"effort": effort}} if effort else {}
    return client, CLAUDE_MODEL, extra


def generate_text(*, system, messages, max_tokens, effort="medium", timeout=None,
                  usage_endpoint="generate"):
    """Generate text with the configured provider, falling back to the other.

    Returns the concatenated text of the response (same as the callers used to
    build inline). Raises AITextNotConfigured if nothing is configured, or
    AITextError if every configured provider failed. `usage_endpoint` labels
    the call in the api_usage_log (💰 admin tab) — e.g. "generate_items",
    "enrich"; the provider logged is the one that actually served the call.
    """
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency missing
        logger.exception("anthropic SDK failed to import.")
        raise AITextError("The anthropic SDK is not available on the server.") from exc

    providers = _configured_providers()
    if not providers:
        raise AITextNotConfigured("No AI text provider is configured on the server.")

    last_exc = None
    for i, provider in enumerate(providers):
        try:
            client, model, extra = _client_and_request(anthropic, provider, effort)
            kwargs = dict(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                **extra,
            )
            if timeout is not None:
                kwargs["timeout"] = timeout
            response = client.messages.create(**kwargs)
            # Content generation is admin-triggered, so no user_id on the row.
            input_tokens, output_tokens = token_usage(response)
            log_usage(
                provider=provider,
                endpoint=usage_endpoint,
                units=input_tokens + output_tokens,
                details={
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "model": model,
                },
            )
            return "".join(b.text for b in response.content if b.type == "text")
        except Exception as exc:  # anthropic.APIError, timeouts, etc.
            last_exc = exc
            is_last = i == len(providers) - 1
            logger.warning(
                "AI text provider %r failed: %s%s",
                provider,
                exc,
                "" if is_last else "; falling back to next provider",
            )

    raise AITextError(f"All AI text providers failed: {last_exc}") from last_exc
