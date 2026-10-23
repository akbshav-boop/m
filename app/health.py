from fastapi import FastAPI
app = FastAPI(title="MediaMate health", docs_url=None, redoc_url=None, openapi_url=None)
_storage_ready = False
def set_storage_ready(value: bool):
    global _storage_ready; _storage_ready = value
@app.get("/")
async def root(): return {"service": "MediaMate", "status": "ok", "storage": "sqlite"}
@app.get("/health")
async def health(): return {"status": "ok", "storage": "sqlite", "storage_ready": _storage_ready}
@app.get("/ready")
async def ready():
    from fastapi.responses import JSONResponse
    return JSONResponse({"status": "ready" if _storage_ready else "not_ready", "storage": "sqlite"}, status_code=200 if _storage_ready else 503)
