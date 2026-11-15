from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import initialize_database
from app.dependencies import get_current_user
from app.repositories.sqlite_repository import get_dashboard_for_user
from app.routes.auth import router as auth_router
from app.routes.medications import router as medications_router
from app.routes.appointments import router as appointments_router
from app.routes.ai import router as ai_router
from app.routes.approvals import router as approvals_router
from app.routes.notifications import router as notifications_router
from app.routes.profiles import router as profiles_router
from app.routes.relay import router as relay_router
from app.routes.timeline import router as timeline_router
from app.routes.tick import router as tick_router
from app.services.auth_service import SESSION_COOKIE

APP_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan
)

app.mount(
    "/static",
    StaticFiles(directory=str(APP_DIR / "static")),
    name="static"
)

templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app.include_router(auth_router)
app.include_router(medications_router)
app.include_router(appointments_router)
app.include_router(relay_router)
app.include_router(approvals_router)
app.include_router(notifications_router)
app.include_router(profiles_router)
app.include_router(timeline_router)
app.include_router(ai_router)
app.include_router(tick_router)


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"app_name": settings.app_name}
    )


@app.get("/")
def dashboard(request: Request):
    try:
        current_user = get_current_user(request)
    except HTTPException as error:
        if error.status_code != 401:
            raise
        login_url = (
            "/login?expired=1"
            if request.cookies.get(SESSION_COOKIE)
            else "/login"
        )
        return RedirectResponse(url=login_url, status_code=303)

    page = templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "app_name": settings.app_name,
            "current_user": current_user
        }
    )
    page.headers["Cache-Control"] = "no-store"
    return page


@app.get("/api/dashboard")
def dashboard_data(
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    response.headers["Cache-Control"] = "no-store"
    data = get_dashboard_for_user(current_user["id"])
    if data is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return data


@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "application": settings.app_name
    }


@app.get("/healthz")
def healthz():
    return {"status": "healthy"}


@app.get("/api")
def api_home():
    return {
        "message": "Welcome to CareLoop AI API",
        "description": "Care that never loses the connection."
    }
