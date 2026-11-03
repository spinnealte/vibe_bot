"""Сквозные проверки админки в приложении: настоящий сервер API, настоящая база, подставной Telegram.

Админ (owner или ADMIN_IDS) делает в приложении то же, что в «⚙️ Админке» бота: курсы и проведения со всеми
полями, справочники, архив, заказы и деньги, клиенты, листы ожидания, выгрузка. Правила - те же сервисы, поэтому
здесь проверяется в первую очередь граница: кто может войти, что принимается на вход, что уходит клиентам.
"""

import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from stubbot.db.enums import OrderStatus, ProfileStatus, SessionStatus
from stubbot.db.models import Advance, Client, CourseSession, Order, Program
from stubbot.utils.dates import local_today
from tests.e2e.conftest import E2E
from tests.e2e.harness import ADMIN_ID, OWNER_ID, RUB, Harness
from tests.e2e.web_client import WebApp

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _day(h: Harness, days: int) -> str:
    return (local_today(h.settings.timezone) + timedelta(days=days)).isoformat()


def test_admin_only_for_owner_and_admin_ids(e2e: E2E, tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        async with WebApp(h, tmp_path) as app:
            stranger = app.user(6001)
            assert (await stranger.get("/me"))["is_admin"] is False
            for method, path in (("GET", "/admin/summary"), ("GET", "/admin/specs"), ("GET", "/admin/e/crs"),
                                 ("POST", "/admin/e/lec"), ("GET", "/admin/orders"), ("GET", "/admin/clients?q=Ив"),
                                 ("POST", "/admin/export"), ("DELETE", "/admin/e/ses/1")):
                body = await stranger.call(method, path, {} if method != "GET" else None, expect=403)
                assert body["error"]["code"] == "forbidden"
            for admin_id in (OWNER_ID, ADMIN_ID):
                admin = app.user(admin_id)
                assert (await admin.get("/me"))["is_admin"] is True
                summary = await admin.get("/admin/summary")
                assert summary["applications"] is True and summary["afisha"] == 5
                assert summary["orders"]["inv"] == 0 and summary["dictionaries"]["spec"] == 3
            specs = await app.user(OWNER_ID).get("/admin/specs")
            assert [f["name"] for f in specs["kinds"]["ses"]["fields"]][:3] == ["program_id", "days", "format"]
            venue = next(f for f in specs["kinds"]["ses"]["fields"] if f["name"] == "venue_id")
            assert venue["when"] == {"field": "format", "values": ["offline", "hybrid"]}

    e2e.run(scenario)


def test_catalog_from_the_app(e2e: E2E, tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        c = h.catalog
        h.tg.started.add(OWNER_ID)
        async with WebApp(h, tmp_path) as app:
            admin = app.user(OWNER_ID)

            # Справочники: ФИО - по правилам бота, названия специальностей не повторяются.
            error = await admin.post("/admin/e/lec", {"values": {"full_name": "Иванов"}}, expect=400)
            assert error["error"]["field"] == "full_name"
            lecturer = (await admin.post("/admin/e/lec", {"values": {
                "full_name": "Иванов Сергей Петрович", "regalia": "к.м.н.",
                "bio_html": "<div><b>Хирург</b></div><div>Стаж 20 лет</div><script>alert(1)</script>"}}))["item"]
            assert lecturer["values"]["bio_html"] == "<b>Хирург</b>\nСтаж 20 лет"
            await admin.post("/admin/e/spec", {"values": {"title": "Хирургия"}}, expect=409)
            spec = (await admin.post("/admin/e/spec", {"values": {"title": "Пародонтология"}}))["item"]
            spec = (await admin.post(f"/admin/e/spec/{spec['id']}/archive", {"archived": True}))["item"]
            assert spec["active"] is False

            # Обложка: только картинка; file_id - с подписью сервера, чужой file_id не подставить.
            await admin.upload("/admin/upload/photo", "cover.pdf", b"%PDF-1.4", expect=400)
            photo = (await admin.upload("/admin/upload/photo", "cover.png", PNG))["photo"]
            uploaded = [m for m in h.tg.chats[OWNER_ID] if m.photo == photo["file_id"]]
            assert uploaded and uploaded[0].deleted  # сообщение с картинкой бот сразу удалил
            program_file = await admin.upload("/admin/upload/program", "Программа.txt",
                                              "День 1\nТеория <b>и</b> практика".encode())
            assert program_file["html"] == "День 1<br>Теория &lt;b&gt;и&lt;/b&gt; практика"

            course = {
                "title": "  Синус-лифтинг   ", "specialties": [c.specialties[0].id], "lecturers": [lecturer["id"]],
                "default_format": "offline", "program_html": "<div><b>День 1</b></div><div>Теория</div>",
                "cover_file_id": {"file_id": "requisites-secret", "token": "0" * 40},
            }
            error = await admin.post("/admin/e/crs/preview", {"values": course}, expect=400)
            assert error["error"]["field"] == "cover_file_id"
            course["cover_file_id"] = photo
            preview = (await admin.post("/admin/e/crs/preview", {"values": course}))["course"]
            assert preview["title"] == "Синус-лифтинг" and preview["program_html"] == "<b>День 1</b><br>Теория"
            created = (await admin.post("/admin/e/crs", {"values": course}))["item"]
            saved = await h.scalar(select(Program).where(Program.id == created["id"]))
            assert saved.program_html == "<b>День 1</b>\nТеория" and saved.cover_file_id == photo["file_id"]
            response = await app.client.get(photo["url"])  # картинка - из кэша, без похода в Telegram
            assert response.status == 200 and await response.read() == PNG
            assert any(i["id"] == created["id"] for i in (await admin.get("/admin/e/crs"))["items"])

            # Проведение: дни, тарифы, «запись до» - те же проверки, что в мастере бота.
            new = await admin.get("/admin/e/ses/new")
            assert any(item["id"] == created["id"] for item in new["courses"])
            venue_id = new["options"]["venue_id"][0]["id"]
            session = {
                "program_id": created["id"], "days": [{"date": _day(h, 20), "start": "10:00", "end": "18:00"},
                                                      {"date": _day(h, 21), "start": "10:00", "end": "17:00"}],
                "format": "offline", "venue_id": venue_id, "capacity": 8, "status": "registration_open",
                "prices": [{"kind": "group", "label": "Вдвоём", "amount": 50_000 * RUB},
                           {"kind": "full", "label": "Полный курс", "amount": 30_000 * RUB}],
                "registration_deadline": f"{_day(h, 18)}T18:00", "online_url": "https://example.com/stream",
            }
            bad = {**session, "days": [{"date": _day(h, 0), "start": "10:00", "end": "18:00"}]}
            assert (await admin.post("/admin/e/ses/preview", {"values": bad}, expect=400))["error"]["field"] == "days"
            bad = {**session, "days": [{"date": _day(h, 20), "start": "18:00", "end": "10:00"}]}
            await admin.post("/admin/e/ses/preview", {"values": bad}, expect=400)
            bad = {**session, "prices": [{"kind": "full", "label": "Курс", "amount": 12_345}]}  # не целые рубли
            assert (await admin.post("/admin/e/ses", {"values": bad}, expect=400))["error"]["field"] == "prices"
            card = (await admin.post("/admin/e/ses/preview", {"values": session}))["card"]
            assert [p["label"] for p in card["prices"]] == ["Полный курс", "Вдвоём"]  # порядок категорий
            item = (await admin.post("/admin/e/ses", {"values": session}))["item"]
            row = await h.scalar(select(CourseSession).where(CourseSession.id == item["id"]))
            assert row.online_url is None  # у очного формата ссылки на трансляцию нет - поле пропущено
            public = (await app.user(6101).get("/schedule"))["items"]
            assert any(i["id"] == item["id"] and i["seats_left"] == 8 for i in public)

            item = (await admin.patch(f"/admin/e/ses/{item['id']}", {"field": "capacity", "value": 12}))["item"]
            assert item["values"]["capacity"] == 12
            await admin.patch(f"/admin/e/ses/{item['id']}", {"field": "status", "value": "full"}, expect=400)
            item = (await admin.post(f"/admin/e/ses/{item['id']}/status", {"status": "full"}))["item"]
            assert item["session"]["status"] == "full"

            # Копия - всё, кроме дней; новые дни - и копия в афише.
            copy = await admin.get(f"/admin/e/ses/{item['id']}/copy")
            assert "days" not in copy["values"] and copy["values"]["status"] == "registration_open"
            values = {**copy["values"], "days": [{"date": _day(h, 40), "start": None, "end": None}]}
            twin = (await admin.post("/admin/e/ses", {"values": values}))["item"]
            assert twin["values"]["capacity"] == 12 and twin["values"]["days"][0]["start"] is None

            # Архив и удаление: курс с проведениями в афише не убрать, удалить - только из архива.
            error = await admin.post(f"/admin/e/crs/{created['id']}/archive", {"archived": True}, expect=409)
            assert error["error"]["code"] == "has_sessions"
            await admin.delete(f"/admin/e/ses/{twin['id']}", expect=409)
            archived = await admin.post(f"/admin/e/ses/{twin['id']}/archive", {"archived": True})
            assert archived["item"]["session"]["scope"] == "archived" and archived["item"]["editable"] is False
            await admin.patch(f"/admin/e/ses/{twin['id']}", {"field": "capacity", "value": 5}, expect=404)
            assert [s["id"] for s in (await admin.get("/admin/e/ses?scope=archived"))["items"]] == [twin["id"]]
            await admin.delete(f"/admin/e/ses/{twin['id']}")
            await admin.post(f"/admin/e/ses/{item['id']}/archive", {"archived": True})
            await admin.post(f"/admin/e/crs/{created['id']}/archive", {"archived": True})
            await admin.post(f"/admin/e/ses/{item['id']}/archive", {"archived": False}, expect=409)  # курс в архиве
            await admin.delete(f"/admin/e/crs/{created['id']}")
            assert await h.scalar(select(Program).where(Program.id == created["id"])) is None
            await admin.delete(f"/admin/e/crs/{c.program.id}", expect=409)  # не в архиве

    e2e.run(scenario)


def test_orders_money_and_clients(e2e: E2E, tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        c = h.catalog
        user_id = 6201
        h.tg.started.update({OWNER_ID, ADMIN_ID, user_id})
        h.pii.update({"+79990003311", "Иванова Мария Сергеевна"})
        async with WebApp(h, tmp_path) as app:
            admin, user = app.user(OWNER_ID), app.user(user_id)
            await user.register("+79990003311")
            order_id = (await user.post(f"/sessions/{c.main.id}/enroll", {"price_id": c.single.id}))[
                "application"]["order"]["id"]
            await user.get(f"/admin/orders/{order_id}", expect=403)

            rows = (await admin.get("/admin/orders?category=inv"))["items"]
            assert [r["id"] for r in rows] == [order_id] and rows[0]["payer_type"] is None
            await admin.get("/admin/orders?category=nope", expect=400)
            number = rows[0]["number"]
            assert (await admin.get(f"/admin/orders/find?number={number}"))["id"] == order_id
            await admin.get("/admin/orders/find?number=12", expect=400)

            # Счёт вне бота: отметить и снять.
            order = (await admin.post(f"/admin/orders/{order_id}/invoice-manual"))["order"]
            assert order["category"] == "sent" and order["invoice"]["manual"] is True
            await admin.post(f"/admin/orders/{order_id}/invoice-manual", expect=409)
            order = (await admin.delete(f"/admin/orders/{order_id}/invoice-manual"))["order"]
            assert order["category"] == "inv"

            # Счёт файлом через бота: подпись бота + подпись админа; второй раз тот же ключ не отправит.
            caption = await admin.get(f"/admin/orders/{order_id}/invoice-caption")
            assert str(number) in caption["html"]
            await admin.send(f"/admin/orders/{order_id}/invoice", "invoice-token-1", "Без файла", expect=400)
            sent = await admin.send(f"/admin/orders/{order_id}/invoice", "invoice-token-1",
                                    "<div><b>Спасибо!</b></div>", ("Счёт.pdf", b"%PDF-1.4 invoice"))
            assert sent["delivered"] is True and sent["order"]["invoice"]["manual"] is False
            invoice = h.tg.messages(user_id)[-1]
            assert invoice.document and str(number) in invoice.plain and invoice.html.endswith("<b>Спасибо!</b>")
            await admin.send(f"/admin/orders/{order_id}/invoice", "invoice-token-1", "",
                             ("Счёт.pdf", b"%PDF"), expect=409)

            # Оплата: больше остатка нельзя, двойная отметка не проходит, клиенту - «оплата получена».
            body = {"method": "cash", "amount": 50_000 * RUB, "token": "pay-token-1"}
            assert (await admin.post(f"/admin/orders/{order_id}/payment", body, expect=400)
                    )["error"]["code"] == "too_much"
            body["amount"] = 10_000 * RUB
            order = (await admin.post(f"/admin/orders/{order_id}/payment", body))["order"]
            assert order["status"] == "partially_paid" and order["left"]["amount"] == 35_000 * RUB
            assert (await admin.post(f"/admin/orders/{order_id}/payment", body, expect=409)
                    )["error"]["code"] == "duplicate"
            assert "10 000" in h.tg.messages(user_id)[-1].plain
            order = (await admin.post(f"/admin/orders/{order_id}/payment",
                                      {"method": "invoice", "full": True, "token": "pay-token-2"}))["order"]
            assert order["status"] == "paid" and order["actions"]["pay"] is False

            # Сообщение плательщику - от имени бота.
            report = await admin.send(f"/admin/orders/{order_id}/message", "message-token-1", "Ждём вас!")
            assert report["delivered"] and h.tg.messages(user_id)[-1].plain == "Ждём вас!"

            # Снятие брони оплаченного: деньги ждут решения - на аванс; клиенту - сообщения.
            order = (await admin.post(f"/admin/orders/{order_id}/remove"))["order"]
            assert order["status"] == "cancelled" and order["money_pending"]["amount"] == 45_000 * RUB
            order = (await admin.post(f"/admin/orders/{order_id}/credit"))["order"]
            assert order["money_pending"] is None and order["money_settled"] is True
            await admin.post(f"/admin/orders/{order_id}/refund", expect=409)  # распределять уже нечего
            assert (await h.scalar(select(Order).where(Order.id == order_id))).status is OrderStatus.CANCELLED

            # Новый заказ - зачесть аванс.
            second = (await user.post(f"/sessions/{c.small.id}/enroll", {"price_id": c.small_price.id}))[
                "application"]["order"]["id"]
            card = await admin.get(f"/admin/orders/{second}")
            assert card["actions"]["apply_advance"] is not None
            order = (await admin.post(f"/admin/orders/{second}/apply-advance"))["order"]
            assert order["paid"] is not None

            # Клиенты: поиск, карточка, правка аванса.
            await admin.get("/admin/clients?q=И", expect=400)
            found = (await admin.get("/admin/clients?q=иванова"))["items"]
            client_id = found[0]["id"]
            assert found[0]["phone_tail"] == "3311"
            assert (await admin.get("/admin/clients?q=3311"))["items"][0]["id"] == client_id
            client = await admin.get(f"/admin/clients/{client_id}")
            assert {o["id"] for o in client["orders"]} >= {second}
            balance = client["advance"]["amount"]
            change = {"amount": 5_000 * RUB, "reason": "подарок за отзыв", "token": "advance-token-1"}
            client = (await admin.post(f"/admin/clients/{client_id}/advance", change))["client"]
            assert client["advance"]["amount"] == balance + 5_000 * RUB
            await admin.post(f"/admin/clients/{client_id}/advance", change, expect=409)  # тот же ключ
            await admin.post(f"/admin/clients/{client_id}/advance",
                             {"amount": -10_000_000 * RUB, "reason": "ошибка", "token": "advance-token-2"},
                             expect=400)
            await admin.post(f"/admin/clients/{client_id}/advance",
                             {"amount": 100 * RUB, "reason": "", "token": "advance-token-3"}, expect=400)
            corrections = await h.scalars(select(Advance).where(Advance.client_id == client_id,
                                                                Advance.comment == "подарок за отзыв"))
            assert len(corrections) == 1
            report = await admin.send(f"/admin/clients/{client_id}/message", "message-token-2", "",
                                      ("photo.png", PNG))
            assert report["delivered"] and h.tg.messages(user_id)[-1].photo

            # Участники проведения и листы ожидания.
            participants = await admin.get(f"/admin/e/ses/{c.small.id}/participants")
            assert [p["id"] for p in participants["confirmed"]] == [client_id]
            await user.post(f"/programs/{c.program.id}/interest")
            lists = (await admin.get("/admin/waitlists"))["items"]
            assert lists == [{"program_id": c.program.id, "title": c.program.title, "count": 1}]
            waiting = await admin.get(f"/admin/waitlists/{c.program.id}")
            assert [row["id"] for row in waiting["items"]] == [client_id]
            assert (await admin.post(f"/admin/waitlists/{c.program.id}/invited"))["count"] == 1
            assert (await admin.get("/admin/waitlists"))["items"] == []

            # Выгрузка - файлом в чат админа с ботом.
            export = await admin.post("/admin/export")
            assert export["count"] >= 1
            file = h.tg.messages(OWNER_ID)[-1]
            assert file.document_name.startswith("clients_") and file.document_name.endswith(".csv")

            status = await h.scalar(select(CourseSession.status).where(CourseSession.id == c.main.id))
            assert status is SessionStatus.REGISTRATION_OPEN
            assert await h.scalar(select(Client.id).where(Client.telegram_id == user_id)) == client_id

    e2e.run(scenario)


def test_requisites_reach_the_admin_chat(e2e: E2E, tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        c = h.catalog
        h.tg.started.update({OWNER_ID, ADMIN_ID})
        async with WebApp(h, tmp_path) as app:
            admin, user = app.user(ADMIN_ID), app.user(6301)
            await user.register("+79990004411")
            order_id = (await user.post(f"/sessions/{c.main.id}/enroll", {"price_id": c.single.id}))[
                "application"]["order"]["id"]
            await admin.post(f"/admin/orders/{order_id}/requisites", expect=409)  # реквизитов ещё нет
            await user.post(f"/orders/{order_id}/payer", {"type": "organization"})
            await user.upload(f"/orders/{order_id}/requisites", "Реквизиты.pdf", b"%PDF-1.4 req")
            before = len(h.tg.messages(ADMIN_ID))
            assert (await admin.post(f"/admin/orders/{order_id}/requisites"))["message"]
            assert len(h.tg.messages(ADMIN_ID)) == before + 1 and h.tg.messages(ADMIN_ID)[-1].document
            assert json.dumps(await admin.get(f"/admin/orders/{order_id}"), ensure_ascii=False).count("Реквизиты.pdf")

    e2e.run(scenario)


def test_full_client_list_pages_sorts_and_filters(e2e: E2E, tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        c = h.catalog
        h.tg.started.update({OWNER_ID, ADMIN_ID})
        names = [f"Тестов{chr(0x430 + i)} Клиент Номер" for i in range(17)]  # «Тестова…», «Тестовб…» - по алфавиту
        async with h.factory() as session, session.begin():
            for i, name in enumerate(reversed(names)):
                session.add(Client(telegram_id=7_000 + i, full_name=name, phone=f"+7999555{i:04d}",
                                   referral_code=f"list{i:04d}", is_bot_blocked=i == 0,
                                   profile_status=ProfileStatus.COMPLETED if i % 2 else ProfileStatus.NEW))
        async with WebApp(h, tmp_path) as app:
            admin, user = app.user(OWNER_ID), app.user(6401)
            await admin.get("/admin/summary")  # клиент-админ заводится первым запросом - раньше записанной
            await user.get("/admin/clients/list", expect=403)
            await user.register("+79990006611", "Записанная Анна Петровна")
            await user.post(f"/sessions/{c.main.id}/enroll", {"price_id": c.single.id})

            first = await admin.get("/admin/clients/list")
            total = first["total"]
            assert first["per_page"] == 15 and len(first["items"]) == 15 and first["pages"] == -(-total // 15)
            assert first["items"][0]["name"] == "Записанная Анна Петровна"  # «Новые» - последний пришедший сверху
            assert first["items"][0]["active"] == 1 and first["items"][0]["profile_done"] is True
            last = await admin.get(f"/admin/clients/list?page={first['pages']}")
            assert len(last["items"]) == total - 15 * (first["pages"] - 1)
            assert (await admin.get("/admin/clients/list?page=999"))["page"] == first["pages"]  # за концом - последняя

            by_name = await admin.get("/admin/clients/list?sort=name")
            listed = [row["name"] for row in by_name["items"]]
            assert listed == sorted(listed, key=str.lower)
            old = await admin.get("/admin/clients/list?sort=old")
            assert old["items"][0]["id"] < old["items"][-1]["id"]

            enrolled = await admin.get("/admin/clients/list?filter=enrolled")
            assert [row["name"] for row in enrolled["items"]] == ["Записанная Анна Петровна"]
            blocked = await admin.get("/admin/clients/list?filter=blocked")
            assert [row["name"] for row in blocked["items"]] == [names[-1]] and blocked["items"][0]["blocked"]
            profile = await admin.get("/admin/clients/list?filter=profile")
            assert all(row["profile_done"] for row in profile["items"])

            found = await admin.get("/admin/clients/list?q=" + "тестовб")
            assert [row["name"] for row in found["items"]] == [names[1]]
            assert (await admin.get("/admin/clients/list?q=5550003"))["items"][0]["phone_tail"] == "0003"
            await admin.get("/admin/clients/list?q=Т", expect=400)
            await admin.get("/admin/clients/list?sort=random", expect=400)
            await admin.get("/admin/clients/list?page=abc", expect=400)

    e2e.run(scenario)
