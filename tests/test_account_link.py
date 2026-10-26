"""Привязка коллеги при регистрации по телефону (полная версия, этап 1, шаг 6). Без БД и Telegram."""

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace

from stubbot.db.enums import ClientEventType, EnrollmentStatus
from stubbot.services.enrollments import MyApplication
from stubbot.services.registration import PhoneResult, PhoneSaved, RegistrationService
from stubbot.tg import flows, keyboards, render, texts

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
PHONE = "+79991234567"


def _client(**kw) -> SimpleNamespace:
    fields = dict(id=10, telegram_id=111, phone=None, phone_confirmed=False, full_name=None,
                  profile_status=None, name_confirmed_at=None)
    return SimpleNamespace(**{**fields, **kw})


class _Clients:
    def __init__(self, owners=(), overlap: bool = False) -> None:
        self.owners, self.overlap = list(owners), overlap
        self.calls: list = []

    async def all_by_phone(self, phone: str):
        self.calls.append(("find", phone))
        return self.owners

    async def records_overlap(self, from_ids, to_id) -> bool:
        return self.overlap

    async def move_records(self, from_ids, to_id) -> None:
        self.calls.append(("move", from_ids, to_id))

    async def close_merged(self, ids, into_id, now) -> None:
        self.calls.append(("close", ids, into_id, now))


def _service(clients: _Clients, events: list) -> RegistrationService:
    service = RegistrationService.__new__(RegistrationService)  # без БД: только правила
    service.clients = clients
    service.analytics = SimpleNamespace(add_event=lambda *args: events.append(args))
    service._mark_partial = lambda client: None
    return service


def _save(service: RegistrationService, client, raw: str = "+7 999 123-45-67") -> PhoneSaved:
    return asyncio.run(service.save_phone(client, raw, NOW))


def test_colleague_without_telegram_is_linked() -> None:
    offline = _client(id=50, telegram_id=None, phone=PHONE, full_name="Петрова Анна Сергеевна")
    clients, events = _Clients([offline]), []
    me = _client()
    saved = _save(_service(clients, events), me)
    assert saved == PhoneSaved(PhoneResult.LINKED, "Петрова Анна Сергеевна")  # ФИО - на шаг «ФИО»
    assert (me.phone, me.phone_confirmed) == (PHONE, True)
    # Сначала записи переходят, потом старый профиль закрывается (и освобождает номер).
    assert clients.calls == [("find", PHONE), ("move", [50], 10), ("close", [50], 10, NOW)]
    assert events == [(10, ClientEventType.ACCOUNT_LINKED, {"from": [50]})]


def test_phone_of_another_account_in_the_bot_is_taken() -> None:
    clients, events = _Clients([_client(id=7, telegram_id=222, phone=PHONE)]), []
    me = _client()
    assert _save(_service(clients, events), me).result is PhoneResult.TAKEN
    assert me.phone is None and clients.calls == [("find", PHONE)] and not events  # два аккаунта не сливаем


def test_overlapping_records_are_not_merged() -> None:
    clients = _Clients([_client(id=50, telegram_id=None, phone=PHONE)], overlap=True)
    me = _client()
    assert _save(_service(clients, []), me).result is PhoneResult.TAKEN  # обе записи на одно проведение
    assert me.phone is None and ("move", [50], 10) not in clients.calls


def test_plain_phone_and_own_phone_and_invalid() -> None:
    assert _save(_service(_Clients(), []), _client()) == PhoneSaved(PhoneResult.OK)
    me = _client(phone=PHONE)
    assert _save(_service(_Clients([me]), []), me).result is PhoneResult.OK  # тот же номер ещё раз
    assert _save(_service(_Clients(), []), _client(), raw="12").result is PhoneResult.INVALID


# --- Экраны --------------------------------------------------------------------------------------------------------

def _app(title: str = "Имплантация <базовый>", payer=None) -> MyApplication:
    session = SimpleNamespace(id=5, title_override=None, program=SimpleNamespace(title=title),
                              start_date=date(2026, 11, 1), end_date=date(2026, 11, 2), days=[])
    enrollment = SimpleNamespace(id=1, status=EnrollmentStatus.APPLICATION, enrolled_by_client_id=(payer or PAYER).id)
    return MyApplication(enrollment, session, session.program, None, None, 0, payer or PAYER, False)


PAYER = SimpleNamespace(id=10, full_name="Петров Пётр Петрович", telegram_id=111)


def test_found_records_text() -> None:
    one = render.linked_records([_app()], registration=True)
    assert "Нашли Вашу запись!" in one and "вместе с коллегой - Петров Пётр Петрович" in one
    assert "Имплантация &lt;базовый&gt;" in one and "1–2 ноября 2026" in one
    assert one.endswith(texts.LINKED_TAIL_REGISTRATION)
    assert not render.linked_records([_app()], registration=False).endswith(texts.LINKED_TAIL_REGISTRATION)
    other = SimpleNamespace(id=20, full_name="Сидорова Ольга Ивановна", telegram_id=None)
    many = render.linked_records([_app(), _app("Синус-лифтинг", other)], registration=True)
    assert "Нашли Ваши записи!" in many and "с коллегой: Петров П. П." in many and "с коллегой: Сидорова О. И." in many
    joined = render.colleague_joined(_app(), "Петрова <Анна>")
    assert "Петрова &lt;Анна&gt; видит запись на курс «Имплантация &lt;базовый&gt;»" in joined


class _Message:
    def __init__(self) -> None:
        self.sent: list = []

    async def answer(self, text, reply_markup=None):
        self.sent.append((text, reply_markup))


class _Bot:
    def __init__(self) -> None:
        self.sent: list = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


def _announce(monkeypatch, apps, client, registration: bool = True):
    class _Enrollments:
        def __init__(self, session) -> None:
            pass

        async def my_applications(self, client_id, today):
            return apps

    monkeypatch.setattr(flows, "EnrollmentService", _Enrollments)
    message, bot = _Message(), _Bot()
    settings = SimpleNamespace(timezone="Europe/Moscow")
    asyncio.run(flows.announce_linked_records(message, None, client, settings, bot, "Петрова Анна Сергеевна",
                                              registration))
    return message, bot


def test_announce_shows_records_and_tells_the_payer_once(monkeypatch) -> None:
    offline_payer = SimpleNamespace(id=20, full_name="Сидорова Ольга Ивановна", telegram_id=None)
    own = MyApplication(SimpleNamespace(id=9, status=EnrollmentStatus.CONFIRMED, enrolled_by_client_id=None),
                        _app().session, _app().program, None, None, 0, None, True)
    apps = [_app(), _app("Синус-лифтинг"), _app("Ортодонтия", offline_payer), own]
    message, bot = _announce(monkeypatch, apps, _client(id=50, full_name=None))
    [(text, markup)] = message.sent
    assert "Нашли Ваши записи!" in text and "Ортодонтия" in text
    assert markup == keyboards.registration_nav()  # вместо «Поделиться номером» - «Назад / Отмена»
    # Записавшему - одно сообщение, даже если записал на два курса; без Telegram - некому.
    assert len(bot.sent) == 1 and bot.sent[0][0] == 111 and "Петрова Анна Сергеевна видит запись" in bot.sent[0][1]


def test_announce_in_cabinet_uses_own_name_and_nothing_to_show(monkeypatch) -> None:
    message, bot = _announce(monkeypatch, [_app()], _client(full_name="Петрова Анна Андреевна"), registration=False)
    assert message.sent[0][1] is None and "Петрова Анна Андреевна видит запись" in bot.sent[0][1]
    message, bot = _announce(monkeypatch, [], _client())
    assert not message.sent and not bot.sent  # записей коллег нет - и сообщать нечего
