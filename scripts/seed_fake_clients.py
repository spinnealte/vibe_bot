"""Выдуманные клиенты для ручной проверки админки и выгрузки. Только для разработки!

Запуск из корня stubBot:
    python -m scripts.seed_fake_clients            — создать 40 клиентов (повторный запуск пересоздаёт тех же)
    python -m scripts.seed_fake_clients --remove   — удалить их

Все данные вымышлены: telegram_id из диапазона 910000001…, телефоны +7 999 555-…, почта @example.com.
Настоящих людей за ними нет, написать им бот не может. В выгрузке их видно по заметке «тестовая запись».
Нужны справочники и документы согласий: сначала python -m scripts.seed.
"""

import asyncio
import random
import sys
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select, update

from stubbot.config import get_settings
from stubbot.db.enums import ClientEventType, ConsentType, LegalDocumentType, ProfileStatus
from stubbot.db.models import (
    Client,
    ClientConsent,
    ClientEvent,
    ClientSourceTouch,
    LegalDocument,
    Position,
    Specialty,
)
from stubbot.db.session import create_engine, create_session_factory
from stubbot.services.deeplinks import parse_start_payload

FAKE_ID_BASE = 910_000_000  # telegram_id выдуманных клиентов: 910000001 … 910000999
COUNT = 40
NOTE = "тестовая запись"
SEED = 20261005  # один и тот же набор при каждом запуске

SURNAMES = ("Иванов", "Смирнов", "Кузнецов", "Попов", "Васильев", "Петров", "Соколов", "Михайлов", "Новиков",
            "Фёдоров", "Морозов", "Волков", "Алексеев", "Лебедев", "Семёнов", "Егоров", "Павлов", "Козлов",
            "Степанов", "Николаев")
MALE_NAMES = ("Александр", "Дмитрий", "Максим", "Сергей", "Андрей", "Алексей", "Артём", "Илья", "Кирилл", "Михаил")
MALE_PATRONYMICS = ("Александрович", "Дмитриевич", "Сергеевич", "Андреевич", "Алексеевич", "Михайлович",
                    "Игоревич", "Олегович")
FEMALE_NAMES = ("Анна", "Мария", "Елена", "Ольга", "Наталья", "Екатерина", "Татьяна", "Ирина", "Светлана", "Юлия")
FEMALE_PATRONYMICS = ("Александровна", "Дмитриевна", "Сергеевна", "Андреевна", "Алексеевна", "Михайловна",
                      "Игоревна", "Олеговна")
CITIES = ("Санкт-Петербург", "Санкт-Петербург", "Санкт-Петербург", "Санкт-Петербург", "Москва", "Гатчина", "Выборг",
          "Великий Новгород", "Псков", "Мурино")
WORKPLACES = ("Стоматология «Улыбка»", "Клиника Дентал-Плюс", "Стоматологическая поликлиника №9",
              "МЕДИ на Невском", "Частная практика", "Dental Art")
# Хвосты ссылок: без ссылки, листовки, сайт, соцсети, хвост без приставки, приглашение друга.
START_PARAMS = (None, None, None, None, "src_flyer_mira", "src_flyer_mira", "src_flyer_nevsky", "src_site",
                "src_site", "src_vk", "src_inst", "src_tg_channel", "promo-2026", "ref", "ref")
PROFILE_KINDS = (ProfileStatus.COMPLETED, ProfileStatus.PARTIAL, ProfileStatus.NEW)


def _fake_ids():
    return Client.telegram_id.between(FAKE_ID_BASE + 1, FAKE_ID_BASE + 999)


async def remove(session) -> int:
    ids = list(await session.scalars(select(Client.id).where(_fake_ids())))
    if not ids:
        return 0
    await session.execute(update(Client).where(Client.referred_by_client_id.in_(ids))
                          .values(referred_by_client_id=None))
    for model in (ClientEvent, ClientSourceTouch, ClientConsent):
        await session.execute(delete(model).where(model.client_id.in_(ids)))
    await session.execute(delete(Client).where(Client.id.in_(ids)))  # специальности и должности — CASCADE
    return len(ids)


def _person(rng: random.Random) -> tuple[str, str, str]:
    """Фамилия, имя, отчество — согласованные по роду."""
    if rng.random() < 0.6:
        return rng.choice(SURNAMES) + "а", rng.choice(FEMALE_NAMES), rng.choice(FEMALE_PATRONYMICS)
    return rng.choice(SURNAMES), rng.choice(MALE_NAMES), rng.choice(MALE_PATRONYMICS)


async def create(session, now: datetime) -> None:
    documents = {doc.type: doc for doc in await session.scalars(select(LegalDocument).where(LegalDocument.is_current))}
    pd_doc = documents.get(LegalDocumentType.PD_CONSENT)
    marketing_doc = documents.get(LegalDocumentType.MARKETING_CONSENT)
    specialties = list(await session.scalars(select(Specialty).where(Specialty.is_active)))
    positions = list(await session.scalars(select(Position).where(Position.is_active)))
    if not (pd_doc and marketing_doc and specialties and positions):
        raise SystemExit("Нет справочников или документов согласий. Сначала: python -m scripts.seed")

    removed = await remove(session)
    rng = random.Random(SEED)
    # Сначала — когда кто пришёл: клиенты добавляются в порядке прихода, как настоящие.
    arrivals = sorted(now - timedelta(days=rng.randint(0, 45), hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
                      for _ in range(COUNT))
    clients: list[Client] = []
    wants_referrer: list[Client] = []
    for number, created in enumerate(arrivals, start=1):
        surname, name, patronymic = _person(rng)
        kind = rng.choices(PROFILE_KINDS, weights=(60, 20, 20))[0]
        start_param = rng.choice(START_PARAMS)
        client = Client(
            telegram_id=FAKE_ID_BASE + number,
            tg_username=f"fake_user_{number:02d}" if rng.random() < 0.7 else None,
            tg_first_name=name,
            tg_last_name=surname if rng.random() < 0.6 else None,
            tg_language_code="ru",
            referral_code=f"fake{number:02d}" + "".join(rng.choices("abcdefghkmnpqrstuvwxyz23456789", k=4)),
            profile_status=kind,
            start_param=start_param if start_param != "ref" else None,
            admin_note=NOTE,
            created_at=created,
            updated_at=created,
            last_seen_at=created + (now - created) * rng.random(),
            specialties=[],
            positions=[],
        )
        if kind is not ProfileStatus.NEW:  # дал согласие и поделился телефоном
            client.phone = f"+7999555{number:04d}"
            client.phone_confirmed = True
        if kind is ProfileStatus.COMPLETED:
            client.full_name = f"{surname} {name} {patronymic}"
            client.name_confirmed_at = created + timedelta(minutes=3)
            client.specialties = rng.sample(specialties, rng.randint(1, 3))
            client.positions = rng.sample(positions, rng.randint(1, 2))
            client.city = rng.choice(CITIES) if rng.random() < 0.8 else None
            client.workplace_raw = rng.choice(WORKPLACES) if rng.random() < 0.6 else None
            client.practice_since_year = rng.randint(1995, now.year - 1) if rng.random() < 0.7 else None
            client.email = f"user{number:02d}@example.com" if rng.random() < 0.5 else None
            client.birth_date = (date(rng.randint(1965, 2000), rng.randint(1, 12), rng.randint(1, 28))
                                 if rng.random() < 0.5 else None)
        if start_param == "ref":
            wants_referrer.append(client)
        clients.append(client)

    # Особые случаи — на них видно, как бот и выгрузка справляются с неудобными данными.
    completed = [c for c in clients if c.profile_status is ProfileStatus.COMPLETED]
    completed[0].full_name = "Константинопольский-Задунайский Александр-Мария Вениаминович"  # очень длинное ФИО
    completed[1].workplace_raw = "=СУММ(A1:A9)"  # похоже на формулу Excel — в выгрузке должно остаться текстом
    completed[2].workplace_raw = "ООО «Зуб <32>» & партнёры"  # знаки, которые нельзя вставлять в HTML как есть
    completed[3].tg_first_name = "Аня 🦷✨"  # эмодзи в имени Telegram
    for client in rng.sample(clients, 3):
        client.is_bot_blocked = True
        client.bot_blocked_at = client.last_seen_at

    session.add_all(clients)
    await session.flush()  # нужны id: для приглашений, согласий и журналов

    for client in wants_referrer:  # пришёл по ссылке друга — того, кто зарегистрировался раньше
        earlier = [c for c in completed if c.created_at < client.created_at]
        if earlier:
            referrer = rng.choice(earlier)
            client.referred_by_client_id = referrer.id
            client.start_param = f"ref_{referrer.referral_code}"

    with_marketing = 0
    for client in clients:
        created = client.created_at
        payload = parse_start_payload(client.start_param)
        session.add(ClientEvent(client_id=client.id, type=ClientEventType.START.value, created_at=created,
                                payload={"payload_kind": payload.kind.value if payload else None}))
        if client.start_param:
            session.add(ClientSourceTouch(client_id=client.id, raw_payload=client.start_param, is_first=True,
                                          created_at=created))
        if client.profile_status is ProfileStatus.NEW:
            continue
        session.add(ClientConsent(client_id=client.id, type=ConsentType.PERSONAL_DATA, document_id=pd_doc.id,
                                  granted_at=created + timedelta(minutes=1)))
        if rng.random() < 0.7:
            with_marketing += 1
            session.add(ClientConsent(client_id=client.id, type=ConsentType.MARKETING, document_id=marketing_doc.id,
                                      granted_at=created + timedelta(minutes=2)))
        if client.profile_status is ProfileStatus.COMPLETED:
            session.add(ClientEvent(client_id=client.id, type=ClientEventType.PROFILE_COMPLETED.value,
                                    created_at=created + timedelta(minutes=5), payload={}))

    by_kind = {kind: sum(1 for c in clients if c.profile_status is kind) for kind in PROFILE_KINDS}
    print(f"Выдуманные клиенты созданы: {len(clients)}" + (f" (прежние {removed} удалены)" if removed else ""))
    print(f"  анкета заполнена: {by_kind[ProfileStatus.COMPLETED]}, частично: {by_kind[ProfileStatus.PARTIAL]}, "
          f"только «Старт»: {by_kind[ProfileStatus.NEW]}")
    print(f"  пришли по ссылке: {sum(1 for c in clients if c.start_param)}, "
          f"из них по приглашению друга: {sum(1 for c in clients if c.referred_by_client_id)}; "
          f"согласны на рассылку: {with_marketing}; заблокировали бота: 3")


async def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            if "--remove" in sys.argv:
                print(f"Выдуманные клиенты удалены: {await remove(session)}")
            else:
                await create(session, datetime.now(UTC))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
