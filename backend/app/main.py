from app.ws import router as ws_router
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Create FastAPI app
app = FastAPI(title="Driver Drowsiness Backend")

# Allow browser → backend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register WebSocket routes
app.include_router(ws_router)


# Simple health check
@app.get("/")
def health():
    return {"status": "ok"}
