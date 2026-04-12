from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Router and worker lifecycle hooks.
from app.ws import router as ws_router, start_cv_worker, stop_cv_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_cv_worker()
    yield
    stop_cv_worker()


# App setup.
app = FastAPI(title="Driver Drowsiness Backend", lifespan=lifespan)

# CORS for local frontend and device clients.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Route registration.
app.include_router(ws_router)


# Basic health endpoint.
@app.get("/")
def health():
    return {"status": "ok"}
