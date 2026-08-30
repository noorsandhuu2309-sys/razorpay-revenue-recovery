from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="OMNIX")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT_DIR = Path(__file__).resolve().parent.parent
INDEX_FILE = ROOT_DIR / "index.html"

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "OMNIX"}

@app.get("/")
async def root():
    if INDEX_FILE.exists():
        return HTMLResponse(content=INDEX_FILE.read_text(encoding="utf-8"))
    return JSONResponse(status_code=404, content={"error": "index.html not found"})

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    if INDEX_FILE.exists():
        return HTMLResponse(content=INDEX_FILE.read_text(encoding="utf-8"))
    return JSONResponse(status_code=404, content={"error": "index.html not found"})
