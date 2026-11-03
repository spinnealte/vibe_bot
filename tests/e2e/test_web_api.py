"""Сквозные проверки веб-приложения (Mini App): настоящий сервер API, настоящая база, подставной Telegram.

Человек открывает приложение (подпись Telegram в каждом запросе) и делает то же, что в боте: согласие, регистрация,
расписание, запись, «Мои записи», реквизиты, отмена, онлайн-оплата. Админы получают те же уведомления, что от бота.
"""

import time
from pathlib import Path

import pytest
from aiogram.utils.web_app import check_webapp_signature
from sqlalchemy import select, update

from stubbot.config import get_settings
from stubbot.db.enums import ConsentSource, ConsentType, EnrollmentSource, OrderStatus
from stubbot.db.models import Client, ClientConsent, Enrollment, Order, Program
from stubbot.tg import texts
from stubbot.web.app import LIMITER
from tests.e2e.conftest import E2E
from tests.e2e.harness import ADMIN_ID, MANAGER_URL, OWNER_ID, RUB, Harness, fragment
from tests.e2e.telegram_stub import BOT_ID, BOT_TOKEN, KNOWN_FILES
from tests.e2e.web_client import WebApp, contact, init_data, sign

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _admins_see(h: Harness, part: str) -> bool:
    return all(any(part in (m.plain or "") for m in h.tg.messages(chat)) for chat in (OWNER_ID, ADMIN_ID))


def test_only_telegram_signature_lets_in(e2e: E2E, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBAPP_DEV_AUTH", "false")
    get_settings.cache_clear()

    async def scenario(h: Harness) -> None:
        async with WebApp(h, tmp_path) as app:
            async def status(authorization: str | None) -> int:
                headers = {"Authorization": authorization} if authorization else {}
                response = await app.client.get("/api/me", headers=headers)
                assert response.headers["Content-Type"].startswith("application/json")
                return response.status

            assert await status(None) == 401
            assert await status("tma ") == 401
            assert await status("tma " + init_data(5001, token=f"{BOT_ID}:" + "B" * 35)) == 401  # чужой токен
            assert await status("tma " + init_data(5001, auth_date=int(time.time()) - 2 * 86400)) == 401  # старая
            assert await status("tma " + init_data(5001, auth_date=int(time.time()) + 3600)) == 401  # из будущего
            assert await status("tma " + init_data(5001).replace("5001", "5002")) == 401  # подменили пользователя
            assert await status("dev 5001") == 401  # вход разработчика выключен
            assert await h.scalar(select(Client).where(Client.telegram_id.in_([5001, 5002]))) is None

            me = await app.user(5001, start_param="src_vk").get("/me")
            assert me["step"] == "consent"
            assert me["config"]["applications"] and me["config"]["fake_payments"]
            assert me["config"]["bot_url"].endswith("aquastom_e2e_bot")
            client = await h.scalar(select(Client).where(Client.telegram_id == 5001))
            assert client.start_param == "src_vk"
            # До согласия из Telegram храним только id (как в боте).
            assert client.tg_first_name is None

    e2e.run(scenario)


def test_dev_login_only_with_the_switch(e2e: E2E, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBAPP_DEV_AUTH", "true")
    get_settings.cache_clear()

    async def scenario(h: Harness) -> None:
        async with WebApp(h, tmp_path) as app:
            response = await app.client.get("/api/me", headers={"Authorization": "dev 5050"})
            assert response.status == 200 and (await response.json())["config"]["dev"] is True
            response = await app.client.get("/api/me", headers={"Authorization": "dev 99999999999999999999"})
            assert response.status == 401

    e2e.run(scenario)


def test_registration_in_the_app(e2e: E2E, tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        h.pii.update({"+79991112233", "Иванова Мария Сергеевна", "test@mail.ru"})
        async with WebApp(h, tmp_path) as app:
            user = app.user(5101, "Мария")
            await user.get("/profile", expect=403)
            await user.get("/schedule")  # расписание - без регистрации

            form = await user.get("/consent")
            pd_id, ads_id = form["pd"]["document_id"], form["ads"]["document_id"]
            assert 'href="https://akvastom-group.ru/privacy/"' in form["pd"]["html"]
            assert form["ads_checked"] is False
            error = await user.post("/consent", {"pd": False, "pd_document_id": pd_id}, expect=400)
            assert error["error"]["code"] == "pd_required"
            await user.post("/consent", {"pd": True, "pd_document_id": pd_id + 100}, expect=409)
            result = await user.post("/consent", {"pd": True, "ads": True, "pd_document_id": pd_id,
                                                  "ads_document_id": ads_id})
            assert result["step"] == "phone"
            consents = await h.scalars(select(ClientConsent).join(Client).where(Client.telegram_id == 5101))
            assert {c.type for c in consents} == {ConsentType.PERSONAL_DATA, ConsentType.MARKETING}
            assert {c.source for c in consents} == {ConsentSource.SITE}

            # Номер - только подписанный Telegram и свой.
            assert (await user.post("/profile/phone", {"phone": "+79991112233"}, expect=400)
                    )["error"]["code"] == "contact_required"
            await user.post("/profile/phone", {"contact": contact(5101, "+79991112233", owner=5999)}, expect=400)
            forged = contact(5101, "+79991112233").replace("79991112233", "79990000000")
            await user.post("/profile/phone", {"contact": forged}, expect=400)
            result = await user.post("/profile/phone", {"contact": contact(5101, "79991112233")})
            assert result == {"result": "ok", "linked_name": None, "linked": [], "step": "name"}
            # «Поделиться номером» в приложении Telegram дублирует контактом в чат с ботом - бот молчит.
            person = h.person(5101, "Мария")
            mark = person.mark()
            await person.share_contact("+79991112233")
            assert person.since(mark) == []

            await user.put("/profile/name", {"full_name": "Иванова"}, expect=400)
            await user.put("/profile/name", {"full_name": "<b>Иванова</b> Мария"}, expect=400)
            result = await user.put("/profile/name", {"full_name": "  Иванова   Мария Сергеевна "})
            assert result == {"full_name": "Иванова Мария Сергеевна", "step": "specialties"}

            dictionaries = await user.get("/dictionaries")
            assert [s["title"] for s in dictionaries["specialties"]] == ["Хирургия", "Имплантация", "Ортодонтия"]
            await user.put("/profile/specialties", {"ids": []}, expect=400)
            await user.put("/profile/specialties", {"ids": [999_999]}, expect=400)
            await user.put("/profile/specialties", {"ids": ["1"]}, expect=400)
            await user.put("/profile/specialties", {"ids": [2 ** 70]}, expect=400)
            spec = [s["id"] for s in dictionaries["specialties"][:2]]
            assert (await user.put("/profile/specialties", {"ids": spec}))["step"] == "positions"
            position = dictionaries["positions"][0]["id"]
            assert await user.put("/profile/positions", {"ids": [position]}) == {"step": "done", "just_completed": True}
            assert (await user.put("/profile/positions", {"ids": [position]}))["just_completed"] is False

            profile = await user.get("/profile")
            assert profile["phone"] == "+79991112233" and profile["full_name"] == "Иванова Мария Сергеевна"
            assert profile["marketing"] is True and profile["referral_link"].startswith("https://t.me/aquastom_e2e_bot")
            assert [s["title"] for s in profile["specialties"]] == ["Хирургия", "Имплантация"]

            error = await user.patch("/profile", {"field": "email", "value": "not-an-email"}, expect=400)
            assert error["error"]["code"] == "invalid_value" and error["error"]["field"] == "email"
            assert (await user.patch("/profile", {"field": "email", "value": "Test@Mail.ru"}))["email"] == "test@mail.ru"
            assert (await user.patch("/profile", {"field": "practice_since_year", "value": 7}))["experience_years"] == 7
            assert (await user.patch("/profile", {"field": "city", "value": "Санкт-Петербург"}))["city"] == "Санкт-Петербург"
            assert (await user.patch("/profile", {"field": "city", "value": ""}))["city"] is None
            await user.patch("/profile", {"field": "phone", "value": "+79990000000"}, expect=400)
            await user.patch("/profile", {"field": "birth_date", "value": "31.02.1990"}, expect=400)

            marketing = await user.get("/marketing")
            assert (await user.post("/marketing", {"on": False})) == {"marketing": False}
            await user.post("/marketing", {"on": True, "document_id": marketing["document_id"] + 1}, expect=409)
            assert (await user.post("/marketing", {"on": True, "document_id": marketing["document_id"]})
                    ) == {"marketing": True}

            client = await h.scalar(select(Client).where(Client.telegram_id == 5101))
            assert client.tg_first_name == "Мария"  # после согласия имя из Telegram запоминается

    e2e.run(scenario)


def test_schedule_course_program_and_about(e2e: E2E, tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        c = h.catalog
        async with h.factory() as session, session.begin():
            await session.execute(update(Program).where(Program.id == c.program.id).values(
                cover_file_id="cover-web-1", program_html='<b>День 1</b>\n<script>alert(1)</script>'
                                                          '<a href="javascript:alert(1)">ссылка</a>'))
        KNOWN_FILES["cover-web-1"] = PNG
        KNOWN_FILES["requisites-secret"] = b"%PDF secret"
        async with WebApp(h, tmp_path) as app:
            user = app.user(5201)
            items = (await user.get("/schedule"))["items"]
            assert [i["id"] for i in items] == [c.soon.id, c.main.id, c.small.id, c.no_price.id, c.announced.id]
            main = next(i for i in items if i["id"] == c.main.id)
            assert main["price_from"]["amount"] == 45_000 * RUB and main["seats_left"] == 10
            assert main["accepts_applications"] and main["cover_url"] == "/api/files/cover-web-1"
            announced = next(i for i in items if i["id"] == c.announced.id)
            assert announced["announced"] and not announced["accepts_applications"] and announced["start_date"] is None
            assert next(i for i in items if i["id"] == c.no_price.id)["price_from"] is None

            details = await user.get(f"/schedule/{c.main.id}")
            assert [p["pair"] for p in details["prices"]] == [False, True]
            assert len(details["days"]) == 2 and details["manager_url"].startswith(MANAGER_URL)
            assert (await user.get("/schedule/999999", expect=404))["error"]["code"] == "not_found"
            assert (await app.client.get("/api/schedule/abc", headers={"Authorization": user.auth})).status == 404

            program = await user.get(f"/programs/{c.program.id}")
            assert program["html"].startswith("<b>День 1</b><br>")
            assert "<script>" not in program["html"] and "javascript:" not in program["html"]
            assert "&lt;script&gt;" not in program["html"]  # тег выброшен, текст внутри - буквами
            interest = await user.post(f"/programs/{c.program.id}/interest")
            assert interest["created"] is True
            assert (await user.post(f"/programs/{c.program.id}/interest"))["created"] is False

            about = await user.get("/about")
            assert "АкваСтом" in about["html"] and about["venues"][0]["name"] == "Учебный класс"

            response = await app.client.get("/api/files/cover-web-1")
            assert response.status == 200 and response.content_type == "image/png" and await response.read() == PNG
            del KNOWN_FILES["cover-web-1"]  # второй раз - из кэша на диске
            assert await (await app.client.get("/api/files/cover-web-1")).read() == PNG
            assert (await app.client.get("/api/files/requisites-secret")).status == 404  # не обложка каталога
            assert (await app.client.get("/api/files/..%2F..%2Fsecret")).status == 404
            default = await app.client.get("/api/files/default")
            assert default.status == 200 and default.content_type == "image/png"

    e2e.run(scenario)


def test_enroll_requisites_and_cancel(e2e: E2E, tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        c = h.catalog
        h.tg.started.update({OWNER_ID, ADMIN_ID})
        h.pii.update({"+79990001122", "+79990001133", "+79990001144", "Петров Пётр Ильич"})
        async with WebApp(h, tmp_path) as app:
            user, other = app.user(5301), app.user(5302)
            error = await user.post(f"/sessions/{c.main.id}/enroll", {"price_id": c.single.id}, expect=403)
            assert error["error"]["code"] == "profile_required" and error["error"]["step"] == "consent"
            await user.register("+79990001122")
            await other.register("+79990001133", "Сидоров Иван")

            await user.post(f"/sessions/{c.main.id}/enroll", {}, expect=400)  # тарифы есть - без выбора нельзя
            await user.post(f"/sessions/{c.main.id}/enroll", {"price_id": c.small_price.id}, expect=400)
            await user.post(f"/sessions/{c.main.id}/enroll", {"price_id": c.pair.id}, expect=400)  # без коллеги
            result = await user.post(f"/sessions/{c.main.id}/enroll", {"price_id": c.single.id})
            assert result["outcome"] == "created"
            app_data = result["application"]
            order = app_data["order"]
            assert order["total"]["amount"] == 45_000 * RUB and order["due"] and app_data["can_choose_payer"]
            assert _admins_see(h, str(order["number"]))
            enrollment = await h.scalar(select(Enrollment).where(Enrollment.id == app_data["id"]))
            assert enrollment.source is EnrollmentSource.SITE
            error = await user.post(f"/sessions/{c.main.id}/enroll", {"price_id": c.single.id}, expect=409)
            assert error["error"]["code"] == "duplicate"
            mine = next(i for i in (await user.get("/schedule"))["items"] if i["id"] == c.main.id)["mine"]
            assert mine["status"] == "application"

            # Чужой заказ не выбрать, не оплатить и реквизиты к нему не прислать.
            await other.post(f"/orders/{order['id']}/payer", {"type": "individual"}, expect=404)
            await other.upload(f"/orders/{order['id']}/requisites", "req.pdf", b"%PDF-1.4", expect=404)
            await other.post(f"/orders/{order['id']}/pay", expect=404)
            await other.post(f"/applications/{app_data['id']}/cancel", expect=404)
            await user.post(f"/orders/{order['id']}/payer", {"type": "nobody"}, expect=400)

            result = await user.post(f"/orders/{order['id']}/payer", {"type": "organization"})
            assert result["application"]["can_send_requisites"] is True
            assert _admins_see(h, str(order["number"]))
            await user.upload(f"/orders/{order['id']}/requisites", "setup.exe", b"MZ\x90\x00", expect=400)
            await user.upload(f"/orders/{order['id']}/requisites", "empty.pdf", b"", expect=400)
            result = await user.upload(f"/orders/{order['id']}/requisites", "Реквизиты.pdf", b"%PDF-1.4 web")
            assert result["application"]["order"]["requisites_received"] is True
            assert result["application"]["order"]["requisites_file_name"] == "Реквизиты.pdf"
            for chat in (OWNER_ID, ADMIN_ID):
                assert h.tg.messages(chat)[-1].document, f"админу {chat} не пришёл файл реквизитов"
            saved = await h.scalar(select(Order).where(Order.id == order["id"]))
            assert saved.requisites_file_id == h.tg.messages(OWNER_ID)[-1].document

            # Вдвоём с коллегой: галочка согласия коллеги обязательна.
            colleague = {"full_name": "Петров Пётр Ильич", "phone": "+7 999 000-11-44", "consent": False}
            await other.post(f"/sessions/{c.main.id}/enroll", {"price_id": c.pair.id, "colleague": colleague},
                             expect=400)
            await other.post(f"/sessions/{c.main.id}/enroll", {
                "price_id": c.pair.id, "colleague": {**colleague, "consent": True, "phone": "+79990001133"}},
                expect=400)  # свой номер
            result = await other.post(f"/sessions/{c.main.id}/enroll",
                                      {"price_id": c.pair.id, "colleague": {**colleague, "consent": True}})
            assert result["outcome"] == "created" and result["colleague_name"] == "Петров Пётр Ильич"
            assert result["invite_link"].endswith("?start=ref_" + (await h.scalar(
                select(Client.referral_code).where(Client.telegram_id == 5302))))
            assert result["application"]["partner"] == "Петров Пётр Ильич"

            # Мест нет - лист ожидания.
            assert (await user.post(f"/sessions/{c.small.id}/enroll", {"price_id": c.small_price.id})
                    )["outcome"] == "created"
            waitlist = await other.post(f"/sessions/{c.small.id}/enroll", {"price_id": c.small_price.id})
            assert waitlist["outcome"] == "waitlist"

            # Отмена без оплаты: запись пропадает из «Моих записей», заказ закрыт, админам - уведомление.
            await user.post(f"/applications/{app_data['id']}/cancel", {"choice": "everything"}, expect=400)
            result = await user.post(f"/applications/{app_data['id']}/cancel")
            assert result["outcome"] == "cancelled" and result["choice"] is None
            items = (await user.get("/applications"))["items"]
            assert [i["session_id"] for i in items] == [c.small.id]
            assert (await h.scalar(select(Order).where(Order.id == order["id"]))).status is OrderStatus.CANCELLED
            assert _admins_see(h, "отменил") or _admins_see(h, fragment(texts.ADMIN_NOTIFY_CANCELLED))
            await user.post(f"/applications/{app_data['id']}/cancel", expect=404)

            # Ближе двух недель до начала - только через менеджера.
            soon = await user.post(f"/sessions/{c.soon.id}/enroll", {"price_id": c.soon_price.id})
            assert soon["application"]["can_cancel"] is False and soon["application"]["manager_cancel"] is True
            await user.post(f"/applications/{soon['application']['id']}/cancel", expect=409)

    e2e.run(scenario)


def test_online_payment_in_the_app(e2e: E2E, tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        c = h.catalog
        h.tg.started.update({OWNER_ID, ADMIN_ID})
        async with WebApp(h, tmp_path) as app:
            user, other = app.user(5401), app.user(5402)
            await user.register("+79990002211")
            await other.register("+79990002222", "Сидоров Иван")
            created = await user.post(f"/sessions/{c.main.id}/enroll", {"price_id": c.single.id})
            order_id = created["application"]["order"]["id"]
            chosen = await user.post(f"/orders/{order_id}/payer", {"type": "individual"})
            assert chosen["application"]["can_pay_online"] is True

            payment = (await user.post(f"/orders/{order_id}/pay"))["payment"]
            assert payment["fake"] is True and payment["amount"]["amount"] == 45_000 * RUB
            await other.post(f"/payments/{payment['id']}/check", expect=404)
            await other.post(f"/payments/{payment['id']}/fake", {"success": True}, expect=404)
            pending = await user.post(f"/payments/{payment['id']}/check")
            assert pending["state"] == "pending"

            declined = await user.post(f"/payments/{payment['id']}/fake", {"success": False})
            assert declined["state"] == "canceled"
            payment = (await user.post(f"/orders/{order_id}/pay"))["payment"]  # новая попытка - новый платёж
            paid = await user.post(f"/payments/{payment['id']}/fake", {"success": True})
            assert paid["state"] == "succeeded" and "Оплата получена" in paid["message_html"]
            assert _admins_see(h, str(created["application"]["order"]["number"]))

            app_data = (await user.get("/applications"))["items"][0]
            assert app_data["status"] == "confirmed" and app_data["paid"] == 45_000 * RUB
            assert app_data["can_pay_online"] is False
            assert (await user.post(f"/orders/{order_id}/pay", expect=404))["error"]["code"] == "order_unavailable"

            # Оплачено - отмена спрашивает, что сделать с деньгами.
            error = await user.post(f"/applications/{app_data['id']}/cancel", expect=409)
            assert error["error"]["code"] == "needs_choice" and error["error"]["paid"]["amount"] == 45_000 * RUB
            result = await user.post(f"/applications/{app_data['id']}/cancel", {"choice": "refund"})
            assert result["choice"] == "refund" and result["manager_url"].startswith(MANAGER_URL)

    e2e.run(scenario)


def test_colleague_opens_the_app_and_finds_the_booking(e2e: E2E, tmp_path: Path) -> None:
    """Коллегу записали вдвоём, он открыл приложение и поделился тем же номером - запись теперь его."""
    async def scenario(h: Harness) -> None:
        c = h.catalog
        h.tg.started.update({OWNER_ID, ADMIN_ID, 5501})
        async with WebApp(h, tmp_path) as app:
            payer, colleague = app.user(5501), app.user(5502)
            await payer.register("+79990003311", "Смирнова Анна")
            await payer.post(f"/sessions/{c.main.id}/enroll", {"price_id": c.pair.id, "colleague": {
                "full_name": "Кузнецов Олег", "phone": "+79990003322", "consent": True}})
            form = await colleague.get("/consent")
            await colleague.post("/consent", {"pd": True, "pd_document_id": form["pd"]["document_id"]})
            linked = await colleague.post("/profile/phone", {"contact": contact(5502, "+79990003322")})
            assert linked["result"] == "linked" and linked["linked_name"] == "Кузнецов Олег"
            assert [a["session_id"] for a in linked["linked"]] == [c.main.id]
            assert linked["linked"][0]["is_payer"] is False and linked["linked"][0]["partner"] == "Смирнова Анна"
            assert any("Кузнецов" in (m.plain or "") for m in h.tg.messages(5501))  # записавшему - «коллега в боте»

    e2e.run(scenario)


def test_rate_limit_and_errors_are_json(e2e: E2E, tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        async with WebApp(h, tmp_path) as app:
            app.client.app[LIMITER].limit = 3
            user = app.user(5601)
            for _ in range(3):
                await user.get("/me")
            assert (await user.get("/me", expect=429))["error"]["code"] == "too_many_requests"
            response = await app.client.post("/api/consent", data="{not json",
                                             headers={"Authorization": app.user(5602).auth})
            assert response.status == 400 and (await response.json())["error"]["code"] == "bad_request"
            response = await app.client.get("/api/me", headers={"Authorization": app.user(5603).auth})
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert response.headers["Cache-Control"] == "no-store"

    e2e.run(scenario)


def test_signature_helper_matches_aiogram() -> None:
    """Подпись в тестах - та же, что проверяет сервер (aiogram.utils.web_app)."""
    assert check_webapp_signature(BOT_TOKEN, sign({"auth_date": "1", "user": '{"id":1}'}))


def test_pages_of_the_app(e2e: E2E, tmp_path: Path) -> None:
    """Сборка интерфейса: файлы - как есть, любой другой адрес - index.html с CSP; наружу из папки не выйти."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>app</title>", encoding="utf-8")
    (dist / "assets" / "app-1a2b.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")

    async def scenario(h: Harness) -> None:
        async with WebApp(h, tmp_path / "cache", static_dir=dist) as app:
            index = await app.client.get("/")
            assert index.status == 200 and "<title>app</title>" in await index.text()
            assert "frame-ancestors" in index.headers["Content-Security-Policy"]
            assert (await (await app.client.get("/course/5")).text()).startswith("<!doctype html>")
            asset = await app.client.get("/assets/app-1a2b.js")
            assert asset.status == 200 and "immutable" in asset.headers["Cache-Control"]
            assert "secret" not in await (await app.client.get("/..%2Fsecret.txt")).text()
            missing = await app.client.get("/api/nothing-here")
            assert missing.status == 404 and (await missing.json())["error"]["code"] == "not_found"

    e2e.run(scenario)
