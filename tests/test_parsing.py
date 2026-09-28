from datetime import date

import pytest

from stubbot.services.deeplinks import PayloadKind, StartPayload, parse_start_payload
from stubbot.utils import profile_fields
from stubbot.utils.names import FullName, parse_full_name
from stubbot.utils.phone import normalize_phone

TODAY = date(2026, 9, 28)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("src_flyer_clinic1", StartPayload(PayloadKind.SOURCE, "flyer_clinic1", "src_flyer_clinic1")),
        ("ref_n8LjNOyu", StartPayload(PayloadKind.REFERRAL, "n8LjNOyu", "ref_n8LjNOyu")),
        ("inv_abc-123", StartPayload(PayloadKind.INVITE, "abc-123", "inv_abc-123")),
        ("promo2026", StartPayload(PayloadKind.UNKNOWN, "promo2026", "promo2026")),
        ("src_", StartPayload(PayloadKind.UNKNOWN, "src_", "src_")),
        ("src_bad code", StartPayload(PayloadKind.UNKNOWN, "src_bad code", "src_bad code")),
    ],
)
def test_parse_start_payload(raw: str | None, expected: StartPayload | None) -> None:
    assert parse_start_payload(raw) == expected


def test_parse_start_payload_truncates_to_telegram_limit() -> None:
    assert len(parse_start_payload("x" * 200).raw) == 64


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("79211234567", "+79211234567"),
        ("+79211234567", "+79211234567"),
        ("89211234567", "+79211234567"),
        ("+7 (921) 123-45-67", "+79211234567"),
        ("9211234567", "+79211234567"),
        ("+375291234567", "+375291234567"),
        ("12345", None),
        ("", None),
    ],
)
def test_normalize_phone(raw: str, expected: str | None) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Иванова Мария Сергеевна", FullName("Иванова", "Мария", "Сергеевна")),
        ("  иванов   пётр  ", FullName("иванов", "пётр", None)),  # регистр не трогаем — как написал
        ("Иванов-Петров Пётр", FullName("Иванов-Петров", "Пётр", None)),
        ("Мамедов Али Гусейн оглы", FullName("Мамедов", "Али", "Гусейн оглы")),
        ("О'Нил Шон", FullName("О'Нил", "Шон", None)),
        ("Иванов", None),
        ("Иванов Иван 3", None),
        ("Иванов 😀", None),
        ("", None),
        ("а" * 101 + " Иван", None),
    ],
)
def test_parse_full_name(raw: str, expected: FullName | None) -> None:
    assert parse_full_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Санкт-Петербург", "Санкт-Петербург"), ("  Сестрорецк ", "Сестрорецк"), ("1", None), ("12345", None)],
)
def test_parse_city(raw: str, expected: str | None) -> None:
    assert profile_fields.parse_city(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("7", 2019), ("7 лет", 2019), ("1 год", 2025), ("0", 2026), ("71", None), ("семь", None), ("-3", None)],
)
def test_parse_experience_years(raw: str, expected: int | None) -> None:
    assert profile_fields.parse_experience_years(raw, TODAY) == expected


def test_experience_years_roundtrip() -> None:
    assert profile_fields.experience_years(2019, TODAY) == 7
    assert profile_fields.experience_years(None, TODAY) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" Ivan.Petrov@Mail.RU ", "ivan.petrov@mail.ru"), ("ivan@mail", None), ("ivan mail.ru", None), ("", None)],
)
def test_parse_email(raw: str, expected: str | None) -> None:
    assert profile_fields.parse_email(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("14.03.1990", date(1990, 3, 14)),
        ("14/03/1990", date(1990, 3, 14)),
        ("31.02.1990", None),  # несуществующая дата
        ("14.03.2020", None),  # младше 16
        ("14.03.1900", None),  # старше 100
        ("1990-03-14", None),
    ],
)
def test_parse_birth_date(raw: str, expected: date | None) -> None:
    assert profile_fields.parse_birth_date(raw, TODAY) == expected
