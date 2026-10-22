"""python -m source: runs the example API (from example/api)."""

import logging

import uvicorn

from .config import ROOT, Settings

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    uvicorn.run(
        "source.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        reload_dirs=[str(ROOT / "source")] if settings.reload else None,
    )
