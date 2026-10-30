"""Безопасность на живом боте: подстановка HTML в данные, подделанные кнопки клиента, мусорные данные кнопок,
ссылки /start, повтор старых кнопок, заблокированный бот. Каждое сообщение бота проверяет подставной Telegram
(разметка, длина, кнопки), каждый сценарий - что в логах нет ошибок и персональных данных."""

import pytest
from sqlalchemy import func, select

from stubbot.db.enums import EnrollmentStatus, OrderStatus, PayerType
from stubbot.db.models import Client, ClientSourceTouch, Enrollment, Order, Payment
from stubbot.tg import texts
from stubbot.tg.callbacks import (
    ApplyAction,
    ApplyCb,
    ApplyPriceCb,
    MyApplicationAction,
    MyApplicationCb,
    OrderAction,
    OrderCb,
    PayAction,
    PayCb,
    ScheduleAction,
    ScheduleCb,
    SelectGroup,
    SkipOptionalCb,
    ToggleCb,
)
from tests.e2e.harness import ADMIN_ID, OWNER_ID, Harness, crawl

HTML = '<b>жирный</b> <a href="https://evil.example">ссылка</a> & <script>alert(1)</script> &amp; </i>'


async def _admins(h: Harness):
    owner, admin = h.owner(), h.admin()
    await owner.start()
    await admin.start()
    return owner, admin


def test_html_in_user_data_is_shown_as_text_everywhere(e2e) -> None:
    """Город, место работы, e-mail и имя файла реквизитов - то, что человек пишет сам. Везде, где бот их
    показывает (кабинет, уведомления, карточки админки), это текст: разметка не ломается и не подменяется."""

    async def scenario(h: Harness) -> None:
        owner, admin = await _admins(h)
        evil = h.person(8001, "<b>Имя</b>", "evil_user", "<i>Фамилия")
        await evil.register("+79990008001", "Хакеров Иван Иванович",
                            optional={"city": "Город " + HTML, "workplace": "Клиника " + HTML, "experience": "",
                                      "email": "<b>x</b>@evil.ru"})
        client = await h.scalar(select(Client).where(Client.telegram_id == 8001))
        assert client.city.startswith("Город <b>")  # хранится как есть
        await evil.send(texts.BTN_CABINET)
        assert "Город <b>жирный</b>" in evil.last_text()  # и показывается как есть - текстом
        await evil.enroll("Имплантация", "Один участник")
        await evil.click(texts.BTN_PAY_ORGANIZATION)
        await evil.send_document('<b>реквизиты</b> & "ООО".pdf')
        order = await h.scalar(select(Order))
        # Карточка заказа, клиента, списки - у админа.
        await admin.send(texts.BTN_ADMIN)
        await admin.click(texts.BTN_ADMIN_CLIENTS)
        await admin.click(texts.BTN_ADMIN_CLIENT_FIND)
        await admin.send("Хакеров")
        await admin.send("1")
        assert "Клиника <b>жирный</b>" in admin.last_text() or "Город <b>жирный</b>" in admin.last_text() \
            or "Хакеров" in admin.last_text()
        await admin.send(texts.BTN_ADMIN)
        await admin.click(texts.BTN_ADMIN_ORDERS)
        await admin.click(texts.BTN_ADMIN_ORDER_FIND)
        await admin.send(str(order.number))
        await admin.click(texts.BTN_ADMIN_ORDER_REQUISITES)
        assert admin.last.document == order.requisites_file_id
        # Обход всех кнопок карточек - любые экраны с этими данными проходят проверку разметки Telegram.
        await crawl(admin, max_clicks=120, skip=lambda text, data: "rmok" in data or "advok" in data)

    e2e.run(scenario)


def test_forged_client_buttons_cannot_touch_someone_elses_records(e2e) -> None:
    async def scenario(h: Harness) -> None:
        await _admins(h)
        victim = h.person(8010, "Жертва")
        await victim.register("+79990008010", "Жертвова Анна Ивановна")
        await victim.enroll("Имплантация", "Один участник")
        order = await h.scalar(select(Order))
        enrollment = await h.scalar(select(Enrollment))
        thief = h.person(8011, "Вор")
        await thief.register("+79990008011", "Воров Пётр Петрович")
        forged = [OrderCb(action=action, order_id=order.id).pack() for action in OrderAction]
        forged += [MyApplicationCb(action=action, enrollment_id=enrollment.id).pack() for action in MyApplicationAction]
        forged += [PayCb(action=action, item_id=order.id).pack() for action in PayAction]
        mark = thief.mark()
        for data in forged:
            await thief.press(data)
        await thief.send_document("подмена.pdf")  # если бы бот ждал реквизиты по чужому заказу
        shown = " ".join(m.plain or "" for m in thief.since(mark))
        assert "Жертвова" not in shown and f"№{order.number}" not in shown
        order = await h.scalar(select(Order))
        assert order.payer_type is None and order.requisites_file_id is None and order.status is \
            OrderStatus.AWAITING_PAYMENT
        assert (await h.scalar(select(Enrollment))).status is EnrollmentStatus.APPLICATION
        assert await h.scalar(select(func.count()).select_from(Payment)) == 0

    e2e.run(scenario)


async def _press_all(h: Harness, user, garbage: list[str]) -> list[str]:
    """Нажать каждую; вернуть те, на которых бот упал (ошибка в логе, «Что-то пошло не так» или второй ответ
    на нажатие). Ошибки собираются списком - отчёт по всем, а не по первой."""
    failed = []
    for data in garbage:
        errors, violations = sum(r.levelno >= 40 for r in h.logs), len(h.tg.violations)
        await user.press(data)
        if user.got_error() or sum(r.levelno >= 40 for r in h.logs) > errors or len(h.tg.violations) > violations:
            failed.append(data)
        del h.tg.violations[violations:]
        user.last_answer = None
    return failed


def test_garbage_button_data(e2e) -> None:
    """Данные кнопок приходят от клиента Telegram и могут быть любыми (до 64 байт). Бот не падает и не трогает
    данные: неизвестное - «кнопка неактуальна»."""

    async def scenario(h: Harness) -> None:
        await _admins(h)
        user = h.person(8020, "Тест")
        await user.register("+79990008020", "Тестов Тест Тестович")
        garbage = ["", "x", "pay", "pay:go", "pay:go:abc", "pay:zz:1", "sch:s:-1:-5:-9", "myapp:yes:abc:0",
                   "aprice:-1", "tgl:spec:999999", "tgl:xx:1", "skip:nope", "cform:zz", "adm:menu:1:2",
                   "∑" * 21, ":::::", "sch:prog:1:0:-100", "sch:apply:-1:0:0", "order:org:-5",
                   PayCb(action=PayAction.START, item_id=-1).pack(), ApplyCb(action=ApplyAction.SEND).pack(),
                   SkipOptionalCb(field="city").pack()]
        failed = await _press_all(h, user, garbage)
        assert not failed, f"бот упал на данных кнопок: {failed}"
        assert await h.scalar(select(func.count()).select_from(Order)) == 0

    e2e.run(scenario)


@pytest.mark.xfail(strict=True, reason="БАГ (найден 28.10.2026): число в кнопке больше bigint - ошибка БД вместо "
                                       "«кнопка неактуальна» (поля int в tg/callbacks.py без границ)")
def test_numbers_beyond_bigint_in_button_data(e2e) -> None:
    async def scenario(h: Harness) -> None:
        await _admins(h)
        user = h.person(8021, "Тест")
        await user.register("+79990008021", "Числов Тест Тестович")
        huge = 2**63  # на единицу больше bigint PostgreSQL
        garbage = [PayCb(action=PayAction.CHECK, item_id=huge).pack(),
                   PayCb(action=PayAction.START, item_id=-huge - 1).pack(),
                   PayCb(action=PayAction.FAKE_OK, item_id=huge).pack(),
                   OrderCb(action=OrderAction.INDIVIDUAL, order_id=huge).pack(),
                   "order:ind:" + "9" * 50,
                   MyApplicationCb(action=MyApplicationAction.CANCEL, enrollment_id=huge).pack(),
                   ScheduleCb(action=ScheduleAction.APPLY, item_id=huge).pack(),
                   ScheduleCb(action=ScheduleAction.PROGRAM, item_id=huge, page=huge).pack(),
                   ScheduleCb(action=ScheduleAction.SLIDE, index=huge).pack(),
                   ScheduleCb(action=ScheduleAction.NOTIFY, item_id=huge).pack(),
                   ToggleCb(group=SelectGroup.SPECIALTIES, item_id=huge).pack(),
                   ApplyPriceCb(price_id=huge).pack()]
        failed = await _press_all(h, user, garbage)
        h.allowed_errors.extend(["Ошибка при обработке", "ошибка DBAPIError", "ошибка DataError"])
        assert not failed, f"бот упал на данных кнопок: {failed}"

    e2e.run(scenario)


def test_start_links(e2e) -> None:
    async def scenario(h: Harness) -> None:
        inviter = h.person(8030, "Пригласивший")
        await inviter.register("+79990008030", "Приглашаев Иван Иванович")
        code = (await h.scalar(select(Client).where(Client.telegram_id == 8030))).referral_code
        friend = h.person(8031, "Друг")
        await friend.start(f"ref_{code}")
        friend_row = await h.scalar(select(Client).where(Client.telegram_id == 8031))
        assert friend_row.referred_by_client_id is not None and friend_row.start_param == f"ref_{code}"
        await inviter.start(f"ref_{code}")  # сам себя не пригласит
        assert (await h.scalar(select(Client).where(Client.telegram_id == 8030))).referred_by_client_id is None
        for index, payload in enumerate(["src_unknown", "inv_x", "<b>html</b>", "a" * 500, "ref_" + "z" * 60,
                                         "ref_../../etc", "src_' OR 1=1 --", "😀" * 30]):
            person = h.person(8040 + index, "Гость")
            await person.start(payload)
            assert person.sees(texts.WELCOME_NEW), payload
            row = await h.scalar(select(Client).where(Client.telegram_id == 8040 + index))
            assert row.start_param is None or len(row.start_param) <= 64
        touches = await h.scalar(select(func.count()).select_from(ClientSourceTouch))
        assert touches >= 1

    e2e.run(scenario)


def test_old_buttons_after_the_step_is_over(e2e) -> None:
    """Старые кнопки (сценарий уже закончен или начат другой) не повторяют действие."""

    async def scenario(h: Harness) -> None:
        await _admins(h)
        user = h.person(8050, "Тест")
        await user.register("+79990008050", "Повторов Иван Иванович")
        await user.open_schedule()
        await user.go_to_course("Имплантация")
        await user.click(texts.BTN_APPLY)
        await user.click("Один участник")
        send = user.find_button(texts.BTN_APPLY_SEND)
        await user.click(texts.BTN_APPLY_SEND)
        await user.press(send[1].callback_data, send[0])
        assert user.alert() == texts.UNKNOWN_CALLBACK
        assert await h.scalar(select(func.count()).select_from(Order)) == 1
        # Начал запись, ушёл в меню - кнопки записи больше не работают.
        await user.open_schedule()
        await user.go_to_course("Пародонтология")
        await user.click(texts.BTN_APPLY)
        old_send = user.find_button(texts.BTN_APPLY_SEND)
        await user.send(texts.BTN_CABINET)
        await user.press(old_send[1].callback_data, old_send[0])
        assert await h.scalar(select(func.count()).select_from(Order)) == 1
        # Отменил запись - кнопка «Да, отменить» второй раз ничего не делает.
        await user.send(texts.BTN_CABINET)
        await user.click(texts.BTN_MY_APPLICATIONS)
        await user.click(texts.BTN_CANCEL_APPLICATION)
        yes = user.find_button(texts.BTN_CANCEL_APPLICATION_YES)
        await user.click(texts.BTN_CANCEL_APPLICATION_YES)
        notices = len(h.tg.messages(OWNER_ID))
        await user.press(yes[1].callback_data, yes[0])
        assert user.alert() == texts.APPLICATION_CANNOT_CANCEL
        assert len(h.tg.messages(OWNER_ID)) == notices  # админам второй раз не сообщили

    e2e.run(scenario)


def test_blocked_bot_and_admin_who_never_started(e2e) -> None:
    async def scenario(h: Harness) -> None:
        owner = h.owner()
        await owner.start()  # второй админ бота не запускал - уведомление ему не доходит, запись от этого не страдает
        user = h.person(8060, "Тест")
        await user.register("+79990008060", "Блоков Иван Иванович")
        await user.enroll("Имплантация", "Один участник")
        assert user.sees(texts.APPLY_ENROLLED) and await h.scalar(select(func.count()).select_from(Order)) == 1
        assert any("№100001" in (m.plain or "") for m in h.tg.messages(OWNER_ID))
        assert not h.tg.messages(ADMIN_ID)
        # Клиент заблокировал бота: письмо об оплате не дошло - отметка в базе, бот работает дальше.
        await user.click(texts.BTN_PAY_INDIVIDUAL)
        await user.click(texts.BTN_PAY_ONLINE)
        payment = await h.scalar(select(Payment))
        h.tg.blocked.add(8060)
        h.gateway.finish(payment.provider_payment_id, success=True)
        from datetime import UTC, datetime, timedelta

        await h.payments.tick(datetime.now(UTC) + timedelta(minutes=2))
        assert (await h.scalar(select(Client).where(Client.telegram_id == 8060))).is_bot_blocked
        assert (await h.scalar(select(Order))).status is OrderStatus.PAID
        await user.send(texts.BTN_CABINET)  # снова пишет - значит, разблокировал
        assert not (await h.scalar(select(Client).where(Client.telegram_id == 8060))).is_bot_blocked

    e2e.run(scenario)


def test_bot_leaves_groups_and_stays_silent_there(e2e) -> None:
    async def scenario(h: Harness) -> None:
        from datetime import UTC, datetime

        from aiogram.types import Chat, ChatMemberLeft, ChatMemberMember, ChatMemberUpdated, Message, Update, User

        from tests.e2e.telegram_stub import BOT_ID

        owner, _ = await _admins(h)
        group = Chat(id=-100500, type="supergroup", title="Чат клиники")
        me = User(id=BOT_ID, is_bot=True, first_name="Бот")
        added = ChatMemberUpdated(chat=group, from_user=owner.user, date=datetime.now(UTC),
                                  old_chat_member=ChatMemberLeft(user=me), new_chat_member=ChatMemberMember(user=me))
        await h.feed(Update(update_id=h.next_update_id(), my_chat_member=added))
        assert h.tg.left_chats == [group.id]
        before = len(h.tg.requests)
        for text in ("/start", texts.BTN_ADMIN, texts.BTN_CABINET):  # даже от владельца - ни слова в группе
            await h.feed(Update(update_id=h.next_update_id(), message=Message(
                message_id=1, date=datetime.now(UTC), chat=group, from_user=owner.user, text=text)))
        assert len(h.tg.requests) == before
        assert await h.scalar(select(func.count()).select_from(Client)) == 2  # клиентов из группы не завели

    e2e.run(scenario)


def test_long_and_strange_input(e2e) -> None:
    async def scenario(h: Harness) -> None:
        user = h.person(8070, "Тест")
        await user.register("+79990008070", "Длиннов Иван Иванович")
        await user.send(texts.BTN_CABINET)
        await user.click(texts.BTN_EDIT_PROFILE)
        await user.click(texts.EDIT_FIELD_LABELS["city"])
        await user.send("Г" * 129)  # длиннее колонки - не сохраняем
        assert (await h.scalar(select(Client).where(Client.telegram_id == 8070))).city is None
        await user.send("Санкт-Петербург")
        await user.click(texts.BTN_EDIT_PROFILE)
        await user.click(texts.EDIT_FIELD_LABELS["workplace_raw"])
        await user.send("К" * 4000)  # длиннее колонки - нет
        await user.click(texts.BTN_EDIT_CANCEL)
        assert (await h.scalar(select(Client).where(Client.telegram_id == 8070))).workplace_raw is None
        await user.click(texts.BTN_EDIT_PROFILE)
        await user.click(texts.EDIT_FIELD_LABELS["email"])
        await user.send("a" * 250 + "@x.ru")
        await user.send("'OR'1'='1;DROP--@x.ru")
        row = await h.scalar(select(Client).where(Client.telegram_id == 8070))
        assert row.email == "'or'1'='1;drop--@x.ru"  # запросы параметризованы: это просто строка
        await user.click(texts.BTN_EDIT_PROFILE)
        await user.click(texts.EDIT_FIELD_LABELS["birth_date"])
        for bad in ("31.02.1990", "01.01.1900", "01.01.2030", "вчера"):
            await user.send(bad)
        await user.send("15.04.1990")
        await user.send(texts.BTN_SCHEDULE)
        await user.send("/unknown_command")
        await user.send("/start " + "x" * 3000)

    e2e.run(scenario)


@pytest.mark.xfail(strict=True, reason="БАГ (найден 28.10.2026): символ NUL (\\x00) в тексте - PostgreSQL отклоняет "
                                       "строку, бот отвечает «Что-то пошло не так»")
def test_nul_character_in_text(e2e) -> None:
    async def scenario(h: Harness) -> None:
        user = h.person(8071, "Тест")
        await user.register("+79990008071", "Нулев Иван Иванович")
        await user.send(texts.BTN_CABINET)
        await user.click(texts.BTN_EDIT_PROFILE)
        await user.click(texts.EDIT_FIELD_LABELS["city"])
        await user.send("Санкт-\x00Петербург")
        h.allowed_errors.extend(["Ошибка при обработке", "ошибка DBAPIError"])
        assert not user.got_error()
        assert (await h.scalar(select(Client).where(Client.telegram_id == 8071))).city == "Санкт-Петербург"

    e2e.run(scenario)
