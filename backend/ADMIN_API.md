# Admin API access for external agents

The admin content-generation endpoints normally require a Supabase admin login
(the `ADMIN_EMAIL` account). A headless, self-hosted agent can instead
authenticate with a **shared API key** sent in a request header — no Supabase
session needed.

## Authentication

Every admin endpoint accepts **either**:

1. The existing Supabase admin login (`Authorization: Bearer <jwt>` for the
   `ADMIN_EMAIL` account), **or**
2. An API key: `X-Admin-Key: <ADMIN_API_KEY>`.

If the `X-Admin-Key` header matches `ADMIN_API_KEY`, access is granted. Otherwise
the existing Supabase gating applies unchanged.

### Configuration (Railway)

Set a single env var on the backend service:

```
ADMIN_API_KEY = <a long random secret, e.g. `openssl rand -hex 32`>
```

The secret lives **only** in this env var — never commit it to git or hard-code
it. Leaving `ADMIN_API_KEY` unset disables the API-key path entirely (the header
can never match an empty value).

## Generating lessons as drafts

The agent goes **only as far as drafts**. Approving/publishing stays in the
`/admin` UI and is intentionally unchanged.

### (α) Send text, get back lessons as drafts

```
POST https://<your-backend-host>/api/admin/generate-items
```

### (β) Headers

```
X-Admin-Key: <ADMIN_API_KEY>
Content-Type: application/json
```

### Payload (JSON)

```json
{
  "source_text": "<the maritime/grammar passage to turn into lessons>",
  "kind": "auto"
}
```

- `source_text` (string, required unless a PDF is uploaded) — the passage.
- `kind` (string, optional, default `"auto"`) — one of `auto` | `grammar` |
  `maritime`.
- `page_range` (string, optional) — only used with a PDF upload, e.g. `"5-48"`.

To send a PDF instead of/with text, use `multipart/form-data` with a `pdf` file
part plus the same `source_text` / `kind` / `page_range` form fields (omit the
`Content-Type` header so the client sets the multipart boundary).

### Response

`200 OK` with the created **draft** lessons and their draft items:

```json
{
  "lessons": [
    {
      "lesson_id": "dl_xxxxxxxxxxxx",
      "title": "...",
      "track": "maritime",
      "role_category": "engineer",
      "cefr_level": "B1",
      "skill_area": "vocabulary",
      "status": "draft",
      "existing": false,
      "items": [ { "item_id": "draft_...", "status": "draft", ... } ]
    }
  ]
}
```

Everything is created with `status: "draft"`, so nothing is visible to learners
until a human approves it in `/admin`.

Each generated lesson also carries two organising dimensions (the home groups
the maritime path by them): `cefr_level` (the whole lesson's CEFR band, one of
`A2 | B1 | B2 | C1 | C2`) and `skill_area` (`vocabulary | grammar | listening |
speaking`). The generator suggests both; a reviewer can override them in
`/admin` or via `POST /api/admin/lessons/<lesson_id>` (`cefr_level`,
`skill_area`). Email-track lessons leave both `null` (separate path).

### Example (curl)

```bash
curl -X POST "https://<your-backend-host>/api/admin/generate-items" \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"source_text": "Hard a starboard. Steady as she goes...", "kind": "maritime"}'
```

## Rate limiting

`POST /api/admin/generate-items` is capped per caller (10 requests / 60s). On
exceeding it the endpoint returns `429` with a `Retry-After` header. The limit
is per backend process; with multiple gunicorn workers the effective ceiling is
multiplied by the worker count (a cheap safety valve, not a hard cluster limit).
