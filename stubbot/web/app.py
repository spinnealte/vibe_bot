"""Сервер веб-приложения (aiohttp): API для страниц Mini App и сами страницы (сборка webapp/dist).

Каждый запрос к API - как апдейт в боте: кто это (подпись Telegram, web/auth.py) → одна транзакция базы → клиент
по telegram_id (тот же, что в боте) → обработчик (web/api.py). Письма в Telegram (админам, коллеге) - после
коммита: запись уже в базе, сбой отправки её не откатит.
"""

import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stubbot.config import Settings
from stubbot.db.models import Client
from stubbot.services.clients import ClientService, TelegramProfile
from stubbot.services.deeplinks import parse_start_payload
from stubbot.tg.middlewares.text_cleanup import strip_nul
from stubbot.tg.online_payments import OnlinePayments
from stubbot.utils.dates import local_today
from stubbot.web.auth import AuthError, WebUser, user_from_init_data

logger = logging.getLogger(__name__)

MAX_UPLOAD = 21 * 1024 * 1024  # реквизиты - до 20 МБ (как в боте) плюс запас на заголовки формы
RATE_LIMIT, RATE_WINDOW = 120, 60.0  # запросов одного человека в минуту: экраны делают по 1-3 запроса
DEV_NAME = "Разработчик"
dumps = partial(json.dumps, ensure_ascii=False)  # кириллица в ответах как есть - вдвое короче

SETTINGS = web.AppKey("settings", Settings)
SESSION_FACTORY = web.AppKey("session_factory", async_sessionmaker)
BOT = web.AppKey("bot", object)
PAYMENTS = web.AppKey("payments", object)
LIMITER = web.AppKey("limiter", object)
TOKEN = web.AppKey("token", str)  # токен бота: им Telegram подписывает initData и номер телефона
COVERS = web.AppKey("covers", object)  # web/files.py: кэш картинок каталога (админ кладёт туда новые обложки)


class ApiError(Exception):
    """Ответ с ошибкой: HTTP-статус, код (его понимает приложение) и текст для человека."""

    def __init__(self, status: int, code: str, message: str = "", **extra: Any) -> None:
        super().__init__(code)
        self.status, self.code, self.message, self.extra = status, code, message, extra


@dataclass
class Ctx:
    """Всё, что нужно обработчику API: как data в хендлере бота."""

    request: web.Request
    session: AsyncSession
    client: Client
    created: bool
    user: WebUser
    settings: Settings
    bot: Bot | None
    payments: OnlinePayments | None
    after: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    @property
    def bot_token(self) -> str:
        return self.request.app[TOKEN]

    @property
    def today(self) -> date:
        return local_today(self.settings.timezone)

    @property
    def now(self) -> datetime:
        return datetime.now(UTC)

    def later(self, job: Callable[[], Awaitable[None]]) -> None:
        """Сделать после коммита (письма в Telegram)."""
        self.after.append(job)


class RateLimiter:
    """Не больше limit запросов одного человека за window секунд (как защита от флуда в боте)."""

    def __init__(self, limit: int = RATE_LIMIT, window: float = RATE_WINDOW,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.limit, self.window, self.clock = limit, window, clock
        self.hits: dict[int, deque[float]] = {}

    def allow(self, user_id: int) -> bool:
        now = self.clock()
        hits = self.hits.setdefault(user_id, deque())
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        if len(self.hits) > 10_000:  # молчащих давно - из памяти
            for key in [k for k, v in self.hits.items() if not v or now - v[-1] > self.window]:
                del self.hits[key]
        return True


def authenticate(request: web.Request) -> WebUser:
    settings: Settings = request.app[SETTINGS]
    scheme, _, value = request.headers.get("Authorization", "").partition(" ")
    if scheme == "tma" and request.app[TOKEN]:
        try:
            return user_from_init_data(value, request.app[TOKEN])
        except AuthError as error:
            raise ApiError(401, "unauthorized", "Откройте приложение заново из Telegram.") from error
    if scheme == "dev" and settings.webapp_dev_allowed and value.isdigit() and 0 < int(value) < 2**63:
        return WebUser(id=int(value), first_name=DEV_NAME, dev=True)
    raise ApiError(401, "unauthorized", "Откройте приложение из Telegram.")


def telegram_profile(user: WebUser) -> TelegramProfile:
    return TelegramProfile(telegram_id=user.id, username=strip_nul(user.username),
                           first_name=strip_nul(user.first_name), last_name=strip_nul(user.last_name),
                           language_code=strip_nul(user.language_code))


Handler = Callable[[Ctx], Awaitable[Any]]


async def _client(session: AsyncSession, user: WebUser) -> tuple[Client, bool]:
    """Клиент по telegram_id - тот же, что в боте. Новый - как после /start: хвост ссылки (startapp) и журнал."""
    service = ClientService(session)
    client, created = await service.get_or_create(telegram_profile(user))
    if created:
        await service.process_start(client, created, parse_start_payload(user.start_param))
    return client, created


def api(handler: Handler, transaction: bool = True) -> Callable[[web.Request], Awaitable[web.Response]]:
    """Обработчик API: вход, транзакция, клиент, JSON. Ошибки - {"error": {"code", "message", ...}}.

    transaction=False - обработчик сам открывает короткие транзакции (ctx.session без открытой транзакции): нужно,
    когда между шагами - запрос наружу (ЮKassa, загрузка файла в Telegram), чтобы не держать транзакцию и строки.
    """

    async def endpoint(request: web.Request) -> web.Response:
        try:
            user = authenticate(request)
            if not request.app[LIMITER].allow(user.id):
                raise ApiError(429, "too_many_requests", "Слишком часто - подождите немного.")
            factory: async_sessionmaker[AsyncSession] = request.app[SESSION_FACTORY]
            context = dict(user=user, settings=request.app[SETTINGS], bot=request.app[BOT],
                           payments=request.app[PAYMENTS])
            if transaction:
                async with factory() as session, session.begin():
                    client, created = await _client(session, user)
                    ctx = Ctx(request, session, client, created, **context)
                    result = await handler(ctx)
            else:
                async with factory() as session, session.begin():
                    client, created = await _client(session, user)
                async with factory() as session:
                    ctx = Ctx(request, session, client, created, **context)
                    result = await handler(ctx)
            for job in ctx.after:
                try:
                    await job()
                except Exception:
                    logger.exception("Веб-приложение: письмо после %s не отправлено", request.path)
            return web.json_response(result if result is not None else {"ok": True}, dumps=dumps)
        except ApiError as error:
            body = {"error": {"code": error.code, "message": error.message, **error.extra}}
            return web.json_response(body, status=error.status, dumps=dumps)

    endpoint.__name__ = handler.__name__
    return endpoint


@web.middleware
async def errors_middleware(request: web.Request, handler: Callable) -> web.StreamResponse:
    """Необработанная ошибка - JSON 500 без подробностей (подробности - в лог, без тела запроса: там ПД)."""
    try:
        response = await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception("Веб-приложение: ошибка %s %s", request.method, request.path)
        response = web.json_response({"error": {"code": "server_error",
                                                "message": "Что-то пошло не так. Попробуйте ещё раз чуть позже."}},
                                     status=500, dumps=dumps)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def create_web_app(settings: Settings, session_factory: async_sessionmaker[AsyncSession], bot: Bot | None,
                   payments: OnlinePayments | None = None, static_dir: Path | None = None,
                   cache_dir: Path | None = None) -> web.Application:
    """bot - для писем админам и коллегам, обложек курсов и файла реквизитов; None - без них (разработка)."""
    from stubbot.web import admin_api  # noqa: F401 - админка регистрирует свои обработчики в routes.ROUTES
    from stubbot.web import api as routes  # обработчики регистрируют себя в routes.ROUTES
    from stubbot.web.files import CoverFiles

    app = web.Application(middlewares=[errors_middleware], client_max_size=MAX_UPLOAD)
    app[SETTINGS] = settings
    app[SESSION_FACTORY] = session_factory
    app[BOT] = bot
    app[PAYMENTS] = payments
    app[LIMITER] = RateLimiter()
    app[TOKEN] = bot.token if bot is not None else (
        settings.bot_token.get_secret_value() if settings.bot_token else "")
    routes.setup(app)
    app[COVERS] = CoverFiles(bot, session_factory, cache_dir or Path("var") / "webapp-cache")
    app[COVERS].setup(app)
    if static_dir is not None and (static_dir / "index.html").exists():
        _static(app, static_dir)
    return app


# Страница может только своё: скрипты - свои и SDK Telegram, запросы - к своему API; встраивать её (iframe) может
# только веб-версия Telegram. Стили в атрибутах (style=) нужны React-разметке.
CSP = ("default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org")


def _static(app: web.Application, root: Path) -> None:
    """Страницы приложения (сборка webapp/dist): файлы - как есть, всё остальное - index.html (одна страница)."""
    index = root / "index.html"
    resolved_root = root.resolve()

    async def page(request: web.Request) -> web.StreamResponse:
        relative = request.match_info.get("path", "")
        if relative == "api" or relative.startswith("api/"):
            return web.json_response({"error": {"code": "not_found", "message": "Нет такого адреса."}}, status=404,
                                     dumps=dumps)
        path = (root / relative).resolve()
        if path.is_file() and resolved_root in path.parents:
            response = web.FileResponse(path)
            if "/assets/" in str(path).replace("\\", "/"):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # имена с хешем
            return response
        response = web.FileResponse(index)
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Content-Security-Policy"] = CSP
        return response

    app.router.add_get("/{path:.*}", page)
