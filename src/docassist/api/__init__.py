"""Doc Assistant web app: the browser UI and the REST API for integrations (n8n, other apps).

uv run uvicorn docassist.api:app --reload
→ http://localhost:8000        web UI
→ http://localhost:8000/docs   interactive API documentation

Access: without API_TOKEN everything is open (internal network). With API_TOKEN set,
integrations send ``X-API-Key`` and browsers get a session cookie when they open the UI.

Layout: `app` assembles the app, `routes/` holds one router per area, `deps` the shared
dependencies, `security` access and limits, `schemas` the request/response models.
"""

from docassist.api.app import create_app
from docassist.api.security import SESSION_COOKIE

__all__ = ["SESSION_COOKIE", "app", "create_app"]

app = create_app()
