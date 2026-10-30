"""Сквозные сценарии каталога в админке: мастера (лектор, площадка, направление, курс с программой из .docx
и обложкой, проведение с днями, тарифами и сроком записи), правка и очистка полей, копия проведения, архив,
«Удалить навсегда». Новое проведение сразу появляется в афише у клиента."""

from datetime import timedelta
from io import BytesIO

from docx import Document as DocxDocument
from sqlalchemy import func, select

from stubbot.db.enums import PriceUnit, SessionStatus
from stubbot.db.models import CourseSession, Lecturer, PriceOption, Program, Specialty, Venue
from stubbot.services.admin_dictionaries import DictKind, FieldKind
from stubbot.services.admin_registry import ADMIN_SPECS
from stubbot.tg import texts
from stubbot.tg.states import AdminDict
from tests.e2e.harness import RUB, TZ, Harness, Person

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx() -> bytes:
    document = DocxDocument()
    document.add_heading("День 1. Теория", level=1)
    paragraph = document.add_paragraph()
    paragraph.add_run("Планирование ").bold = True
    paragraph.add_run("имплантации <и> протокол & чек-лист")
    document.add_paragraph("Практика на моделях", style="List Bullet")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _button(person: Person, part: str):
    """Кнопка последнего сообщения, в данных которой есть part (например, «:pick:»)."""
    return next(b for b in person.last.buttons() if b.callback_data and part in b.callback_data)


async def fill_wizard(h: Harness, admin: Person, answers: dict | None = None, skip: tuple[str, ...] = (),
                      log: list[str] | None = None) -> None:
    """Пройти мастер «➕ Добавить» до конца: на каждом шаге - ответ по виду поля (или из answers), необязательные из
    skip - «Пропустить». Шаг мастера берётся из его состояния в FSM: kind, ask, step, price_stage."""
    answers = answers or {}
    today = h.catalog.main.start_date - timedelta(days=30)
    for _ in range(60):
        state = h.state(admin.id)
        if await state.get_state() == AdminDict.confirm.state:
            await admin.press(_button(admin, ":save:").callback_data, admin.last)
            return
        data = await state.get_data()
        assert data.get("mode") == "add", (await state.get_state(), admin.last_text()[:300])
        field = ADMIN_SPECS[DictKind(data["kind"])].field(data["ask"][data["step"]])
        stage = data.get("price_stage")
        if log is not None:
            log.append(f"{field.name}:{stage or ''}")
        if stage == "label":
            await admin.press(_button(admin, ":pick:").callback_data, admin.last)  # стандартная подпись тарифа
        elif stage == "amount":
            await admin.send("45000")
        elif field.name in answers:
            answer = answers[field.name]
            await (answer(admin) if callable(answer) else admin.send(answer))
        elif field.name in skip:
            await admin.click(texts.BTN_ADMIN_SKIP)
        else:
            match field.kind:
                case FieldKind.FULL_NAME:
                    await admin.send("Лекторов Пётр Петрович")
                case FieldKind.LINE:
                    await admin.send(f"E2E {field.name}")
                case FieldKind.HTML:
                    await admin.send("Описание <b>с тегами</b> & знаками")
                case FieldKind.URL:
                    await admin.send("ftp://нет")  # не ссылка http(s) - ошибка, мастер ждёт дальше
                    await admin.send("https://example.com/e2e")
                case FieldKind.NUMBER:
                    await admin.send("0")  # меньше допустимого
                    await admin.send("12")
                case FieldKind.CHOICE:
                    await admin.press(_button(admin, ":pick:").callback_data, admin.last)
                case FieldKind.MULTI:
                    await admin.send("99")  # номера нет в списке
                    await admin.send("1")
                case FieldKind.REF:
                    await admin.send("1")
                case FieldKind.COURSE:
                    await admin.press(_button(admin, ":br:").callback_data, admin.last)  # ▶️ - соседний курс
                    await admin.click(texts.BTN_ADMIN_PICK_COURSE)
                case FieldKind.PHOTO:
                    await admin.send("не фото")
                    await admin.send_photo()
                case FieldKind.DOCUMENT:
                    await admin.send_document("программа.pdf")  # не .docx / .txt
                    await admin.send_document("программа.docx", _docx(), mime=DOCX_MIME)
                case FieldKind.DAYS:
                    await admin.send(f"{today:%d.%m} 10:00-18:00")  # сегодня - уже поздно
                    first = today + timedelta(days=50)
                    await admin.send(f"{first:%d.%m.%Y} 10:00-18:00\n{first + timedelta(days=1):%d.%m.%Y} 10:00-17:00")
                case FieldKind.DATETIME:
                    await admin.send(f"{today + timedelta(days=45):%d.%m.%Y} 18:00")
                case FieldKind.PRICES:
                    toggles = [b for b in admin.last.buttons() if ":tgl:" in (b.callback_data or "")]
                    for button in toggles[:2]:
                        await admin.press(button.callback_data, admin.last)
                    await admin.press(_button(admin, ":done:").callback_data, admin.last)
                case _:
                    raise AssertionError(f"не знаю, как ответить на поле {field.name} ({field.kind})")
    raise AssertionError("мастер не закончился за 60 шагов")


async def _admin(h: Harness) -> Person:
    admin = h.admin()
    await admin.start()
    await admin.send(texts.BTN_ADMIN)
    return admin


async def _section(admin: Person, label: str) -> None:
    await admin.send(texts.BTN_ADMIN)
    await admin.click(label)


def test_dictionaries_wizards_edit_and_archive(e2e) -> None:
    async def scenario(h: Harness) -> None:
        admin = await _admin(h)
        await admin.click("Лекторы")
        await admin.click(texts.BTN_ADMIN_ADD)
        await admin.send_sticker_like()  # не текст - подсказка, мастер ждёт
        await fill_wizard(h, admin)
        lecturer = await h.scalar(select(Lecturer))
        assert lecturer.full_name == "Лекторов Пётр Петрович" and lecturer.bio_html.startswith("Описание &lt;b&gt;")
        # Правка поля и «Очистить».
        await admin.click(texts.BTN_ADMIN_EDIT_FIELD.format(label=texts.ADMIN_FIELD_LABELS["regalia"]))
        await admin.send("Д.м.н., профессор")
        assert (await h.scalar(select(Lecturer))).regalia == "Д.м.н., профессор"
        await admin.click(texts.BTN_ADMIN_EDIT_FIELD.format(label=texts.ADMIN_FIELD_LABELS["regalia"]))
        await admin.click(texts.BTN_ADMIN_CLEAR)
        assert (await h.scalar(select(Lecturer))).regalia is None
        await admin.click(texts.BTN_ADMIN_ARCHIVE)
        assert not (await h.scalar(select(Lecturer))).is_active
        await admin.click(texts.BTN_ADMIN_RESTORE)
        assert (await h.scalar(select(Lecturer))).is_active

        await _section(admin, "Площадки")
        await admin.click(texts.BTN_ADMIN_ADD)
        await fill_wizard(h, admin, skip=("directions_text",))
        assert await h.scalar(select(func.count()).select_from(Venue)) == 2

        await _section(admin, "Специальности")
        await admin.click(texts.BTN_ADMIN_ADD)
        await fill_wizard(h, admin, answers={"title": "Хирургия"})  # такое направление уже есть
        assert admin.alert() == texts.ADMIN_DUPLICATE
        await admin.send("текст на шаге предпросмотра")
        assert admin.sees(texts.REG_USE_BUTTONS)
        await admin.click(texts.BTN_ADMIN_CANCEL)
        assert await h.scalar(select(func.count()).select_from(Specialty)) == 3

    e2e.run(scenario)


def test_course_and_session_wizards_reach_the_schedule(e2e) -> None:
    async def scenario(h: Harness) -> None:
        admin = await _admin(h)
        await admin.click("Лекторы")
        await admin.click(texts.BTN_ADMIN_ADD)
        await fill_wizard(h, admin, skip=("regalia", "bio_html"))
        # Курс: направления и лекторы номерами, программа - файлом .docx, обложка - фото.
        await _section(admin, "Курсы")
        await admin.click(texts.BTN_ADMIN_NEW_COURSE)
        steps: list[str] = []
        await fill_wizard(h, admin, answers={"title": "Синус-лифтинг E2E"}, skip=("short_description",), log=steps)
        assert {"title:", "specialties:", "lecturers:", "program_html:", "cover_file_id:"} <= set(steps)
        course = await h.scalar(select(Program).where(Program.title == "Синус-лифтинг E2E"))
        assert course is not None and course.cover_file_id is not None
        assert "<b>День 1. Теория</b>" in course.program_html
        assert "имплантации &lt;и&gt; протокол &amp; чек-лист" in course.program_html
        assert "• Практика на моделях" in course.program_html
        await admin.click(texts.BTN_ADMIN_PROGRAM)
        assert "День 1. Теория" in admin.last_text()
        await admin.click(texts.BTN_ADMIN_BACK_TO_COURSE)

        # Проведение этого курса: дни, офлайн (площадка номером), места, два тарифа, срок записи, статус.
        await _section(admin, "Проведения")
        await admin.click(texts.BTN_ADMIN_ADD)

        async def pick_course(person: Person) -> None:
            for _ in range(10):
                if "Синус-лифтинг E2E" in (person.last.plain or ""):
                    break
                await person.click(texts.BTN_NEXT_PAGE)
            await person.click(texts.BTN_ADMIN_PICK_COURSE)

        async def offline(person: Person) -> None:
            await person.press(next(b.callback_data for b in person.last.buttons()
                                    if (b.callback_data or "").endswith(":offline")), person.last)

        async def open_status(person: Person) -> None:
            await person.press(next(b.callback_data for b in person.last.buttons()
                                    if (b.callback_data or "").endswith(":registration_open")), person.last)

        steps = []
        await fill_wizard(h, admin, answers={"program_id": pick_course, "format": offline, "status": open_status},
                          skip=("title_override", "cover_file_id"), log=steps)
        session = await h.scalar(select(CourseSession).where(CourseSession.program_id == course.id))
        assert session is not None, (steps, admin.last_text()[:500], admin.alert())
        assert session.status is SessionStatus.REGISTRATION_OPEN and session.capacity == 12
        assert session.start_date == h.catalog.main.start_date + timedelta(days=20)
        assert session.registration_deadline is not None and session.venue_id is not None
        prices = await h.scalars(select(PriceOption).where(PriceOption.session_id == session.id))
        assert len(prices) == 2 and all(p.amount == 45_000 * RUB for p in prices)
        assert any(p.unit is PriceUnit.PER_GROUP for p in prices) or len({p.kind for p in prices}) == 2

        # Клиент видит его в афише и может записаться.
        guest = h.person(9601, "Гость")
        await guest.register("+79990009601", "Афишева Анна Ивановна")
        await guest.open_schedule()
        await guest.go_to_course("Синус-лифтинг E2E")
        assert guest.has_button(texts.BTN_APPLY, guest.last)

        # Копия: спрашивает только новые дни; копия - тоже в афише.
        await admin.click(texts.BTN_ADMIN_COPY)
        start = session.start_date + timedelta(days=30)
        await fill_wizard(h, admin, answers={"days": f"{start:%d.%m.%Y} 10:00-18:00"})
        copies = await h.scalars(select(CourseSession).where(CourseSession.program_id == course.id)
                                 .order_by(CourseSession.id))
        assert len(copies) == 2 and copies[1].start_date == start and copies[1].capacity == 12
        assert len(await h.scalars(select(PriceOption).where(PriceOption.session_id == copies[1].id))) == 2

        # Курс с проведениями в афише не удалить; проведение - в архив, потом навсегда.
        await admin.click(texts.BTN_ADMIN_ARCHIVE)
        assert (await h.scalar(select(CourseSession).where(CourseSession.id == copies[1].id))).archived_at
        await admin.send(texts.BTN_ADMIN)
        await admin.click(texts.BTN_ADMIN_ARCHIVE_MENU)
        await admin.click(texts.BTN_ADMIN_ARCHIVE_SESSIONS.split(" · ")[0])
        for _ in range(3):  # раздел архива - месяцы, потом карточка
            if admin.has_button(texts.BTN_ADMIN_DELETE, admin.last):
                break
            await admin.press(next(b.callback_data for b in admin.last.buttons() if ":mon:" in (b.callback_data or "")
                                   or ":v:" in (b.callback_data or "")), admin.last)
        await admin.click(texts.BTN_ADMIN_DELETE)
        await admin.click(texts.BTN_ADMIN_DELETE_YES)
        assert await h.scalar(select(func.count()).select_from(CourseSession).where(
            CourseSession.program_id == course.id)) == 1

    e2e.run(scenario)
