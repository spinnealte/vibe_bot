"""Напоминания об оплате (полная версия, этап 1, шаг 5): что и когда напоминать, письма, фоновая задача.
Без БД и Telegram."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramForbiddenError

from stubbot.db.enums import OrderStatus, PayerType
from stubbot.services.reminders import REMIND_AFTER, ReminderJob, ReminderKind, ReminderService
from stubbot.tg import admin_orders_views, render, texts
from stubbot.tg import reminders as reminders_module
from stubbot.tg.callbacks import OrderAdminAction, OrderAdminCb
from stubbot.tg.reminders import Letter, PaymentReminders, letters
from stubbot.utils.money import format_rub

TZ = "Europe/Moscow"
NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
TODAY = date(2026, 10, 22)
PAYER = SimpleNamespace(id=10, full_name="Петров Пётр Петрович", phone="+79990000010", telegram_id=111)


def _order(**kw) -> SimpleNamespace:
    fields = dict(id=77, number=100001, session_id=5, payer_client_id=PAYER.id, status=OrderStatus.AWAITING_PAYMENT,
                  payer_type=None, total_amount=8_000_000, due_at=NOW + timedelta(hours=48), invoice_sent_at=None,
                  payment_reminded_at=None, overdue_notified_at=None)
    return SimpleNamespace(**{**fields, **kw})


def _session() -> SimpleNamespace:
    return SimpleNamespace(id=5, title_override=None, program=SimpleNamespace(title="Имплантация <базовый>"),
                           start_date=date(2026, 11, 13), end_date=date(2026, 11, 14), days=[])


def _job(kind: ReminderKind = ReminderKind.DAY, participants: int = 1, payer=PAYER, **order) -> ReminderJob:
    return ReminderJob(kind, _order(**order), _session(), payer, participants)


def _settings(manager_url: str | None = "https://t.me/manager") -> SimpleNamespace:
    return SimpleNamespace(manager_url=manager_url, timezone=TZ, admin_chat_ids=[1, 2])


def _rows(markup) -> list[list[str]]:
    return [[b.text for b in row] for row in markup.inline_keyboard]


# --- Что и когда ---------------------------------------------------------------------------------------------------

class _Orders:
    def __init__(self, overdue=(), unreminded=()) -> None:
        self.overdue, self.unreminded = list(overdue), list(unreminded)
        self.calls: list = []

    async def lock_overdue(self, now, today, limit):
        self.calls.append(("overdue", now, today))
        return self.overdue

    async def lock_unreminded(self, created_before, now, today, limit):
        self.calls.append(("unreminded", created_before, now, today))
        return self.unreminded

    async def session_for_card(self, session_id):
        return _session()

    async def client(self, client_id):
        return PAYER

    async def participants(self, order_id):
        return [object(), object()]


def test_claim_marks_orders_before_sending() -> None:
    late, fresh = _order(id=1), _order(id=2)
    repo = _Orders(overdue=[late], unreminded=[fresh])
    service = ReminderService.__new__(ReminderService)
    service.orders = repo
    jobs = asyncio.run(service.claim(NOW, TODAY))
    assert [(job.kind, job.order.id, job.participants) for job in jobs] == [
        (ReminderKind.OVERDUE, 1, 2), (ReminderKind.DAY, 2, 2)]
    # Просрочен - и «сутки без оплаты» больше не нужно: обе отметки сразу.
    assert (late.overdue_notified_at, late.payment_reminded_at) == (NOW, NOW)
    assert (fresh.payment_reminded_at, fresh.overdue_notified_at) == (NOW, None)
    assert repo.calls == [("overdue", NOW, TODAY), ("unreminded", NOW - REMIND_AFTER, NOW, TODAY)]
    assert REMIND_AFTER == timedelta(hours=24)


def test_already_reminded_overdue_keeps_first_mark() -> None:
    earlier = NOW - timedelta(hours=48)
    late = _order(payment_reminded_at=earlier)
    service = ReminderService.__new__(ReminderService)
    service.orders = _Orders(overdue=[late])
    asyncio.run(service.claim(NOW, TODAY))
    assert (late.payment_reminded_at, late.overdue_notified_at) == (earlier, NOW)


# --- Тексты и кнопки -----------------------------------------------------------------------------------------------

def test_client_texts() -> None:
    day = render.payment_reminder(_session(), _order(), overdue=False, has_manager=True, timezone=TZ)
    assert "Напоминание об оплате</b> · заказ №100001" in day and "Имплантация &lt;базовый&gt;" in day
    assert f"К оплате: {format_rub(8_000_000)}" in day and "Оплатить до 24 октября, 12:00" in day
    assert texts.PAYMENT_REMINDER_HINT in day
    no_manager = render.payment_reminder(_session(), _order(total_amount=None), False, False, TZ)
    assert texts.APPLY_NO_PRICE in no_manager and texts.PAYMENT_REMINDER_HINT_NO_MANAGER in no_manager
    overdue = render.payment_reminder(_session(), _order(), overdue=True, has_manager=True, timezone=TZ)
    assert "Срок оплаты истёк</b> · заказ №100001" in overdue and "13–14 ноября 2026" in overdue
    assert texts.PAYMENT_OVERDUE_HINT in overdue and "К оплате" not in overdue


def test_admin_texts() -> None:
    text, markup = admin_orders_views.reminder_notice(_job(participants=2), TZ)
    assert "Сутки без оплаты</b> · заказ №100001" in text and "Петров Пётр Петрович · +79990000010 (и коллега)" in text
    assert f"{format_rub(8_000_000)} · оплатить до 24 октября, 12:00" in text and texts.ADMIN_ORDER_INVOICE_NONE in text
    assert _rows(markup) == [[texts.BTN_ADMIN_OPEN_ORDER]]
    text, markup = admin_orders_views.reminder_notice(_job(ReminderKind.OVERDUE), TZ)
    assert "Просрочена оплата" in text and "срок был" in text and "(и коллега)" not in text
    assert _rows(markup) == [[texts.BTN_ADMIN_ORDER_REMOVE], [texts.BTN_ADMIN_OPEN_ORDER]]
    remove = OrderAdminCb.unpack(markup.inline_keyboard[0][0].callback_data)
    # «Снять бронь» сначала спросит - и новым сообщением: уведомление остаётся в чате.
    assert (remove.action, remove.order_id, remove.value) == (OrderAdminAction.REMOVE, 77,
                                                                admin_orders_views.NEW_SCREEN)


def test_letters_to_client_and_every_admin() -> None:
    mail = letters(_job(), _settings())
    assert [letter.chat_id for letter in mail] == [111, 1, 2]
    client = mail[0]
    assert client.client_telegram_id == 111  # заблокировал бота - отметим у клиента
    # Ещё не выбрал «Физлицо / Юрлицо» - кнопки выбора прямо в напоминании.
    assert _rows(client.markup) == [[texts.BTN_PAY_INDIVIDUAL], [texts.BTN_PAY_ORGANIZATION],
                                    [texts.BTN_CANCEL_VIA_MANAGER]]
    assert client.markup.inline_keyboard[2][0].url.startswith("https://t.me/manager?text=")
    assert all(letter.client_telegram_id is None for letter in mail[1:])
    chosen = letters(_job(payer_type=PayerType.INDIVIDUAL), _settings())[0]
    assert _rows(chosen.markup) == [[texts.BTN_CANCEL_VIA_MANAGER]]
    overdue = letters(_job(ReminderKind.OVERDUE), _settings(manager_url=None))[0]
    assert overdue.markup is None and texts.PAYMENT_OVERDUE_HINT_NO_MANAGER in overdue.text
    offline = SimpleNamespace(**{**vars(PAYER), "telegram_id": None})
    assert [letter.chat_id for letter in letters(_job(payer=offline), _settings())] == [1, 2]


# --- Фоновая задача ------------------------------------------------------------------------------------------------

class _Factory:
    """async with factory() as session, session.begin(): - записывает, когда транзакция закрылась."""

    def __init__(self, log: list) -> None:
        self.log = log

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def begin(self):
        log = self.log

        class _Tx:
            async def __aenter__(self):
                log.append("begin")

            async def __aexit__(self, *exc):
                log.append("commit")

        return _Tx()


class _Bot:
    def __init__(self, log: list, blocked: set[int] = frozenset()) -> None:
        self.log, self.blocked = log, blocked

    async def send_message(self, chat_id, text, reply_markup=None):
        if chat_id in self.blocked:
            raise TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")
        self.log.append(("send", chat_id))


def _reminders(monkeypatch, log: list, jobs: list, blocked: set[int] = frozenset()) -> PaymentReminders:
    class _Service:
        def __init__(self, session) -> None:
            pass

        async def claim(self, now, today):
            log.append(("claim", now, today))
            return jobs

    monkeypatch.setattr(reminders_module, "ReminderService", _Service)
    monkeypatch.setattr(reminders_module, "SEND_PAUSE", 0)
    return PaymentReminders(_Bot(log, blocked), _Factory(log), _settings(), interval=0)


def test_tick_sends_only_after_marks_are_committed(monkeypatch) -> None:
    log: list = []
    count = asyncio.run(_reminders(monkeypatch, log, [_job()]).tick(NOW))
    assert count == 1
    assert log == ["begin", ("claim", NOW, TODAY), "commit", ("send", 111), ("send", 1), ("send", 2)]


def test_blocked_client_is_marked_and_admins_still_get_it(monkeypatch) -> None:
    log: list = []
    marked: list[int] = []

    class _Clients:
        def __init__(self, session) -> None:
            pass

        async def mark_bot_blocked(self, telegram_id: int) -> None:
            marked.append(telegram_id)

    monkeypatch.setattr(reminders_module, "ClientService", _Clients)
    asyncio.run(_reminders(monkeypatch, log, [_job()], blocked={111}).tick(NOW))
    assert marked == [111] and ("send", 1) in log and ("send", 2) in log


def test_run_forever_survives_a_failed_check(monkeypatch) -> None:
    calls: list[int] = []
    reminders = PaymentReminders(_Bot([]), _Factory([]), _settings(), interval=0)

    async def tick(now=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("база недоступна")  # одна неудачная проверка не останавливает напоминания
        raise asyncio.CancelledError  # остановка бота

    monkeypatch.setattr(reminders, "tick", tick)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(reminders.run_forever())
    assert len(calls) == 2


def test_letter_is_plain_data() -> None:
    assert Letter(1, "текст").markup is None and Letter(1, "текст").client_telegram_id is None
