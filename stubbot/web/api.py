"""API веб-приложения: то же, что слушатель делает в боте, - экранами вместо сообщений.

Обработчики тонкие, как хендлеры в tg/handlers: проверяют ввод и зовут те же сервисы (services/*). Кто может
записаться, отменить или оплатить - решают только сервисы. Письма в Telegram (админам, коллеге) - после коммита
(ctx.later). Тексты ошибок - тексты бота без разметки (to_plain): человек видит те же формулировки.

Порядок как в боте: согласие → телефон → ФИО → специальности → должности («step» в ответах), запись на курс -
только с заполненным профилем.
"""

import logging
from collections.abc import Callable
from pathlib import PureWindowsPath
from typing import Any
from urllib.parse import unquote

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup
from aiohttp import web

from stubbot.db.enums import CancelChoice, ConsentSource, ConsentType, EnrollmentSource, PayerType, PaymentState
from stubbot.db.models import Client
from stubbot.payments import receipts_enabled
from stubbot.services.clients import ClientService
from stubbot.services.consents import ConsentService
from stubbot.services.enrollments import (
    CancelOutcome,
    Colleague,
    EnrollmentService,
    EnrollOutcome,
    EnrollResult,
    MyApplication,
)
from stubbot.services.legal import document_text
from stubbot.services.online_payments import Applied, StartOutcome
from stubbot.services.orders import OrderService
from stubbot.services.registration import OptionalField, PhoneResult, RegistrationService, RegistrationStep
from stubbot.services.schedule import ScheduleService
from stubbot.tg import admin_orders_views, keyboards, render, texts
from stubbot.tg.admin_notify import notify_admins
from stubbot.tg.middlewares.text_cleanup import strip_nul
from stubbot.tg.online_payments import OnlinePayments
from stubbot.utils.files import requisites_file_ok
from stubbot.utils.links import with_draft_text
from stubbot.utils.names import parse_full_name
from stubbot.utils.phone import normalize_phone
from stubbot.utils.profile_fields import parse_email
from stubbot.web import serializers
from stubbot.web.app import ApiError, Ctx, Handler, api, telegram_profile
from stubbot.web.auth import AuthError, phone_from_contact
from stubbot.web.html import to_plain, to_web_html

logger = logging.getLogger(__name__)

ROUTES: list[tuple[str, str, Handler, bool]] = []
ID = r"{id:\d{1,18}}"  # id из адреса: только цифры и в пределах bigint
MAX_IDS = 100
CONSENT_STEP = "consent"


def route(method: str, path: str, transaction: bool = True) -> Callable:
    def register(handler: Handler) -> Handler:
        ROUTES.append((method, path, handler, transaction))
        return handler

    return register


def setup(app: web.Application) -> None:
    app.router.add_get("/api/health", health)
    for method, path, handler, transaction in ROUTES:
        app.router.add_route(method, "/api" + path, api(handler, transaction))


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


# --- Ввод --------------------------------------------------------------------------------------------------------

def _bad(code: str, text: str = "Не получилось прочитать запрос - обновите приложение.", **extra: Any) -> ApiError:
    return ApiError(400, code, to_plain(text), **extra)


async def _body(ctx: Ctx) -> dict[str, Any]:
    if not ctx.request.can_read_body:
        return {}
    try:
        data = await ctx.request.json()
    except ValueError as error:
        raise _bad("bad_request") from error
    if not isinstance(data, dict):
        raise _bad("bad_request")
    return data


def _path_id(ctx: Ctx) -> int:
    return int(ctx.request.match_info["id"])


def _int(data: dict[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 2**63:
        raise _bad("bad_request")
    return value


def _ids(data: dict[str, Any], key: str) -> list[int]:
    value = data.get(key)
    if not isinstance(value, list) or len(value) > MAX_IDS:
        raise _bad("bad_request")
    return [_int({"v": item}, "v") or 0 for item in value]


def _text(data: dict[str, Any], key: str, limit: int) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise _bad("bad_request")
    return strip_nul(value)


def _flag(data: dict[str, Any], key: str) -> bool:
    return data.get(key) is True


# --- Общее -------------------------------------------------------------------------------------------------------

async def _step(ctx: Ctx) -> str:
    """Где человек в регистрации: consent / phone / name / specialties / positions / done."""
    if not await ConsentService(ctx.session).has_current_consent(ctx.client.id, ConsentType.PERSONAL_DATA):
        return CONSENT_STEP
    return (await RegistrationService(ctx.session).next_step(ctx.client)).value


async def _need_consent(ctx: Ctx) -> None:
    if not await ConsentService(ctx.session).has_current_consent(ctx.client.id, ConsentType.PERSONAL_DATA):
        raise ApiError(403, "consent_required", "Сначала - согласие на обработку персональных данных.",
                       step=CONSENT_STEP)


async def _need_profile(ctx: Ctx) -> None:
    step = await _step(ctx)
    if step != RegistrationStep.DONE.value:
        raise ApiError(403, "profile_required", to_plain(texts.APPLY_NEEDS_PROFILE), step=step)


def _need_applications(ctx: Ctx) -> None:
    if not ctx.settings.applications_enabled:
        raise ApiError(404, "applications_off", "Запись через приложение пока недоступна - напишите менеджеру.")


def _need_payments(ctx: Ctx) -> OnlinePayments:
    _need_applications(ctx)
    if ctx.payments is None:
        raise ApiError(404, "payments_off", texts.PAY_ONLINE_OFF)
    return ctx.payments


async def _bot_username(ctx: Ctx) -> str | None:
    """Имя бота - только из API (aiogram кэширует ответ); нет бота или связи - без ссылок на бота."""
    if ctx.bot is None:
        return None
    try:
        return (await ctx.bot.me()).username
    except TelegramAPIError as error:
        logger.warning("Веб-приложение: имя бота не получено - %s", type(error).__name__)
        return None


async def _referral_link(ctx: Ctx, client: Client) -> str | None:
    username = await _bot_username(ctx)
    return f"https://t.me/{username}?start=ref_{client.referral_code}" if username else None


def _manager(ctx: Ctx, draft: str) -> str | None:
    """Чат с менеджером с готовым черновиком; ссылки нет в настройках - None (кнопки нет)."""
    return with_draft_text(ctx.settings.manager_url, draft) if ctx.settings.manager_url else None


def _to_admins(ctx: Ctx, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Уведомление всем админам - после коммита. Текст собран сейчас, пока данные в транзакции."""
    bot, settings = ctx.bot, ctx.settings
    if bot is not None:
        ctx.later(lambda: notify_admins(bot, settings, text, markup))


def _to_client(ctx: Ctx, person: Client, text: str, what: str) -> None:
    """Сообщение клиенту в чат с ботом (коллеге из пары) - после коммита; не дошло - только в лог. Всё в приложении
    (WEBAPP_ONLY) - под сообщением «Мои записи»: все эти сообщения - о записях человека."""
    bot, telegram_id, person_id = ctx.bot, person.telegram_id, person.id
    if bot is None or telegram_id is None:
        return
    markup = keyboards.app_link(ctx.settings, texts.BTN_APP_MY, "/my")

    async def send() -> None:
        try:
            await bot.send_message(telegram_id, text, reply_markup=markup)
        except (TelegramForbiddenError, TelegramBadRequest):
            logger.info("Клиенту #%s не доставлено сообщение %s", person_id, what)

    ctx.later(send)


def _application(ctx: Ctx, app: MyApplication) -> dict[str, Any]:
    manager_url = _manager(ctx, render.manager_cancel_draft(app))
    data = serializers.application(app, ctx.settings.timezone, ctx.now, ctx.settings.online_payments_enabled,
                                   manager_url)
    data["manager_url"] = manager_url
    return data


async def _my_applications(ctx: Ctx) -> list[MyApplication]:
    return await EnrollmentService(ctx.session).my_applications(ctx.client.id, ctx.today)


async def _find_application(ctx: Ctx, enrollment_id: int | None = None,
                            order_id: int | None = None) -> MyApplication | None:
    """Своя запись (id из запроса может быть подделан - ищем только среди своих)."""
    for app in await _my_applications(ctx):
        if app.enrollment.id == enrollment_id or (order_id is not None and app.is_payer and app.order is not None
                                                  and app.order.id == order_id):
            return app
    return None


# --- Кто я -------------------------------------------------------------------------------------------------------

@route("GET", "/me")
async def me(ctx: Ctx) -> dict[str, Any]:
    username = await _bot_username(ctx)
    settings = ctx.settings
    return {
        "client_id": ctx.client.id,
        "first_name": ctx.client.full_name.split()[1] if ctx.client.full_name and len(ctx.client.full_name.split()) > 1
        else ctx.user.first_name,
        "step": await _step(ctx),
        # Админка в приложении - тем же, кому в боте (owner и ADMIN_IDS); каждый её запрос проверяет это заново.
        "is_admin": settings.is_admin(ctx.user.id),
        "config": {
            "applications": settings.applications_enabled,
            "online_payments": settings.online_payments_enabled,
            "fake_payments": ctx.payments is not None and not ctx.payments.has_payment_page,
            "manager_url": _manager(ctx, texts.MANAGER_DRAFT_ABOUT),
            "bot_url": f"https://t.me/{username}" if username else None,
            "dev": ctx.user.dev,
        },
    }


# --- Согласия ----------------------------------------------------------------------------------------------------

@route("GET", "/consent")
async def consent_form(ctx: Ctx) -> dict[str, Any]:
    """Экран согласий, как в боте: 1 - политика и обработка ПД (обязательно), 2 - рассылки (по желанию)."""
    service = ConsentService(ctx.session)
    pd_document = await service.current_document(ConsentType.PERSONAL_DATA)
    pd_text = document_text(pd_document) if pd_document else None
    if pd_text is None:
        raise ApiError(503, "document_unavailable", to_plain(texts.DOCUMENT_UNAVAILABLE))
    ads_document = await service.current_document(ConsentType.MARKETING)
    ads_text = document_text(ads_document) if ads_document else None
    return {
        "intro_html": to_web_html(texts.CONSENT_PD_INTRO),
        "pd": {"document_id": pd_document.id, "html": to_web_html(pd_text)},
        "ads": {"document_id": ads_document.id, "html": to_web_html(ads_text)} if ads_text is not None else None,
        # Вторая галочка уже стоит, если человек соглашался на рассылки раньше (как в боте).
        "ads_checked": ads_text is not None and await service.has_any_consent(ctx.client.id, ConsentType.MARKETING),
    }


@route("POST", "/consent")
async def accept_consent(ctx: Ctx) -> dict[str, Any]:
    """«Продолжить»: согласия засчитываются на те версии документов, которые человек видел (их id - в запросе)."""
    data = await _body(ctx)
    if not _flag(data, "pd"):
        raise _bad("pd_required", texts.CONSENT_PD_CHECK_REQUIRED)
    service = ConsentService(ctx.session)
    pd_document = await service.current_document(ConsentType.PERSONAL_DATA)
    ads_document = await service.current_document(ConsentType.MARKETING)
    if pd_document is None or document_text(pd_document) is None:
        raise ApiError(503, "document_unavailable", to_plain(texts.DOCUMENT_UNAVAILABLE))
    shown_ads = _int(data, "ads_document_id")
    if pd_document.id != _int(data, "pd_document_id") or (shown_ads is not None and (
            ads_document is None or ads_document.id != shown_ads)):
        # Пока человек читал, вышла новая версия документов - соглашаться надо с ней.
        raise ApiError(409, "consent_updated", to_plain(texts.CONSENT_PD_UPDATED))

    if not await service.has_current_consent(ctx.client.id, ConsentType.PERSONAL_DATA):
        await service.grant(ctx.client.id, ConsentType.PERSONAL_DATA, pd_document, ConsentSource.SITE)
    # Согласие есть - теперь можно запомнить имя и @username из Telegram (до согласия их не храним).
    ClientService(ctx.session).remember_profile(ctx.client, telegram_profile(ctx.user))
    if shown_ads is not None:
        if _flag(data, "ads"):
            if not await service.has_current_consent(ctx.client.id, ConsentType.MARKETING):
                await service.grant(ctx.client.id, ConsentType.MARKETING, ads_document, ConsentSource.SITE)
        elif await service.has_any_consent(ctx.client.id, ConsentType.MARKETING):
            await service.revoke(ctx.client.id, ConsentType.MARKETING)  # галочку сняли - это отказ от рассылок
    return {"step": await _step(ctx)}


# --- Регистрация и профиль ---------------------------------------------------------------------------------------

def _contact_phone(ctx: Ctx, data: dict[str, Any]) -> str:
    """Номер - только из подписанного ответа Telegram «Поделиться номером» своего аккаунта (как кнопка в боте).
    Ввести номер руками может только разработчик (вход без Telegram)."""
    contact = _text(data, "contact", 4096)
    if contact:
        try:
            return phone_from_contact(contact, ctx.bot_token, ctx.user.id)
        except AuthError as error:
            raise _bad("contact_invalid", "Не получилось подтвердить номер. Попробуйте ещё раз.") from error
    phone = _text(data, "phone", 32)
    if phone and ctx.user.dev:
        return phone
    raise _bad("contact_required", "Поделитесь номером кнопкой Telegram.")


@route("POST", "/profile/phone")
async def save_phone(ctx: Ctx) -> dict[str, Any]:
    await _need_consent(ctx)
    raw = _contact_phone(ctx, await _body(ctx))
    saved = await RegistrationService(ctx.session).save_phone(ctx.client, raw)
    match saved.result:
        case PhoneResult.INVALID:
            raise _bad("phone_invalid", "Не получилось распознать номер. Попробуйте ещё раз.")
        case PhoneResult.TAKEN:
            raise ApiError(409, "phone_taken", to_plain(texts.REG_PHONE_TAKEN))
    linked = await _announce_linked(ctx, saved.linked_name) if saved.result is PhoneResult.LINKED else []
    return {"result": saved.result.value, "linked_name": saved.linked_name, "linked": linked,
            "step": await _step(ctx)}


async def _announce_linked(ctx: Ctx, colleague_name: str | None) -> list[dict[str, Any]]:
    """Номер был у коллеги без Telegram (его записали вдвоём): записи теперь здесь. Записавшим - «коллега в боте»
    (как flows.announce_linked_records)."""
    apps = [app for app in await _my_applications(ctx) if app.active and not app.is_payer]
    name = ctx.client.full_name or colleague_name
    told: set[int] = set()
    for app in apps:
        payer = app.partner
        if payer is not None and payer.id not in told:
            told.add(payer.id)
            _to_client(ctx, payer, render.colleague_joined(app, name), "«коллега в боте»")
    return [_application(ctx, app) for app in apps]


@route("PUT", "/profile/name")
async def save_name(ctx: Ctx) -> dict[str, Any]:
    """ФИО одной строкой - как в сертификат. Подтверждение («Всё верно?») - на экране приложения до отправки."""
    await _need_consent(ctx)
    name = parse_full_name(_text(await _body(ctx), "full_name", 300) or "")
    if name is None:
        raise _bad("name_invalid", texts.REG_NAME_INVALID)
    RegistrationService(ctx.session).save_name(ctx.client, name)
    return {"full_name": name, "step": await _step(ctx)}


@route("GET", "/dictionaries")
async def dictionaries(ctx: Ctx) -> dict[str, Any]:
    service = RegistrationService(ctx.session)
    return {
        "specialties": [{"id": s.id, "title": s.title} for s in await service.specialties()],
        "positions": [{"id": p.id, "title": p.title} for p in await service.positions()],
        "selected_specialties": await service.selected_specialty_ids(ctx.client.id),
        "selected_positions": await service.selected_position_ids(ctx.client.id),
    }


async def _save_selection(ctx: Ctx, specialties: bool) -> dict[str, Any]:
    await _need_consent(ctx)
    ids = _ids(await _body(ctx), "ids")
    service = RegistrationService(ctx.session)
    saved = await (service.save_specialties if specialties else service.save_positions)(ctx.client, ids)
    if not saved:
        raise _bad("select_at_least_one", texts.REG_SELECT_AT_LEAST_ONE)
    step = await _step(ctx)
    # Обязательная часть пройдена только что - приложение предложит необязательные поля (как бот).
    just_completed = step == RegistrationStep.DONE.value and await service.complete(ctx.client)
    return {"step": step, "just_completed": just_completed}


@route("PUT", "/profile/specialties")
async def save_specialties(ctx: Ctx) -> dict[str, Any]:
    return await _save_selection(ctx, specialties=True)


@route("PUT", "/profile/positions")
async def save_positions(ctx: Ctx) -> dict[str, Any]:
    return await _save_selection(ctx, specialties=False)


async def _profile(ctx: Ctx) -> dict[str, Any]:
    loaded = await RegistrationService(ctx.session).load_profile(ctx.client.id)
    marketing = await ConsentService(ctx.session).has_any_consent(ctx.client.id, ConsentType.MARKETING)
    referrals = await ClientService(ctx.session).count_referrals(ctx.client.id)
    advance = await OrderService(ctx.session).advance_balance(ctx.client.id) if ctx.settings.applications_enabled else 0
    data = serializers.profile(loaded, ctx.today, marketing, advance, referrals, await _referral_link(ctx, loaded))
    data["step"] = await _step(ctx)
    return data


@route("GET", "/profile")
async def profile(ctx: Ctx) -> dict[str, Any]:
    await _need_consent(ctx)
    return await _profile(ctx)


@route("PATCH", "/profile")
async def edit_profile(ctx: Ctx) -> dict[str, Any]:
    """Необязательное поле: город, место работы, стаж (лет), e-mail, дата рождения. Пусто - очистить."""
    await _need_consent(ctx)
    data = await _body(ctx)
    try:
        field = OptionalField(_text(data, "field", 64) or "")
    except ValueError as error:
        raise _bad("bad_request") from error
    raw = data.get("value")
    if isinstance(raw, int) and not isinstance(raw, bool):
        raw = str(raw)
    if raw is not None and (not isinstance(raw, str) or len(raw) > 300):
        raise _bad("bad_request")
    value = None
    if raw is not None and raw.strip():
        value = RegistrationService.parse_optional(field, strip_nul(raw), ctx.today)
        if value is None:
            raise _bad("invalid_value", texts.OPTIONAL_ERRORS[field.value], field=field.value)
    RegistrationService.save_optional(ctx.client, field, value)
    return await _profile(ctx)


@route("GET", "/marketing")
async def marketing_form(ctx: Ctx) -> dict[str, Any]:
    service = ConsentService(ctx.session)
    document = await service.current_document(ConsentType.MARKETING)
    ads_text = document_text(document) if document else None
    if ads_text is None:
        raise ApiError(503, "document_unavailable", to_plain(texts.DOCUMENT_UNAVAILABLE))
    return {"document_id": document.id, "html": to_web_html(ads_text),
            "on": await service.has_any_consent(ctx.client.id, ConsentType.MARKETING)}


@route("POST", "/marketing")
async def set_marketing(ctx: Ctx) -> dict[str, Any]:
    """Рассылки из профиля: включить - новое согласие на текущую версию текста (его id - в запросе); выключить."""
    await _need_consent(ctx)
    data = await _body(ctx)
    service = ConsentService(ctx.session)
    if _flag(data, "on"):
        document = await service.current_document(ConsentType.MARKETING)
        if document is None or document_text(document) is None:
            raise ApiError(503, "document_unavailable", to_plain(texts.DOCUMENT_UNAVAILABLE))
        if document.id != _int(data, "document_id"):
            raise ApiError(409, "consent_updated", to_plain(texts.CONSENT_PD_UPDATED))
        if not await service.has_current_consent(ctx.client.id, ConsentType.MARKETING):
            await service.grant(ctx.client.id, ConsentType.MARKETING, document, ConsentSource.SITE)
        return {"marketing": True}
    if await service.has_any_consent(ctx.client.id, ConsentType.MARKETING):
        await service.revoke(ctx.client.id, ConsentType.MARKETING)
    return {"marketing": False}


# --- Расписание и курсы ------------------------------------------------------------------------------------------

async def _mine(ctx: Ctx) -> dict[int, dict[str, str]]:
    """Мои действующие записи по проведениям - метка «Вы записаны» в расписании."""
    return {app.session.id: serializers.my_enrollment(app.enrollment.status)
            for app in await _my_applications(ctx) if app.active}


@route("GET", "/schedule")
async def schedule(ctx: Ctx) -> dict[str, Any]:
    cards = await ScheduleService(ctx.session).cards(ctx.today)
    mine = await _mine(ctx)
    applications = ctx.settings.applications_enabled
    return {"items": [serializers.course_card(card, ctx.settings.timezone, applications, mine.get(card.session.id))
                      for card in cards],
            "manager_url": _manager(ctx, texts.MANAGER_DRAFT_SCHEDULE)}


@route("GET", f"/schedule/{ID}")
async def course(ctx: Ctx) -> dict[str, Any]:
    card = await ScheduleService(ctx.session).card(_path_id(ctx), ctx.today)
    if card is None:
        raise ApiError(404, "not_found", "Этого проведения уже нет в расписании")
    mine = await _mine(ctx)
    data = serializers.course_details(card, ctx.settings.timezone, ctx.settings.applications_enabled,
                                      mine.get(card.session.id))
    data["manager_url"] = _manager(ctx, render.manager_draft(card))
    return data


@route("GET", f"/programs/{ID}")
async def program(ctx: Ctx) -> dict[str, Any]:
    found = await ScheduleService(ctx.session).program(_path_id(ctx))
    if found is None:
        raise ApiError(404, "not_found", "Этого курса уже нет.")
    data = serializers.program_page(found)
    data["manager_url"] = _manager(ctx, texts.MANAGER_DRAFT_PROGRAM.format(title=found.title))
    return data


@route("POST", f"/programs/{ID}/interest")
async def interest(ctx: Ctx) -> dict[str, Any]:
    """«Сообщить о наборе» - лист ожидания курса (у проведения «скоро набор»)."""
    _need_applications(ctx)
    program_id = _path_id(ctx)
    if await ScheduleService(ctx.session).program(program_id) is None:
        raise ApiError(404, "not_found", "Этого курса уже нет.")
    created = await EnrollmentService(ctx.session).subscribe_interest(ctx.client.id, program_id)
    return {"created": created, "message": to_plain(texts.INTEREST_CREATED if created else texts.INTEREST_EXISTS)}


@route("GET", "/about")
async def about(ctx: Ctx) -> dict[str, Any]:
    venues = await ScheduleService(ctx.session).venues()
    return {"html": to_web_html(texts.ABOUT), "venues": [serializers.venue(v) for v in venues],
            "manager_url": _manager(ctx, texts.MANAGER_DRAFT_ABOUT)}


# --- Запись ------------------------------------------------------------------------------------------------------

def _colleague(ctx: Ctx, raw: object) -> Colleague | None:
    """Коллега для тарифа «Для двух коллег»: ФИО, телефон и галочка «Коллега согласен на передачу ФИО и телефона»."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _bad("bad_request")
    name = parse_full_name(_text(raw, "full_name", 300) or "")
    if name is None:
        raise _bad("colleague_name_invalid", texts.APPLY_COLLEAGUE_NAME_INVALID)
    phone = normalize_phone(_text(raw, "phone", 32) or "")
    if phone is None:
        raise _bad("colleague_phone_invalid", texts.APPLY_COLLEAGUE_PHONE_INVALID)
    if phone == ctx.client.phone:
        raise _bad("colleague_phone_is_yours", texts.APPLY_COLLEAGUE_PHONE_IS_YOURS)
    if not _flag(raw, "consent"):
        raise _bad("colleague_consent_required", texts.APPLY_COLLEAGUE_OK_REQUIRED)
    return Colleague(name, phone)


@route("POST", f"/sessions/{ID}/enroll")
async def enroll(ctx: Ctx) -> dict[str, Any]:
    """«Записаться»: тариф (и коллега для «Для двух коллег») → место и заказ, или лист ожидания."""
    _need_applications(ctx)
    await _need_profile(ctx)
    data = await _body(ctx)
    price_id = _int(data, "price_id")
    colleague = _colleague(ctx, data.get("colleague"))
    result = await EnrollmentService(ctx.session).enroll(ctx.client, _path_id(ctx), price_id, colleague, ctx.today,
                                                         source=EnrollmentSource.SITE)
    match result.outcome:
        case EnrollOutcome.CREATED:
            invite_link = None
            if result.colleague is not None:
                if result.colleague.telegram_id is None:
                    # Коллеги ещё нет в боте - ссылка, чтобы пригласить: по телефону запись свяжется с его аккаунтом.
                    invite_link = await _referral_link(ctx, ctx.client)
                else:
                    _to_client(ctx, result.colleague, render.colleague_enrolled(result.card, ctx.client.full_name),
                               "о записи вдвоём")
            _notify_enrolled(ctx, result, price_id)
            app = await _find_application(ctx, enrollment_id=result.enrollment.id)
            return {"outcome": "created", "application": _application(ctx, app) if app else None,
                    "colleague_name": result.colleague.full_name if result.colleague else None,
                    "colleague_in_bot": bool(result.colleague and result.colleague.telegram_id),
                    "invite_link": invite_link}
        case EnrollOutcome.WAITLIST:
            _notify_enrolled(ctx, result, price_id)
            return {"outcome": "waitlist", "pair": colleague is not None,
                    "message": to_plain(texts.APPLY_WAITLISTED_PAIR if colleague else texts.APPLY_WAITLISTED)}
        case EnrollOutcome.DUPLICATE:
            status = result.enrollment.status.value if result.enrollment else "application"
            raise ApiError(409, "duplicate", to_plain(
                texts.APPLY_DUPLICATE.format(status=texts.ENROLLMENT_STATUS_LABELS[status])))
        case EnrollOutcome.COLLEAGUE_DUPLICATE:
            raise ApiError(409, "colleague_duplicate", to_plain(texts.APPLY_COLLEAGUE_DUPLICATE))
        case EnrollOutcome.COLLEAGUE_IS_SELF:
            raise _bad("colleague_is_self", texts.APPLY_COLLEAGUE_IS_SELF)
        case EnrollOutcome.CLOSED:
            raise ApiError(409, "closed", to_plain(texts.APPLY_CLOSED))
        case EnrollOutcome.BUSY:
            raise ApiError(409, "busy", to_plain(texts.APPLY_BUSY))
        case EnrollOutcome.BAD_PRICE:
            # Тариф не этого проведения, без выбора при нескольких тарифах, «для двух коллег» без коллеги.
            raise _bad("bad_price", "Выберите вариант участия - и для тарифа «Для двух коллег» укажите коллегу.")
        case _:
            raise ApiError(409, "expired", to_plain(texts.APPLY_EXPIRED))


def _notify_enrolled(ctx: Ctx, result: EnrollResult, price_id: int | None) -> None:
    """Админам и владельцу: новая запись (с кнопкой «Открыть заказ») или лист ожидания."""
    price = next((p for p in result.card.active_prices if p.id == price_id), None)
    _to_admins(ctx, *admin_orders_views.notify_enrolled(result.card, result.order, ctx.client, result.colleague,
                                                        price, ctx.settings.timezone))


# --- Мои записи --------------------------------------------------------------------------------------------------

@route("GET", "/applications")
async def applications(ctx: Ctx) -> dict[str, Any]:
    await _need_consent(ctx)
    advance = await OrderService(ctx.session).advance_balance(ctx.client.id) if ctx.settings.applications_enabled else 0
    return {"items": [_application(ctx, app) for app in await _my_applications(ctx)],
            "advance": serializers.money(advance) if advance > 0 else None}


@route("POST", f"/orders/{ID}/payer")
async def choose_payer(ctx: Ctx) -> dict[str, Any]:
    """«Как будете оплачивать?»: физлицо (счёт придёт в Telegram или на почту) или юрлицо (реквизиты файлом)."""
    _need_applications(ctx)
    try:
        payer_type = PayerType(_text(await _body(ctx), "type", 32) or "")
    except ValueError as error:
        raise _bad("bad_request") from error
    order, changed = await OrderService(ctx.session).choose_payer_type(ctx.client.id, _path_id(ctx), payer_type)
    if order is None:
        raise ApiError(404, "order_unavailable", texts.ORDER_UNAVAILABLE)
    if changed:  # повторный выбор того же админов не беспокоит
        _to_admins(ctx, *admin_orders_views.notify_payer_type(order, ctx.client))
    app = await _find_application(ctx, order_id=order.id)
    return {"application": _application(ctx, app) if app else None}


def _file_name(raw: str) -> str:
    """Имя файла из формы: без пути (любые разделители) и без %-кодирования (так шлёт не-латиницу часть клиентов)."""
    name = PureWindowsPath(raw).name
    if "%" in name:
        try:
            name = unquote(name, errors="strict")
        except UnicodeDecodeError:
            pass
    return PureWindowsPath(name).name


async def _requisites_upload(ctx: Ctx) -> tuple[str, bytes]:
    try:
        form = await ctx.request.post()
    except web.HTTPRequestEntityTooLarge as error:
        raise ApiError(413, "file_too_large", to_plain(texts.REQUISITES_FILE_TYPES)) from error
    except ValueError as error:
        raise _bad("bad_request") from error
    field = form.get("file")
    if not isinstance(field, web.FileField):
        raise _bad("file_required", texts.REQUISITES_FILE_ONLY)
    content = field.file.read()
    name = strip_nul(_file_name(field.filename or ""))[:256]
    if not requisites_file_ok(name, len(content)):
        raise _bad("file_type", texts.REQUISITES_FILE_TYPES)
    return name, content


@route("POST", f"/orders/{ID}/requisites", transaction=False)
async def upload_requisites(ctx: Ctx) -> dict[str, Any]:
    """Реквизиты организации файлом. Файл уходит админам через бота (так у него появляется file_id, как у файла,
    присланного в чат), потом сохраняется в заказ. Транзакция не держится, пока файл грузится в Telegram."""
    _need_applications(ctx)
    order_id = _path_id(ctx)
    name, content = await _requisites_upload(ctx)
    async with ctx.session.begin():
        order = await OrderService(ctx.session).payer_order(ctx.client.id, order_id)
        if order is None:
            raise ApiError(404, "order_unavailable", texts.ORDER_UNAVAILABLE)
        caption, markup = admin_orders_views.notify_requisites(order, ctx.client)
    file_id = await _deliver_requisites(ctx, name, content, caption, markup)
    async with ctx.session.begin():
        order = await OrderService(ctx.session).save_requisites(ctx.client.id, order_id, file_id, name)
        if order is None:  # заказ отменили, пока файл грузился
            raise ApiError(404, "order_unavailable", texts.ORDER_UNAVAILABLE)
        number = order.number
        app = await _find_application(ctx, order_id=order_id)
        data = _application(ctx, app) if app else None
    return {"message": to_plain(texts.REQUISITES_RECEIVED.format(number=number)), "application": data}


async def _deliver_requisites(ctx: Ctx, name: str, content: bytes, caption: str,
                              markup: InlineKeyboardMarkup) -> str:
    """Первому доступному админу - сам файл (file_id из ответа), остальным - по file_id после коммита."""
    bot = ctx.bot
    if bot is None:
        raise ApiError(503, "bot_unavailable", "Файл сейчас не передать - попробуйте позже или пришлите его боту.")
    file_id, rest = None, []
    for chat_id in ctx.settings.admin_chat_ids:
        if file_id is not None:
            rest.append(chat_id)
            continue
        try:
            sent = await bot.send_document(chat_id, BufferedInputFile(content, filename=name), caption=caption,
                                           reply_markup=markup)
            file_id = sent.document.file_id
        except (TelegramForbiddenError, TelegramBadRequest) as error:
            logger.warning("Админу %s не доставлены реквизиты: %s", chat_id, type(error).__name__)
    if file_id is None:
        raise ApiError(503, "bot_unavailable", "Файл сейчас не передать - попробуйте позже или пришлите его боту.")

    async def to_rest() -> None:
        for chat_id in rest:
            try:
                await bot.send_document(chat_id, file_id, caption=caption, reply_markup=markup)
            except (TelegramForbiddenError, TelegramBadRequest) as error:
                logger.warning("Админу %s не доставлены реквизиты: %s", chat_id, type(error).__name__)

    ctx.later(to_rest)
    return file_id


@route("POST", f"/applications/{ID}/cancel")
async def cancel(ctx: Ctx) -> dict[str, Any]:
    """Отмена записи: без оплаты - сразу (заказ закрыт, в лист ожидания курса); с оплатой - choice: перенос, аванс
    или возврат. Можно ли - решает сервис под блокировкой заказа."""
    _need_applications(ctx)
    raw_choice = _text(await _body(ctx), "choice", 32)
    try:
        choice = CancelChoice(raw_choice) if raw_choice else None
    except ValueError as error:
        raise _bad("bad_request") from error
    # Карточку берём до отмены: отменённая пропадёт из списка, а название и даты нужны коллеге и админам.
    app = await _find_application(ctx, enrollment_id=_path_id(ctx))
    if app is None:
        raise ApiError(404, "cannot_cancel", texts.APPLICATION_CANNOT_CANCEL)
    result = await EnrollmentService(ctx.session).cancel(ctx.client.id, app.enrollment.id, ctx.today, choice)
    if result.outcome is CancelOutcome.NOT_ALLOWED:
        raise ApiError(409, "cannot_cancel", texts.APPLICATION_CANNOT_CANCEL)
    if result.outcome is CancelOutcome.NEEDS_CHOICE:
        # Пока человек думал, пришла оплата - сначала решить, что сделать с деньгами.
        raise ApiError(409, "needs_choice", to_plain(render.cancel_paid_question(app, result.paid)),
                       paid=serializers.money(result.paid))
    manager_url = None
    if result.choice is CancelChoice.REFUND:
        manager_url = _manager(ctx, render.manager_refund_draft(result.order))
    for colleague in result.colleagues:
        _to_client(ctx, colleague, render.colleague_cancelled(app, ctx.client.full_name), "об отмене записи")
    _to_admins(ctx, *admin_orders_views.notify_cancelled(app, result, ctx.client))
    return {"outcome": "cancelled", "choice": result.choice.value if result.choice else None,
            "message_html": to_web_html(render.cancelled(result, has_manager=bool(ctx.settings.manager_url))),
            "manager_url": manager_url}


# --- Онлайн-оплата -----------------------------------------------------------------------------------------------
# Шаги - в своих коротких транзакциях (tg/online_payments.py), запрос к ЮKassa - между ними: transaction=False.

@route("POST", f"/orders/{ID}/pay", transaction=False)
async def pay_online(ctx: Ctx) -> dict[str, Any]:
    """«Оплатить онлайн»: весь остаток заказа, ссылка на страницу оплаты (fake - кнопки «прошла / не прошла»).
    email - для чека, если его нет в профиле (сохраняется в профиль, как в боте)."""
    payments = _need_payments(ctx)
    order_id = _path_id(ctx)
    email = ctx.client.email
    raw_email = _text(await _body(ctx), "email", 320)
    if raw_email:
        email = parse_email(raw_email)
        if email is None:
            raise _bad("email_invalid", texts.PAY_ONLINE_EMAIL_INVALID)
        async with ctx.session.begin():
            client = await ctx.session.get(Client, ctx.client.id)
            RegistrationService.save_optional(client, OptionalField.EMAIL, email)
    started = await payments.start(ctx.client.id, email, order_id)
    number = started.order_number
    match started.outcome:
        case StartOutcome.READY:
            return {"payment": {
                "id": started.payment_id, "url": started.url, "order_number": number, "title": started.title,
                "amount": serializers.money(started.amount), "fake": not payments.has_payment_page,
                "receipt_email": email if receipts_enabled(ctx.settings) else None,
            }}
        case StartOutcome.NEED_EMAIL:
            raise ApiError(409, "need_email", to_plain(texts.PAY_ONLINE_ASK_EMAIL))
        case StartOutcome.NO_AMOUNT:
            raise ApiError(409, "no_amount", to_plain(texts.PAY_ONLINE_NO_AMOUNT.format(number=number)))
        case StartOutcome.NOTHING_LEFT:
            raise ApiError(409, "nothing_left", to_plain(texts.PAY_ONLINE_NOTHING_LEFT.format(number=number)))
        case StartOutcome.ERROR:
            raise ApiError(502, "payment_error", to_plain(texts.PAY_ONLINE_ERROR.format(number=number)))
        case _:
            raise ApiError(404, "order_unavailable", texts.ORDER_UNAVAILABLE)


@route("POST", f"/payments/{ID}/check", transaction=False)
async def check_payment(ctx: Ctx) -> dict[str, Any]:
    """«Проверить оплату» - только свой платёж. Письмо клиенту не нужно: итог на экране; админам - как в боте."""
    payments = _need_payments(ctx)
    return _payment_result(await payments.check(_path_id(ctx), client_id=ctx.client.id))


@route("POST", f"/payments/{ID}/fake", transaction=False)
async def finish_fake_payment(ctx: Ctx) -> dict[str, Any]:
    """Тестовая оплата (ONLINE_PAYMENTS=fake): «Оплата прошла / не прошла» вместо страницы ЮKassa."""
    payments = _need_payments(ctx)
    if payments.has_payment_page:
        raise ApiError(404, "not_found", "Тестовой оплаты здесь нет.")
    success = _flag(await _body(ctx), "success")
    return _payment_result(await payments.finish_fake(_path_id(ctx), ctx.client.id, success=success))


def _payment_result(applied: Applied | None) -> dict[str, Any]:
    if applied is None:
        raise ApiError(404, "order_unavailable", texts.ORDER_UNAVAILABLE)
    payment, order = applied.payment, applied.order
    match payment.state:
        case PaymentState.SUCCEEDED:
            message = render.online_payment_received(applied)
        case PaymentState.CANCELED:
            message = render.pay_online_failed(order.number, payment.cancel_reason)
        case _:
            message = texts.PAY_ONLINE_PENDING
    return {"state": payment.state.value, "order_id": order.id, "order_number": order.number,
            "url": payment.confirmation_url, "message_html": to_web_html(message)}
