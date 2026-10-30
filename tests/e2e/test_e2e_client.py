"""Сквозные сценарии клиента: регистрация и согласия, афиша, запись (один, вдвоём, лист ожидания), оплата
физлицом и юрлицом, «Мои записи» и отмена, кабинет. Бот целиком - диспетчер, база, тексты, кнопки."""

from datetime import date

from sqlalchemy import func, select

from stubbot.db.enums import (
    ConsentType,
    EnrollmentStatus,
    OrderStatus,
    PayerType,
    ProfileStatus,
)
from stubbot.db.models import Client, ClientConsent, Enrollment, Order, ProgramInterest
from stubbot.tg import texts
from tests.e2e.harness import ADMIN_ID, OWNER_ID, RUB, Harness


async def _client(h: Harness, telegram_id: int) -> Client:
    return await h.scalar(select(Client).where(Client.telegram_id == telegram_id))


async def _admins_started(h: Harness) -> None:
    await h.owner().start()
    await h.admin().start()


# --- Регистрация и согласия --------------------------------------------------------------------------------------

def test_registration_with_all_fields_and_marketing(e2e) -> None:
    async def scenario(h: Harness) -> None:
        anna = h.person(5001, "Анна", "anna_e2e")
        await anna.register("+79990000001", "Иванова Анна Сергеевна", ads=True, specialties=2, positions=1,
                            optional={"city": "Санкт-Петербург", "workplace": "Клиника «Улыбка»", "experience": "7",
                                      "email": "Anna.Ivanova@Mail.ru", "birth": "15.04.1990"})
        client = await _client(h, 5001)
        assert client.phone == "+79990000001" and client.phone_confirmed
        assert client.full_name == "Иванова Анна Сергеевна" and client.name_confirmed_at is not None
        assert client.profile_status is ProfileStatus.COMPLETED
        assert (client.city, client.workplace_raw, client.email) == ("Санкт-Петербург", "Клиника «Улыбка»",
                                                                     "anna.ivanova@mail.ru")
        assert client.birth_date == date(1990, 4, 15) and client.practice_since_year == date.today().year - 7
        # Имя и @username из Telegram - только после согласия на ПД.
        assert client.tg_username == "anna_e2e"
        consents = await h.scalars(select(ClientConsent.type).where(ClientConsent.client_id == client.id,
                                                                            ClientConsent.revoked_at.is_(None)))
        assert set(consents) == {ConsentType.PERSONAL_DATA, ConsentType.MARKETING}

        await anna.send(texts.BTN_CABINET)
        cabinet = anna.last_text()
        for value in ("Иванова Анна Сергеевна", "+79990000001", "anna.ivanova@mail.ru", "Санкт-Петербург",
                      "15.04.1990", "7 лет", "Хирургия", "Имплантация", "Врач"):
            assert value in cabinet, value
        assert "ref_" + client.referral_code in cabinet

    e2e.run(scenario)


def test_nothing_is_saved_without_the_personal_data_checkbox(e2e) -> None:
    async def scenario(h: Harness) -> None:
        ivan = h.person(5002, "Иван", "ivan_e2e")
        await ivan.start()
        client = await _client(h, 5002)
        assert client.tg_username is None and client.tg_first_name is None  # до согласия - только Telegram ID
        await ivan.send(texts.BTN_CABINET)
        await ivan.click(texts.CONSENT_FORM_ADS)  # реклама без ПД дальше не пускает
        await ivan.click(texts.CONSENT_FORM_CONTINUE)
        assert ivan.alert() == texts.CONSENT_PD_CHECK_REQUIRED
        await ivan.send("Иванов Иван Иванович")  # текст вместо кнопок
        assert ivan.sees(texts.REG_USE_BUTTONS)
        await ivan.share_contact("+79990000002")  # контакт до согласия - не телефон регистрации
        client = await _client(h, 5002)
        assert client.phone is None and client.full_name is None
        assert await h.scalar(select(func.count()).select_from(ClientConsent)) == 0
        # Галочку поставил и снял - тоже нет согласия.
        await ivan.click(texts.CONSENT_FORM_PD)
        await ivan.click(texts.CONSENT_FORM_PD)
        await ivan.click(texts.CONSENT_FORM_CONTINUE)
        assert ivan.alert() == texts.CONSENT_PD_CHECK_REQUIRED

    e2e.run(scenario)


def test_phone_only_own_contact_and_not_taken(e2e) -> None:
    async def scenario(h: Harness) -> None:
        h.pii.update({"+79990000003", "79990000003"})
        first = h.person(5003, "Олег")
        await first.register("+79990000003", "Петров Олег Иванович")
        second = h.person(5004, "Пётр")
        await second.start()
        await second.send(texts.BTN_CABINET)
        await second.click(texts.CONSENT_FORM_PD)
        await second.click(texts.CONSENT_FORM_CONTINUE)
        await second.share_contact("+79990000004", own=False)  # чужой контакт из записной книжки
        assert second.sees(texts.REG_PHONE_FOREIGN_CONTACT)
        await second.send("+79990000004")  # номер текстом - только кнопкой
        assert second.sees(texts.REG_PHONE_USE_BUTTON)
        await second.share_contact("+79990000003")  # номер, который уже у другого клиента в боте
        assert second.sees(texts.REG_PHONE_TAKEN)
        assert (await _client(h, 5004)).phone is None

    e2e.run(scenario)


def test_name_validation_and_back_cancel(e2e) -> None:
    async def scenario(h: Harness) -> None:
        olga = h.person(5005, "Ольга")
        h.pii.update({"+79990000005", "79990000005", "Сидорова Ольга"})
        await olga.start()
        await olga.send(texts.BTN_CABINET)
        await olga.click(texts.CONSENT_FORM_PD)
        await olga.click(texts.CONSENT_FORM_CONTINUE)
        await olga.share_contact("+79990000005")
        for bad in ("Ольга", "Сидорова Ольга 1990", "Сидорова <b>Ольга</b>", "Я" * 301 + " Б"):
            await olga.send(bad)
            assert olga.last_text().startswith(texts.REG_NAME_INVALID[:20]), bad
        await olga.send_photo()  # фото вместо текста
        assert olga.sees(texts.REG_TEXT_ONLY)
        await olga.send("Сидорова Ольга")
        await olga.click(texts.REG_NAME_FIX)
        await olga.send(texts.BTN_BACK)  # назад - к телефону
        assert olga.sees(texts.REG_ASK_PHONE)
        await olga.send(texts.BTN_CANCEL)
        assert olga.sees(texts.REG_CANCELLED)
        assert texts.BTN_SCHEDULE in olga.reply_keyboard()
        # Вернулась - продолжает с того места, что не сохранено (телефон уже есть - сразу ФИО).
        await olga.send(texts.BTN_CABINET)
        assert olga.sees(texts.REG_ASK_FULL_NAME)

    e2e.run(scenario)


# --- Афиша -------------------------------------------------------------------------------------------------------

def test_schedule_carousel_program_and_notify(e2e) -> None:
    async def scenario(h: Harness) -> None:
        guest = h.person(5010, "Гость")
        await guest.start()
        await guest.open_schedule()
        # Слайды - по дате начала (ближайшее первым), проведение без дат - в конце. Одно сообщение на всю карусель.
        expected = ["Гигиена", "Имплантация", "Пародонтология", "Ортодонтия", "Имплантация"]
        carousel = guest.last
        assert carousel.photo is not None and expected[0] in carousel.plain
        seen = [carousel.plain]
        for _ in expected[1:]:
            await guest.click(texts.BTN_NEXT_PAGE)
            seen.append(guest.last.plain)
        assert guest.last.message_id == carousel.message_id  # листание меняет то же сообщение
        assert all(title in text for title, text in zip(expected, seen)), seen
        assert not guest.has_button(texts.BTN_NEXT_PAGE, guest.last)  # последний слайд
        assert "уточняются" in seen[-1]
        for _ in expected[1:-1]:
            await guest.click(texts.BTN_PREV_PAGE)
        assert "Имплантация" in guest.last.plain
        labels = [b.text for b in guest.last.buttons()]
        assert texts.BTN_APPLY in labels and texts.BTN_PROGRAM_TEXT in labels
        assert texts.BTN_MANAGER not in labels  # полная версия: менеджера под карточкой нет
        assert texts.BTN_COURSE_EDIT not in labels  # «Править» - только админам
        await guest.click(texts.BTN_PROGRAM_TEXT)
        assert "День 1" in guest.last_text() and guest.has_button(texts.BTN_MANAGER)
        await guest.click(texts.BTN_BACK_TO_COURSE)
        assert "Имплантация" in guest.last.plain and guest.last.photo is not None
        await guest.click(texts.BTN_CLOSE_SCHEDULE)
        assert guest.sees(texts.SCHEDULE_CLOSED)
        assert texts.BTN_SCHEDULE in guest.reply_keyboard()

    e2e.run(scenario)


def test_announced_course_notify_me(e2e) -> None:
    async def scenario(h: Harness) -> None:
        guest = h.person(5011, "Гость")
        await guest.start()
        await guest.open_schedule()
        for _ in range(5):
            if guest.has_button(texts.BTN_NOTIFY_ME, guest.last):
                break
            await guest.click(texts.BTN_NEXT_PAGE)
        assert "уточняются" in guest.last.plain  # анонс без дат: «Сообщить о наборе» вместо записи
        assert not guest.has_button(texts.BTN_APPLY, guest.last)
        await guest.click(texts.BTN_NOTIFY_ME)
        assert guest.alert() == texts.INTEREST_CREATED
        await guest.click(texts.BTN_NOTIFY_ME)
        assert guest.alert() == texts.INTEREST_EXISTS
        assert await h.scalar(select(func.count()).select_from(ProgramInterest)) == 1

    e2e.run(scenario)


# --- Запись ------------------------------------------------------------------------------------------------------

def test_enroll_single_then_pay_as_individual(e2e) -> None:
    async def scenario(h: Harness) -> None:
        await _admins_started(h)
        anna = h.person(5020, "Анна")
        await anna.register("+79990000020", "Иванова Анна Сергеевна")
        await anna.enroll("Имплантация", "Один участник")
        assert anna.sees(texts.APPLY_ENROLLED)
        order = await h.scalar(select(Order))
        assert order.number == 100001 and order.total_amount == 45_000 * RUB
        assert order.status is OrderStatus.AWAITING_PAYMENT and order.payer_type is None
        enrollment = await h.scalar(select(Enrollment))
        assert enrollment.status is EnrollmentStatus.APPLICATION and enrollment.order_id == order.id
        # Админам - уведомление с кнопкой «Открыть заказ».
        for admin_id in (OWNER_ID, ADMIN_ID):
            notice = h.tg.messages(admin_id)[-1]
            assert "№100001" in notice.plain and "Иванова Анна Сергеевна" in notice.plain
            assert any(b.text == texts.BTN_ADMIN_OPEN_ORDER for b in notice.buttons())
        await anna.click(texts.BTN_PAY_INDIVIDUAL)
        assert "№100001" in anna.last_text() and anna.has_button(texts.BTN_PAY_ONLINE)
        assert (await h.scalar(select(Order))).payer_type is PayerType.INDIVIDUAL
        notices = len(h.tg.messages(OWNER_ID))
        # Тот же выбор ещё раз (кнопка из «Моих записей») - админов не беспокоим повторно.
        from stubbot.tg.callbacks import OrderAction, OrderCb

        await anna.press(OrderCb(action=OrderAction.INDIVIDUAL, order_id=order.id).pack())
        assert len(h.tg.messages(OWNER_ID)) == notices
        # Повторная запись на то же проведение - «Вы уже записаны».
        await anna.open_schedule()
        await anna.go_to_course("Имплантация")
        await anna.click(texts.BTN_APPLY)
        assert anna.sees(texts.APPLY_DUPLICATE)
        assert await h.scalar(select(func.count()).select_from(Order)) == 1

    e2e.run(scenario)


def test_enroll_before_registration_returns_to_the_course(e2e) -> None:
    async def scenario(h: Harness) -> None:
        await _admins_started(h)
        boris = h.person(5021, "Борис")
        h.pii.update({"+79990000021", "79990000021", "Борисов Борис Борисович"})
        await boris.start()
        await boris.open_schedule()
        await boris.go_to_course("Пародонтология")
        await boris.click(texts.BTN_APPLY)
        assert boris.sees(texts.APPLY_NEEDS_PROFILE)
        await boris.click(texts.CONSENT_FORM_PD)
        await boris.click(texts.CONSENT_FORM_CONTINUE)
        await boris.share_contact("+79990000021")
        await boris.send("Борисов Борис Борисович")
        await boris.click(texts.REG_NAME_OK)
        await boris.click(h.catalog.specialties[0].title)
        await boris.click(texts.REG_DONE_BUTTON)
        await boris.click(h.catalog.positions[0].title)
        await boris.click(texts.REG_DONE_BUTTON)
        assert boris.sees(texts.APPLY_PROFILE_READY)
        assert "Пародонтология" in boris.last_text()  # сразу к записи на тот же курс
        await boris.click(texts.BTN_APPLY_SEND)
        order = await h.scalar(select(Order))
        assert order is not None and order.session_id == h.catalog.small.id

    e2e.run(scenario)


def test_pair_enrollment_and_colleague_joins_later(e2e) -> None:
    async def scenario(h: Harness) -> None:
        await _admins_started(h)
        anna = h.person(5022, "Анна")
        await anna.register("+79990000022", "Иванова Анна Сергеевна")
        h.pii.update({"+79990000023", "79990000023", "Петрова Мария Ивановна"})
        await anna.open_schedule()
        await anna.go_to_course("Имплантация")
        await anna.click(texts.BTN_APPLY)
        await anna.click("Для двух коллег")
        await anna.send("Мария")  # одно слово - не ФИО
        assert anna.sees(texts.APPLY_COLLEAGUE_NAME_INVALID)
        await anna.send("Петрова Мария Ивановна")
        await anna.send("+7 999 000-00-22")  # свой телефон
        assert anna.sees(texts.APPLY_COLLEAGUE_PHONE_IS_YOURS)
        await anna.send("не телефон")
        assert anna.sees(texts.APPLY_COLLEAGUE_PHONE_INVALID)
        await anna.send("8 (999) 000-00-23")
        await anna.click(texts.BTN_APPLY_SEND)  # без галочки «Коллега согласен» - нельзя
        assert anna.alert() == texts.APPLY_COLLEAGUE_OK_REQUIRED
        await anna.click(texts.BTN_APPLY_COLLEAGUE_OK)
        await anna.click(texts.BTN_APPLY_SEND)
        order = await h.scalar(select(Order))
        assert order.total_amount == 80_000 * RUB
        rows = await h.scalars(select(Enrollment).order_by(Enrollment.id))
        assert len(rows) == 2 and rows[1].enrolled_by_client_id == rows[0].client_id
        colleague = await h.scalar(select(Client).where(Client.phone == "+79990000023"))
        assert colleague.telegram_id is None and colleague.full_name == "Петрова Мария Ивановна"
        assert anna.sees(texts.APPLY_INVITE_COLLEAGUE)

        # Коллега приходит в бот с тем же номером - запись уже её.
        maria = h.person(5023, "Мария")
        await maria.start()
        await maria.send(texts.BTN_CABINET)
        await maria.click(texts.CONSENT_FORM_PD)
        await maria.click(texts.CONSENT_FORM_CONTINUE)
        await maria.share_contact("+79990000023")
        assert "Петрова Мария Ивановна" in maria.last_text()  # подтвердить ФИО, которое ввела Анна
        await maria.click(texts.REG_NAME_OK)
        merged = await h.scalar(select(Client).where(Client.telegram_id == 5023))
        assert merged.phone == "+79990000023"
        mine = await h.scalars(select(Enrollment).where(Enrollment.client_id == merged.id))
        assert len(mine) == 1 and mine[0].order_id == order.id
        assert any("Петрова Мария Ивановна" in m.plain for m in anna.messages())  # Анне сообщили

    e2e.run(scenario)


def test_waitlist_when_no_seats(e2e) -> None:
    async def scenario(h: Harness) -> None:
        await _admins_started(h)
        first = h.person(5024, "Первый")
        await first.register("+79990000024", "Первов Пётр Петрович")
        await first.enroll("Пародонтология")
        second = h.person(5025, "Второй")
        await second.register("+79990000025", "Вторых Иван Иванович")
        await second.open_schedule()
        await second.go_to_course("Пародонтология")
        assert second.has_button(texts.BTN_APPLY_WAITLIST, second.last)
        await second.click(texts.BTN_APPLY_WAITLIST)
        await second.click(texts.BTN_APPLY_SEND)
        assert second.sees(texts.APPLY_WAITLISTED)
        rows = await h.scalars(select(Enrollment).order_by(Enrollment.id))
        assert [r.status for r in rows] == [EnrollmentStatus.APPLICATION, EnrollmentStatus.WAITLIST]
        assert rows[1].order_id is None
        assert await h.scalar(select(func.count()).select_from(Order)) == 1

    e2e.run(scenario)


def test_course_without_price(e2e) -> None:
    async def scenario(h: Harness) -> None:
        await _admins_started(h)
        vera = h.person(5026, "Вера")
        await vera.register("+79990000026", "Верова Вера Викторовна")
        await vera.enroll("Ортодонтия")
        order = await h.scalar(select(Order))
        assert order.total_amount is None
        await vera.click(texts.BTN_PAY_INDIVIDUAL)
        assert not vera.has_button(texts.BTN_PAY_ONLINE, vera.last)  # суммы нет - онлайн платить нечего

    e2e.run(scenario)


def test_organization_sends_requisites(e2e) -> None:
    async def scenario(h: Harness) -> None:
        await _admins_started(h)
        gleb = h.person(5027, "Глеб")
        await gleb.register("+79990000027", "Глебов Глеб Глебович")
        await gleb.enroll("Имплантация", "Один участник")
        await gleb.click(texts.BTN_PAY_ORGANIZATION)
        await gleb.send("ИНН 7801234567")  # текстом - нельзя, нужен файл
        assert gleb.sees(texts.REQUISITES_FILE_ONLY)
        file_id = await gleb.send_document("Реквизиты ООО.pdf")
        assert gleb.sees(texts.REQUISITES_RECEIVED)
        order = await h.scalar(select(Order))
        assert order.payer_type is PayerType.ORGANIZATION and order.requisites_file_id == file_id
        assert order.requisites_file_name == "Реквизиты ООО.pdf"
        for admin_id in (OWNER_ID, ADMIN_ID):
            assert h.tg.messages(admin_id)[-1].document == file_id  # админам - сам файл

    e2e.run(scenario)


# --- Мои записи и отмена -----------------------------------------------------------------------------------------

def test_cancel_unpaid_enrollment(e2e) -> None:
    async def scenario(h: Harness) -> None:
        await _admins_started(h)
        anna = h.person(5030, "Анна")
        await anna.register("+79990000030", "Иванова Анна Сергеевна")
        await anna.enroll("Имплантация", "Один участник")
        await anna.send(texts.BTN_CABINET)
        await anna.click(texts.BTN_MY_APPLICATIONS)
        assert "Имплантация" in anna.last_text()
        await anna.click(texts.BTN_CANCEL_APPLICATION)
        await anna.click(texts.BTN_CANCEL_APPLICATION_NO)
        assert (await h.scalar(select(Order))).status is OrderStatus.AWAITING_PAYMENT
        await anna.click(texts.BTN_CANCEL_APPLICATION)
        await anna.click(texts.BTN_CANCEL_APPLICATION_YES)
        order = await h.scalar(select(Order))
        enrollment = await h.scalar(select(Enrollment))
        assert order.status is OrderStatus.CANCELLED and enrollment.status is EnrollmentStatus.CANCELLED_BY_CLIENT
        # Без оплаты - в лист ожидания курса; админам - уведомление.
        assert await h.scalar(select(func.count()).select_from(ProgramInterest)) == 1
        assert "№100001" in h.tg.messages(OWNER_ID)[-1].plain
        # Старая кнопка «Да, отменить» второй раз ничего не делает.
        await anna.press(next(b.callback_data for m in anna.messages() for b in m.buttons()
                              if b.text == texts.BTN_TO_MY_APPLICATIONS))
        await anna.send(texts.BTN_CABINET)
        await anna.click(texts.BTN_MY_APPLICATIONS)
        assert anna.sees(texts.MY_APPLICATIONS_EMPTY)

    e2e.run(scenario)


def test_course_soon_is_cancelled_via_manager_only(e2e) -> None:
    async def scenario(h: Harness) -> None:
        await _admins_started(h)
        lev = h.person(5031, "Лев")
        await lev.register("+79990000031", "Львов Лев Львович")
        await lev.enroll("Гигиена")
        await lev.send(texts.BTN_CABINET)
        await lev.click(texts.BTN_MY_APPLICATIONS)
        labels = [b.text for b in lev.last.buttons()]
        assert texts.BTN_CANCEL_APPLICATION not in labels and texts.BTN_CANCEL_VIA_MANAGER in labels
        enrollment = await h.scalar(select(Enrollment))
        from stubbot.tg.callbacks import MyApplicationAction, MyApplicationCb

        for action in (MyApplicationAction.ASK_CANCEL, MyApplicationAction.CANCEL):
            await lev.press(MyApplicationCb(action=action, enrollment_id=enrollment.id).pack())
            assert lev.alert() == texts.APPLICATION_CANNOT_CANCEL
        assert (await h.scalar(select(Enrollment))).status is EnrollmentStatus.APPLICATION

    e2e.run(scenario)


# --- Кабинет -----------------------------------------------------------------------------------------------------

def test_cabinet_edit_fields_and_marketing(e2e) -> None:
    async def scenario(h: Harness) -> None:
        nina = h.person(5040, "Нина")
        await nina.register("+79990000040", "Нинова Нина Николаевна")
        h.pii.update({"Казань", "Нинова Нина Петровна", "nina@example.ru"})
        await nina.send(texts.BTN_CABINET)
        await nina.click(texts.BTN_EDIT_PROFILE)
        await nina.click(texts.EDIT_FIELD_LABELS["city"])
        await nina.send("1")  # без букв - не город
        assert nina.last_text().startswith(texts.OPTIONAL_ERRORS["city"][:15])
        await nina.send("Казань")
        assert (await _client(h, 5040)).city == "Казань" and nina.sees(texts.EDIT_SAVED)
        await nina.click(texts.BTN_EDIT_PROFILE)
        await nina.click(texts.EDIT_FIELD_LABELS["city"])
        await nina.click(texts.BTN_EDIT_CLEAR)
        assert (await _client(h, 5040)).city is None
        await nina.click(texts.BTN_EDIT_PROFILE)
        await nina.click(texts.EDIT_FIELD_LABELS["email"])
        await nina.send("nina@example.ru")
        await nina.click(texts.BTN_EDIT_PROFILE)
        await nina.click(texts.EDIT_FIELD_LABELS["name"])
        await nina.send("Нинова Нина Петровна")
        await nina.click(texts.REG_NAME_OK)
        client = await _client(h, 5040)
        assert client.full_name == "Нинова Нина Петровна" and client.email == "nina@example.ru"
        # Специальности: снять единственную - «выберите хотя бы одну».
        await nina.click(texts.BTN_EDIT_PROFILE)
        await nina.click(texts.EDIT_FIELD_LABELS["spec"])
        await nina.click(h.catalog.specialties[0].title)
        await nina.click(texts.REG_DONE_BUTTON)
        assert nina.alert() == texts.REG_SELECT_AT_LEAST_ONE
        await nina.click(h.catalog.specialties[2].title)
        await nina.click(texts.REG_DONE_BUTTON)
        # Рассылки: включить (согласие на текущую версию) и выключить (отзыв).
        await nina.click(texts.BTN_MARKETING_TURN_ON)
        await nina.click(texts.CONSENT_MARKETING_YES)
        assert nina.has_button(texts.BTN_MARKETING_TURN_OFF)
        await nina.click(texts.BTN_MARKETING_TURN_OFF)
        active = await h.scalars(select(ClientConsent).where(ClientConsent.type == ConsentType.MARKETING,
                                                             ClientConsent.revoked_at.is_(None)))
        assert active == []

    e2e.run(scenario)


def test_about_manager_and_unknown_text(e2e) -> None:
    async def scenario(h: Harness) -> None:
        guest = h.person(5041, "Гость")
        await guest.start()
        await guest.send(texts.BTN_ABOUT)
        assert guest.last.buttons()[0].url.startswith("https://t.me/aquastom_manager_e2e")
        # Площадки из админки - списком в «О центре» (список включён 30.10.2026).
        assert "Учебный класс - Санкт-Петербург, Невский пр., 1" in guest.last_text()
        assert "Уточняется" not in guest.last_text()
        await guest.send(texts.BTN_MANAGER)
        assert guest.last.buttons()[0].url.startswith("https://t.me/aquastom_manager_e2e?text=")
        await guest.send("Сколько стоит курс?")
        assert guest.sees(texts.UNKNOWN_MESSAGE_MANAGER)
        assert "%D0%A1%D0%BA%D0%BE%D0%BB%D1%8C%D0%BA%D0%BE" in guest.last.buttons()[0].url  # вопрос - в черновик
        await guest.send_sticker_like()
        assert guest.last_text() == texts.UNKNOWN_MESSAGE or guest.sees(texts.UNKNOWN_MESSAGE)

    e2e.run(scenario)
