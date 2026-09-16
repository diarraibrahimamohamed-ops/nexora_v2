from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.api.v1 import admin, auth, docking, sequences, analysis, ligands, hsa, research


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app.database import bootstrap_admin, ensure_schema
    ensure_schema()
    bootstrap_admin()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Nexora v2 — Bioinformatique et docking computationnel",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Return a generic error to clients; never expose exception internals."""
    if isinstance(exc, (StarletteHTTPException, RequestValidationError)):
        raise exc
    return JSONResponse(status_code=500, content={"detail": "Erreur interne du serveur"})


app.add_middleware(
    CORSMiddleware,
    # Configure the exact trusted web origin through an explicit production value.
    allow_origins=["https://nexora.example.com"] if not settings.DEBUG else ["*"],
    allow_credentials=not settings.DEBUG,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(docking.router, prefix="/api/v1")
app.include_router(sequences.router, prefix="/api/v1")
app.include_router(analysis.router, prefix="/api/v1")
app.include_router(ligands.router, prefix="/api/v1")
app.include_router(hsa.router, prefix="/api/v1")
app.include_router(hsa.legacy_router, prefix="/api/v1")
app.include_router(research.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/v1/health")
def health_v1():
    return {"status": "ok"}