"""Single FastAPI entrypoint for Azure App Service: /api/*, /retrieve and the built React UI at /."""
from pathlib import Path
from dotenv import load_dotenv
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)

from app.routers.tickets import router as tickets_router  # noqa: E402
from app.routers.similar import router as similar_router  # noqa: E402
from app.routers.kb import router as kb_router  # noqa: E402
from app.routers.osticket import router as osticket_router, sync_loop  # noqa: E402
from retrieval_agent.main import retrieve_ticket, TicketRequest  # noqa: E402

@asynccontextmanager
async def lifespan(_):
    task = asyncio.create_task(sync_loop())
    yield
    task.cancel()

app = FastAPI(title="Guidewire AI AMS", lifespan=lifespan)
app.include_router(tickets_router)
app.include_router(similar_router)
app.include_router(kb_router)
app.include_router(osticket_router)
app.post("/retrieve")(retrieve_ticket)

@app.get("/health")
def health():
    return {"status": "ok"}

DIST = ROOT / "ui" / "dist"
if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (DIST / path).resolve()
        return FileResponse(f if f.is_file() and DIST in f.parents else DIST / "index.html")
