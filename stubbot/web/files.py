"""Картинки каталога для приложения: обложки курсов и проведений, фото лекторов.

Картинки лежат в Telegram (в базе - file_id, как их загрузил админ в боте). Сервер скачивает файл через Bot API один
раз и кладёт в кэш на диске; дальше отдаёт из кэша. Отдаёт только file_id из каталога (programs, sessions,
lecturers): реквизиты организаций и счета - тоже file_id, но их здесь не достать, даже зная id. Обложку, которую
админ загрузил в приложении, сервер кладёт в кэш сам (store) - она видна в предпросмотре до сохранения курса.

Адрес без входа: <img> не умеет присылать заголовок с подписью Telegram, а обложки курсов и так публичны.
Нет бота, файл не скачался или это не картинка - заглушка (assets/default_cover.png), как в афише бота.
"""

import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiohttp import web
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stubbot.db.models import CourseSession, Lecturer, Program
from stubbot.tg.screen import DEFAULT_COVER_PATH

logger = logging.getLogger(__name__)

FILE_ID = re.compile(r"[A-Za-z0-9_-]{1,256}")  # только безопасные символы: id идёт в путь кэша
MAX_IMAGE = 10 * 1024 * 1024  # фото из Telegram - до 10 МБ
RETRY_AFTER = 600.0  # не скачался - не спрашиваем Telegram снова 10 минут (заглушка)
CACHE_FOR = "public, max-age=86400"
_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF8", "image/gif"),
)


def image_type(head: bytes) -> str | None:
    """Тип картинки по первым байтам; не картинка - None (такой файл не отдаём)."""
    for signature, content_type in _SIGNATURES:
        if head.startswith(signature):
            return content_type
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


class CoverFiles:
    def __init__(self, bot: Bot | None, session_factory: async_sessionmaker[AsyncSession], cache_dir: Path) -> None:
        self.bot = bot
        self.session_factory = session_factory
        self.cache_dir = cache_dir
        self.failed: dict[str, float] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    def setup(self, app: web.Application) -> None:
        app.router.add_get("/api/files/default", self.default)
        app.router.add_get("/api/files/{file_id}", self.file)

    async def default(self, _: web.Request | None = None, cache: str = CACHE_FOR) -> web.StreamResponse:
        response = web.FileResponse(DEFAULT_COVER_PATH)
        response.headers["Cache-Control"] = cache
        return response

    async def file(self, request: web.Request) -> web.StreamResponse:
        file_id = request.match_info["file_id"]
        if not FILE_ID.fullmatch(file_id):
            raise web.HTTPNotFound()
        path = self.cache_dir / hashlib.sha256(file_id.encode()).hexdigest()
        if not path.exists():
            if not await self._in_catalog(file_id):
                raise web.HTTPNotFound()
            async with self.locks.setdefault(file_id, asyncio.Lock()):
                if not path.exists() and not await self._download(file_id, path):
                    return await self.default(cache="no-cache")  # временно: попробуем скачать позже
            self.locks.pop(file_id, None)
        content = path.read_bytes()
        content_type = image_type(content[:16])
        if content_type is None:
            return await self.default(cache="no-cache")
        return web.Response(body=content, content_type=content_type, headers={"Cache-Control": CACHE_FOR})

    def store(self, file_id: str, content: bytes) -> None:
        """Картинка, которую админ только что загрузил из приложения (обложка): сразу в кэш - её видно
        в предпросмотре ещё до сохранения курса, и скачивать её обратно из Telegram не нужно."""
        if not FILE_ID.fullmatch(file_id) or image_type(content[:16]) is None:
            return
        path = self.cache_dir / hashlib.sha256(file_id.encode()).hexdigest()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".part")
        temporary.write_bytes(content)
        temporary.replace(path)

    async def _in_catalog(self, file_id: str) -> bool:
        async with self.session_factory() as session:
            stmt = select(or_(
                exists().where(Program.cover_file_id == file_id),
                exists().where(CourseSession.cover_file_id == file_id),
                exists().where(Lecturer.photo_file_id == file_id),
            ))
            return bool(await session.scalar(stmt))

    async def _download(self, file_id: str, path: Path) -> bool:
        if self.bot is None or time.monotonic() - self.failed.get(file_id, -RETRY_AFTER) < RETRY_AFTER:
            return False
        try:
            remote = await self.bot.get_file(file_id)
            if remote.file_size and remote.file_size > MAX_IMAGE:
                raise ValueError("слишком большой файл")
            data = await self.bot.download_file(remote.file_path)
            content = data.read() if data is not None else b""
            if not content or len(content) > MAX_IMAGE or image_type(content[:16]) is None:
                raise ValueError("не картинка")
        except (TelegramAPIError, ValueError) as error:
            self.failed[file_id] = time.monotonic()
            logger.warning("Веб-приложение: картинка каталога не скачана - %s", type(error).__name__)
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".part")
        temporary.write_bytes(content)
        temporary.replace(path)
        return True
