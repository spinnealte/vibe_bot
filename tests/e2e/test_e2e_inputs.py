"""Неподходящий ввод на каждом шаге (стикер вместо текста, текст вместо кнопок), смена телефона и ФИО в кабинете,
отмена посреди записи, возврат денег, листы ожидания курсов в админке."""

from sqlalchemy import func, select

from stubbot.db.enums import AdvanceReason, OrderStatus, PaymentMethod
from stubbot.db.models import Advance, Client, Order, ProgramInterest
from stubbot.services.admin_orders import AdminOrderService
from stubbot.tg import texts
from stubbot.utils.money import format_rub
from tests.e2e.harness import ADMIN_ID, RUB, Harness, stem


async def _admins(h: Harness):
    owner, admin = h.owner(), h.admin()
    await owner.start()
    await admin.start()
    return owner, admin


def test_wrong_input_during_registration_and_enrollment(e2e) -> None:
    async def scenario(h: Harness) -> None:
        await _admins(h)
        user = h.person(9701, "Тест")
        h.pii.update({"+79990009701", "79990009701", "Вводов Иван Иванович", "Коллегова Анна Петровна"})
        await user.start()
        await user.send(texts.BTN_CABINET)
        await user.send_sticker_like()  # согласие - только кнопками
        await user.click(texts.CONSENT_FORM_PD)
        await user.click(texts.CONSENT_FORM_CONTINUE)
        await user.send_photo()  # телефон - только кнопкой «Поделиться номером»
        assert user.sees(texts.REG_PHONE_USE_BUTTON)
        await user.share_contact("+79990009701")
        await user.send("Вводов Иван Иванович")
        await user.send("ещё текст")  # подтверждение ФИО - кнопками
        assert user.sees(texts.REG_USE_BUTTONS)
        await user.click(texts.REG_NAME_OK)
        await user.click(h.catalog.specialties[0].title)
        await user.click(texts.REG_DONE_BUTTON)
        await user.click(h.catalog.positions[0].title)
        await user.click(texts.REG_DONE_BUTTON)
        await user.send(texts.BTN_CANCEL)  # из необязательных полей - выйти
        assert user.sees(texts.REG_OPTIONAL_CANCELLED)

        # Запись: текст вместо кнопок тарифа, стикер вместо ФИО коллеги, отмена посреди шагов.
        await user.open_schedule()
        await user.go_to_course("Имплантация")
        await user.click(texts.BTN_APPLY)
        await user.send("хочу дешевле")
        assert user.sees(texts.REG_USE_BUTTONS)
        await user.click("Для двух коллег")
        await user.send_sticker_like()
        assert user.sees(texts.REG_TEXT_ONLY)
        await user.click(texts.BTN_APPLY_CANCEL)
        assert user.sees(texts.APPLY_CANCELLED)
        assert await h.scalar(select(func.count()).select_from(Order)) == 0

        # Оплата онлайн с чеком не включена - e-mail не спрашивают; реквизиты юрлица - только файлом.
        await user.enroll("Пародонтология")
        await user.click(texts.BTN_PAY_ORGANIZATION)
        await user.send_sticker_like()
        assert user.sees(texts.REQUISITES_FILE_ONLY)

    e2e.run(scenario)


def test_cabinet_phone_and_name_change(e2e) -> None:
    async def scenario(h: Harness) -> None:
        user = h.person(9702, "Тест")
        await user.register("+79990009702", "Кабинетов Иван Иванович")
        h.pii.update({"+79990009703", "79990009703", "Кабинетов Пётр Иванович"})
        await user.send(texts.BTN_CABINET)
        await user.click(texts.BTN_EDIT_PROFILE)
        await user.click(texts.EDIT_FIELD_LABELS["phone"])
        await user.send("+79990009703")  # текстом нельзя
        assert user.sees(texts.REG_PHONE_USE_BUTTON)
        await user.share_contact("+79990009703", own=False)
        assert user.sees(texts.REG_PHONE_FOREIGN_CONTACT)
        await user.send(texts.BTN_CANCEL)
        assert user.sees(texts.EDIT_CANCELLED) and texts.BTN_SCHEDULE in user.reply_keyboard()
        await user.click(texts.BTN_EDIT_PROFILE)
        await user.click(texts.EDIT_FIELD_LABELS["phone"])
        await user.share_contact("+79990009703")
        assert (await h.scalar(select(Client).where(Client.telegram_id == 9702))).phone == "+79990009703"
        # ФИО: стикер, «Исправить», новое ФИО.
        await user.click(texts.BTN_EDIT_PROFILE)
        await user.click(texts.EDIT_FIELD_LABELS["name"])
        await user.send_sticker_like()
        assert user.sees(texts.REG_TEXT_ONLY)
        await user.send("Кабинетов Пётр Иванович")
        await user.send("текст")  # подтверждение - кнопками
        await user.click(texts.REG_NAME_FIX)
        await user.send("Кабинетов Пётр Иванович")
        await user.click(texts.REG_NAME_OK)
        assert (await h.scalar(select(Client).where(Client.telegram_id == 9702))).full_name == "Кабинетов Пётр Иванович"
        # Город: стикер вместо текста.
        await user.click(texts.BTN_EDIT_PROFILE)
        await user.click(texts.EDIT_FIELD_LABELS["city"])
        await user.send_sticker_like()
        assert user.sees(texts.REG_TEXT_ONLY)
        await user.click(texts.BTN_EDIT_CANCEL)

    e2e.run(scenario)


def test_refund_after_cancel_and_admin_wrong_input(e2e) -> None:
    async def scenario(h: Harness) -> None:
        owner, admin = await _admins(h)
        user = h.person(9703, "Тест")
        await user.register("+79990009704", "Возвратов Иван Иванович")
        await user.enroll("Имплантация", "Один участник")
        order = await h.scalar(select(Order))
        async with h.factory() as session, session.begin():
            await AdminOrderService(session, ADMIN_ID).mark_payment(order.id, 45_000 * RUB, PaymentMethod.CARD, "r1")
        await user.send(texts.BTN_CABINET)
        await user.click(texts.BTN_MY_APPLICATIONS)
        await user.click(texts.BTN_CANCEL_APPLICATION)
        await user.click(texts.BTN_CANCEL_REFUND)
        assert user.has_button(texts.BTN_CANCEL_VIA_MANAGER, user.last)  # возврат - через менеджера
        # Админ: заказы - ввод не тем, потом «Деньги вернули».
        await admin.send(texts.BTN_ADMIN)
        await admin.click(texts.BTN_ADMIN_ORDERS)
        await admin.click(texts.BTN_ADMIN_ORDER_FIND)
        await admin.send_sticker_like()
        assert admin.sees(texts.ADMIN_ORDER_BAD_NUMBER)
        await admin.send(str(order.number))
        await admin.click(texts.BTN_ADMIN_ORDER_REFUND)
        await admin.click(texts.BTN_ADMIN_ORDER_REFUND_YES)
        assert admin.sees(texts.ADMIN_ORDER_REFUNDED.format(amount=format_rub(45_000 * RUB)))
        reasons = await h.scalars(select(Advance.reason).order_by(Advance.id))
        assert reasons == [AdvanceReason.CANCELLED_ORDER, AdvanceReason.REFUND]
        assert await h.scalar(select(func.sum(Advance.amount))) == 0  # баланс не изменился
        assert (await h.scalar(select(Order))).status is OrderStatus.CANCELLED
        # Сумма заказа и оплата: стикер вместо суммы.
        other = h.person(9704, "Тест")
        await other.register("+79990009705", "Суммов Иван Иванович")
        await other.enroll("Ортодонтия")
        new_order = await h.scalar(select(Order).order_by(Order.id.desc()))
        await admin.send(texts.BTN_ADMIN)
        await admin.click(texts.BTN_ADMIN_ORDERS)
        await admin.click(texts.BTN_ADMIN_ORDER_FIND)
        await admin.send(str(new_order.number))
        await admin.click(texts.BTN_ADMIN_ORDER_TOTAL)
        await admin.send_sticker_like()
        assert admin.sees(texts.ADMIN_ORDER_AMOUNT_INVALID)
        # Список «1/2» между стрелками и номер строки, которой нет.
        await admin.send(texts.BTN_ADMIN)
        await admin.click(texts.BTN_ADMIN_ORDERS)
        await admin.click(stem(texts.BTN_ADMIN_ORDER_CATEGORIES["inv"]))
        await admin.send("99")
        await admin.send_sticker_like()

    e2e.run(scenario)


def test_course_waitlists_in_admin(e2e) -> None:
    async def scenario(h: Harness) -> None:
        owner, admin = await _admins(h)
        for index in range(3):
            user = h.person(9710 + index, "Тест")
            await user.register(f"+7999000971{index}", f"Ожидаев Иван {['Иванович', 'Петрович', 'Олегович'][index]}")
            await user.enroll("Имплантация", "Один участник")
            await user.send(texts.BTN_CABINET)
            await user.click(texts.BTN_MY_APPLICATIONS)
            await user.click(texts.BTN_CANCEL_APPLICATION)
            await user.click(texts.BTN_CANCEL_APPLICATION_YES)  # без оплаты - в лист ожидания курса
        assert await h.scalar(select(func.count()).select_from(ProgramInterest)) == 3
        await admin.send(texts.BTN_ADMIN)
        await admin.click(texts.BTN_ADMIN_CLIENTS)
        await admin.click(texts.BTN_ADMIN_WAITLISTS)
        await admin.send("1")
        assert "Ожидаев" in admin.last_text()
        # Тот же лист - из карточки курса.
        await admin.send(texts.BTN_ADMIN)
        await admin.click("Курсы")
        await admin.go_to_course("Имплантация")
        await admin.click(stem(texts.BTN_ADMIN_COURSE_WAITLIST))
        assert "Ожидаев" in admin.last_text()
        await admin.click(texts.BTN_ADMIN_INVITED)
        await admin.click(texts.BTN_ADMIN_YES)
        assert admin.sees(texts.ADMIN_INVITED_DONE.format(count=3))
        # Клиенты: поиск - стикер, аванс - стикер.
        await admin.send(texts.BTN_ADMIN)
        await admin.click(texts.BTN_ADMIN_CLIENTS)
        await admin.click(texts.BTN_ADMIN_CLIENT_FIND)
        await admin.send_sticker_like()
        assert admin.sees(texts.ADMIN_CLIENT_QUERY_SHORT)
        await admin.send("Ожидаев")
        await admin.send("1")
        await admin.click(texts.BTN_ADMIN_CLIENT_ADVANCE)
        await admin.send_sticker_like()
        assert admin.sees(texts.ADMIN_CLIENT_ADVANCE_INVALID)

    e2e.run(scenario)
