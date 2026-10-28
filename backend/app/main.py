import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.api.health import router as health_router
from app.api.documents import router as documents_router
from app.api.v1_documents import router as v1_documents_router
from app.api.search import router as search_router
from app.config import get_settings
from app.database import init_db
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    logger.info("Application initialized")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    application.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                               allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                               allow_headers=["Authorization", "Content-Type"])
    application.include_router(health_router)
    application.include_router(documents_router)
    application.include_router(health_router, prefix="/api")
    application.include_router(documents_router, prefix="/api")
    application.include_router(v1_documents_router)
    application.include_router(search_router)

    @application.exception_handler(RequestValidationError)
    async def request_validation_handler(_request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content=jsonable_encoder({"error": {"code": "validation_error", "message": "Request validation failed", "details": exc.errors()}}))

    @application.exception_handler(StarletteHTTPException)
    async def http_error_handler(_request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        error = detail if isinstance(detail, dict) and "code" in detail else {"code": "http_error", "message": str(detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": error}, headers=exc.headers)

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled request error", extra={"path": request.url.path})
        return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "An unexpected error occurred"}})

    return application


app = create_app()
