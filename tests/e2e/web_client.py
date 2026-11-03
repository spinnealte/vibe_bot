"""Человек в веб-приложении: запросы к настоящему серверу (aiohttp TestServer) с подписью Telegram, как у Mini App.

Сервер собирается из тех же частей, что у бота в сценариях Harness: база во временной схеме, подставной Telegram
(письма админам и коллегам видны в h.tg), тестовая онлайн-оплата.
"""

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from stubbot.web.app import create_web_app
from tests.e2e.harness import Harness
from tests.e2e.telegram_stub import BOT_TOKEN


def sign(fields: dict[str, str], token: str = BOT_TOKEN) -> str:
    """Строка initData (или ответ requestContact), подписанная так, как это делает Telegram."""
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": signature})


def init_data(user_id: int, first_name: str = "Тест", auth_date: int | None = None, start_param: str | None = None,
              token: str = BOT_TOKEN) -> str:
    user = {"id": user_id, "first_name": first_name, "language_code": "ru"}
    fields = {"auth_date": str(auth_date or int(time.time())), "query_id": "AAE-test",
              "user": json.dumps(user, ensure_ascii=False, separators=(",", ":"))}
    if start_param:
        fields["start_param"] = start_param
    return sign(fields, token)


def contact(user_id: int, phone: str, owner: int | None = None) -> str:
    """Подписанный ответ WebApp.requestContact; owner - чей контакт (по умолчанию свой)."""
    data = {"user_id": owner or user_id, "phone_number": phone, "first_name": "Тест"}
    return sign({"auth_date": str(int(time.time())),
                 "contact": json.dumps(data, ensure_ascii=False, separators=(",", ":"))})


class WebApp:
    """Сервер приложения на время сценария: async with WebApp(h) as app: user = app.user(…)."""

    def __init__(self, h: Harness, cache_dir: Path, static_dir: Path | None = None) -> None:
        self.h = h
        self.cache_dir = cache_dir
        self.static_dir = static_dir
        self.client: TestClient | None = None

    async def __aenter__(self) -> "WebApp":
        app = create_web_app(self.h.settings, self.h.factory, self.h.bot, self.h.payments,
                             static_dir=self.static_dir, cache_dir=self.cache_dir)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.client.close()

    def user(self, user_id: int, first_name: str = "Тест", **kwargs: Any) -> "WebUserClient":
        return WebUserClient(self, user_id, init_data(user_id, first_name, **kwargs))


class WebUserClient:
    def __init__(self, app: WebApp, user_id: int, init: str) -> None:
        self.app = app
        self.id = user_id
        self.auth = f"tma {init}"

    async def call(self, method: str, path: str, json_body: Any = None, data: Any = None,
                   expect: int = 200) -> dict[str, Any]:
        response = await self.app.client.request(method, "/api" + path, json=json_body, data=data,
                                                 headers={"Authorization": self.auth})
        body = await response.json()
        assert response.status == expect, f"{method} {path}: {response.status} {body}"
        return body

    async def get(self, path: str, expect: int = 200) -> dict[str, Any]:
        return await self.call("GET", path, expect=expect)

    async def post(self, path: str, body: Any = None, expect: int = 200) -> dict[str, Any]:
        return await self.call("POST", path, body, expect=expect)

    async def put(self, path: str, body: Any = None, expect: int = 200) -> dict[str, Any]:
        return await self.call("PUT", path, body, expect=expect)

    async def patch(self, path: str, body: Any = None, expect: int = 200) -> dict[str, Any]:
        return await self.call("PATCH", path, body, expect=expect)

    async def delete(self, path: str, expect: int = 200) -> dict[str, Any]:
        return await self.call("DELETE", path, expect=expect)

    async def upload(self, path: str, file_name: str, content: bytes, expect: int = 200) -> dict[str, Any]:
        form = FormData()
        form.add_field("file", content, filename=file_name, content_type="application/octet-stream")
        return await self.call("POST", path, data=form, expect=expect)

    async def send(self, path: str, token: str, text: str = "", file: tuple[str, bytes] | None = None,
                   expect: int = 200) -> dict[str, Any]:
        """Форма «текст + файл + одноразовый ключ» - сообщение или счёт клиенту из админки."""
        form = FormData()
        form.add_field("text", text)
        form.add_field("token", token)
        if file is not None:
            form.add_field("file", file[1], filename=file[0], content_type="application/octet-stream")
        return await self.call("POST", path, data=form, expect=expect)

    async def register(self, phone: str, full_name: str = "Иванова Мария Сергеевна",
                       ads: bool = False) -> dict[str, Any]:
        """Вся обязательная регистрация, как её проходит приложение: согласие → телефон → ФИО → направления."""
        form = await self.get("/consent")
        await self.post("/consent", {"pd": True, "ads": ads, "pd_document_id": form["pd"]["document_id"],
                                     "ads_document_id": form["ads"]["document_id"] if form["ads"] else None})
        await self.post("/profile/phone", {"contact": contact(self.id, phone)})
        await self.put("/profile/name", {"full_name": full_name})
        dictionaries = await self.get("/dictionaries")
        await self.put("/profile/specialties", {"ids": [dictionaries["specialties"][0]["id"]]})
        return await self.put("/profile/positions", {"ids": [dictionaries["positions"][0]["id"]]})
