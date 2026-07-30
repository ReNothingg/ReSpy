from __future__ import annotations

import mimetypes
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from aiogram import Bot
from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.database import Database
from app.security import (
    make_session,
    verify_password,
    verify_session,
    verify_telegram_init_data,
)


COOKIE_NAME = "respy_session"
GIFT_STATUS_LABELS = {
    "sent": "Отправлен",
    "received": "Получен",
    "upgrade_paid": "Апгрейд оплачен",
    "upgraded": "Вскрыт / улучшен",
    "transferred": "Передан",
    "resold": "Перепродан",
    "offer": "Предложение",
    "missing": "Исчез",
    "refunded": "Возвращён",
}
GIFT_EVENT_LABELS = {
    "gift_sent": "Подарок отправлен",
    "gift_received": "Подарок получен",
    "gift_upgrade_paid": "Апгрейд оплачен",
    "gift_upgraded": "Подарок вскрыт / улучшен",
    "gift_transferred": "Подарок передан",
    "gift_resold": "Подарок перепродан",
    "gift_offer": "Событие предложения",
    "gift_missing": "Подарок исчез",
    "gift_source_message_deleted": "Исходное сообщение удалено",
    "gift_profile_shown": "Подарок снова появился в доступном списке",
    "gift_profile_hidden": "Подарок исчез из доступного списка",
    "gift_inventory_present": "Подарок снова появился в инвентаре",
    "gift_inventory_missing": "Подарок исчез из инвентаря",
}


def build_web_app(
    db: Database, settings: Settings, bot: Bot | None = None
) -> FastAPI:
    base_dir = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=base_dir / "templates")
    app = FastAPI(title="ReSpy", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=base_dir / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/avatars/"):
            response.headers["Cache-Control"] = "private, max-age=3600"
        else:
            response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://telegram.org; "
            "style-src 'self'; img-src 'self' data:; media-src 'self'; "
            "connect-src 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

    @app.post("/telegram-auth")
    async def telegram_auth(request: Request, init_data: str = Form(...)) -> JSONResponse:
        user = verify_telegram_init_data(init_data, settings.bot_token)
        if not user or user.get("id") != settings.owner_telegram_id:
            raise HTTPException(status_code=401, detail="Telegram authentication failed")
        response = JSONResponse({"ok": True})
        response.set_cookie(
            COOKIE_NAME,
            make_session(settings.session_secret),
            max_age=7 * 24 * 60 * 60,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
        )
        return response

    def authenticated(request: Request) -> bool:
        return verify_session(request.cookies.get(COOKIE_NAME), settings.session_secret)

    def login_redirect(request: Request) -> RedirectResponse:
        next_path = quote(request.url.path, safe="/")
        return RedirectResponse(f"/login?next={next_path}", status_code=303)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, next: str = "/") -> HTMLResponse:
        if authenticated(request):
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": next if next.startswith("/") else "/", "error": None},
        )

    @app.post("/login", response_class=HTMLResponse)
    async def login(
        request: Request,
        password: str = Form(...),
        next: str = Form("/"),
    ) -> HTMLResponse:
        destination = next if next.startswith("/") and not next.startswith("//") else "/"
        if not verify_password(password, settings.panel_password):
            return templates.TemplateResponse(
                request,
                "login.html",
                {"next": destination, "error": "Неверный пароль"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        response = RedirectResponse(destination, status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            make_session(settings.session_secret),
            max_age=7 * 24 * 60 * 60,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
        )
        return response

    @app.post("/logout")
    async def logout() -> RedirectResponse:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE_NAME)
        return response

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request, q: str = "") -> HTMLResponse:
        if not authenticated(request):
            return login_redirect(request)
        chats = await db.list_chats(q)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"chats": chats, "query": q},
        )

    @app.get("/chats/{connection_id}/{chat_id}", response_class=HTMLResponse)
    async def chat_page(
        request: Request,
        connection_id: str,
        chat_id: int,
        q: str = "",
        event: str = "all",
    ) -> HTMLResponse:
        if not authenticated(request):
            return login_redirect(request)
        if event not in {"all", "deleted", "edited"}:
            event = "all"
        chat = await db.get_chat(connection_id, chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found")
        archive_stats = await db.chat_archive_stats(connection_id, chat_id)
        messages = await db.list_messages(connection_id, chat_id, q, event)
        return templates.TemplateResponse(
            request,
            "chat.html",
            {
                "chat": chat,
                "messages": messages,
                "query": q,
                "event_filter": event,
                "archive_stats": archive_stats,
            },
        )

    @app.get("/gifts", response_class=HTMLResponse)
    async def gifts_page(
        request: Request,
        status_filter: str = "all",
        direction: str = "all",
    ) -> HTMLResponse:
        if not authenticated(request):
            return login_redirect(request)
        allowed_statuses = {"all", *GIFT_STATUS_LABELS}
        if status_filter not in allowed_statuses:
            status_filter = "all"
        if direction not in {"all", "sent", "received"}:
            direction = "all"
        stats = await db.gift_stats()
        gifts = await db.list_gifts(status_filter, direction)
        events = await db.recent_gift_events()
        return templates.TemplateResponse(
            request,
            "gifts.html",
            {
                "stats": stats,
                "gifts": gifts,
                "events": events,
                "status_filter": status_filter,
                "direction": direction,
                "status_labels": GIFT_STATUS_LABELS,
                "event_labels": GIFT_EVENT_LABELS,
            },
        )

    @app.get("/gifts/{gift_id}", response_class=HTMLResponse)
    async def gift_page(request: Request, gift_id: int) -> HTMLResponse:
        if not authenticated(request):
            return login_redirect(request)
        gift = await db.get_gift(gift_id)
        if not gift:
            raise HTTPException(status_code=404, detail="Gift not found")
        gift_events = await db.get_gift_events(gift_id)
        return templates.TemplateResponse(
            request,
            "gift.html",
            {
                "gift": gift,
                "events": gift_events,
                "status_labels": GIFT_STATUS_LABELS,
                "event_labels": GIFT_EVENT_LABELS,
            },
        )

    @app.get("/messages/{row_id}", response_class=HTMLResponse)
    async def message_page(request: Request, row_id: int) -> HTMLResponse:
        if not authenticated(request):
            return login_redirect(request)
        message = await db.get_message(row_id)
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")
        versions = await db.get_versions(row_id)
        return templates.TemplateResponse(
            request,
            "message.html",
            {"message": message, "versions": versions},
        )

    @app.get("/media/{row_id}")
    async def media(request: Request, row_id: int) -> FileResponse:
        if not authenticated(request):
            raise HTTPException(status_code=401, detail="Authentication required")
        message = await db.get_message(row_id)
        if not message or not message.get("media_path"):
            raise HTTPException(status_code=404, detail="Media not found")
        path = Path(message["media_path"]).resolve()
        media_root = settings.media_dir.resolve()
        if media_root not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="Media not found")
        media_type = message.get("media_mime") or mimetypes.guess_type(path.name)[0]
        return FileResponse(
            path,
            media_type=media_type,
            filename=message.get("media_name") or path.name,
            content_disposition_type="inline",
        )

    @app.get("/avatars/{connection_id}/{chat_id}")
    async def avatar(
        request: Request, connection_id: str, chat_id: int
    ) -> Response:
        if not authenticated(request):
            raise HTTPException(status_code=401, detail="Authentication required")
        chat = await db.get_chat(connection_id, chat_id)
        if not chat or not chat.get("avatar_file_id"):
            raise HTTPException(status_code=404, detail="Avatar not found")
        if bot is None:
            raise HTTPException(status_code=503, detail="Telegram is unavailable")
        buffer = BytesIO()
        try:
            await bot.download(chat["avatar_file_id"], destination=buffer)
        except Exception:
            raise HTTPException(status_code=502, detail="Avatar download failed")
        return Response(
            content=buffer.getvalue(),
            media_type="image/jpeg",
        )

    return app
