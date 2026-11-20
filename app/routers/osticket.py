"""osTicket -> ticket_records sync (osTicket is the source of truth for tickets)."""
import asyncio, logging, os
from fastapi import APIRouter, HTTPException
from app.routers.tickets import get_conn
from app.services import osticket

router = APIRouter(prefix="/api/osticket")
log = logging.getLogger("osticket")


def configured() -> bool:
    return bool(os.environ.get("OSTICKET_DB_USER") and os.environ.get("OSTICKET_DB_PASSWORD"))


@router.get("/status")
def status():
    return {"configured": configured(), "interval_seconds": os.environ.get("OSTICKET_SYNC_INTERVAL")}


@router.post("/sync")
def run_sync(dry_run: bool = False):
    if not configured():
        raise HTTPException(503, "osTicket DB is not configured (OSTICKET_DB_USER / OSTICKET_DB_PASSWORD)")
    try:
        return osticket.sync(get_conn, dry_run)
    except Exception as exc:
        log.exception("osTicket sync failed")
        raise HTTPException(502, f"osTicket sync failed: {type(exc).__name__}") from exc


async def sync_loop():
    """Background poller, enabled by setting OSTICKET_SYNC_INTERVAL (seconds)."""
    interval = int(os.environ.get("OSTICKET_SYNC_INTERVAL", "0"))
    while interval > 0 and configured():
        try:
            log.info("osTicket sync: %s", await asyncio.to_thread(osticket.sync, get_conn))
        except Exception:
            log.exception("osTicket sync failed")
        await asyncio.sleep(interval)
