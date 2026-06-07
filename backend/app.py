import logging

from flask import Flask, jsonify

from db import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Create tables on startup if they don't exist. Guarded so a database hiccup
# never takes down the health check.
try:
    init_db()
    logger.info("Database tables initialized.")
except Exception as exc:  # pragma: no cover - startup best effort
    logger.warning("Could not initialize database on startup: %s", exc)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
