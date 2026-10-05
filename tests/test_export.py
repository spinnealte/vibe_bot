"""Этап 6: хвост ссылки /start у клиента и выгрузка клиентов в CSV — без БД и Telegram."""

import asyncio
import csv
import io
from datetime import UTC, date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from stubbot.db.enums import ProfileStatus
from stubbot.services.client_export import HEADER, client_row
from stubbot.services.clients import ClientService
from stubbot.services.deeplinks import parse_start_payload
from stubbot.tg import admin_views, texts
from stubbot.utils.csv_export import build_csv, safe_cell

MSK = ZoneInfo("Europe/Moscow")


class _FakeSession:
    """Сессия без БД: журнал пишется «в никуда», источников в справочнике нет."""

    def add(self, obj: object) -> None:
        pass

    async def scalar(self, statement: object) -> None:
        return None


def _new_client() -> SimpleNamespace:
    return SimpleNamespace(id=1, start_param=None, first_source_id=None, referred_by_client_id=None)


@pytest.mark.parametrize(
    ("raw", "saved"),
    [
        ("src_flyer_mira", "src_flyer_mira"),  # источник (в справочнике его может и не быть)
        ("site", "site"),  # любой хвост без префикса — тоже сохраняем как есть
        ("ref_n8LjNOyu", "ref_n8LjNOyu"),
        ("привет =1+1", None),  # набрано руками после /start — из ссылки так прийти не может
        (None, None),  # обычный /start без ссылки
    ],
)
def test_start_param_saved_on_first_start(raw: str | None, saved: str | None) -> None:
    client = _new_client()
    asyncio.run(ClientService(_FakeSession()).process_start(client, True, parse_start_payload(raw)))
    assert client.start_param == saved


def test_start_param_not_changed_on_later_starts() -> None:
    client = _new_client()
    asyncio.run(ClientService(_FakeSession()).process_start(client, False, parse_start_payload("src_vk")))
    assert client.start_param is None  # клиент уже был: «откуда пришёл» — только про первый заход


@pytest.mark.parametrize(
    ("value", "cell"),
    [
        (None, ""),
        (15, "15"),
        ("Иванов Иван", "Иванов Иван"),
        ("+79211234567", "+79211234567"),  # телефон — число со знаком, не формула
        ("-5", "-5"),
        ("=1+1", "'=1+1"),
        ("+7 (921) 123", "'+7 (921) 123"),
        ("-abc", "'-abc"),
        ("@user", "'@user"),
        ("\tx", "'\tx"),
    ],
)
def test_safe_cell(value: object, cell: str) -> None:
    assert safe_cell(value) == cell


def test_build_csv_for_excel() -> None:
    content = build_csv(("ID", "ФИО"), [(1, "Иванов; Иван"), (2, '=HYPERLINK("http://x")'), (3, None)])
    assert content.startswith(b"\xef\xbb\xbf")  # BOM — Excel поймёт, что это UTF-8
    text = content.decode("utf-8-sig")
    assert text.count("\r\n") == 4
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=";"))
    assert rows == [["ID", "ФИО"], ["1", "Иванов; Иван"], ["2", "'=HYPERLINK(\"http://x\")"], ["3", ""]]


def test_client_row() -> None:
    client = SimpleNamespace(
        id=7, created_at=datetime(2026, 10, 5, 9, 30, tzinfo=UTC), last_seen_at=None, telegram_id=900000001,
        tg_username="ivanov", tg_first_name="Иван", tg_last_name=None, full_name="Иванов Иван Иванович",
        name_confirmed_at=datetime(2026, 10, 5, 9, 31, tzinfo=UTC), phone="+79211234567", email=None, city="СПб",
        workplace_raw=None, practice_since_year=2015, birth_date=date(1990, 3, 7),
        specialties=[SimpleNamespace(title="Терапевт"), SimpleNamespace(title="Ортопед")],
        positions=[SimpleNamespace(title="Врач")], profile_status=ProfileStatus.COMPLETED,
        start_param="src_flyer_mira", referred_by_client_id=None, referral_code="-Ab12Cd3", is_bot_blocked=False,
        admin_note=None,
    )
    row = dict(zip(HEADER, client_row(client, pd_consent=True, marketing=False, tz=MSK), strict=True))
    assert row["Первый заход"] == "05.10.2026 12:30"  # по Москве
    assert row["Последний визит"] == "" and row["Имя в Telegram"] == "Иван"
    assert row["ФИО подтверждено"] == "да" and row["Дата рождения"] == "07.03.1990"
    assert row["Специальности"] == "Ортопед, Терапевт" and row["Должности"] == "Врач"
    assert row["Анкета"] == "заполнена"
    assert row["Согласие на обработку ПД"] == "да" and row["Согласие на рассылку"] == "нет"
    assert row["Хвост ссылки"] == "src_flyer_mira" and row["Заблокировал бота"] == "нет"
    # В файле код, начинающийся с «-», экранируется, телефон остаётся как есть.
    line = build_csv(HEADER, [list(row.values())]).decode("utf-8-sig").splitlines()[1]
    assert "'-Ab12Cd3" in line and ";+79211234567;" in line


def test_admin_menu_has_export_button() -> None:
    _, markup = admin_views.menu()
    rows = [[b.text for b in row] for row in markup.inline_keyboard]
    assert rows[-2:] == [[texts.BTN_ADMIN_EXPORT], [texts.BTN_ADMIN_CLOSE]]
