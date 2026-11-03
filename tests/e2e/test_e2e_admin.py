"""Сквозные сценарии админки: доступ только админам, заказы (оплата, сумма, счёт, бронь, аванс), клиенты
(поиск, карточка, сообщение, аванс), выгрузка CSV, афиша (архив, статус)."""

import csv
import io

from sqlalchemy import func, select

from stubbot.db.enums import (
    AdvanceReason,
    CancelChoice,
    EnrollmentStatus,
    InvoiceChannel,
    OrderStatus,
    PaymentMethod,
    PaymentState,
    SessionStatus,
)
from stubbot.db.models import Advance, Client, CourseSession, Enrollment, Order, Payment, Program, ProgramInterest
from stubbot.services.admin_dictionaries import DictKind
from stubbot.tg import texts
from stubbot.tg.callbacks import (
    AdminAction,
    AdminCb,
    ClientAdminAction,
    ClientAdminCb,
    DictAction,
    DictCb,
    OrderAdminAction,
    OrderAdminCb,
    PickCb,
)
from stubbot.utils.money import format_rub
from tests.e2e.harness import ADMIN_ID, OWNER_ID, RUB, Harness, Person, fragment, stem

TABLES = (Order, Payment, Advance, Enrollment, CourseSession, Program, ProgramInterest, Client)


async def _counts(h: Harness) -> dict[str, int]:
    return {model.__tablename__: await h.scalar(select(func.count()).select_from(model)) for model in TABLES}


async def _snapshot(h: Harness) -> list:
    """Всё, что могло бы поменять чужое нажатие: статусы заказов, записей, проведений, курсов, платежи."""
    rows = []
    for model, columns in ((Order, ("status", "total_amount", "invoice_sent_at", "payer_type")),
                           (Enrollment, ("status", "payment_status")),
                           (CourseSession, ("status", "archived_at")),
                           (Program, ("is_published", "archived_at", "title")),
                           (Payment, ("state", "amount"))):
        for row in await h.scalars(select(model).order_by(model.id)):
            rows.append((model.__tablename__, row.id, *(getattr(row, c) for c in columns)))
    return rows


async def _client_with_order(h: Harness, user_id: int = 7001, title: str = "Имплантация",
                             price: str | None = "Один участник") -> tuple[Person, Order]:
    person = h.person(user_id, "Клиент")
    await person.register(f"+7999000{user_id}", f"Клиентова Анна {['Ивановна', 'Петровна', 'Олеговна'][user_id % 3]}")
    await person.enroll(title, price)
    order = await h.scalar(select(Order).order_by(Order.id.desc()))
    return person, order


async def _admins(h: Harness) -> tuple[Person, Person]:
    owner, admin = h.owner(), h.admin()
    await owner.start()
    await admin.start()
    return owner, admin


async def _open_order(admin: Person, order: Order) -> None:
    """Карточка заказа - кнопкой «Открыть заказ» под уведомлением о записи."""
    notice = next(m for m in reversed(admin.messages()) if f"№{order.number}" in (m.plain or "")
                  and any(b.text == texts.BTN_ADMIN_OPEN_ORDER for b in m.buttons()))
    await admin.click(texts.BTN_ADMIN_OPEN_ORDER, notice)
    assert f"№{order.number}" in admin.last_text()


# --- Доступ ------------------------------------------------------------------------------------------------------

def test_admin_buttons_do_nothing_for_others(e2e) -> None:
    """Не-админ, подделав данные кнопок админки (они видны в логах, их легко угадать), не получает ни экрана,
    ни файла и ничего не меняет в базе."""

    async def scenario(h: Harness) -> None:
        await _admins(h)
        client, order = await _client_with_order(h)
        stranger = h.person(7099, "Чужой")
        await stranger.register("+79990007099", "Чужаков Пётр Петрович")
        before_counts, before = await _counts(h), await _snapshot(h)
        cat = h.catalog
        forged = [AdminCb(action=action).pack() for action in AdminAction]
        forged += [OrderAdminCb(action=action, order_id=order.id, value=value).pack()
                   for action in OrderAdminAction for value in ("", "inv", "cash", "n")]
        forged += [ClientAdminCb(action=action, item_id=item, value="x").pack()
                   for action in ClientAdminAction for item in (1, cat.main.id, cat.program.id)]
        forged += [DictCb(kind=kind, action=action, item_id=item, value=value).pack()
                   for kind in DictKind for action in DictAction
                   for item, value in ((cat.main.id, ""), (cat.program.id, "a"), (cat.main.id, SessionStatus.CANCELLED.value))]
        forged += [PickCb(token="deadbeef", page=0).pack()]
        mark = stranger.mark()
        for data in forged:
            if len(data.encode()) > 64:
                continue
            await stranger.press(data)
            assert stranger.alert() == texts.UNKNOWN_CALLBACK, (data, stranger.alert())
        await stranger.send(texts.BTN_ADMIN)  # кнопки меню нет, но текст набрать можно
        await stranger.send("1")  # номер строки из списка админки
        await stranger.send("100001")
        shown = [m for m in stranger.since(mark)]
        assert all(m.document is None for m in shown)  # выгрузка клиентов не пришла
        assert all(t not in (m.plain or "") for m in shown for t in ("№100001", "Клиентова", fragment(texts.ADMIN_MENU)))
        assert await _counts(h) == before_counts and await _snapshot(h) == before
        assert texts.BTN_ADMIN not in stranger.reply_keyboard()
        assert texts.BTN_ADMIN in h.owner().reply_keyboard()

    e2e.run(scenario)


def test_admin_sees_admin_button_and_edit_under_course(e2e) -> None:
    async def scenario(h: Harness) -> None:
        owner, admin = await _admins(h)
        for person in (owner, admin):
            assert texts.BTN_ADMIN in person.reply_keyboard()
            await person.open_schedule()
            assert person.has_button(texts.BTN_COURSE_EDIT, person.last)
            await person.click(texts.BTN_COURSE_EDIT)
            assert "Гигиена" in person.last_text()  # карточка этого проведения в админке
            await person.send(texts.BTN_ADMIN)
            assert person.sees(texts.ADMIN_MENU)

    e2e.run(scenario)


# --- Заказы ------------------------------------------------------------------------------------------------------

def test_mark_payment_partial_then_full(e2e) -> None:
    async def scenario(h: Harness) -> None:
        owner, admin = await _admins(h)
        client, order = await _client_with_order(h)
        await _open_order(admin, order)
        await admin.click(texts.BTN_ADMIN_ORDER_PAY)
        for bad, error in (("abc", texts.ADMIN_ORDER_AMOUNT_INVALID), ("0", texts.ADMIN_ORDER_AMOUNT_INVALID),
                           ("-5000", texts.ADMIN_ORDER_AMOUNT_INVALID),
                           ("50000", texts.ADMIN_ORDER_AMOUNT_TOO_MUCH)):
            await admin.send(bad)
            assert admin.sees(error), bad
        await admin.send("10 000")
        mark = client.mark()
        method_buttons = admin.find_button(texts.BTN_PAYMENT_METHODS["invoice"])  # экран способа - запомним
        await admin.click(texts.BTN_PAYMENT_METHODS["cash"])
        assert admin.sees(texts.ADMIN_ORDER_PAYMENT_SAVED.format(amount=format_rub(10_000 * RUB)))
        assert any(fragment(texts.PAYMENT_RECEIVED) in m.plain for m in client.since(mark))  # клиенту сообщили
        order_row = await h.scalar(select(Order))
        assert order_row.status is OrderStatus.PARTIALLY_PAID
        assert (await h.scalar(select(Enrollment))).status is EnrollmentStatus.CONFIRMED  # место закреплено
        # Повтор со старого экрана способа - не второй платёж (ввод суммы уже закончен - кнопка неактуальна).
        await admin.press(method_buttons[1].callback_data, method_buttons[0])
        assert admin.alert() in (texts.ADMIN_ORDER_EXPIRED, texts.UNKNOWN_CALLBACK)
        assert await h.scalar(select(func.count()).select_from(Payment)) == 1
        payment = await h.scalar(select(Payment))
        assert payment.created_by_telegram_id == ADMIN_ID and payment.method is PaymentMethod.CASH
        await admin.click(texts.BTN_ADMIN_ORDER_PAY)
        await admin.click(texts.BTN_ADMIN_ORDER_PAY_FULL.format(amount=format_rub(35_000 * RUB)))
        await admin.click(texts.BTN_PAYMENT_METHODS["invoice"])
        assert (await h.scalar(select(Order))).status is OrderStatus.PAID
        # Оплачен - «Отметить оплату» больше нет, подделанная кнопка не срабатывает.
        assert not admin.has_button(texts.BTN_ADMIN_ORDER_PAY, admin.last)
        await admin.press(OrderAdminCb(action=OrderAdminAction.PAY, order_id=order.id).pack())
        assert admin.alert() == texts.ADMIN_ORDER_UNAVAILABLE

    e2e.run(scenario)


def test_two_admins_mark_the_same_money(e2e) -> None:
    """Владелец и админ одновременно отмечают остаток: второй не проведёт переплату."""

    async def scenario(h: Harness) -> None:
        owner, admin = await _admins(h)
        client, order = await _client_with_order(h)
        for person in (owner, admin):
            await _open_order(person, order)
            await person.click(texts.BTN_ADMIN_ORDER_PAY)
            await person.click(texts.BTN_ADMIN_ORDER_PAY_FULL.format(amount=format_rub(45_000 * RUB)))
        await owner.click(texts.BTN_PAYMENT_METHODS["card"])
        await admin.click(texts.BTN_PAYMENT_METHODS["cash"])
        assert admin.alert() == texts.ADMIN_ORDER_UNAVAILABLE
        paid = await h.scalar(select(func.sum(Payment.amount)).where(Payment.state == PaymentState.SUCCEEDED))
        assert paid == 45_000 * RUB

    e2e.run(scenario)


def test_total_for_a_course_without_price(e2e) -> None:
    async def scenario(h: Harness) -> None:
        from datetime import UTC, datetime, timedelta

        from stubbot.tg.reminders import PaymentReminders

        owner, admin = await _admins(h)
        client, order = await _client_with_order(h, title="Ортодонтия", price=None)
        assert client.sees(texts.DUE_LINE_NO_AMOUNT)  # срок не обещаем, пока суммы нет (ревью 03.11.2026)
        reminders = PaymentReminders(h.bot, h.factory, h.settings)
        mark = client.mark()
        assert await reminders.tick(datetime.now(UTC) + timedelta(hours=80)) == 0  # ни «сутки», ни «просрочено»
        assert not client.since(mark)
        await _open_order(admin, order)
        await admin.click(texts.BTN_ADMIN_ORDER_TOTAL)
        await admin.send("тридцать тысяч")
        assert admin.sees(texts.ADMIN_ORDER_AMOUNT_INVALID)
        await admin.send("30000")
        assert admin.sees(texts.ADMIN_ORDER_TOTAL_SAVED.format(amount=format_rub(30_000 * RUB)))
        saved = await h.scalar(select(Order))
        assert saved.total_amount == 30_000 * RUB
        assert abs(saved.due_at - (datetime.now(UTC) + timedelta(hours=72))) < timedelta(minutes=1)  # 72 ч - отсюда
        assert await reminders.tick(datetime.now(UTC) + timedelta(hours=25)) == 1  # теперь напоминание идёт
        assert not admin.has_button(texts.BTN_ADMIN_ORDER_TOTAL, admin.last)  # указанную сумму не меняют
        # Теперь клиенту доступна онлайн-оплата.
        await client.send(texts.BTN_CABINET)
        await client.click(texts.BTN_MY_APPLICATIONS)
        await client.click(texts.BTN_ORDER_PAY)
        await client.click(texts.BTN_PAY_INDIVIDUAL)
        assert client.has_button(texts.BTN_PAY_ONLINE, client.last)

    e2e.run(scenario)


def test_send_invoice_and_manual_mark(e2e) -> None:
    async def scenario(h: Harness) -> None:
        owner, admin = await _admins(h)
        client, order = await _client_with_order(h)
        await _open_order(admin, order)
        await admin.click(texts.BTN_ADMIN_ORDER_INVOICE)
        await admin.send("Счёт во вложении")  # счёт - только файлом
        assert admin.sees(texts.ADMIN_ORDER_INVOICE_FILE_ONLY)
        invoice = await admin.send_document("Счёт 100001.pdf", caption="Оплатите, пожалуйста")
        preview = admin.last
        assert preview.document == invoice and "№100001" in preview.plain
        mark = client.mark()
        send = admin.find_button(texts.BTN_ADMIN_SEND)
        await admin.click(texts.BTN_ADMIN_SEND)
        [received] = [m for m in client.since(mark) if m.document]
        assert received.document == invoice and "Оплатите, пожалуйста" in received.plain
        await admin.press(send[1].callback_data, send[0])
        assert len([m for m in client.since(mark) if m.document]) == 1  # второе нажатие - не второй счёт
        order_row = await h.scalar(select(Order))
        assert order_row.invoice_channel is InvoiceChannel.BOT and order_row.invoice_sent_at is not None
        # Через бот - отметку не снять.
        await admin.press(OrderAdminCb(action=OrderAdminAction.INVOICE_UNDO, order_id=order.id).pack())
        assert admin.alert() == texts.ADMIN_ORDER_UNAVAILABLE

        second, order2 = await _client_with_order(h, 7002, "Пародонтология", None)
        await _open_order(admin, order2)
        await admin.click(texts.BTN_ADMIN_ORDER_INVOICE_MANUAL)
        assert (await h.scalar(select(Order).where(Order.id == order2.id))).invoice_channel is InvoiceChannel.MANUAL
        await admin.press(OrderAdminCb(action=OrderAdminAction.INVOICE_MANUAL, order_id=order2.id).pack())
        assert admin.alert() == texts.ADMIN_ORDER_INVOICE_ALREADY
        await admin.click(texts.BTN_ADMIN_ORDER_INVOICE_UNDO)
        assert (await h.scalar(select(Order).where(Order.id == order2.id))).invoice_sent_at is None

    e2e.run(scenario)


def test_remove_booking(e2e) -> None:
    async def scenario(h: Harness) -> None:
        owner, admin = await _admins(h)
        client, order = await _client_with_order(h)
        await _open_order(admin, order)
        await admin.click(texts.BTN_ADMIN_ORDER_REMOVE)
        await admin.click(texts.BTN_ADMIN_ORDER_NO)
        assert (await h.scalar(select(Order))).status is OrderStatus.AWAITING_PAYMENT
        await admin.click(texts.BTN_ADMIN_ORDER_REMOVE)
        mark = client.mark()
        await admin.click(texts.BTN_ADMIN_ORDER_REMOVE_YES)
        assert admin.sees(texts.ADMIN_ORDER_REMOVED)
        assert (await h.scalar(select(Order))).status is OrderStatus.CANCELLED
        assert (await h.scalar(select(Enrollment))).status is EnrollmentStatus.CANCELLED_BY_ADMIN
        assert client.since(mark)  # клиенту сообщили
        assert await h.scalar(select(func.count()).select_from(ProgramInterest)) == 1  # в лист ожидания курса
        await admin.press(OrderAdminCb(action=OrderAdminAction.REMOVE_OK, order_id=order.id).pack())
        assert admin.alert() == texts.ADMIN_ORDER_UNAVAILABLE  # второй раз - нечего снимать

    e2e.run(scenario)


def test_money_after_cancel_goes_to_advance_and_back_into_an_order(e2e) -> None:
    async def scenario(h: Harness) -> None:
        owner, admin = await _admins(h)
        client, order = await _client_with_order(h)
        await _open_order(admin, order)
        await admin.click(texts.BTN_ADMIN_ORDER_PAY)
        await admin.send("45000")
        await admin.click(texts.BTN_PAYMENT_METHODS["invoice"])
        # Клиент отменяет оплаченную запись: «оставить на аванс».
        await client.send(texts.BTN_CABINET)
        await client.click(texts.BTN_MY_APPLICATIONS)
        await client.click(texts.BTN_CANCEL_APPLICATION)
        await client.click(texts.BTN_CANCEL_ADVANCE)
        assert (await h.scalar(select(Order))).cancel_choice is CancelChoice.ADVANCE
        # Админ: «Деньги после отмены» → зачислить на аванс.
        await admin.send(texts.BTN_ADMIN)
        await admin.click(texts.BTN_ADMIN_ORDERS)
        await admin.click(stem(texts.BTN_ADMIN_ORDER_CATEGORIES["money"]))
        await admin.send("1")
        rub45 = format_rub(45_000 * RUB)
        await admin.click(texts.BTN_ADMIN_ORDER_CREDIT.format(amount=rub45))
        await admin.click(texts.BTN_ADMIN_ORDER_CREDIT_YES)
        assert admin.sees(texts.ADMIN_ORDER_CREDITED.format(amount=rub45, balance=rub45))
        await admin.press(OrderAdminCb(action=OrderAdminAction.CREDIT_OK, order_id=order.id).pack())
        assert admin.alert() == texts.ADMIN_ORDER_UNAVAILABLE  # второй раз не зачислить
        balance = await h.scalar(select(func.sum(Advance.amount)))
        assert balance == 45_000 * RUB
        await client.send(texts.BTN_CABINET)
        assert rub45 in client.last_text()  # аванс виден в кабинете
        # Новая запись - аванс зачитывается в неё.
        await client.enroll("Пародонтология")
        new_order = await h.scalar(select(Order).order_by(Order.id.desc()))
        await _open_order(admin, new_order)
        await admin.click(texts.BTN_ADMIN_ORDER_APPLY.format(amount=format_rub(30_000 * RUB)))
        await admin.click(texts.BTN_ADMIN_ORDER_APPLY_YES)
        assert (await h.scalar(select(Order).where(Order.id == new_order.id))).status is OrderStatus.PAID
        assert await h.scalar(select(func.sum(Advance.amount))) == 15_000 * RUB
        reasons = await h.scalars(select(Advance.reason).order_by(Advance.id))
        assert reasons == [AdvanceReason.CANCELLED_ORDER, AdvanceReason.APPLIED_TO_ORDER]

    e2e.run(scenario)


def test_order_menu_sections_and_search(e2e) -> None:
    async def scenario(h: Harness) -> None:
        owner, admin = await _admins(h)
        client, order = await _client_with_order(h)
        await admin.send(texts.BTN_ADMIN)
        await admin.click(texts.BTN_ADMIN_ORDERS)
        for key in ("inv", "sent", "late", "part", "paid", "money"):
            await admin.click(stem(texts.BTN_ADMIN_ORDER_CATEGORIES[key]))
            await admin.click(texts.BTN_ADMIN_TO_ORDERS)
        await admin.click(texts.BTN_ADMIN_ORDER_FIND)
        for bad in ("12345", "abc", "1000001"):
            await admin.send(bad)
            assert admin.sees(texts.ADMIN_ORDER_BAD_NUMBER)
        await admin.send("999999")
        assert admin.sees(texts.ADMIN_ORDER_NOT_FOUND.format(number="999999"))
        await admin.send("№100001")
        assert "№100001" in admin.last_text() and admin.has_button(texts.BTN_ADMIN_ORDER_PAY, admin.last)
        await admin.press(OrderAdminCb(action=OrderAdminAction.NOOP).pack())  # счётчик «1/2» между стрелками
        assert admin.alert() is None

    e2e.run(scenario)


# --- Клиенты и выгрузка ------------------------------------------------------------------------------------------

def test_clients_search_card_message_and_advance(e2e) -> None:
    async def scenario(h: Harness) -> None:
        owner, admin = await _admins(h)
        client, order = await _client_with_order(h)
        await admin.send(texts.BTN_ADMIN)
        await admin.click(texts.BTN_ADMIN_CLIENTS)
        await admin.click(texts.BTN_ADMIN_CLIENT_FIND)
        await admin.send("К")  # слишком коротко
        assert admin.sees(texts.ADMIN_CLIENT_QUERY_SHORT)
        await admin.send("Клиентова")
        assert "Клиентова" in admin.last_text()
        await admin.send("1")
        assert "+79990007001" in admin.last_text()
        await admin.click(texts.BTN_ADMIN_CLIENT_WRITE)
        await admin.send("Здравствуйте! <b>Ждём</b> Вас на курсе")  # HTML от админа - текстом, не разметкой
        mark = client.mark()
        await admin.click(texts.BTN_ADMIN_SEND)
        [letter] = client.since(mark)
        assert "<b>Ждём</b>" in letter.plain
        await admin.click(texts.BTN_ADMIN_TO_CLIENT)
        await admin.click(texts.BTN_ADMIN_CLIENT_ADVANCE)
        for bad in ("5000", "+5000", "много"):
            await admin.send(bad)
        await admin.send("-5000 ошибка")  # аванс не уходит в минус
        assert admin.sees(texts.ADMIN_CLIENT_ADVANCE_NEGATIVE.format(balance=format_rub(0)))
        await admin.send("+5000 подарок за отзыв")
        yes = admin.find_button(texts.BTN_ADMIN_YES)
        await admin.click(texts.BTN_ADMIN_YES)
        await admin.press(yes[1].callback_data, yes[0])
        assert await h.scalar(select(func.sum(Advance.amount))) == 5_000 * RUB  # двойное «Да» - одна правка
        assert (await h.scalar(select(Advance))).created_by_telegram_id == ADMIN_ID

    e2e.run(scenario)


def test_export_csv_is_safe_for_excel(e2e) -> None:
    async def scenario(h: Harness) -> None:
        owner, admin = await _admins(h)
        evil = h.person(7010, "Хакер")
        await evil.register("+79990007010", "Формулов Иван Иванович",
                            optional={"city": "=HYPERLINK(\"http://evil\";\"Город\")", "workplace": "+cmd|' /C calc'!A0",
                                      "experience": "", "email": "-2+3@evil.ru"})
        await admin.send(texts.BTN_ADMIN)
        await admin.click(texts.BTN_ADMIN_EXPORT)
        [(chat, name, content)] = [f for f in h.tg.sent_files if f[0] == ADMIN_ID]
        assert name.endswith(".csv") and content.startswith(b"\xef\xbb\xbf")
        rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig")), delimiter=";"))
        cells = [cell for row in rows for cell in row]
        assert "'=HYPERLINK(\"http://evil\";\"Город\")" in cells  # формула стала текстом
        assert "'+cmd|' /C calc'!A0" in cells and "'-2+3@evil.ru" in cells
        assert not any(cell.startswith(("=", "@")) for cell in cells)
        assert "+79990007010" in cells  # телефон не испорчен
        await h.person(7011, "Обычный").start()
        assert len([f for f in h.tg.sent_files if f[0] == 7011]) == 0

    e2e.run(scenario)


# --- Афиша из админки --------------------------------------------------------------------------------------------

def test_archive_and_status_change_the_schedule(e2e) -> None:
    async def scenario(h: Harness) -> None:
        owner, admin = await _admins(h)
        guest = h.person(7020, "Гость")
        await guest.start()
        await admin.open_schedule()
        await admin.click(texts.BTN_COURSE_EDIT)  # «Гигиена»
        await admin.click(texts.BTN_ADMIN_ARCHIVE)
        session = await h.scalar(select(CourseSession).where(CourseSession.id == h.catalog.soon.id))
        assert session.archived_at is not None
        await guest.open_schedule()
        assert "Гигиена" not in guest.last_text()
        await admin.press(DictCb(kind=DictKind.SESSIONS, action=DictAction.RESTORE, item_id=h.catalog.soon.id).pack())
        await guest.open_schedule()
        assert "Гигиена" in guest.last_text()
        # Статус «Набор закрыт» - кнопки «Записаться» нет, подделанное нажатие - «запись закрыта».
        await admin.press(DictCb(kind=DictKind.SESSIONS, action=DictAction.STATUS, item_id=h.catalog.soon.id,
                                 value=SessionStatus.REGISTRATION_CLOSED.value).pack())
        assert (await h.scalar(select(CourseSession).where(CourseSession.id == h.catalog.soon.id))).status \
            is SessionStatus.REGISTRATION_CLOSED
        await guest.open_schedule()
        assert not guest.has_button(texts.BTN_APPLY, guest.last)

    e2e.run(scenario)
