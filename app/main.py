import logging
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from app.config import settings
from app.flow import handle_update
from app.gigachat import GigaChatClient
from app.max_client import MaxClient
from app.models import HostedImage, SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    app.state.max_client = MaxClient(settings.max_bot_token) if settings.max_bot_token else None
    app.state.giga = GigaChatClient(settings.gigachat_auth_key, settings.gigachat_scope) if settings.gigachat_auth_key else None

    if app.state.max_client and settings.public_base_url:
        try:
            webhook_url = settings.public_base_url.rstrip("/") + "/webhook"
            result = app.state.max_client.subscribe_webhook(webhook_url)
            log.info("webhook subscription: %s", result)
        except Exception as e:
            log.warning("webhook subscription failed: %s", e)

    yield


app = FastAPI(title="MAX Greeting Bot", lifespan=lifespan)
RECENT_UPDATES: deque = deque(maxlen=20)


@app.get("/")
def root():
    return {
        "name": "MAX Greeting Bot",
        "status": "ok",
        "max_configured": bool(settings.max_bot_token),
        "gigachat_configured": bool(settings.gigachat_auth_key),
        "smtp_configured": bool(settings.smtp_host and settings.smtp_user),
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    return {"build": "2026-04-24-v4-history-finish-dynamic-occasions"}


@app.post("/webhook")
async def webhook(request: Request):
    update = await request.json()
    RECENT_UPDATES.append(update)
    log.info("webhook update: type=%s", update.get("update_type"))
    if not app.state.max_client or not app.state.giga:
        log.error("bot not fully configured (max=%s giga=%s)", bool(app.state.max_client), bool(app.state.giga))
        return {"ok": False, "reason": "not_configured"}
    db = SessionLocal()
    try:
        handle_update(update, db, app.state.max_client, app.state.giga)
    except Exception:
        log.exception("handler error")
    finally:
        db.close()
    return {"ok": True}


@app.get("/image/{image_id}.jpg")
def get_image(image_id: int):
    db = SessionLocal()
    try:
        img = db.query(HostedImage).filter(HostedImage.id == image_id).first()
        if not img:
            raise HTTPException(status_code=404)
        return Response(content=img.content, media_type="image/jpeg")
    finally:
        db.close()


@app.get("/admin/last-updates")
def admin_last_updates():
    return list(RECENT_UPDATES)


@app.post("/admin/subscribe")
def admin_subscribe(request: Request):
    """Manual re-subscription endpoint in case lifespan hook didn't run (cold start edge cases)."""
    if not app.state.max_client:
        return {"error": "MAX_BOT_TOKEN not configured"}
    url = settings.public_base_url.rstrip("/") + "/webhook"
    return app.state.max_client.subscribe_webhook(url)


@app.get("/admin/subscriptions")
def admin_subscriptions():
    if not app.state.max_client:
        return {"error": "MAX_BOT_TOKEN not configured"}
    return app.state.max_client.list_subscriptions()
