"""
routes — FastAPI router modules for CreatorPulse.

Each sub-module exposes a single `router` object that main.py registers
via importlib.import_module.  Importing this package directly is not
required; routers are loaded dynamically at startup.

    routes.health    GET /health, GET /ready (liveness + readiness)
    routes.chat      POST /api/v1/chat  (streaming Claude chat)
    routes.analytics GET /api/v1/analytics/*  (metrics + timeseries)
    routes.insights  GET /api/v1/insights/*   (AI intelligence cards)
    routes.sources   GET /api/v1/sources/*    (data-source health)

Direct import example (rarely needed — prefer dynamic loading in main.py):
    from routes.analytics import router as analytics_router
"""
# Expose routers for static-analysis tools and type-checkers.
# The actual registration happens in main.py via importlib to allow
# partial startup when some routers are still being built.
from routes.health    import router as health_router
from routes.chat      import router as chat_router
from routes.analytics import router as analytics_router
from routes.insights  import router as insights_router
from routes.sources   import router as sources_router

__all__ = [
    "health_router",
    "chat_router",
    "analytics_router",
    "insights_router",
    "sources_router",
]
