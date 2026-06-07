# Backend image for Railway (service Root Directory = backend/, builder = Dockerfile).
#
# Python is pinned to 3.12 (not 3.13) and ffmpeg is installed via apt so the
# pronunciation audio conversion (WebM/Opus -> 16 kHz mono PCM WAV) always works.
FROM python:3.12-slim

# ffmpeg is required at runtime for audio conversion.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the backend (app.py, pronunciation.py, content/, seed.py, ...).
COPY . .

# Railway injects $PORT at runtime; default to 8080 for local runs.
ENV PORT=8080
EXPOSE 8080

# Mirrors the Procfile web command.
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT:-8080}"]
