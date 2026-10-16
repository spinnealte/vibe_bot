"""Согласия по документам заказчика (16.10.2026): текст и ссылки, галочка, версии в базе, данные до согласия.
Без БД и Telegram."""

import asyncio
import hashlib
import re
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from stubbot.db.enums import LegalDocumentType
from stubbot.services import legal
from stubbot.services.clients import ClientService, TelegramProfile
from stubbot.services.legal import (
    CONSENT_URL,
    DOCUMENTS,
    MARKETING_CONSENT,
    PD_CONSENT,
    POLICY_URL,
    LegalTextChanged,
    document_text,
    publish_documents,
)
from stubbot.tg import keyboards, texts
from stubbot.tg.callbacks import ConsentFormAction, ConsentFormCb
from stubbot.tg.render import visible_length

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def _plain(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def test_pd_consent_is_customer_wording_with_links_to_site() -> None:
    assert _plain(PD_CONSENT.html) == (
        "Я согласен с Политикой обработки персональных данных, даю согласие на обработку моих персональных данных "
        "и на получение информационных сообщений."
    )
    assert f'<a href="{POLICY_URL}">Политикой обработки персональных данных</a>' in PD_CONSENT.html
    assert f'<a href="{CONSENT_URL}">согласие на обработку моих персональных данных</a>' in PD_CONSENT.html
    assert POLICY_URL == "https://akvastom-group.ru/privacy/" and CONSENT_URL == "https://akvastom-group.ru/privacy/consent/"
    # Рекламные рассылки — отдельное согласие со своей ссылкой (вторая часть той же страницы).
    assert f'<a href="{CONSENT_URL}">согласие на получение рекламных рассылок</a>' in MARKETING_CONSENT.html


def test_consent_screen_and_popup_fit_telegram_limits() -> None:
    screen = texts.CONSENT_FORM.format(intro=texts.CONSENT_PD_INTRO, pd=PD_CONSENT.html, ads=MARKETING_CONSENT.html,
                                       hint=texts.CONSENT_FORM_HINT)
    assert PD_CONSENT.html in screen and MARKETING_CONSENT.html in screen and visible_length(screen) < 4096
    assert screen.index("Обязательно") < screen.index(PD_CONSENT.html) < screen.index("По желанию") \
        < screen.index(MARKETING_CONSENT.html)
    assert len(texts.CONSENT_PD_CHECK_REQUIRED) <= 200 and "<" not in texts.CONSENT_PD_CHECK_REQUIRED


def test_two_separate_checkboxes_and_continue() -> None:
    """Отдельные галочки: 1 — политика и обработка ПД, 2 — реклама; «Продолжить» — третьей кнопкой."""
    rows = lambda markup: [[b.text for b in row] for row in markup.inline_keyboard]  # noqa: E731
    on, off = texts.CONSENT_MARK_ON, texts.CONSENT_MARK_OFF
    pd, ads, go = texts.CONSENT_FORM_PD, texts.CONSENT_FORM_ADS, texts.CONSENT_FORM_CONTINUE
    assert rows(keyboards.consent_form(pd=False, ads=False)) == [[off + pd], [off + ads], [go]]
    assert rows(keyboards.consent_form(pd=True, ads=False)) == [[on + pd], [off + ads], [go]]
    assert rows(keyboards.consent_form(pd=False, ads=True)) == [[off + pd], [on + ads], [go]]
    assert rows(keyboards.consent_form(pd=True, ads=False, with_ads=False)) == [[on + pd], [go]]
    # «Не согласен» нет: не поставил галочку — значит, не согласился.
    markup = keyboards.consent_form(pd=False, ads=False)
    assert [row[0].callback_data for row in markup.inline_keyboard] == [
        ConsentFormCb(action=action).pack()
        for action in (ConsentFormAction.PD, ConsentFormAction.ADS, ConsentFormAction.CONTINUE)]


def test_document_text_by_version() -> None:
    assert {d.type for d in DOCUMENTS} == {LegalDocumentType.PRIVACY_POLICY, LegalDocumentType.PD_CONSENT,
                                           LegalDocumentType.MARKETING_CONSENT}
    assert document_text(SimpleNamespace(type=LegalDocumentType.PD_CONSENT, version="1")) == PD_CONSENT.html
    # Черновик «0» (или версия, которой нет в коде) — показывать нечего, согласие не собираем.
    assert document_text(SimpleNamespace(type=LegalDocumentType.PD_CONSENT, version="0")) is None
    assert PD_CONSENT.text_hash == hashlib.sha256(PD_CONSENT.html.encode("utf-8")).hexdigest()


class _FakeDocuments:
    """legal_documents в памяти: то, что publish_documents читает и пишет."""

    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows

    async def document(self, doc_type: LegalDocumentType, version: str) -> SimpleNamespace | None:
        return next((r for r in self.rows if r.type is doc_type and r.version == version), None)

    async def unset_current(self, doc_type: LegalDocumentType) -> None:
        for row in self.rows:
            if row.type is doc_type:
                row.is_current = False

    def add_document(self, document: object) -> None:
        self.rows.append(document)


def _publish(monkeypatch: pytest.MonkeyPatch, rows: list[SimpleNamespace]) -> list[str]:
    fake = _FakeDocuments(rows)
    monkeypatch.setattr(legal, "ConsentRepository", lambda session: fake)
    return asyncio.run(publish_documents(None, NOW))


def _current(rows: list[SimpleNamespace]) -> dict[LegalDocumentType, str]:
    return {r.type: r.version for r in rows if r.is_current}


def test_publish_replaces_placeholder_and_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [SimpleNamespace(type=t, version="0", text_hash="old", is_current=True)
            for t in (LegalDocumentType.PD_CONSENT, LegalDocumentType.MARKETING_CONSENT,
                      LegalDocumentType.PRIVACY_POLICY)]
    published = _publish(monkeypatch, rows)
    assert sorted(published) == ["marketing_consent v2", "pd_consent v1", "privacy_policy v1"]
    assert "0" not in _current(rows).values()  # черновики «0» больше не текущие — согласия на них не в счёт
    new = next(r for r in rows if r.type is LegalDocumentType.PD_CONSENT and r.version == "1")
    assert (new.url, new.text_hash, new.published_at) == (CONSENT_URL, PD_CONSENT.text_hash, NOW)
    # Повторный запуск (каждый старт бота) ничего не выпускает и не меняет.
    assert _publish(monkeypatch, rows) == [] and len(rows) == 6


def test_publish_refuses_changed_text_under_old_version(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [SimpleNamespace(type=LegalDocumentType.PD_CONSENT, version="1", text_hash="другой текст",
                            is_current=True)]
    with pytest.raises(LegalTextChanged, match="pd_consent v1"):
        _publish(monkeypatch, rows)


def test_publish_makes_existing_version_current_again(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [SimpleNamespace(type=LegalDocumentType.PD_CONSENT, version="1", text_hash=PD_CONSENT.text_hash,
                            is_current=False),
            SimpleNamespace(type=LegalDocumentType.PD_CONSENT, version="2", text_hash="x", is_current=True)]
    assert "pd_consent v1" not in _publish(monkeypatch, rows)
    assert _current(rows)[LegalDocumentType.PD_CONSENT] == "1"


# --- Данные из Telegram — только после согласия ------------------------------------------------------------------

PROFILE = TelegramProfile(telegram_id=77, username="ivanov", first_name="Иван", last_name="Иванов",
                          language_code="ru")


class _FakeClients:
    def __init__(self, existing: SimpleNamespace | None = None) -> None:
        self.existing, self.inserted = existing, None

    async def get_by_telegram_id(self, telegram_id: int) -> SimpleNamespace | None:
        return self.existing

    async def insert_if_absent(self, values: dict) -> SimpleNamespace:
        self.inserted = values
        return SimpleNamespace(id=1, **values)


class _FakeConsents:
    def __init__(self, active: bool) -> None:
        self.active, self.asked = active, 0

    async def active_consent(self, client_id: int, consent_type: object) -> object | None:
        self.asked += 1
        return object() if self.active else None


def _service(existing: SimpleNamespace | None, consent: bool) -> ClientService:
    service = ClientService(None)
    service.clients, service.consents = _FakeClients(existing), _FakeConsents(consent)
    return service


def _client(**names: object) -> SimpleNamespace:
    fields = {"tg_username": None, "tg_first_name": None, "tg_last_name": None, "tg_language_code": None, **names}
    return SimpleNamespace(id=5, is_bot_blocked=False, bot_blocked_at=None, last_seen_at=None, **fields)


def test_new_client_is_saved_without_telegram_name() -> None:
    service = _service(None, consent=False)
    client, created = asyncio.run(service.get_or_create(PROFILE))
    assert created and service.clients.inserted["telegram_id"] == 77
    assert not {key for key in service.clients.inserted if key.startswith("tg_")}  # ни имени, ни @username


def test_telegram_name_is_kept_only_after_consent() -> None:
    client = _client()
    asyncio.run(_service(client, consent=False).get_or_create(PROFILE))
    assert (client.tg_username, client.tg_first_name) == (None, None)  # согласия нет — не запоминаем
    assert client.last_seen_at is not None  # а время визита — техническое, его ведём всегда
    asyncio.run(_service(client, consent=True).get_or_create(PROFILE))
    assert (client.tg_username, client.tg_first_name, client.tg_last_name) == ("ivanov", "Иван", "Иванов")


def test_unchanged_telegram_name_needs_no_consent_lookup() -> None:
    client = _client(tg_username="ivanov", tg_first_name="Иван", tg_last_name="Иванов", tg_language_code="ru")
    service = _service(client, consent=True)
    asyncio.run(service.get_or_create(PROFILE))
    assert service.consents.asked == 0
