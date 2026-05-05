"""Web frontend for Mini Claude Code — FastAPI app factory."""

from __future__ import annotations

from pathlib import Path
from fastapi import FastAPI

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app(agent) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        agent: A pre-configured Agent instance (singleton for the web session).
    """
    app = FastAPI(title="Mini Claude Code Web")

    # Inject agent into app state for route access
    app.state.agent = agent

    # Import and register API routes
    from .api import router
    app.include_router(router, prefix="/api")

    # Serve static files (CSS, JS)
    @app.get("/static/{filename}")
    async def static_file(filename: str):
        from fastapi.responses import FileResponse
        from fastapi import HTTPException
        filepath = _TEMPLATES_DIR / filename
        if not filepath.exists():
            raise HTTPException(404)
        if filename.endswith(".css"):
            return FileResponse(filepath, media_type="text/css")
        elif filename.endswith(".js"):
            return FileResponse(filepath, media_type="application/javascript")
        return FileResponse(filepath)

    # Serve the main chat page
    @app.get("/")
    async def index():
        from fastapi.responses import HTMLResponse
        content = (_TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(content)

    # Serve the admin panel
    @app.get("/admin")
    async def admin():
        from fastapi.responses import HTMLResponse
        content = (_TEMPLATES_DIR / "admin.html").read_text(encoding="utf-8")
        return HTMLResponse(content)

    return app
