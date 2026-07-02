# Marlingo — Maritime English App

A monorepo skeleton for a maritime English learning app. This is just the
empty plumbing — no real features or database yet.

## Structure

```
.
├── backend/    # Python Flask API (deploys to Railway)
└── frontend/   # React app built with Vite (deploys to Vercel)
```

### `backend/`

A minimal Flask app exposing a single health-check endpoint.

- `GET /health` → `{"status": "ok"}`

**Run locally:**

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
gunicorn app:app                # or: python app.py
```

**Deploy on Railway:**

1. Create a new Railway project and point it at this repo.
2. Set the service root directory to `backend/`.
3. Railway detects `requirements.txt` and uses the `Procfile`
   (`web: gunicorn app:app`) to start the service.

**Environment variables:** see [`backend/.env.example`](backend/.env.example)
for the full list. AI lesson-content generation is provider-configurable via
`AI_PROVIDER` (`deepseek` default, `claude` fallback) — set `AI_PROVIDER=claude`
to keep the previous Claude-only behaviour. Roleplay and email feedback always
use Claude. If `DEEPSEEK_API_KEY` is missing, generation silently falls back to
Claude.

### `frontend/`

A default Vite + React (JavaScript) app showing a placeholder page.

**Run locally:**

```bash
cd frontend
npm install
npm run dev
```

**Deploy on Vercel:**

1. Import this repo into Vercel.
2. Set the project root directory to `frontend/`.
3. Vercel auto-detects Vite — build command `npm run build`, output `dist/`.
