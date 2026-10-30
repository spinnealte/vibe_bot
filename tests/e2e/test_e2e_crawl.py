"""Обход всех кнопок: бот сам нажимает каждую кнопку, до которой может дойти человек, - в админке на полном наборе
демо-данных (scripts/seed_full_demo.py: 120 клиентов, больше 100 заказов во всех состояниях, аванс, листы ожидания)
и у клиента. Проверяется, что ни один экран не падает и каждое сообщение принял бы Telegram."""

from datetime import UTC, datetime

from scripts import seed_full_demo
from stubbot.tg import texts
from tests.e2e.harness import TZ, Harness, Person, crawl, fragment

PICK_HINT = fragment(texts.PICK_HINT)


async def _open_first_row(person: Person) -> None:
    """Пронумерованный список (picker) - открыть первую строку, как написал бы админ «1»."""
    if PICK_HINT in (person.last.plain or ""):
        await person.send("1")


def test_crawl_admin_on_demo_data(e2e) -> None:
    async def scenario(h: Harness) -> None:
        async with h.factory() as session, session.begin():
            await seed_full_demo.create(session, datetime.now(UTC), TZ)
        owner = h.owner()
        await owner.start()
        await owner.send(texts.BTN_ADMIN)
        # «Удалить навсегда» не нажимаем, чтобы дойти до остальных экранов с данными (его проверяют сценарии).
        async def menu(person: Person) -> None:
            await person.send(texts.BTN_ADMIN)

        clicked = await crawl(owner, max_clicks=800, skip=lambda text, data: ":purge:" in data,
                              after_click=_open_first_row, restart=menu)
        prefixes = {data.split(":")[0] for data in clicked}
        assert {"adm", "dic", "aord", "acl"} <= prefixes, prefixes
        assert len(clicked) > 50, len(clicked)
        # Админка работает и после обхода.
        await owner.send(texts.BTN_ADMIN)
        assert owner.sees(texts.ADMIN_MENU)

    e2e.run(scenario)


def test_crawl_client(e2e) -> None:
    async def scenario(h: Harness) -> None:
        owner, admin = h.owner(), h.admin()
        await owner.start()
        await admin.start()
        anna = h.person(9001, "Анна")
        await anna.register("+79990009001", "Обходова Анна Ивановна", ads=True)
        await anna.enroll("Имплантация", "Один участник")
        await anna.click(texts.BTN_PAY_INDIVIDUAL)
        await anna.enroll("Пародонтология")
        await anna.enroll("Ортодонтия")
        await anna.click(texts.BTN_PAY_ORGANIZATION)
        await anna.send_document("реквизиты.pdf")
        await anna.enroll("Гигиена")
        async def menu(person: Person) -> None:
            for start in (texts.BTN_CABINET, texts.BTN_SCHEDULE, texts.BTN_ABOUT):
                await person.send(start)

        clicked = await crawl(anna, max_clicks=300, restart=menu)
        assert {data.split(":")[0] for data in clicked} >= {"cab", "edit", "myapp", "sch", "pay", "order"}, clicked
        # Гость без регистрации - то же самое.
        guest = h.person(9002, "Гость")
        await guest.start()
        await crawl(guest, max_clicks=150, restart=menu)

    e2e.run(scenario)
