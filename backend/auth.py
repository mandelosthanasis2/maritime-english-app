"""Supabase access-token (JWT) verification.

The frontend sends the Supabase access token in the Authorization header
(`Bearer <jwt>`). We verify its signature before trusting the user id — never
trust a user id sent in the request body.

Supabase signs access tokens one of two ways:
  - Legacy shared secret (HS256) — verify with SUPABASE_JWT_SECRET.
  - New asymmetric signing keys (RS256 / ES256) — verify against the project's
    public JWKS at {SUPABASE_URL}/auth/v1/.well-known/jwks.json.

Configure whichever your project uses (you can set both):
  - SUPABASE_JWT_SECRET — Dashboard -> Project Settings -> API ->
    "JWT Settings" / "JWT Keys" -> the (legacy) JWT Secret.
  - SUPABASE_URL — your project URL, e.g. https://abcd.supabase.co (used to
    fetch the public JWKS when tokens are signed with RS256/ES256).
"""

import hmac
import logging
import os

logger = logging.getLogger(__name__)

# Small tolerance for clock skew between Supabase and our server (seconds).
LEEWAY_SECONDS = 30

# Cached JWKS client (lazily created).
_jwks_client = None


class AuthError(Exception):
    """An authentication failure with an HTTP status code."""

    def __init__(self, message, status_code=401):
        super().__init__(message)
        self.status_code = status_code


def _jwks_client_for(jwt_module):
    """Return a cached PyJWKClient for the project's JWKS endpoint, or None."""
    global _jwks_client
    if _jwks_client is not None:
        return _jwks_client
    base = os.environ.get("SUPABASE_URL")
    if not base:
        return None
    jwks_url = base.rstrip("/") + "/auth/v1/.well-known/jwks.json"
    _jwks_client = jwt_module.PyJWKClient(jwks_url)
    return _jwks_client


def verify_request(request):
    """Verify the bearer token and return (user_id, email).

    Logs a clear, secret-free diagnostic of each attempt and raises AuthError
    with the specific reason on failure.
    """
    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - dependency missing
        logger.exception("PyJWT failed to import.")
        raise AuthError("Authentication is not available on the server.", 503) from exc

    header = request.headers.get("Authorization", "")
    has_bearer = header.startswith("Bearer ")
    token = header.split(" ", 1)[1].strip() if has_bearer else ""

    if not token:
        logger.warning("auth attempt: no bearer token received")
        raise AuthError("Missing or invalid Authorization header.", 401)

    # Inspect the token header + unverified claims for diagnostics (no secrets).
    try:
        alg = jwt.get_unverified_header(token).get("alg")
        unverified = jwt.decode(token, options={"verify_signature": False})
        aud = unverified.get("aud")
    except Exception as exc:
        logger.warning("auth attempt: token is not a decodable JWT: %s", exc)
        raise AuthError("Invalid authentication token.", 401) from exc

    logger.info("auth attempt: token received, alg=%s, aud=%s", alg, aud)

    # We don't reject on audience — Supabase uses aud="authenticated", and it can
    # vary; verifying the signature + expiry is what matters.
    decode_options = {"verify_aud": False}

    try:
        if alg == "HS256":
            secret = os.environ.get("SUPABASE_JWT_SECRET")
            if not secret:
                logger.error(
                    "auth failure: token alg=HS256 but SUPABASE_JWT_SECRET is not set"
                )
                raise AuthError("Authentication is not configured on the server.", 503)
            payload = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                leeway=LEEWAY_SECONDS,
                options=decode_options,
            )

        elif alg in ("RS256", "ES256"):
            client = _jwks_client_for(jwt)
            if client is None:
                logger.error(
                    "auth failure: token alg=%s (asymmetric) but SUPABASE_URL is "
                    "not set, so the public JWKS can't be fetched. Set SUPABASE_URL "
                    "to your project URL.",
                    alg,
                )
                raise AuthError("Authentication is not configured on the server.", 503)
            try:
                signing_key = client.get_signing_key_from_jwt(token).key
            except Exception as exc:
                logger.error("auth failure: could not load JWKS signing key: %s", exc)
                raise AuthError("Invalid authentication token.", 401) from exc
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256", "ES256"],
                leeway=LEEWAY_SECONDS,
                options=decode_options,
            )

        else:
            logger.error("auth failure: unsupported token algorithm %r", alg)
            raise AuthError("Unsupported authentication token.", 401)

    except AuthError:
        raise
    except jwt.ExpiredSignatureError as exc:
        logger.warning("auth failure: token expired")
        raise AuthError("Session expired. Please sign in again.", 401) from exc
    except jwt.InvalidSignatureError as exc:
        logger.warning(
            "auth failure: signature verification failed — the configured "
            "secret/key does not match the token's signer (check SUPABASE_JWT_SECRET "
            "value, or whether the project uses asymmetric keys requiring SUPABASE_URL)"
        )
        raise AuthError("Invalid authentication token.", 401) from exc
    except jwt.InvalidTokenError as exc:
        logger.warning("auth failure: invalid token: %s", exc)
        raise AuthError("Invalid authentication token.", 401) from exc

    user_id = payload.get("sub")
    if not user_id:
        logger.warning("auth failure: token verified but has no 'sub' claim")
        raise AuthError("Invalid token: missing subject.", 401)

    logger.info("auth success: alg=%s, user verified", alg)
    return user_id, payload.get("email")


ADMIN_API_KEY_HEADER = "X-Admin-Key"


def _api_key_admin(request):
    """Return True if the request carries a valid admin API key.

    A headless agent (no Supabase login) authenticates by sending the shared
    secret in the `X-Admin-Key` header. The secret lives ONLY in the
    ADMIN_API_KEY env var — never in code or git. When that env var is unset the
    API-key path is fully disabled, so an absent header can never match an empty
    configured value. The comparison is constant-time to avoid leaking the key
    through response timing.
    """
    configured = os.environ.get("ADMIN_API_KEY")
    if not configured:
        return False
    provided = request.headers.get(ADMIN_API_KEY_HEADER, "")
    if not provided:
        return False
    return hmac.compare_digest(provided, configured)


def verify_admin(request):
    """Authorize an admin caller via EITHER an API key OR a Supabase admin login.

    Two ways to authenticate:
      1. API key — `X-Admin-Key: <ADMIN_API_KEY>` header. Lets an external,
         self-hosted agent reach the admin content-generation endpoints without a
         Supabase session. Matches → access is granted immediately.
      2. Supabase admin — a verified bearer token whose email equals ADMIN_EMAIL
         (matched case-insensitively). This is the existing gating and stays the
         fallback whenever the API key is absent or does not match.

    Returns (user_id, email) — both None for the API-key path (there is no
    Supabase user). Raises AuthError(403) for non-admins, AuthError(503) if
    neither admin mechanism is configured.

    Env vars:
      - ADMIN_API_KEY — shared secret for the X-Admin-Key header (optional).
      - ADMIN_EMAIL — the email allowed to use the admin endpoints (optional if
        ADMIN_API_KEY is set, but at least one must be configured).
    """
    if _api_key_admin(request):
        logger.info("admin access granted via %s", ADMIN_API_KEY_HEADER)
        return None, None

    # No (valid) API key → fall back to the existing Supabase admin gating.
    user_id, email = verify_request(request)

    admin_email = os.environ.get("ADMIN_EMAIL")
    if not admin_email:
        logger.error("ADMIN_EMAIL is not set; admin endpoints are unavailable.")
        raise AuthError("Admin access is not configured on the server.", 503)

    if not email or email.strip().lower() != admin_email.strip().lower():
        logger.warning("admin access denied for email=%s", email)
        raise AuthError("Forbidden: admin access required.", 403)

    return user_id, email
