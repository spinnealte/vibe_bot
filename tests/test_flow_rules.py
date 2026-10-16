"""Правила сценариев без БД и Telegram: мультивыбор (фильтр кнопок, сохранение), пересборка карусели расписания."""

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from stubbot.services.registration import RegistrationService
from stubbot.services.schedule import ScheduleService
from stubbot.tg.callbacks import SelectDoneCb, SelectGroup, ToggleCb
from stubbot.tg.handlers.selection import group_matches_state
from stubbot.tg.states import EditProfile, Registration

TODAY = date(2026, 10, 1)


@pytest.mark.parametrize(
    ("raw_state", "group", "expected"),
    [
        (Registration.specialties.state, SelectGroup.SPECIALTIES, True),
        (Registration.positions.state, SelectGroup.POSITIONS, True),
        (EditProfile.specialties.state, SelectGroup.SPECIALTIES, True),
        # Старая клавиатура специальностей, а человек уже выбирает должности - кнопка неактуальна.
        (Registration.positions.state, SelectGroup.SPECIALTIES, False),
        (EditProfile.positions.state, SelectGroup.SPECIALTIES, False),
        (Registration.full_name.state, SelectGroup.SPECIALTIES, False),
        (None, SelectGroup.POSITIONS, False),
    ],
)
def test_selection_button_must_match_current_step(raw_state: str | None, group: SelectGroup, expected: bool) -> None:
    assert group_matches_state(None, ToggleCb(group=group, item_id=1), raw_state) is expected
    assert group_matches_state(None, SelectDoneCb(group=group), raw_state) is expected


class _FakeCatalog:
    """Каталог, где проведение #1 убрали в архив между запросом списка и запросом карточки."""

    def __init__(self) -> None:
        self.visible = [1, 2]
        self.archived_after_listing = {1}

    async def upcoming_session_ids(self, today: date) -> list[int]:
        ids = list(self.visible)
        self.visible = [sid for sid in self.visible if sid not in self.archived_after_listing]
        return ids

    async def public_session(self, session_id: int, today: date) -> SimpleNamespace | None:
        if session_id not in self.visible:
            return None
        return SimpleNamespace(id=session_id, capacity=None, program=SimpleNamespace(id=10 + session_id))


class _FakeDictionaries:
    async def active_specialties(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(id=1), SimpleNamespace(id=2)]

    async def active_positions(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(id=5)]


class _FakeClients:
    def __init__(self) -> None:
        self.saved: dict[str, list[int]] = {}

    async def replace_specialties(self, client_id: int, ids: list[int]) -> None:
        self.saved["specialties"] = ids

    async def replace_positions(self, client_id: int, ids: list[int]) -> None:
        self.saved["positions"] = ids


def test_selection_saves_only_active_items() -> None:
    """У клиента осталась специальность из архива (#7) - она не мешает сохранить выбор, а сама не сохраняется."""
    service = RegistrationService(None)
    service.dictionaries, service.clients = _FakeDictionaries(), _FakeClients()
    client = SimpleNamespace(id=1)
    assert asyncio.run(service.save_specialties(client, [2, 7, 1, 2]))
    assert service.clients.saved["specialties"] == [1, 2]
    assert not asyncio.run(service.save_specialties(client, [7]))  # отмечено только то, чего нет в списке
    assert not asyncio.run(service.save_positions(client, []))
    assert asyncio.run(service.save_positions(client, [5, 999999]))  # подделанная кнопка
    assert service.clients.saved["positions"] == [5]


def test_carousel_shows_neighbour_when_session_archived_meanwhile() -> None:
    service = ScheduleService(None)
    service.catalog = _FakeCatalog()
    slide = asyncio.run(service.slide(TODAY, 0))
    assert slide is not None, "вместо «курсы скоро появятся» - соседнее проведение"
    assert slide.card.session.id == 2
    assert (slide.index, slide.total) == (0, 1)


def test_carousel_is_empty_without_sessions() -> None:
    """Афиша - только проведения: курс без проведения в неё сам не попадает (решение 16)."""
    service = ScheduleService(None)
    service.catalog = _FakeCatalog()
    service.catalog.visible = []
    assert asyncio.run(service.slide(TODAY, 0)) is None
