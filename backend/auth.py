"""Supabase access-token (JWT) verification.

The frontend sends the Supabase access token in the Authorization header
(`Bearer <jwt>`). We verify its signature with the project's JWT secret before
trusting the user id — never trust a user id sent in the request body.

Required env var: SUPABASE_JWT_SECRET
  Supabase Dashboard -> Project Settings -> API -> "JWT Settings" -> JWT Secret.
"""

import logging
import os

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """An authentication failure with an HTTP status code."""

    def __init__(self, message, status_code=401):
        super().__init__(message)
        self.status_code = status_code


def verify_request(request):
    """Verify the bearer token and return (user_id, email).

    Raises AuthError on any problem (missing config, missing/invalid token).
    """
    secret = os.environ.get("SUPABASE_JWT_SECRET")
    if not secret:
        logger.error("SUPABASE_JWT_SECRET is not set; cannot verify users.")
        raise AuthError("Authentication is not configured on the server.", 503)

    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - dependency missing
        logger.exception("PyJWT failed to import.")
        raise AuthError("Authentication is not available on the server.", 503) from exc

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise AuthError("Missing or invalid Authorization header.", 401)
    token = header.split(" ", 1)[1].strip()

    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Session expired. Please sign in again.", 401) from exc
    except jwt.InvalidTokenError as exc:
        logger.warning("JWT verification failed: %s", exc)
        raise AuthError("Invalid authentication token.", 401) from exc

    user_id = payload.get("sub")
    if not user_id:
        raise AuthError("Invalid token: missing subject.", 401)

    return user_id, payload.get("email")
