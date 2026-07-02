from __future__ import annotations

import uvicorn

from backend.app.settings import get_settings


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "backend.app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )

