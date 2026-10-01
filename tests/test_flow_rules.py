"""Правила сценариев без БД и Telegram: фильтр мультивыбора, пересборка карусели расписания."""

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

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
        # Старая клавиатура специальностей, а человек уже выбирает должности — кнопка неактуальна.
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
    """Каталог, где поток #1 скрыли между запросом списка и запросом карточки."""

    def __init__(self) -> None:
        self.visible = [1, 2]
        self.hidden_after_listing = {1}

    async def upcoming_session_ids(self, today: date) -> list[int]:
        ids = list(self.visible)
        self.visible = [sid for sid in self.visible if sid not in self.hidden_after_listing]
        return ids

    async def programs_without_upcoming_ids(self, today: date) -> list[int]:
        return []

    async def public_session(self, session_id: int, today: date) -> SimpleNamespace | None:
        if session_id not in self.visible:
            return None
        return SimpleNamespace(id=session_id, capacity=None, program=SimpleNamespace(id=10 + session_id))

    async def published_program(self, program_id: int) -> None:
        return None


def test_carousel_shows_neighbour_when_course_hidden_meanwhile() -> None:
    service = ScheduleService(None)
    service.catalog = _FakeCatalog()
    slide = asyncio.run(service.slide(TODAY, 0))
    assert slide is not None, "вместо «курсы скоро появятся» — соседний курс"
    assert slide.card.session.id == 2
    assert (slide.index, slide.total) == (0, 1)
