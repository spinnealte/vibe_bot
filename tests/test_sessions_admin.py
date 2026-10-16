"""Этап 5: ввод расписания админом, правила полей проведения, экраны карточек — без БД и Telegram."""

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from stubbot.services.admin_courses import COURSE_SPEC, AdminCourseService
from stubbot.services.admin_dictionaries import DeleteResult, DictKind, FieldKind, SaveResult, parse_field
from stubbot.services.admin_sessions import (
    NO_DATES,
    SESSION_SPEC,
    AdminSessionService,
    SessionScope,
    session_scope,
    valid_prices,
)
from stubbot.tg import admin_views, texts
from stubbot.tg.callbacks import DictAction, DictCb
from stubbot.utils.dates import format_month, month_bounds, month_key
from stubbot.utils.money import format_rub
from stubbot.utils.schedule_input import format_days, parse_days, parse_deadline, parse_rubles

TODAY = date(2026, 10, 2)
MSK = ZoneInfo("Europe/Moscow")


def _buttons(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def test_parse_days_lines_year_and_sorting() -> None:
    days = parse_days("14.10 10:00-17:00\n13.10.2026 10:00–18:00\n\n05.01", TODAY)
    assert days == [
        {"date": "2026-10-13", "start": "10:00", "end": "18:00"},
        {"date": "2026-10-14", "start": "10:00", "end": "17:00"},
        {"date": "2027-01-05", "start": None, "end": None},  # без года, уже прошло в этом году → следующий
    ]
    assert format_days(days).splitlines()[0] == "13.10.2026 10:00–18:00"


@pytest.mark.parametrize(
    "raw",
    ["", "завтра", "13.10 18:00-10:00", "31.02", "01.10.2026", "13.10 25:00-26:00", "\n".join(["13.10"] * 15)],
    ids=["empty", "words", "end-before-start", "no-such-date", "past-with-year", "bad-time", "too-many"],
)
def test_parse_days_rejects(raw: str) -> None:
    assert parse_days(raw, TODAY) is None


def test_parse_deadline() -> None:
    now = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)  # 12:00 по Москве
    assert parse_deadline("11.10 18:00", now, MSK) == "2026-10-11T18:00:00+03:00"
    assert parse_deadline("11.10", now, MSK) == "2026-10-11T23:59:00+03:00"
    assert parse_deadline("02.10 11:00", now, MSK) is None  # сегодня, но уже прошло
    assert parse_deadline("в пятницу", now, MSK) is None
    field = SESSION_SPEC.field("registration_deadline")
    assert parse_field(field, "11.10 18:00", now, MSK) == "2026-10-11T18:00:00+03:00"


@pytest.mark.parametrize(
    ("raw", "kopecks"),
    [("45000", 4_500_000), ("45 000 ₽", 4_500_000), ("80000 руб", 8_000_000), ("0", None), ("сорок", None),
     ("99999999", None)],
)
def test_parse_rubles(raw: str, kopecks: int | None) -> None:
    assert parse_rubles(raw) == kopecks


def test_valid_prices() -> None:
    good = [{"kind": "full", "label": "Полный курс", "amount": 4_500_000},
            {"kind": "group", "label": "Для двух коллег", "amount": 8_000_000}]
    assert valid_prices(good) and valid_prices(None)
    assert not valid_prices([{"kind": "vip", "label": "VIP", "amount": 1}])  # не из 4 категорий
    assert not valid_prices([{"kind": "full", "label": "", "amount": 1}])
    assert not valid_prices([{"kind": "full", "label": "Полный", "amount": 0}])
    assert not valid_prices("Полный курс 45000")


def test_session_fields_by_format() -> None:
    venue, link = SESSION_SPEC.field("venue_id"), SESSION_SPEC.field("online_url")
    assert venue.applies({"format": "offline"}) and not link.applies({"format": "offline"})
    assert link.applies({"format": "online"}) and not venue.applies({"format": "online"})
    assert venue.applies({"format": "hybrid"}) and link.applies({"format": "hybrid"})
    # Курс у проведения и статус не правятся через «✏️» (курс фиксирован, статус — кнопками в карточке).
    assert not SESSION_SPEC.field("program_id").editable and not SESSION_SPEC.field("status").editable


def _ses(action: DictAction, **kwargs: object) -> str:
    return DictCb(kind=DictKind.SESSIONS, action=action, **kwargs).pack()


def _crs(action: DictAction, **kwargs: object) -> str:
    return DictCb(kind=DictKind.COURSES, action=action, **kwargs).pack()


def _rows(markup) -> list[list[str]]:
    return [[b.text for b in row] for row in markup.inline_keyboard]


def test_month_helpers() -> None:
    assert month_key(date(2026, 10, 5)) == 202610
    assert month_bounds(202610) == (date(2026, 10, 1), date(2026, 11, 1))
    assert month_bounds(202612) == (date(2026, 12, 1), date(2027, 1, 1))
    assert month_bounds(202613) is None and month_bounds(0) is None and month_bounds(-5) is None  # подделанная кнопка
    assert format_month(202701) == "Январь 2027"


# --- Три раздела проведений: афиша, архив, прошедшие -----------------------------------------------------------

@pytest.mark.parametrize(
    ("archived", "end_date", "expected"),
    [
        (False, date(2026, 10, 2), SessionScope.ACTIVE),  # идёт сегодня — ещё в афише
        (False, date(2026, 11, 20), SessionScope.ACTIVE),
        (False, None, SessionScope.ACTIVE),  # без дат — в афише, пока не убрали
        (False, date(2026, 10, 1), SessionScope.PAST),  # прошло вчера — само ушло в «прошедшие»
        (True, date(2026, 11, 20), SessionScope.ARCHIVED),
        (True, None, SessionScope.ARCHIVED),
        (True, date(2026, 10, 1), SessionScope.ARCHIVED),  # убрано вручную — остаётся в архиве и после дат
    ],
    ids=["today", "future", "no-dates", "yesterday", "archived", "archived-no-dates", "archived-and-past"],
)
def test_session_scope(archived: bool, end_date: date | None, expected: SessionScope) -> None:
    assert session_scope(archived, end_date, TODAY) is expected


def _service() -> AdminSessionService:
    service = AdminSessionService.__new__(AdminSessionService)  # без БД: только правила
    service.today, service.admin_telegram_id = TODAY, 1
    return service


class _Dates:
    def __init__(self, dates: list[date | None]) -> None:
        self.dates = dates

    async def start_dates(self, scope: SessionScope, today: date) -> list[date | None]:
        return self.dates


def test_months_put_sessions_without_dates_last() -> None:
    service = _service()
    service.repo = _Dates([date(2026, 11, 20), None, date(2026, 10, 13), date(2026, 10, 27), None])
    assert asyncio.run(service.months()) == [(202610, 2), (202611, 1), (NO_DATES, 2)]
    service.repo = _Dates([])
    assert asyncio.run(service.months(SessionScope.PAST)) == []


def test_days_are_optional_session_can_have_no_dates() -> None:
    days = SESSION_SPEC.field("days")
    assert not days.required and days.editable
    # В мастере вместо «Пропустить» — «Даты уточняются», при правке вместо «Очистить» — она же.
    _, markup = admin_views.field_prompt(SESSION_SPEC, days, "h", None)
    assert _buttons(markup) == [texts.BTN_ADMIN_NO_DATES, texts.BTN_ADMIN_CANCEL]
    saved = [{"date": "2026-10-13", "start": "10:00", "end": "18:00"}]
    text, markup = admin_views.field_prompt(SESSION_SPEC, days, "h", saved, item_id=7)
    assert _buttons(markup) == [texts.BTN_ADMIN_NO_DATES, texts.BTN_ADMIN_CANCEL] and "13.10.2026" in text
    # Дат и так нет — убирать нечего.
    assert _buttons(admin_views.field_prompt(SESSION_SPEC, days, "h", None, item_id=7)[1]) == [texts.BTN_ADMIN_CANCEL]


def test_session_dates_can_be_removed_and_set_again() -> None:
    service = _service()
    item = SimpleNamespace(days=[SimpleNamespace()], start_date=date(2026, 10, 13), end_date=date(2026, 10, 14),
                           title_override=None, program=SimpleNamespace(title="Имплантация"), archived_at=None)
    field = SESSION_SPEC.field("days")
    assert asyncio.run(service._apply(item, field, None))
    assert (item.days, item.start_date, item.end_date) == ([], None, None)
    assert service.title(item) == "без дат · Имплантация"
    assert service.month_of(item) == NO_DATES and not service.is_past(item)
    assert service.scope(item) is SessionScope.ACTIVE  # без дат проведение остаётся в афише
    days = [{"date": "2026-10-13", "start": "10:00", "end": "18:00"}, {"date": "2026-10-14", "start": None, "end": None}]
    assert asyncio.run(service._apply(item, field, days))
    assert (item.start_date, item.end_date) == (date(2026, 10, 13), date(2026, 10, 14))
    assert service.month_of(item) == 202610 and service.title(item) == "13–14 октября · Имплантация"


class _SessionRepo:
    def __init__(self, item: SimpleNamespace | None, enrollments: bool = False) -> None:
        self.item, self.enrollments, self.deleted = item, enrollments, None

    async def get(self, session_id: int) -> SimpleNamespace | None:
        return self.item

    async def has_enrollments(self, session_ids: list[int]) -> bool:
        return self.enrollments

    async def delete(self, session_ids: list[int]) -> None:
        self.deleted = list(session_ids)


def _session_item(archived: bool = False, end: date | None = date(2026, 11, 20),
                  course_archived: bool = False) -> SimpleNamespace:
    moment = datetime.now(UTC)
    return SimpleNamespace(id=7, archived_at=moment if archived else None, start_date=end, end_date=end, status=None,
                           program=SimpleNamespace(archived_at=moment if course_archived else None))


def test_session_archive_and_restore_rules() -> None:
    service, item = _service(), _session_item()
    service.repo = _SessionRepo(item)
    assert asyncio.run(service.set_active(7, False)) is item and service.scope(item) is SessionScope.ARCHIVED
    assert asyncio.run(service.set_active(7, True)) is item and service.scope(item) is SessionScope.ACTIVE
    # Курс в архиве — проведение из архива не вернуть: в афише его всё равно не было бы видно.
    orphan = _session_item(archived=True, course_archived=True)
    service.repo = _SessionRepo(orphan)
    assert asyncio.run(service.set_active(7, True)) is None and orphan.archived_at is not None


def test_session_delete_and_edit_only_where_allowed() -> None:
    service = _service()
    service.repo = _SessionRepo(_session_item())
    assert asyncio.run(service.delete(7)) is DeleteResult.NOT_IN_ARCHIVE  # из афиши удалить нельзя — сначала в архив
    assert service.repo.deleted is None
    for item in (_session_item(archived=True), _session_item(end=date(2026, 10, 1))):  # архив и прошедшее
        service.repo = _SessionRepo(item)
        # Править и менять статус там нельзя: карточка «как была».
        assert asyncio.run(service.update_field(7, SESSION_SPEC.field("capacity"), 5)) is SaveResult.NOT_FOUND
        assert asyncio.run(service.set_status(7, "full")) is None
        assert asyncio.run(service.delete(7)) is DeleteResult.OK and service.repo.deleted == [7]
    service.repo = _SessionRepo(_session_item(archived=True), enrollments=True)
    assert asyncio.run(service.delete(7)) is DeleteResult.IN_USE and service.repo.deleted is None  # есть заявки
    service.repo = _SessionRepo(None)
    assert asyncio.run(service.delete(7)) is DeleteResult.NOT_FOUND


# --- Курс: архив и удаление ------------------------------------------------------------------------------------

class _CourseSessions:
    """Проведения курса для проверки без БД."""

    def __init__(self, in_afisha: int = 0, ids: tuple[int, ...] = (), enrollments: bool = False) -> None:
        self.in_afisha, self.ids, self.enrollments, self.deleted = in_afisha, ids, enrollments, None

    async def active_count_for_program(self, program_id: int, today: date) -> int:
        return self.in_afisha

    async def ids_for_program(self, program_id: int) -> list[int]:
        return list(self.ids)

    async def has_enrollments(self, session_ids: list[int]) -> bool:
        return self.enrollments

    async def delete(self, session_ids: list[int]) -> None:
        self.deleted = list(session_ids)


class _CourseRepo:
    def __init__(self, program: SimpleNamespace | None, interests: bool = False) -> None:
        self.program, self.interests, self.deleted = program, interests, None

    async def get(self, program_id: int) -> SimpleNamespace | None:
        return self.program

    async def has_interests(self, program_id: int) -> bool:
        return self.interests

    async def delete(self, program_id: int) -> None:
        self.deleted = program_id


def _course_service(program: SimpleNamespace | None, sessions: _CourseSessions,
                    interests: bool = False) -> AdminCourseService:
    service = AdminCourseService.__new__(AdminCourseService)
    service.repo, service.sessions = _CourseRepo(program, interests), sessions
    service.admin_telegram_id, service.today = 1, TODAY
    return service


def test_course_with_sessions_in_afisha_cannot_be_archived() -> None:
    """Из раздела «Курсы» афиша не меняется: пока у курса есть проведения в афише, в архив он не уходит."""
    program = SimpleNamespace(id=5, archived_at=None)
    service = _course_service(program, _CourseSessions(in_afisha=2))
    assert asyncio.run(service.set_active(5, False)) is None and program.archived_at is None
    service.sessions.in_afisha = 0
    assert asyncio.run(service.set_active(5, False)) is program and program.archived_at is not None
    # В архиве курс не правится; вернуть можно всегда.
    assert asyncio.run(service.update_field(5, COURSE_SPEC.field("title"), "Новое")) is SaveResult.NOT_FOUND
    assert asyncio.run(service.set_active(5, True)) is program and program.archived_at is None


def test_course_delete_rules() -> None:
    active = SimpleNamespace(id=5, archived_at=None)
    archived = SimpleNamespace(id=5, archived_at=datetime.now(UTC))
    assert asyncio.run(_course_service(active, _CourseSessions()).delete(5)) is DeleteResult.NOT_IN_ARCHIVE
    assert asyncio.run(_course_service(None, _CourseSessions()).delete(5)) is DeleteResult.NOT_FOUND
    blocked = _course_service(archived, _CourseSessions(in_afisha=1))
    assert asyncio.run(blocked.delete(5)) is DeleteResult.HAS_ACTIVE_SESSIONS
    with_applications = _course_service(archived, _CourseSessions(ids=(1,), enrollments=True))
    assert asyncio.run(with_applications.delete(5)) is DeleteResult.IN_USE
    with_interests = _course_service(archived, _CourseSessions(), interests=True)
    assert asyncio.run(with_interests.delete(5)) is DeleteResult.IN_USE
    assert blocked.repo.deleted is None and with_applications.repo.deleted is None
    # Курс удаляется вместе со своими проведениями (они все в архиве или прошли).
    service = _course_service(archived, _CourseSessions(ids=(1, 2)))
    assert asyncio.run(service.sessions_total(5)) == 2
    assert asyncio.run(service.delete(5)) is DeleteResult.OK
    assert service.sessions.deleted == [1, 2] and service.repo.deleted == 5


# --- Экраны ----------------------------------------------------------------------------------------------------

def test_session_months_screen() -> None:
    """Раздел «Проведения» — это афиша: месяцы в два столбца (только те, где проведения есть), отдельной кнопкой
    проведения без дат; прошедших здесь нет — они в архиве."""
    months = [(202610, 3), (202611, 1), (202701, 4), (NO_DATES, 2)]
    text, markup = admin_views.session_months(months)
    assert _rows(markup) == [["Октябрь 2026 · 3", "Ноябрь 2026 · 1"], ["Январь 2027 · 4"], ["⏳ Без дат · 2"],
                             [texts.BTN_ADMIN_ADD], [texts.BTN_ADMIN_BACK]]
    assert "<b>10</b>" in text  # столько сейчас в афише
    assert markup.inline_keyboard[0][0].callback_data == _ses(DictAction.MONTH, page=202610)
    assert markup.inline_keyboard[2][0].callback_data == _ses(DictAction.MONTH, page=NO_DATES)

    text, markup = admin_views.session_months([])
    assert "пока пусто" in text and _buttons(markup) == [texts.BTN_ADMIN_ADD, texts.BTN_ADMIN_BACK]

    # Очень много месяцев — по 16 на странице, со стрелками.
    many = [(202600 + m, 1) for m in range(1, 13)] + [(202700 + m, 1) for m in range(1, 9)]
    _, markup = admin_views.session_months(many)
    assert [len(row) for row in markup.inline_keyboard[:8]] == [2] * 8 and "1/2" in _buttons(markup)
    _, markup = admin_views.session_months(many, page=1)
    assert _buttons(markup)[:4] == ["Май 2027 · 1", "Июнь 2027 · 1", "Июль 2027 · 1", "Август 2027 · 1"]


def test_archive_months_screens() -> None:
    """В архиве и в прошедших свежие месяцы первыми, добавлять нельзя, назад — к архиву; раздел едет в кнопках."""
    text, markup = admin_views.session_months([(202608, 2), (202609, 1), (NO_DATES, 1)], SessionScope.ARCHIVED)
    assert _rows(markup) == [["Сентябрь 2026 · 1", "Август 2026 · 2"], ["⏳ Без дат · 1"],
                             [texts.BTN_ADMIN_TO_ARCHIVE]]
    assert texts.ADMIN_ARCHIVE_SESSIONS_TITLE in text and "<b>4</b>" in text
    assert markup.inline_keyboard[0][1].callback_data == _ses(DictAction.MONTH, page=202608, value="a")
    assert markup.inline_keyboard[1][0].callback_data == _ses(DictAction.MONTH, page=NO_DATES, value="a")

    text, markup = admin_views.session_months([(202609, 1)], SessionScope.PAST)
    assert texts.ADMIN_ARCHIVE_PAST_TITLE in text
    assert markup.inline_keyboard[0][0].callback_data == _ses(DictAction.MONTH, page=202609, value="p")
    text, markup = admin_views.session_months([], SessionScope.PAST)
    assert "пока пусто" in text and _buttons(markup) == [texts.BTN_ADMIN_TO_ARCHIVE]


def test_archive_menu() -> None:
    text, markup = admin_views.archive_menu(courses=2, sessions=3, past=7)
    assert _rows(markup) == [["📚 Курсы · 2"], ["🗓 Проведения · 3"], ["✔️ Прошедшие · 7"], [texts.BTN_ADMIN_BACK]]
    assert markup.inline_keyboard[0][0].callback_data == _crs(DictAction.LIST, value="a")
    assert markup.inline_keyboard[1][0].callback_data == _ses(DictAction.LIST, value="a")
    assert markup.inline_keyboard[2][0].callback_data == _ses(DictAction.LIST, value="p")
    assert all(f"<b>{n}</b>" in text for n in (2, 3, 7))


def test_session_card_in_afisha() -> None:
    markup = admin_views.session_card_markup(SESSION_SPEC, 7, "registration_open", index=1, total=3,
                                             prev_id=5, next_id=9)
    buttons = _buttons(markup)
    # Листание по проведениям месяца — первой строкой, как в афише.
    pager = markup.inline_keyboard[0]
    assert [b.text for b in pager] == ["◀️", "2/3", "▶️"]
    assert pager[0].callback_data == _ses(DictAction.VIEW, item_id=5)
    assert pager[2].callback_data == _ses(DictAction.VIEW, item_id=9)
    assert "✏️ Дни и время" in buttons and "✏️ Тарифы" in buttons
    assert "✏️ Курс" not in buttons and "✏️ Статус" not in buttons
    assert "✅ 🟢 Идёт набор" in buttons and "🔴 Мест нет" in buttons
    status_data = next(b.callback_data for row in markup.inline_keyboard for b in row if b.text == "🔴 Мест нет")
    assert status_data == _ses(DictAction.STATUS, item_id=7, value="full")
    # «Скрыть» больше нет: убрать из афиши — отправить в архив. Удаления в афише тоже нет.
    assert _rows(markup)[-3:] == [[texts.BTN_ADMIN_COPY], [texts.BTN_ADMIN_ARCHIVE], [texts.BTN_ADMIN_TO_MONTHS]]
    assert markup.inline_keyboard[-2][0].callback_data == _ses(DictAction.ARCHIVE, item_id=7)
    assert markup.inline_keyboard[-1][0].callback_data == _ses(DictAction.LIST)
    assert texts.BTN_ADMIN_DELETE not in buttons and texts.BTN_ADMIN_RESTORE not in buttons


def test_session_card_in_archive_and_past() -> None:
    """В архиве карточка «как была»: без правки и статусов. Вернуть можно убранное вручную, прошедшее — нет."""
    archived = admin_views.session_card_markup(SESSION_SPEC, 7, "full", SessionScope.ARCHIVED)
    assert _rows(archived) == [["1/1"], [texts.BTN_ADMIN_RESTORE], [texts.BTN_ADMIN_COPY], [texts.BTN_ADMIN_DELETE],
                               [texts.BTN_ADMIN_TO_MONTHS]]
    assert archived.inline_keyboard[1][0].callback_data == _ses(DictAction.RESTORE, item_id=7)
    assert archived.inline_keyboard[3][0].callback_data == _ses(DictAction.DELETE, item_id=7)
    assert archived.inline_keyboard[4][0].callback_data == _ses(DictAction.LIST, value="a")
    past = admin_views.session_card_markup(SESSION_SPEC, 7, "full", SessionScope.PAST)
    assert _rows(past) == [["1/1"], [texts.BTN_ADMIN_COPY], [texts.BTN_ADMIN_DELETE], [texts.BTN_ADMIN_TO_MONTHS]]
    assert past.inline_keyboard[-1][0].callback_data == _ses(DictAction.LIST, value="p")


def test_course_card_is_library_slider() -> None:
    """Курсы — не кнопка на каждый курс, а карточки со стрелками ◀️ n/N ▶️; все поля правятся из карточки."""
    markup = admin_views.course_card_markup(COURSE_SPEC, 5, has_program=True, index=1, total=4, prev_id=2, next_id=9)
    pager = markup.inline_keyboard[0]
    assert [b.text for b in pager] == ["◀️", "2/4", "▶️"]
    assert pager[0].callback_data == _crs(DictAction.VIEW, item_id=2)
    assert pager[2].callback_data == _crs(DictAction.VIEW, item_id=9)
    buttons = _buttons(markup)
    for field in COURSE_SPEC.fields:
        assert texts.BTN_ADMIN_EDIT_FIELD.format(label=texts.ADMIN_FIELD_LABELS[field.name]) in buttons
    assert [len(row) for row in markup.inline_keyboard[1:6]] == [2] * 5
    # Курс — запись библиотеки, афиша отсюда не меняется: ни проведений, ни «показывать без дат», ни удаления.
    assert _rows(markup)[6:] == [[texts.BTN_ADMIN_PROGRAM], [texts.BTN_ADMIN_ARCHIVE], [texts.BTN_ADMIN_NEW_COURSE],
                                 [texts.BTN_ADMIN_BACK]]
    assert markup.inline_keyboard[-2][0].callback_data == _crs(DictAction.ADD)
    assert markup.inline_keyboard[-3][0].callback_data == _crs(DictAction.ARCHIVE, item_id=5)


def test_course_card_in_archive() -> None:
    markup = admin_views.course_card_markup(COURSE_SPEC, 5, has_program=True, index=0, total=2, next_id=9,
                                            archived=True)
    assert _rows(markup) == [["1/2", "▶️"], [texts.BTN_ADMIN_PROGRAM], [texts.BTN_ADMIN_RESTORE],
                             [texts.BTN_ADMIN_DELETE], [texts.BTN_ADMIN_TO_ARCHIVE]]
    assert markup.inline_keyboard[2][0].callback_data == _crs(DictAction.RESTORE, item_id=5)
    assert markup.inline_keyboard[3][0].callback_data == _crs(DictAction.DELETE, item_id=5)
    no_program = admin_views.course_card_markup(COURSE_SPEC, 5, has_program=False, archived=True)
    assert texts.BTN_ADMIN_PROGRAM not in _buttons(no_program)


def test_session_wizard_picks_course_with_arrows() -> None:
    """Шаг «Курс» в мастере проведения — не кнопка на каждый курс, а карточки курсов со стрелками ◀️ n/N ▶️."""
    field = SESSION_SPEC.field("program_id")
    assert field.kind is FieldKind.COURSE
    assert "course" in texts.ADMIN_FIELD_ERRORS  # ответ на текст вместо кнопки
    intro, markup = admin_views.course_pick(SESSION_SPEC, field, "ШАГ 1", 5, index=1, total=4, prev_id=2, next_id=9)
    assert _rows(markup) == [["◀️", "2/4", "▶️"], [texts.BTN_ADMIN_PICK_COURSE], [texts.BTN_ADMIN_CANCEL]]
    pager = markup.inline_keyboard[0]
    assert pager[0].callback_data == _ses(DictAction.BROWSE, field="program_id", value="2")
    assert pager[2].callback_data == _ses(DictAction.BROWSE, field="program_id", value="9")
    # «Выбрать» берёт показанный курс — тем же действием, что и остальные поля-выборы мастера.
    assert markup.inline_keyboard[1][0].callback_data == _ses(DictAction.PICK, field="program_id", value="5")
    assert markup.inline_keyboard[2][0].callback_data == _ses(DictAction.CANCEL)
    assert intro.startswith("ШАГ 1") and texts.ADMIN_FIELD_HINTS["program_id"] in intro and "◀️ ▶️" in intro
    # Один курс в библиотеке — стрелок нет, и текст про них не говорит.
    intro, markup = admin_views.course_pick(SESSION_SPEC, field, "ШАГ 1", 5, index=0, total=1, prev_id=0, next_id=0)
    assert _rows(markup)[0] == ["1/1"] and "◀️" not in intro
    # Курсов нет совсем — выбирать не из чего: подсказка, куда идти, и только «Отмена».
    text, markup = admin_views.course_pick_empty(SESSION_SPEC, "ШАГ 1")
    assert "Курсы" in text and _rows(markup) == [[texts.BTN_ADMIN_CANCEL]]


def test_delete_confirm_and_popup_limits() -> None:
    markup = admin_views.delete_confirm_markup(DictKind.SESSIONS, 7)
    assert _rows(markup) == [[texts.BTN_ADMIN_DELETE_YES], [texts.BTN_ADMIN_CANCEL]]
    assert markup.inline_keyboard[0][0].callback_data == _ses(DictAction.PURGE, item_id=7)
    assert markup.inline_keyboard[1][0].callback_data == _ses(DictAction.VIEW, item_id=7)  # «Отмена» — к карточке
    # На каждую причину отказа есть объяснение; всплывающие окна Telegram — до 200 символов.
    assert set(texts.ADMIN_DELETE_ERRORS) == {result.value for result in DeleteResult} - {DeleteResult.OK.value}
    popups = [texts.ADMIN_COURSE_HAS_SESSIONS.format(count=999), texts.ADMIN_RESTORE_COURSE_FIRST,
              texts.ADMIN_COPY_COURSE_ARCHIVED, texts.ADMIN_DELETED_NOTICE, *texts.ADMIN_DELETE_ERRORS.values()]
    assert all(len(popup) <= 200 for popup in popups)


def test_session_prompts_ref_and_prices() -> None:
    venue = SESSION_SPEC.field("venue_id")
    text, markup = admin_views.field_prompt(SESSION_SPEC, venue, "h", 2, item_id=7,
                                            options=[(1, "Учебный класс"), (2, "Клиника <на Мира>")])
    assert "Клиника &lt;на Мира&gt;" in text  # «Сейчас:» — название площадки, экранированное
    assert _buttons(markup)[:2] == ["Учебный класс", "✅ Клиника <на Мира>"]
    prices = SESSION_SPEC.field("prices")
    assert prices.kind is FieldKind.PRICES
    _, markup = admin_views.field_prompt(SESSION_SPEC, prices, "h", None, selected=["group"])
    assert _buttons(markup) == ["Полный курс", "✅ Для двух коллег", "Только теория", "Практическая часть",
                                texts.BTN_ADMIN_DONE, texts.BTN_ADMIN_SKIP, texts.BTN_ADMIN_CANCEL]
    # Правка: «Сейчас» — сохранённые тарифы (подпись и цена), галочки — категории; есть «Очистить».
    saved = [{"kind": "full", "label": "Полный <курс>", "amount": 4_500_000}]
    text, markup = admin_views.field_prompt(SESSION_SPEC, prices, "h", saved, item_id=7, selected=["full", "theory"])
    assert f"• Полный &lt;курс&gt; — {format_rub(4_500_000)}" in text  # сумма — с неразрывными пробелами
    assert "✅ Только теория" in _buttons(markup) and texts.BTN_ADMIN_CLEAR in _buttons(markup)
    text, markup = admin_views.price_label_prompt(SESSION_SPEC, "h", 1, 2, "group")
    assert "Тариф 1 из 2: Для двух коллег" in text and _buttons(markup)[0] == "✅ «Для двух коллег»"
    text, _ = admin_views.price_amount_prompt(SESSION_SPEC, "h", 2, 2, "Вдвоём <скидка>", "per_group")
    assert "за группу из двух человек" in text and "Вдвоём &lt;скидка&gt;" in text
