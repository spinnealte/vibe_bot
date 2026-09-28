"""Демо-данные для проверки расписания, пока нет админки. Только для разработки!

Запуск из корня stubBot:
    python -m scripts.seed_demo            — создать (повторный запуск ничего не дублирует)
    python -m scripts.seed_demo --remove   — удалить демо-курсы и всё, что к ним привязано (в т.ч. заявки на них)

Даты считаются от сегодняшнего дня, так что расписание всегда «ближайшее».
"""

import asyncio
import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from stubbot.config import get_settings
from stubbot.db.enums import DeliveryFormat, PriceKind, PriceUnit, ProgramLevel, SessionStatus
from stubbot.db.models import (
    CourseSession,
    Enrollment,
    Lecturer,
    PriceOption,
    Program,
    ProgramInterest,
    SessionDay,
    Specialty,
    Venue,
    program_specialties,
    session_lecturers,
)
from stubbot.db.session import create_engine, create_session_factory
from stubbot.utils.dates import local_today

DEMO_SLUG_PREFIX = "demo-"
DEMO_MARK = "demo-seed"  # метка в служебном поле лекторов/площадки, чтобы удалить только своё
RUB = 100  # копеек в рубле

IMPLANT_PROGRAM = """<b>День 1. Планирование</b>
• Диагностика и КЛКТ: что смотреть перед операцией
• Выбор системы и размера имплантата
• Цифровое планирование и хирургические шаблоны

<b>День 2. Практика</b>
• Установка имплантатов на фантомах
• Формирование мягких тканей
• Разбор клинических случаев участников"""

# Длинная программа на несколько экранов — на ней проверяется листание полной программы.
ENDO_PROGRAM = """<b>Модуль 1. Микроскоп в эндодонтии</b>
• Эргономика: положение врача, ассистента и пациента
• Увеличение и освещение на разных этапах лечения
• Документирование: фото и видео через микроскоп

<b>Модуль 2. Диагностика и планирование</b>
• Чтение КЛКТ: дополнительные каналы, резорбции, трещины
• Прогноз зуба и выбор: лечить, перелечивать или удалять
• Разговор с пациентом о плане и рисках

<b>Модуль 3. Доступ и поиск каналов</b>
• Эндодонтический доступ без лишней потери тканей
• Поиск MB2 в молярах верхней челюсти
• Облитерированные каналы: ультразвук и терпение

<b>Модуль 4. Механическая обработка</b>
• Никель-титановые системы: ротация и реципрокация
• Изогнутые и S-образные каналы
• Как не сломать инструмент — и что делать, если сломали

<b>Модуль 5. Ирригация</b>
• Гипохлорит, ЭДТА, хлоргексидин: концентрации и порядок
• Активация: ультразвуковая, звуковая, лазерная
• Профилактика гипохлоритной аварии

<b>Модуль 6. Обтурация</b>
• Латеральная и вертикальная конденсация
• Биокерамические силеры: показания и ограничения
• Контроль качества на рентгенограмме

<b>Модуль 7. Перелечивание</b>
• Удаление гуттаперчи, паст и штифтов
• Извлечение фрагментов инструментов
• Закрытие перфораций MTA и биокерамикой

<b>Модуль 8. Практика на удалённых зубах</b>
• Полный цикл лечения моляра под микроскопом
• Разбор работ каждого участника с преподавателем
• Клинические случаи участников: приносите свои снимки"""


async def create(session, today: date, tz: ZoneInfo) -> None:
    if await session.scalar(select(Program.id).where(Program.slug.startswith(DEMO_SLUG_PREFIX)).limit(1)):
        print("Демо-данные уже есть. Удалить: python -m scripts.seed_demo --remove")
        return

    venue = Venue(name="Учебный класс на Невском", address="Санкт-Петербург, Невский пр., 1",
                  map_url="https://yandex.ru/maps/?text=Невский%20проспект%201", directions_text=DEMO_MARK)
    ivanov = Lecturer(full_name="Иванов Сергей Петрович",
                      regalia="к.м.н., челюстно-лицевой хирург, стаж 20 лет", bio_html=DEMO_MARK)
    smirnova = Lecturer(full_name="Смирнова Анна Олеговна",
                        regalia="пародонтолог, автор курсов по регенерации", bio_html=DEMO_MARK)
    session.add_all([venue, ivanov, smirnova])

    specialties = {s.title: s for s in await session.scalars(select(Specialty))}

    implant = Program(slug="demo-implant", title="Имплантация: от планирования до протезирования",
                      short_description="Двухдневный курс с практикой на фантомах",
                      program_html=IMPLANT_PROGRAM, level=ProgramLevel.BASIC, default_format=DeliveryFormat.OFFLINE,
                      duration_hours=16, nmo_points=14, is_published=True, sort_order=10)
    perio = Program(slug="demo-perio", title="Пародонтология: базовый курс",
                    short_description="Диагностика и лечение заболеваний пародонта",
                    level=ProgramLevel.BASIC, duration_hours=8, is_published=True, sort_order=20)
    endo = Program(slug="demo-endo", title="Эндодонтия под микроскопом",
                   short_description="Продвинутый курс для практикующих эндодонтистов",
                   description_html="Курс для тех, кто уже лечит каналы и хочет выйти на новый уровень точности.",
                   program_html=ENDO_PROGRAM, level=ProgramLevel.ADVANCED, duration_hours=24, is_published=True,
                   sort_order=30)
    aligners = Program(slug="demo-aligners", title="Элайнеры в практике ортодонта",
                       short_description="Планирование и ведение лечения на элайнерах",
                       level=ProgramLevel.ADVANCED, duration_hours=8, is_published=True, sort_order=40)
    session.add_all([implant, perio, endo, aligners])
    await session.flush()

    for program, titles in ((implant, ["Имплантация", "Хирургия"]), (perio, ["Пародонтология"]),
                            (endo, ["Терапия"]), (aligners, ["Ортодонтия"])):
        ids = [specialties[t].id for t in titles if t in specialties]
        if ids:
            await session.execute(program_specialties.insert(), [{"program_id": program.id, "specialty_id": i} for i in ids])

    # 1) Открыт набор, мало мест (3) — удобно проверять лист ожидания; два тарифа, дедлайн записи.
    s1_start = today + timedelta(days=14)
    s1 = CourseSession(program_id=implant.id, status=SessionStatus.REGISTRATION_OPEN, format=DeliveryFormat.OFFLINE,
                       venue_id=venue.id, start_date=s1_start, end_date=s1_start + timedelta(days=1), capacity=3,
                       registration_deadline=datetime.combine(s1_start - timedelta(days=2), time(18, 0), tz),
                       is_visible=True)
    # 2) Анонс: запись ещё не открыта — кнопка «Сообщить о наборе».
    s2_start = today + timedelta(days=45)
    s2 = CourseSession(program_id=perio.id, status=SessionStatus.ANNOUNCED, format=DeliveryFormat.OFFLINE,
                       venue_id=venue.id, start_date=s2_start, end_date=s2_start, is_visible=True)
    # 3) Мест нет — заявка уходит в лист ожидания.
    s3_start = today + timedelta(days=21)
    s3 = CourseSession(program_id=aligners.id, status=SessionStatus.FULL, format=DeliveryFormat.HYBRID,
                       venue_id=venue.id, start_date=s3_start, end_date=s3_start, capacity=10, is_visible=True)
    session.add_all([s1, s2, s3])
    await session.flush()

    session.add_all([
        SessionDay(session_id=s1.id, date=s1.start_date, start_time=time(10), end_time=time(18),
                   topic="Теория и планирование"),
        SessionDay(session_id=s1.id, date=s1.end_date, start_time=time(10), end_time=time(17),
                   topic="Практика на фантомах"),
        SessionDay(session_id=s2.id, date=s2.start_date, start_time=time(10), end_time=time(18)),
        SessionDay(session_id=s3.id, date=s3.start_date, start_time=time(11), end_time=time(19),
                   topic="Теория + разбор кейсов"),
        PriceOption(session_id=s1.id, kind=PriceKind.FULL, label="Полный курс", amount=45_000 * RUB, sort_order=10),
        PriceOption(session_id=s1.id, kind=PriceKind.GROUP, label="Для двух коллег", amount=80_000 * RUB,
                    unit=PriceUnit.PER_GROUP, min_seats=2, max_seats=2, sort_order=20),
        PriceOption(session_id=s3.id, kind=PriceKind.FULL, label="Участие", amount=25_000 * RUB, sort_order=10),
    ])
    await session.execute(session_lecturers.insert(), [
        {"session_id": s1.id, "lecturer_id": ivanov.id},
        {"session_id": s2.id, "lecturer_id": smirnova.id},
        {"session_id": s3.id, "lecturer_id": smirnova.id},
    ])
    print("Демо-данные созданы: 4 курса, 3 потока (набор / анонс / мест нет), площадка, 2 лектора.")


async def remove(session) -> None:
    program_ids = list(await session.scalars(select(Program.id).where(Program.slug.startswith(DEMO_SLUG_PREFIX))))
    session_ids = list(await session.scalars(select(CourseSession.id).where(CourseSession.program_id.in_(program_ids))))
    await session.execute(delete(Enrollment).where(Enrollment.session_id.in_(session_ids)))
    await session.execute(delete(ProgramInterest).where(ProgramInterest.program_id.in_(program_ids)))
    await session.execute(delete(PriceOption).where(PriceOption.session_id.in_(session_ids)))
    await session.execute(delete(CourseSession).where(CourseSession.id.in_(session_ids)))  # дни и лекторы — CASCADE
    await session.execute(delete(Program).where(Program.id.in_(program_ids)))  # специальности курса — CASCADE
    await session.execute(delete(Lecturer).where(Lecturer.bio_html == DEMO_MARK))
    await session.execute(delete(Venue).where(Venue.directions_text == DEMO_MARK))
    print(f"Демо-данные удалены: курсов {len(program_ids)}, потоков {len(session_ids)}.")


async def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            if "--remove" in sys.argv:
                await remove(session)
            else:
                await create(session, local_today(settings.timezone), ZoneInfo(settings.timezone))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
