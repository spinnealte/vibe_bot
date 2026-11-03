"""Демо-данные полной версии для ручной проверки: курсы, проведения, клиенты, записи, заказы, оплаты, аванс,
листы ожидания. Только для разработки - только локальная база stubbot_full!

Запуск из корня fullbot:
    python -m scripts.seed_full_demo            - создать (повторный запуск пересоздаёт те же данные заново)
    python -m scripts.seed_full_demo --remove   - удалить их и всё, что к ним привязано

Что создаётся (даты - от сегодняшнего дня, набор каждый раз одинаковый):
- 10 курсов, 7 лекторов, 2 площадки; 15 проведений: прошедшие, скоро (меньше двух недель - отменить самому
  нельзя), открытый набор, «мест нет» с листом ожидания, статус «лист ожидания», анонсы, без дат;
- 120 клиентов «в боте» и коллеги без Telegram, согласия, специальности и должности;
- больше 100 заказов: ждут оплаты (свежие и просроченные), частично и полностью оплачены, отменены клиентом
  (без оплаты, с переносом, авансом, возвратом) и админом; пары «для двух коллег»; платежи разными способами;
  аванс: зачисления, зачёт в заказ, возврат, ручная правка; листы ожидания курсов.

Telegram ID выдуманных клиентов - от 9 100 000 000 001: таких пользователей Telegram нет, сообщения им не
дойдут («chat not found» в логе) и никому постороннему не придут. Напоминания об оплате по демо-заказам
помечены отправленными (и будущие тоже) - админов не завалит; два заказа специально сработают на проверке
напоминаний: «сутки без оплаты» - примерно через 10 минут после запуска, «просрочено» - примерно через 20.
Если заказов в базе после удаления не осталось - нумерация снова с №100001.

Удаляются только свои данные: клиенты с заметкой DEMO_NOTE, курсы со slug «fdemo-», их проведения и всё,
что к ним привязано (в том числе записи и заказы других клиентов на эти проведения).
"""

import asyncio
import random
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, or_, select, text, update

from stubbot.config import get_settings
from stubbot.db.enums import (
    AdvanceReason,
    CancelChoice,
    ConsentType,
    DeliveryFormat,
    EnrollmentSource,
    EnrollmentStatus,
    InvoiceChannel,
    LegalDocumentType,
    OrderStatus,
    PayerType,
    PaymentMethod,
    PaymentProvider,
    PaymentState,
    PaymentStatus,
    PriceKind,
    PriceUnit,
    ProfileStatus,
    ProgramLevel,
    SessionStatus,
)
from stubbot.db.models import (
    Advance,
    Client,
    ClientConsent,
    ClientEvent,
    ClientSourceTouch,
    CourseSession,
    Enrollment,
    Lecturer,
    LegalDocument,
    Order,
    Payment,
    Position,
    PriceOption,
    Program,
    ProgramInterest,
    SessionDay,
    Specialty,
    Venue,
    program_lecturers,
    program_specialties,
)
from stubbot.db.models.orders import PAYMENT_HOURS
from stubbot.db.session import create_engine, create_session_factory

DEMO_DB = "stubbot_full"
SLUG_PREFIX = "fdemo-"
DEMO_NOTE = "демо-данные полной версии"
DEMO_MARK = "fdemo-seed"  # метка в служебном поле лекторов и площадок
TG_BASE = 9_100_000_000_000  # telegram_id выдуманных клиентов - таких пользователей Telegram нет
CLIENTS_IN_BOT = 120
SEED = 20261023
RUB = 100
CLIENT_CANCEL_REASON = "Отменена клиентом в боте"
ADMIN_REMOVE_REASON = "Бронь снята менеджером"

PROGRAM_TEMPLATE = """<b>День 1. Теория</b>
• {a}
• {b}
• Разбор клинических случаев

<b>День 2. Практика</b>
• {c}
• Работа в парах под контролем лектора
• Ответы на вопросы, сертификаты"""

LECTURERS = {
    "orlov": ("Орлов Дмитрий Андреевич", "к.м.н., хирург-имплантолог, стаж 18 лет"),
    "belova": ("Белова Ирина Викторовна", "терапевт-эндодонтист, работает с микроскопом с 2012 года"),
    "zakharov": ("Захаров Михаил Олегович", "ортопед, цифровые протоколы и CAD/CAM"),
    "kravtsova": ("Кравцова Елена Сергеевна", "к.м.н., детский стоматолог"),
    "gusev": ("Гусев Артём Игоревич", "пародонтолог, мягкотканная хирургия"),
    "tikhonova": ("Тихонова Мария Павловна", "врач-ортодонт, сертифицированный тренер"),
    "kim": ("Ким Виктор Александрович", "анестезиолог-реаниматолог, преподаватель неотложной помощи"),
}

# ключ: название, кратко, уровень, часы, лекторы, специальности, (три пункта программы)
PROGRAMS = {
    "implant": ("Немедленная имплантация и временные конструкции", "Двухдневный курс с практикой на фантомах",
                ProgramLevel.ADVANCED, 16, ["orlov"], ["Имплантация", "Хирургия"],
                ("Показания к немедленной имплантации", "Работа с лункой и мягкими тканями",
                 "Установка имплантата и временной коронки на фантоме")),
    "sinus": ("Синус-лифтинг: открытый и закрытый доступ", "Хирургия верхней челюсти без страха",
              ProgramLevel.ADVANCED, 8, ["orlov"], ["Хирургия", "Имплантация"],
              ("Анатомия и КЛКТ-диагностика пазухи", "Осложнения и как их избежать", "Синус-лифтинг на свиных головах")),
    "digital": ("Цифровой протокол: от сканирования до коронки", "Внутриротовой сканер, CAD/CAM, примерка",
                ProgramLevel.BASIC, 16, ["zakharov"], ["Ортопедия"],
                ("Сканирование: ошибки и как их видеть", "Моделирование и выбор материала",
                 "Полный цикл на сканере и фрезере")),
    "posterior": ("Прямая реставрация жевательной группы", "Контактные пункты и анатомия за одно посещение",
                  ProgramLevel.BASIC, 8, ["belova"], ["Терапия"],
                  ("Изоляция и адгезивные протоколы", "Матричные системы", "Реставрация моляра на модели")),
    "anterior": ("Эстетическая реставрация фронтальной группы", "Цвет, форма и послойное нанесение",
                 ProgramLevel.ADVANCED, 8, ["belova"], ["Терапия"],
                 ("Цвет и оптика композита", "Силиконовый ключ и послойная техника", "Реставрация резца на модели")),
    "perio": ("Пародонтальная хирургия и мягкие ткани", "Рецессии, трансплантаты, туннельная техника",
              ProgramLevel.ADVANCED, 16, ["gusev"], ["Пародонтология", "Хирургия"],
              ("Классификация рецессий и прогноз", "Забор соединительнотканного трансплантата",
               "Туннельная техника на свиных челюстях")),
    "kids": ("Детская стоматология: поведение и лечение", "Как лечить детей без слёз и стресса",
             ProgramLevel.BASIC, 8, ["kravtsova"], ["Детская стоматология"],
             ("Психология маленького пациента", "Лечение временных зубов", "Отработка приёмов на моделях")),
    "endo": ("Микроскоп в эндодонтии: перелечивание", "Удаление штифтов, обломков и закрытие перфораций",
             ProgramLevel.ADVANCED, 24, ["belova"], ["Терапия"],
             ("Диагностика перед перелечиванием", "Ультразвук и извлечение фрагментов",
              "Перелечивание на удалённых зубах под микроскопом")),
    "ortho": ("Брекет-системы для начинающих", "Диагностика, фиксация, первые дуги",
              ProgramLevel.BASIC, 16, ["tikhonova"], ["Ортодонтия"],
              ("Диагностика и ТРГ", "Выбор системы и позиционирование", "Фиксация брекетов на типодонте")),
    "anesthesia": ("Анестезия и неотложные состояния в кабинете", "Что делать, пока едет скорая",
                   ProgramLevel.BASIC, 6, ["kim"], [],
                   ("Местные анестетики: дозы и риски", "Обморок, анафилаксия, гипертонический криз",
                    "Сердечно-лёгочная реанимация на манекене")),
}


@dataclass
class SessionPlan:
    program: str
    start: int | None  # дней от сегодня; None - без дат
    days: int
    status: SessionStatus
    capacity: int | None
    full: int  # рублей за одного
    pair: int | None = None  # рублей за двоих, «Для двух коллег»
    early: int | None = None  # «Ранняя запись»
    venue: str = "ligovsky"
    seats: int = 0  # сколько мест занято записями (без отменённых)
    past: bool = False


SESSIONS = [
    SessionPlan("implant", -40, 2, SessionStatus.COMPLETED, 12, 52_000, 96_000, seats=11, past=True),
    SessionPlan("sinus", -20, 1, SessionStatus.COMPLETED, 10, 38_000, seats=8, past=True),
    SessionPlan("posterior", -7, 1, SessionStatus.COMPLETED, 14, 24_000, venue="moskovsky", seats=10, past=True),
    SessionPlan("posterior", 3, 1, SessionStatus.REGISTRATION_OPEN, 14, 24_000, 44_000, venue="moskovsky", seats=8),
    SessionPlan("implant", 9, 2, SessionStatus.REGISTRATION_OPEN, 12, 52_000, 96_000, seats=9),
    SessionPlan("digital", 16, 2, SessionStatus.REGISTRATION_OPEN, 10, 48_000, 88_000, early=42_000, seats=7),
    SessionPlan("anterior", 23, 1, SessionStatus.REGISTRATION_OPEN, 8, 27_000, 50_000, seats=7),
    SessionPlan("perio", 30, 2, SessionStatus.FULL, 6, 58_000, 108_000, seats=6),
    SessionPlan("kids", 37, 1, SessionStatus.REGISTRATION_OPEN, 16, 19_000, 35_000, venue="moskovsky", seats=9),
    SessionPlan("endo", 44, 3, SessionStatus.REGISTRATION_OPEN, 8, 65_000, 120_000, seats=5),
    SessionPlan("ortho", 51, 2, SessionStatus.WAITLIST, 10, 41_000, seats=10),
    SessionPlan("anesthesia", 58, 1, SessionStatus.REGISTRATION_OPEN, 20, 15_000, 27_000, venue="moskovsky",
                seats=10),
    SessionPlan("sinus", 65, 1, SessionStatus.ANNOUNCED, 10, 38_000),
    SessionPlan("digital", None, 1, SessionStatus.ANNOUNCED, None, 48_000),
    SessionPlan("kids", None, 1, SessionStatus.REGISTRATION_OPEN, None, 19_000, seats=6),
]
REMINDER_CHECK_SESSION = "anesthesia"  # сюда - два заказа для проверки напоминаний (там есть свободные места)

SURNAMES = ("Иванов", "Смирнов", "Кузнецов", "Попов", "Васильев", "Петров", "Соколов", "Михайлов", "Новиков",
            "Фёдоров", "Морозов", "Волков", "Алексеев", "Лебедев", "Семёнов", "Егоров", "Павлов", "Козлов",
            "Степанов", "Николаев", "Орлов", "Андреев", "Макаров", "Никитин", "Захаров", "Зайцев", "Соловьёв",
            "Борисов", "Яковлев", "Григорьев", "Романов", "Воробьёв", "Сергеев", "Фролов", "Александров")
MALE_NAMES = ("Александр", "Дмитрий", "Максим", "Сергей", "Андрей", "Алексей", "Артём", "Илья", "Кирилл", "Михаил",
              "Никита", "Роман", "Павел", "Владимир", "Евгений")
FEMALE_NAMES = ("Анна", "Мария", "Елена", "Ольга", "Наталья", "Екатерина", "Татьяна", "Ирина", "Светлана", "Юлия",
                "Дарья", "Анастасия", "Ксения", "Виктория", "Полина")
PATRONYMIC_ROOTS = ("Александров", "Дмитриев", "Сергеев", "Андреев", "Алексеев", "Михайлов", "Игорев", "Олегов",
                    "Викторов", "Николаев", "Владимиров", "Павлов")
CITIES = ("Санкт-Петербург",) * 7 + ("Москва", "Гатчина", "Выборг", "Всеволожск", "Великий Новгород", "Псков")
WORKPLACES = ("Стоматология «Улыбка»", "Клиника «Дентал-Плюс»", "Стоматологическая поликлиника №9",
              "МЕДИ на Невском", "Частная практика", "Dental Art", "«Зубная фея»", "Клиника доктора Захарова",
              "СМ-Стоматология", None, None)
# Судьба заказа на будущее проведение и её вес.
FATES = {"paid": 30, "partial": 14, "fresh": 22, "overdue": 10, "cancelled_unpaid": 9, "cancelled_paid": 9,
         "removed": 6}


def _person_name(rng: random.Random) -> str:
    root = rng.choice(PATRONYMIC_ROOTS)
    if rng.random() < 0.6:
        return f"{rng.choice(SURNAMES)}а {rng.choice(FEMALE_NAMES)} {root}на"
    return f"{rng.choice(SURNAMES)} {rng.choice(MALE_NAMES)} {root}ич"


def _round_thousands(kopecks: float) -> int:
    return max(round(kopecks / (1000 * RUB)), 1) * 1000 * RUB


def _guard() -> None:
    settings = get_settings()
    if settings.db_host not in ("localhost", "127.0.0.1") or settings.db_name != DEMO_DB:
        raise SystemExit(f"Демо-данные - только в локальной базе {DEMO_DB}. Сейчас в .env другая база - отказ.")


async def remove(session) -> dict[str, int]:
    client_ids = list(await session.scalars(select(Client.id).where(Client.admin_note == DEMO_NOTE)))
    program_ids = list(await session.scalars(select(Program.id).where(Program.slug.startswith(SLUG_PREFIX))))
    session_ids = list(await session.scalars(select(CourseSession.id).where(CourseSession.program_id.in_(program_ids))))
    order_ids = list(await session.scalars(select(Order.id).where(
        or_(Order.payer_client_id.in_(client_ids), Order.session_id.in_(session_ids)))))
    await session.execute(delete(Advance).where(or_(Advance.client_id.in_(client_ids), Advance.order_id.in_(order_ids))))
    await session.execute(delete(Payment).where(Payment.order_id.in_(order_ids)))
    await session.execute(delete(Enrollment).where(or_(Enrollment.client_id.in_(client_ids),
                                                       Enrollment.session_id.in_(session_ids),
                                                       Enrollment.order_id.in_(order_ids))))
    await session.execute(delete(Order).where(Order.id.in_(order_ids)))
    await session.execute(delete(ProgramInterest).where(or_(ProgramInterest.client_id.in_(client_ids),
                                                            ProgramInterest.program_id.in_(program_ids))))
    for model in (ClientConsent, ClientEvent, ClientSourceTouch):
        await session.execute(delete(model).where(model.client_id.in_(client_ids)))
    await session.execute(update(Client).where(Client.referred_by_client_id.in_(client_ids))
                          .values(referred_by_client_id=None))
    await session.execute(delete(Client).where(Client.id.in_(client_ids)))  # специальности, должности - CASCADE
    await session.execute(delete(PriceOption).where(PriceOption.session_id.in_(session_ids)))
    await session.execute(delete(CourseSession).where(CourseSession.id.in_(session_ids)))  # дни - CASCADE
    await session.execute(delete(Program).where(Program.id.in_(program_ids)))  # связи - CASCADE
    await session.execute(delete(Lecturer).where(Lecturer.bio_html == DEMO_MARK))
    await session.execute(delete(Venue).where(Venue.directions_text == DEMO_MARK))
    return {"клиентов": len(client_ids), "курсов": len(program_ids), "проведений": len(session_ids),
            "заказов": len(order_ids)}


@dataclass
class Planned:
    plan: SessionPlan
    session: CourseSession
    full: PriceOption
    pair: PriceOption | None


class Seeder:
    def __init__(self, session, now: datetime, tz: ZoneInfo) -> None:
        self.db = session
        self.now = now
        self.tz = tz
        self.today = now.astimezone(tz).date()
        self.rng = random.Random(SEED)
        self.stats: dict[str, int] = {}
        self.busy: dict[int, set[int]] = {}  # клиент → проведения, где у него уже есть запись (одна на проведение)
        self.interests: set[tuple[int, int]] = set()  # (клиент, курс) - одна подписка на курс
        self.offline = 0
        self.admin_id = get_settings().owner_telegram_id  # «кто менял аванс» - владелец бота

    def count(self, key: str, value: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + value

    def free_client(self, people: list[Client], session_id: int) -> Client:
        """Случайный клиент без записи на это проведение (и не заблокировавший бота) - сразу «занят» там."""
        client = self.rng.choice([c for c in people if session_id not in self.busy.get(c.id, set())
                                  and not c.is_bot_blocked])
        self.busy.setdefault(client.id, set()).add(session_id)
        return client

    def interest(self, client: Client, program_id: int, created: datetime) -> None:
        """Лист ожидания курса («Сообщить о наборе» или отмена без оплаты) - без повторов."""
        if (client.id, program_id) in self.interests:
            return
        self.interests.add((client.id, program_id))
        self.db.add(ProgramInterest(client_id=client.id, program_id=program_id, created_at=created))
        self.count("ждут курс")

    # --- Каталог -------------------------------------------------------------------------------------------------

    async def catalog(self) -> list[Planned]:
        venues = {
            "ligovsky": Venue(name="Учебный класс на Лиговском", address="Санкт-Петербург, Лиговский пр., 50, корп. 2",
                              map_url="https://yandex.ru/maps/?text=Лиговский%20проспект%2050", directions_text=DEMO_MARK),
            "moskovsky": Venue(name="Конференц-зал «Московский»", address="Санкт-Петербург, Московский пр., 97",
                               directions_text=DEMO_MARK),
        }
        lecturers = {key: Lecturer(full_name=name, regalia=regalia, bio_html=DEMO_MARK)
                     for key, (name, regalia) in LECTURERS.items()}
        self.db.add_all([*venues.values(), *lecturers.values()])
        specialties = {s.title: s for s in await self.db.scalars(select(Specialty))}
        programs: dict[str, Program] = {}
        for number, (key, (title, short, level, hours, _, _, points)) in enumerate(PROGRAMS.items(), start=1):
            programs[key] = Program(
                slug=f"{SLUG_PREFIX}{key}", title=title, short_description=short, level=level, duration_hours=hours,
                program_html=PROGRAM_TEMPLATE.format(a=points[0], b=points[1], c=points[2]),
                description_html=f"{short}. Курс для практикующих врачей: теория, разбор случаев и практика.",
                default_format=DeliveryFormat.OFFLINE, is_published=True, sort_order=100 + number * 10,
            )
        self.db.add_all(programs.values())
        await self.db.flush()
        for key, (_, _, _, _, lecturer_keys, specialty_titles, _) in PROGRAMS.items():
            await self.db.execute(program_lecturers.insert(), [
                {"program_id": programs[key].id, "lecturer_id": lecturers[k].id} for k in lecturer_keys])
            ids = [specialties[t].id for t in specialty_titles if t in specialties]
            if ids:
                await self.db.execute(program_specialties.insert(),
                                      [{"program_id": programs[key].id, "specialty_id": i} for i in ids])
        self.count("курсов", len(programs))
        self.count("лекторов", len(lecturers))

        result = []
        for plan in SESSIONS:
            start = self.today + timedelta(days=plan.start) if plan.start is not None else None
            end = start + timedelta(days=plan.days - 1) if start else None
            course_session = CourseSession(
                program_id=programs[plan.program].id, status=plan.status, format=DeliveryFormat.OFFLINE,
                venue_id=venues[plan.venue].id, start_date=start, end_date=end, capacity=plan.capacity,
                registration_deadline=(datetime.combine(start - timedelta(days=1), time(18), self.tz)
                                       if start and not plan.past else None),
            )
            self.db.add(course_session)
            await self.db.flush()
            if start:
                self.db.add_all([SessionDay(session_id=course_session.id, date=start + timedelta(days=i),
                                            start_time=time(10), end_time=time(18)) for i in range(plan.days)])
            full = PriceOption(session_id=course_session.id, kind=PriceKind.FULL, label="Полный курс",
                               amount=plan.full * RUB, sort_order=10)
            pair = (PriceOption(session_id=course_session.id, kind=PriceKind.GROUP, label="Для двух коллег",
                                amount=plan.pair * RUB, unit=PriceUnit.PER_GROUP, min_seats=2, max_seats=2,
                                sort_order=20) if plan.pair else None)
            self.db.add_all([full, *([pair] if pair else [])])
            if plan.early:
                self.db.add(PriceOption(session_id=course_session.id, kind=PriceKind.EARLY_BIRD,
                                        label="Ранняя запись", amount=plan.early * RUB, sort_order=5))
            await self.db.flush()
            result.append(Planned(plan, course_session, full, pair))
        self.count("проведений", len(result))
        return result

    # --- Клиенты -------------------------------------------------------------------------------------------------

    async def clients(self) -> list[Client]:
        documents = {doc.type: doc for doc in await self.db.scalars(select(LegalDocument).where(LegalDocument.is_current))}
        pd_doc = documents.get(LegalDocumentType.PD_CONSENT)
        marketing_doc = documents.get(LegalDocumentType.MARKETING_CONSENT)
        specialties = list(await self.db.scalars(select(Specialty).where(Specialty.is_active)))
        positions = list(await self.db.scalars(select(Position).where(Position.is_active)))
        rng, now = self.rng, self.now
        people = []
        arrivals = sorted(now - timedelta(days=rng.randint(10, 120), hours=rng.randint(0, 23))
                          for _ in range(CLIENTS_IN_BOT))
        for number, created in enumerate(arrivals, start=1):
            full_name = _person_name(rng)
            surname, first = full_name.split()[:2]
            people.append(Client(
                telegram_id=TG_BASE + number, tg_username=f"demo_doctor_{number:03d}" if rng.random() < 0.75 else None,
                tg_first_name=first, tg_last_name=surname, tg_language_code="ru",
                full_name=full_name, name_confirmed_at=created + timedelta(minutes=3),
                phone=f"+79995{number:06d}", phone_confirmed=True,
                email=f"doctor{number:03d}@example.com" if rng.random() < 0.6 else None,
                city=rng.choice(CITIES), workplace_raw=rng.choice(WORKPLACES),
                practice_since_year=rng.randint(1998, now.year - 1) if rng.random() < 0.8 else None,
                birth_date=(date(rng.randint(1968, 2000), rng.randint(1, 12), rng.randint(1, 28))
                            if rng.random() < 0.5 else None),
                profile_status=ProfileStatus.COMPLETED,
                referral_code=f"fd{number:03d}" + "".join(rng.choices("abcdefghkmnpqrstuvwxyz23456789", k=4)),
                admin_note=DEMO_NOTE, created_at=created, updated_at=created,
                last_seen_at=created + (now - created) * rng.random(),
                specialties=rng.sample(specialties, min(len(specialties), rng.randint(1, 2))),
                positions=rng.sample(positions, min(len(positions), 1)),
            ))
        # Особые случаи: длинное ФИО, HTML-символы в клинике, двое заблокировали бота.
        people[0].full_name = "Константинопольская-Задунайская Александра-Мария Вениаминовна"
        people[1].workplace_raw = "ООО «Зуб <32>» & партнёры"
        for client in (people[5], people[17]):
            client.is_bot_blocked = True
            client.bot_blocked_at = now - timedelta(days=2)
        self.db.add_all(people)
        await self.db.flush()
        for client in people[10:25]:  # пришли по приглашению коллеги
            referrer = people[rng.randint(30, 60)]
            client.referred_by_client_id = referrer.id
            client.start_param = f"ref_{referrer.referral_code}"
        if pd_doc and marketing_doc:
            for client in people:
                self.db.add(ClientConsent(client_id=client.id, type=ConsentType.PERSONAL_DATA,
                                          document_id=pd_doc.id, granted_at=client.created_at + timedelta(minutes=1)))
                if rng.random() < 0.65:
                    self.db.add(ClientConsent(client_id=client.id, type=ConsentType.MARKETING,
                                              document_id=marketing_doc.id,
                                              granted_at=client.created_at + timedelta(minutes=2)))
        self.count("клиентов в боте", len(people))
        return people

    async def offline_colleague(self) -> Client:
        """Коллега, которого записали вдвоём: без Telegram, заведён по ФИО и телефону."""
        self.offline += 1
        created = self.now - timedelta(days=self.rng.randint(1, 30))
        client = Client(full_name=_person_name(self.rng), phone=f"+79994{self.offline:06d}",
                        referral_code=f"fdo{self.offline:03d}" + "".join(self.rng.choices("abcdefghkmnpqrstuvwxyz", k=4)),
                        admin_note=DEMO_NOTE, created_at=created, updated_at=created, specialties=[], positions=[])
        self.db.add(client)
        await self.db.flush()
        self.count("коллег без Telegram")
        return client

    # --- Записи и заказы -----------------------------------------------------------------------------------------

    async def orders(self, sessions: list[Planned], people: list[Client]) -> list[tuple[Order, Client, int]]:
        """Заказы и записи по всем проведениям. Отменённые с оплатой - для раздела «Деньги после отмены»."""
        rng = self.rng
        cancelled_paid = []
        for item in sessions:
            plan, course_session = item.plan, item.session
            taken = 0
            while taken < plan.seats:
                as_pair = item.pair is not None and plan.seats - taken >= 2 and rng.random() < 0.25
                payer = self.free_client(people, course_session.id)
                colleague = None
                if as_pair:
                    colleague = (self.free_client(people, course_session.id) if rng.random() < 0.5
                                 else await self.offline_colleague())
                order, paid = await self._order(item, item.pair if as_pair else item.full, payer, colleague)
                if order.status is not OrderStatus.CANCELLED:
                    taken += 2 if as_pair else 1
                elif paid:
                    cancelled_paid.append((order, payer, paid))
            # «Мест нет» и «лист ожидания» - ещё и лист ожидания проведения (записи без заказа).
            if plan.status in (SessionStatus.FULL, SessionStatus.WAITLIST):
                for _ in range(4):
                    self._enrollment(course_session, self.free_client(people, course_session.id), None, None,
                                     EnrollmentStatus.WAITLIST, self.now - timedelta(days=rng.randint(1, 10)))
                    self.count("в листе ожидания проведения")
            # Анонсы: «Сообщить о наборе».
            if plan.status is SessionStatus.ANNOUNCED:
                for client in rng.sample(people, 8):
                    self.interest(client, course_session.program_id, self.now - timedelta(days=rng.randint(1, 20)))
        await self.db.flush()
        return cancelled_paid

    async def _order(self, item: Planned, price: PriceOption, payer: Client,
                     colleague: Client | None) -> tuple[Order, int]:
        """Один заказ (и записи участников) в правдоподобном состоянии. Сколько по нему оплачено."""
        rng, now, plan, course_session = self.rng, self.now, item.plan, item.session
        start = datetime.combine(course_session.start_date, time(10), self.tz) if course_session.start_date else None
        if plan.past:
            fate = rng.choices(["paid", "cancelled_unpaid"], weights=(90, 10))[0]
            created = start - timedelta(days=rng.randint(10, 40), hours=rng.randint(0, 23))
        else:
            fate = rng.choices(list(FATES), weights=list(FATES.values()))[0]
            if fate == "fresh":  # ждёт оплату, срок ещё не вышел
                created = now - timedelta(hours=rng.randint(1, 70), minutes=rng.randint(0, 59))
            else:
                created = now - timedelta(days=rng.randint(4, 25), hours=rng.randint(0, 23))
        total = price.amount
        payer_type = rng.choices([PayerType.INDIVIDUAL, PayerType.ORGANIZATION, None], weights=(70, 20, 10))[0]
        order = Order(payer_client_id=payer.id, session_id=course_session.id, price_option_id=price.id,
                      payer_type=payer_type, total_amount=total, due_at=created + timedelta(hours=PAYMENT_HOURS),
                      status=OrderStatus.AWAITING_PAYMENT, created_at=created, updated_at=created)
        payments: list[Payment] = []
        if fate in ("paid", "partial", "cancelled_paid"):
            if fate == "partial" or (fate == "cancelled_paid" and rng.random() < 0.5):
                parts = [_round_thousands(total * rng.choice((0.3, 0.4, 0.5)))]
            elif fate == "paid" and rng.random() < 0.25:  # двумя платежами
                first = _round_thousands(total * 0.5)
                parts = [first, total - first]
            else:
                parts = [total]
            moment = created + timedelta(hours=rng.randint(2, 60))
            for part in parts:
                method = rng.choices([PaymentMethod.INVOICE, PaymentMethod.CASH, PaymentMethod.CARD],
                                     weights=(6, 2, 3))[0]
                payments.append(Payment(amount=part, method=method, provider=PaymentProvider.MANUAL,
                                        state=PaymentState.SUCCEEDED, paid_at=min(moment, now), created_at=min(moment, now),
                                        updated_at=min(moment, now), created_by_telegram_id=self.admin_id,
                                        comment=DEMO_NOTE))
                moment += timedelta(days=rng.randint(1, 5))
            order.payer_type = order.payer_type or PayerType.INDIVIDUAL
            order.invoice_sent_at = created + timedelta(hours=1)
            order.invoice_channel = InvoiceChannel.BOT
            paid = sum(p.amount for p in payments)
            order.status = OrderStatus.PAID if paid >= total else OrderStatus.PARTIALLY_PAID
            if order.status is OrderStatus.PAID:
                order.paid_at = payments[-1].paid_at
        else:
            paid = 0
            if payer_type is not None and rng.random() < 0.4:
                order.invoice_sent_at = created + timedelta(hours=3)
                # Часть счетов «ушла на почту» - отмечена вручную (раздел «📨 Счёт отправлен», решение 27.10.2026).
                order.invoice_channel = InvoiceChannel.MANUAL if rng.random() < 0.3 else InvoiceChannel.BOT
        # Напоминания по демо-заказам - «уже отправлены», в том числе будущие: иначе свежие заказы сами дошли бы
        # до 24 и 72 часов и завалили админов. Для проверки напоминаний - два заказа из reminder_checks.
        order.payment_reminded_at = created + timedelta(hours=24)
        order.overdue_notified_at = order.due_at
        cancelled_at = None
        if fate in ("cancelled_unpaid", "cancelled_paid", "removed"):
            latest = min(start - timedelta(hours=1), now) if start else now
            cancelled_at = created + timedelta(days=rng.randint(1, 3))
            if payments:  # отменили уже после оплаты
                cancelled_at = max(cancelled_at, payments[-1].paid_at + timedelta(hours=1))
            cancelled_at = min(cancelled_at, latest - timedelta(minutes=30))
            order.status, order.cancelled_at = OrderStatus.CANCELLED, cancelled_at
            order.cancel_reason = ADMIN_REMOVE_REASON if fate == "removed" else CLIENT_CANCEL_REASON
            order.paid_at = None
            if fate == "cancelled_paid":
                order.cancel_choice = rng.choice(list(CancelChoice))
        self.db.add(order)
        await self.db.flush()  # номер заказа - из последовательности, по порядку
        for payment in payments:
            payment.order_id = order.id
        self.db.add_all(payments)

        status = self._enrollment_status(plan, order, fate)
        for person, enrolled_by in ((payer, None), *(((colleague, payer.id),) if colleague else ())):
            enrollment = self._enrollment(course_session, person, price, order, status, created, enrolled_by)
            if payments and status not in (EnrollmentStatus.CANCELLED_BY_CLIENT, EnrollmentStatus.CANCELLED_BY_ADMIN):
                enrollment.confirmed_at = payments[0].paid_at
                enrollment.payment_status = (PaymentStatus.PAID if order.status is OrderStatus.PAID
                                             else PaymentStatus.PARTIAL)
            if cancelled_at:
                enrollment.cancelled_at, enrollment.cancel_reason = cancelled_at, order.cancel_reason
            if fate in ("cancelled_unpaid", "removed"):  # без оплаты - в лист ожидания курса
                self.interest(person, course_session.program_id, cancelled_at)
        self.count("заказов")
        self.count(f"  {fate}")
        if colleague:
            self.count("  из них вдвоём с коллегой")
        return order, paid

    def _enrollment_status(self, plan: SessionPlan, order: Order, fate: str) -> EnrollmentStatus:
        if fate == "removed":
            return EnrollmentStatus.CANCELLED_BY_ADMIN
        if order.status is OrderStatus.CANCELLED:
            return EnrollmentStatus.CANCELLED_BY_CLIENT
        if plan.past:
            return EnrollmentStatus.NO_SHOW if self.rng.random() < 0.08 else EnrollmentStatus.ATTENDED
        if order.status is OrderStatus.AWAITING_PAYMENT:
            return EnrollmentStatus.APPLICATION
        return EnrollmentStatus.CONFIRMED

    def _enrollment(self, course_session: CourseSession, client: Client, price: PriceOption | None,
                    order: Order | None, status: EnrollmentStatus, created: datetime,
                    enrolled_by: int | None = None) -> Enrollment:
        enrollment = Enrollment(client_id=client.id, session_id=course_session.id,
                                price_option_id=price.id if price else None, order_id=order.id if order else None,
                                enrolled_by_client_id=enrolled_by, status=status, source=EnrollmentSource.BOT,
                                requested_seats=1, created_at=created, updated_at=created)
        self.db.add(enrollment)
        self.count("записей")
        return enrollment

    # --- Аванс ---------------------------------------------------------------------------------------------------

    async def advances(self, cancelled_paid: list[tuple[Order, Client, int]], sessions: list[Planned],
                       people: list[Client]) -> None:
        """Оплаченные и отменённые: у двух третей деньги уже распределены (аванс, возврат), остальные ждут админа
        в «💸 Деньги после отмены». Один перенос: аванс зачтён в заказ на другое проведение."""
        settled = [entry for index, entry in enumerate(cancelled_paid) if index % 3 != 2]
        for order, payer, paid in settled:
            moment = order.cancelled_at + timedelta(hours=5)
            self._advance(payer, order, paid, AdvanceReason.CANCELLED_ORDER, moment)
            if order.cancel_choice is CancelChoice.REFUND:
                self._advance(payer, order, -paid, AdvanceReason.REFUND, moment + timedelta(hours=1))
        transfers = sorted((entry for entry in settled if entry[0].cancel_choice is not CancelChoice.REFUND),
                           key=lambda entry: entry[0].cancel_choice is not CancelChoice.TRANSFER)
        if transfers:
            old_order, payer, paid = transfers[0]
            target = next((item for item in sessions if not item.plan.past and item.session.start_date
                           and item.plan.status is SessionStatus.REGISTRATION_OPEN
                           and item.session.id not in self.busy.get(payer.id, set())), None)
            if target is not None:
                await self._transfer(old_order, payer, paid, target)
        self._advance(people[42], None, 5_000 * RUB, AdvanceReason.CORRECTION, self.now - timedelta(days=3),
                      comment="подарок за отзыв")

    async def _transfer(self, old_order: Order, payer: Client, paid: int, target: Planned) -> None:
        created = min(old_order.cancelled_at + timedelta(days=1), self.now - timedelta(hours=2))
        applied = min(paid, target.full.amount)
        status = OrderStatus.PAID if applied >= target.full.amount else OrderStatus.PARTIALLY_PAID
        order = Order(payer_client_id=payer.id, session_id=target.session.id, price_option_id=target.full.id,
                      payer_type=PayerType.INDIVIDUAL, total_amount=target.full.amount,
                      due_at=created + timedelta(hours=PAYMENT_HOURS), status=status, created_at=created,
                      updated_at=created, payment_reminded_at=created + timedelta(hours=24),
                      overdue_notified_at=created + timedelta(hours=PAYMENT_HOURS))
        self.db.add(order)
        await self.db.flush()
        moment = created + timedelta(hours=1)
        self.db.add(Payment(order_id=order.id, amount=applied, method=PaymentMethod.ADVANCE,
                            provider=PaymentProvider.MANUAL, state=PaymentState.SUCCEEDED, paid_at=moment,
                            created_at=moment, updated_at=moment, created_by_telegram_id=self.admin_id,
                            comment=DEMO_NOTE))
        self._advance(payer, order, -applied, AdvanceReason.APPLIED_TO_ORDER, moment)
        self.busy.setdefault(payer.id, set()).add(target.session.id)
        enrollment = self._enrollment(target.session, payer, target.full, order, EnrollmentStatus.CONFIRMED, created)
        enrollment.confirmed_at = moment
        enrollment.payment_status = PaymentStatus.PAID if status is OrderStatus.PAID else PaymentStatus.PARTIAL
        self.count("заказов")
        self.count("  перенос: оплачен из аванса")

    def _advance(self, client: Client, order: Order | None, amount: int, reason: AdvanceReason, moment: datetime,
                 comment: str | None = None) -> None:
        self.db.add(Advance(client_id=client.id, order_id=order.id if order else None, amount=amount, reason=reason,
                            created_by_telegram_id=self.admin_id, comment=comment, created_at=moment))
        self.count("строк аванса")

    # --- Для проверки напоминаний --------------------------------------------------------------------------------

    async def reminder_checks(self, sessions: list[Planned], people: list[Client]) -> None:
        """Два свежих заказа, на которых сработают напоминания: «сутки без оплаты» - через ~10 минут,
        «просрочено» - через ~20 (фоновая задача проверяет раз в 5 минут)."""
        item = next(i for i in sessions if i.plan.program == REMINDER_CHECK_SESSION and i.session.start_date)
        for minutes, overdue in ((10, False), (20, True)):
            client = self.free_client(people, item.session.id)
            created = self.now - timedelta(hours=PAYMENT_HOURS if overdue else 24) + timedelta(minutes=minutes)
            order = Order(payer_client_id=client.id, session_id=item.session.id, price_option_id=item.full.id,
                          payer_type=None, total_amount=item.full.amount,
                          due_at=created + timedelta(hours=PAYMENT_HOURS), status=OrderStatus.AWAITING_PAYMENT,
                          created_at=created, updated_at=created,
                          payment_reminded_at=created + timedelta(hours=24) if overdue else None)
            self.db.add(order)
            await self.db.flush()
            self._enrollment(item.session, client, item.full, order, EnrollmentStatus.APPLICATION, created)
            self.count("заказов")
            self.count("  для проверки напоминаний")


async def create(session, now: datetime, tz: ZoneInfo) -> None:
    removed = await remove(session)
    if await session.scalar(select(Order.id).limit(1)) is None:
        # Заказов в базе не осталось - номера снова с 100001 (последовательность сама не откатывается).
        await session.execute(text("ALTER SEQUENCE order_number_seq RESTART WITH 100001"))
    seeder = Seeder(session, now, tz)
    sessions = await seeder.catalog()
    people = await seeder.clients()
    cancelled_paid = await seeder.orders(sessions, people)
    await seeder.advances(cancelled_paid, sessions, people)
    await seeder.reminder_checks(sessions, people)
    await session.flush()
    if any(removed.values()):
        print("Прежние демо-данные удалены:", ", ".join(f"{k} {v}" for k, v in removed.items()))
    print("Демо-данные полной версии созданы:")
    for key, value in seeder.stats.items():
        print(f"  {key}: {value}")


async def main() -> None:
    _guard()
    settings = get_settings()
    engine = create_engine(settings.database_url, settings.db_schema)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            if "--remove" in sys.argv:
                removed = await remove(session)
                print("Демо-данные полной версии удалены:", ", ".join(f"{k} {v}" for k, v in removed.items()))
            else:
                await create(session, datetime.now(UTC), ZoneInfo(settings.timezone))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
