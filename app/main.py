import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.flow import handle_update, process_due_scheduled
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

        # регистрируем меню команд (если MAX поддерживает PATCH /me с commands)
        try:
            cmds = [
                {"name": "start", "description": "Новое поздравление"},
                {"name": "history", "description": "История отправленных"},
                {"name": "scheduled", "description": "Запланированные"},
                {"name": "cancel", "description": "Отменить текущий диалог"},
            ]
            r = app.state.max_client.set_commands(cmds)
            log.info("set_commands: %s", r)
        except Exception as e:
            log.warning("set_commands failed (likely not supported by MAX): %s", e)

    # внутренний таймер: каждые 60 сек обрабатывает запланированные.
    # ВАЖНО: на бесплатном Render сервис засыпает через 15 мин — таймер останавливается.
    # Чтобы он работал постоянно, нужно держать сервис разбужённым (см. .github/workflows/tick.yml).
    async def _scheduler_loop():
        log.info("scheduler loop started (every 60s)")
        while True:
            try:
                await asyncio.sleep(60)
                if not app.state.max_client:
                    continue

                def _run():
                    db = SessionLocal()
                    try:
                        return process_due_scheduled(db, app.state.max_client)
                    finally:
                        db.close()

                result = await run_in_threadpool(_run)
                if result.get("processed", 0) > 0:
                    log.info("scheduler tick: %s", result)
            except asyncio.CancelledError:
                log.info("scheduler loop cancelled")
                raise
            except Exception:
                log.exception("scheduler loop error")

    task = asyncio.create_task(_scheduler_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


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
    return {"build": "2026-04-25-v20-no-ps"}


def _process_update_in_bg(update: dict) -> None:
    """Обрабатываем апдейт в отдельном потоке — чтобы webhook мог сразу вернуть 200
    и не блокировать единственный worker на время генерации."""
    if not app.state.max_client:
        log.error("MAX_BOT_TOKEN not configured")
        return
    db = SessionLocal()
    try:
        handle_update(update, db, app.state.max_client, app.state.giga)
        process_due_scheduled(db, app.state.max_client)
    except Exception:
        log.exception("handler error in background")
    finally:
        db.close()


@app.post("/webhook")
async def webhook(request: Request, bg: BackgroundTasks):
    update = await request.json()
    RECENT_UPDATES.append(update)
    log.info("webhook update: type=%s", update.get("update_type"))
    if not app.state.max_client:
        log.error("MAX_BOT_TOKEN not configured; cannot handle webhook")
        return {"ok": False, "reason": "max_not_configured"}
    # Отвечаем MAX немедленно, чтобы он не начал слать повторы.
    # Генерация и все долгие операции — в фоне.
    bg.add_task(_process_update_in_bg, update)
    return {"ok": True}


@app.get("/admin/me")
def admin_me():
    if not app.state.max_client:
        return {"error": "max not configured"}
    import httpx
    with httpx.Client(timeout=10) as c:
        r = c.get("https://botapi.max.ru/me", headers=app.state.max_client._headers())
        return {"status": r.status_code, "body": r.text}


@app.post("/admin/set-commands")
def admin_set_commands():
    if not app.state.max_client:
        return {"error": "max not configured"}
    cmds = [
        {"name": "start", "description": "Новое поздравление"},
        {"name": "history", "description": "История отправленных"},
        {"name": "scheduled", "description": "Запланированные"},
        {"name": "cancel", "description": "Отменить текущий диалог"},
    ]
    return app.state.max_client.set_commands(cmds)


@app.post("/admin/tick")
def admin_tick():
    """Manually process pending scheduled greetings (can be pinged by cron-job.org every 5-10 min)."""
    if not app.state.max_client:
        return {"error": "MAX_BOT_TOKEN not configured"}
    db = SessionLocal()
    try:
        return process_due_scheduled(db, app.state.max_client)
    finally:
        db.close()


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
