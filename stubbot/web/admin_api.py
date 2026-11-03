"""Админка в приложении: то же, что «⚙️ Админка» в боте, - экранами вместо сообщений.

Кто админ - как в боте: owner и ADMIN_IDS (settings.is_admin); проверка - в каждом запросе (admin_route), кнопка
в приложении только прячет раздел. Все правила - в тех же сервисах, что у бота (services/admin_*): обработчики
только читают ввод и превращают ответы сервисов в JSON и ошибки. Действия попадают в тот же журнал logs/admin.log.

Ввод - тот же, что принимает бот, только без текстовых шаблонов: дни проведения приходят списком дат, «запись
до» - датой и временем, тарифы - списком, текст с форматированием - HTML из редактора (web/html.py превращает
его в разметку Telegram). Проверяют их те же функции бота (parse_field, parse_days, parse_deadline, valid_prices).

Файлы: обложку сервер отправляет админу в чат с ботом (так у неё появляется file_id, как у фото в боте) и сразу
удаляет это сообщение; программу курса (.docx / .txt) читает сам. Счёт и сообщение клиенту уходят от имени бота,
как из админки в боте. Выгрузка клиентов - файлом в чат админа с ботом: персональные данные не скачиваются в браузер.
"""

import hashlib
import hmac
import logging
import re
from collections.abc import Callable
from datetime import date, datetime
from pathlib import PurePath
from typing import Any
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup
from aiohttp import web

from stubbot.db.enums import MANUAL_PAYMENT_METHODS, ProfileStatus
from stubbot.db.models import Client
from stubbot.repositories.admin_courses import AdminCourseRepository
from stubbot.repositories.admin_sessions import AdminSessionRepository, SessionScope
from stubbot.repositories.catalog import CatalogRepository
from stubbot.services.admin_clients import (
    ADVANCE_REASON_LIMIT,
    CLIENTS_PAGE,
    AdminClientService,
    ClientFilter,
    ClientSort,
    AdvanceInputError,
    AdvanceOutcome,
    QueryKind,
    parse_advance,
)
from stubbot.services.admin_courses import AdminCourseService
from stubbot.services.admin_dictionaries import (
    SPECS,
    AdminDictionaryService,
    AdminEntityService,
    DeleteResult,
    DictField,
    DictKind,
    FieldKind,
    SaveResult,
    parse_field,
)
from stubbot.services.admin_log import log_admin_action
from stubbot.services.admin_orders import ActionResult, AdminOrderService, OrderCategory, Outcome
from stubbot.services.admin_registry import ADMIN_SPECS
from stubbot.services.admin_sessions import ADMIN_STATUSES, MAX_PRICE_LABEL, PRICE_KINDS, AdminSessionService
from stubbot.services.client_export import ClientExportService
from stubbot.services.clients import ClientService
from stubbot.services.schedule import SessionCard, deadline_passed
from stubbot.tg import admin_orders_views, keyboards, render, texts
from stubbot.tg.middlewares.text_cleanup import strip_nul
from stubbot.tg.outgoing import CAPTION_LIMIT, TEXT_LIMIT, Delivery, send_once
from stubbot.utils.documents import MAX_FILE_SIZE, SUPPORTED_EXTENSIONS, file_to_html
from stubbot.utils.files import requisites_file_ok
from stubbot.utils.money import format_rub
from stubbot.utils.dates import format_day, format_moment
from stubbot.utils.schedule_input import MAX_DAYS, MAX_PRICE_RUB, parse_days, parse_deadline
from stubbot.web import admin_serializers as views
from stubbot.web import serializers
from stubbot.web.api import ID, _body, _bad, _file_name, _flag, _ids, _int, _need_applications, _path_id, _text, \
    _to_client, route
from stubbot.web.app import COVERS, ApiError, Ctx, Handler
from stubbot.web.files import FILE_ID, MAX_IMAGE, image_type
from stubbot.web.html import to_plain, to_telegram_html, to_web_html

logger = logging.getLogger(__name__)

KIND = r"{kind:lec|ven|spec|pos|crs|ses}"
DICTIONARIES = (DictKind.LECTURERS, DictKind.VENUES, DictKind.SPECIALTIES, DictKind.POSITIONS)
TOKEN = re.compile(r"[A-Za-z0-9_-]{8,64}")
PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp")
PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
MAX_RICH_INPUT = 200_000  # HTML из редактора до очистки: с тегами он длиннее того, что сохранится
LOG_ORDER = "заказ"
LOG_CLIENTS = "клиенты"


def admin_route(method: str, path: str, transaction: bool = True) -> Callable[[Handler], Handler]:
    """Обработчик админки: сначала - админ ли это (как фильтр IsAdmin на роутере админки в боте)."""

    def register(handler: Handler) -> Handler:
        async def guarded(ctx: Ctx) -> Any:
            if not ctx.settings.is_admin(ctx.user.id):
                raise ApiError(403, "forbidden", "Этот раздел - только для администраторов центра.")
            return await handler(ctx)

        guarded.__name__ = handler.__name__
        return route(method, path, transaction)(guarded)

    return register


# --- Общее -------------------------------------------------------------------------------------------------------

def _kind(ctx: Ctx) -> DictKind:
    return DictKind(ctx.request.match_info["kind"])


def _service(ctx: Ctx, kind: DictKind) -> AdminEntityService:
    """Тот же сервис, что у админки в боте; Telegram ID админа - в журнал действий."""
    if kind is DictKind.COURSES:
        return AdminCourseService(ctx.session, ctx.user.id, ctx.today)
    if kind is DictKind.SESSIONS:
        return AdminSessionService(ctx.session, ctx.user.id, ctx.today)
    return AdminDictionaryService(ctx.session, SPECS[kind], ctx.user.id)


def _courses(ctx: Ctx) -> AdminCourseService:
    return AdminCourseService(ctx.session, ctx.user.id, ctx.today)


def _sessions(ctx: Ctx) -> AdminSessionService:
    return AdminSessionService(ctx.session, ctx.user.id, ctx.today)


def _clients(ctx: Ctx) -> AdminClientService:
    return AdminClientService(ctx.session, ctx.user.id)


def _orders(ctx: Ctx) -> AdminOrderService:
    return AdminOrderService(ctx.session, ctx.user.id)


def _conflict(code: str, text: str) -> ApiError:
    return ApiError(409, code, to_plain(text))


def _not_found(text: str = texts.ADMIN_NOT_FOUND) -> ApiError:
    return ApiError(404, "not_found", to_plain(text))


def _token(data: dict[str, Any]) -> str:
    """Одноразовый ключ действия из приложения: им отсекается двойное нажатие (как ключ в кнопке бота)."""
    value = data.get("token")
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise _bad("bad_request")
    return value


def _kopecks(data: dict[str, Any], key: str, signed: bool = False) -> int:
    """Сумма в копейках, целыми рублями (так деньги вводят и в боте), не больше 10 млн ₽."""
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value % 100:
        raise _bad("bad_amount", "Нужна сумма в рублях, целым числом.")
    if not 0 < abs(value) <= MAX_PRICE_RUB * 100 or (value < 0 and not signed):
        raise _bad("bad_amount", "Нужна сумма в рублях, целым числом.")
    return value


def _photo_token(ctx: Ctx, file_id: str) -> str:
    """Подпись к file_id обложки: сохранить можно только картинку, загруженную через этот сервер, - не чужой
    file_id (например, файла реквизитов), который потом отдавался бы как обложка."""
    key = (ctx.bot_token or "webapp").encode()
    return hmac.new(key, f"cover:{file_id}".encode(), hashlib.sha256).hexdigest()[:40]


def _photo(ctx: Ctx, file_id: str | None) -> dict[str, Any] | None:
    if not file_id:
        return None
    return {"file_id": file_id, "token": _photo_token(ctx, file_id), "url": serializers.cover_url(file_id)}


# --- Сводка и описание полей --------------------------------------------------------------------------------------

@admin_route("GET", "/admin/summary")
async def summary(ctx: Ctx) -> dict[str, Any]:
    """Главный экран админки: сколько чего в каждом разделе."""
    applications = ctx.settings.applications_enabled
    sessions, courses = _sessions(ctx), _courses(ctx)
    data: dict[str, Any] = {
        "applications": applications,
        "afisha": await sessions.count(SessionScope.ACTIVE),
        "courses": len(await courses.items()),
        "archive": {"courses": await courses.archived_count(), "sessions": await sessions.count(SessionScope.ARCHIVED),
                    "past": await sessions.count(SessionScope.PAST)},
        "dictionaries": {kind.value: sum(1 for item in await _service(ctx, kind).items() if item.is_active)
                         for kind in DICTIONARIES},
    }
    if applications:
        counts, waiting = await _orders(ctx).counts(ctx.today, ctx.now)
        data["orders"] = {category.value: count for category, count in counts.items()}
        data["orders_waiting_client"] = waiting
        data["clients"] = await _clients(ctx).total()
        data["waitlists"] = sum(count for _, count in await _clients(ctx).waitlists())
    log_admin_action(ctx.user.id, "админка", "вход (приложение)")
    return data


@admin_route("GET", "/admin/specs")
async def specs(ctx: Ctx) -> dict[str, Any]:
    """Поля курсов, проведений и справочников - по ним приложение строит формы (как мастер в боте)."""
    return {
        "kinds": {kind.value: views.spec(spec) for kind, spec in ADMIN_SPECS.items()},
        "price_kinds": [{"value": kind, "label": texts.PRICE_KIND_LABELS[kind],
                         "per_group": kind == "group"} for kind in PRICE_KINDS],
        "statuses": [{"value": status, "label": to_plain(texts.SESSION_STATUS_LABELS[status])}
                     for status in ADMIN_STATUSES],
        "payment_methods": [{"value": method.value, "label": to_plain(texts.BTN_PAYMENT_METHODS[method.value])}
                            for method in MANUAL_PAYMENT_METHODS],
        "max_days": MAX_DAYS,
        "max_price_label": MAX_PRICE_LABEL,
    }


# --- Ввод значений полей -------------------------------------------------------------------------------------------

def _field_error(field: DictField) -> str:
    match field.kind:
        case FieldKind.NUMBER:
            return to_plain(texts.ADMIN_FIELD_ERRORS["number"].format(min=field.min_value, max=field.max_value))
        case FieldKind.FULL_NAME | FieldKind.LINE | FieldKind.URL | FieldKind.HTML | FieldKind.DOCUMENT:
            return to_plain(texts.ADMIN_FIELD_ERRORS[field.kind.value].format(max=field.max_length))
        case FieldKind.DAYS:
            return (f"Проверьте дни: даты - начиная с завтрашнего, время начала раньше конца, "
                    f"не больше {MAX_DAYS} дней.")
        case FieldKind.DATETIME:
            return "Нужна будущая дата и время."
        case FieldKind.PRICES:
            return (f"Проверьте тарифы: у каждого - подпись до {MAX_PRICE_LABEL} символов и цена в рублях, "
                    f"категории без повторов.")
        case FieldKind.PHOTO:
            return "Загрузите картинку ещё раз."
    return "Выберите вариант из списка."


def _invalid(field: DictField, message: str | None = None) -> ApiError:
    return ApiError(400, "invalid_value", message or _field_error(field), field=field.name)


def _value(ctx: Ctx, field: DictField, raw: Any, current: Any = None) -> Any:
    """Значение поля из приложения → как его принимает сервис (то же, что после ввода в боте). Пусто - None."""
    if raw is None or raw == "" or raw == []:
        return None
    match field.kind:
        case FieldKind.FULL_NAME | FieldKind.LINE | FieldKind.URL:
            if not isinstance(raw, str) or len(raw) > 5000:
                raise _invalid(field)
            value = parse_field(field, strip_nul(raw))
        case FieldKind.NUMBER:
            if isinstance(raw, bool) or not isinstance(raw, int | str) or len(str(raw)) > 12:
                raise _invalid(field)
            value = parse_field(field, str(raw))
        case FieldKind.HTML | FieldKind.DOCUMENT:
            if not isinstance(raw, str) or len(raw) > MAX_RICH_INPUT:
                raise _invalid(field)
            telegram_html = to_telegram_html(strip_nul(raw))
            if not telegram_html:
                return None
            value = parse_field(field, telegram_html)
        case FieldKind.CHOICE:
            value = raw if isinstance(raw, str) and raw in field.choices else None
        case FieldKind.MULTI:
            value = sorted(set(_ids({"ids": raw}, "ids")))
        case FieldKind.REF | FieldKind.COURSE:
            value = _int({"id": raw}, "id")
        case FieldKind.PHOTO:
            value = _photo_value(ctx, raw, current)
        case FieldKind.DAYS:
            value = _days(ctx, raw)
        case FieldKind.DATETIME:
            value = _deadline(ctx, raw)
        case FieldKind.PRICES:
            value = _prices(raw)
        case _:
            value = None
    if value is None:
        raise _invalid(field)
    return value


def _photo_value(ctx: Ctx, raw: Any, current: Any) -> str | None:
    if isinstance(raw, str):
        return raw if raw == current else None  # file_id как есть - только тот, что уже стоит
    if isinstance(raw, dict):
        file_id, token = raw.get("file_id"), raw.get("token")
        if isinstance(file_id, str) and isinstance(token, str) and FILE_ID.fullmatch(file_id) \
                and hmac.compare_digest(token, _photo_token(ctx, file_id)):
            return file_id
    return None


def _days(ctx: Ctx, raw: Any) -> list[dict[str, str | None]] | None:
    """[{date: «2026-11-13», start: «10:00», end: «18:00»}] → те же строки «13.11.2026 10:00-18:00», что вводят
    в боте, и та же проверка parse_days: даты не раньше завтра, начало раньше конца, не больше 14 дней."""
    if not isinstance(raw, list) or len(raw) > MAX_DAYS:
        return None
    lines = []
    for day in raw:
        if not isinstance(day, dict):
            return None
        value, start, end = day.get("date"), day.get("start"), day.get("end")
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d") if isinstance(value, str) else None
        except ValueError:
            return None
        if parsed is None:
            return None
        line = parsed.strftime("%d.%m.%Y")
        if start or end:
            if not (isinstance(start, str) and isinstance(end, str) and len(start) <= 5 and len(end) <= 5):
                return None
            line += f" {start}-{end}"
        lines.append(line)
    return parse_days("\n".join(lines), ctx.today)


def _deadline(ctx: Ctx, raw: Any) -> str | None:
    """«Запись до»: «2026-11-12T18:00» (часовой пояс центра) или ISO с поясом - та же проверка, что в боте."""
    if not isinstance(raw, str) or len(raw) > 40:
        return None
    tz = ZoneInfo(ctx.settings.timezone)
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(tz)
    return parse_deadline(moment.strftime("%d.%m.%Y %H:%M"), ctx.now, tz)


def _prices(raw: Any) -> list[dict[str, Any]] | None:
    """[{kind, label, amount}] (amount - копейки, целые рубли) → тарифы в порядке категорий, как в афише."""
    if not isinstance(raw, list) or len(raw) > len(PRICE_KINDS):
        return None
    prices: dict[str, dict[str, Any]] = {}
    for price in raw:
        if not isinstance(price, dict):
            return None
        kind, label, amount = price.get("kind"), price.get("label"), price.get("amount")
        if kind not in PRICE_KINDS or kind in prices or not isinstance(label, str):
            return None
        label = " ".join(strip_nul(label).split())
        if not 0 < len(label) <= MAX_PRICE_LABEL:
            return None
        if isinstance(amount, bool) or not isinstance(amount, int) or amount % 100 \
                or not 0 < amount <= MAX_PRICE_RUB * 100:
            return None
        prices[kind] = {"kind": kind, "label": label, "amount": amount}
    return [prices[kind] for kind in PRICE_KINDS if kind in prices]


def _values(ctx: Ctx, kind: DictKind, raw: Any) -> dict[str, Any]:
    """Все поля новой записи. Поле, которое к выбранному не относится (площадка у онлайна), - пустое, как пропуск
    шага в мастере бота. Обязательное пустое - ошибка с именем поля."""
    if not isinstance(raw, dict):
        raise _bad("bad_request")
    values: dict[str, Any] = {}
    for field in ADMIN_SPECS[kind].fields:
        if not field.applies(values):
            values[field.name] = None
            continue
        values[field.name] = _value(ctx, field, raw.get(field.name))
        if field.required and not values[field.name]:
            raise _invalid(field, f"Заполните поле «{texts.ADMIN_FIELD_LABELS.get(field.name, field.name)}».")
    return values


def _save_error(result: SaveResult, field: DictField | None = None) -> ApiError:
    match result:
        case SaveResult.DUPLICATE:
            return ApiError(409, "duplicate", texts.ADMIN_DUPLICATE + ".", field=field.name if field else None)
        case SaveResult.MISSING_REQUIRED:
            return ApiError(400, "required", texts.ADMIN_REQUIRED + ".", field=field.name if field else None)
        case SaveResult.NOT_FOUND:
            return _not_found()
    return ApiError(400, "bad_value", "Значение не подходит - обновите экран и выберите снова.",
                    field=field.name if field else None)


# --- Курсы, проведения, справочники: списки ------------------------------------------------------------------------

def _scope(ctx: Ctx) -> SessionScope:
    return {"archived": SessionScope.ARCHIVED, "past": SessionScope.PAST}.get(ctx.request.query.get("scope", ""),
                                                                            SessionScope.ACTIVE)


@admin_route("GET", f"/admin/e/{KIND}")
async def entity_list(ctx: Ctx) -> dict[str, Any]:
    kind = _kind(ctx)
    tz = ctx.settings.timezone
    if kind is DictKind.COURSES:
        archived = ctx.request.query.get("scope") == "archived"
        programs = await AdminCourseRepository(ctx.session).list_with_relations(archived)
        sessions = AdminSessionRepository(ctx.session)
        waiting = {program.id: count for program, count in await _clients(ctx).waitlists()} \
            if ctx.settings.applications_enabled else {}
        return {"items": [views.course_row(program, await sessions.active_count_for_program(program.id, ctx.today),
                                           waiting.get(program.id, 0), tz) for program in programs]}
    if kind is DictKind.SESSIONS:
        scope = _scope(ctx)
        items = await AdminSessionRepository(ctx.session).list_scope(scope, ctx.today)
        if scope is SessionScope.PAST:
            items.reverse()  # прошедшие - сначала недавние
        taken = await CatalogRepository(ctx.session).taken_seats_by_session([item.id for item in items])
        return {"items": [views.session_row(item, scope, taken.get(item.id, 0), tz) for item in items]}
    service = _service(ctx, kind)
    rows = []
    for item in await service.items():
        subtitle = getattr(item, "regalia", None) or getattr(item, "address", None)
        rows.append({"id": item.id, "title": service.title(item), "active": service.is_active(item),
                     "subtitle": subtitle})
    return {"items": rows}


async def _options(ctx: Ctx, kind: DictKind, values: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Варианты полей-выборов (направления, лекторы, площадка, курс) - действующие и уже выбранные."""
    service = _service(ctx, kind)
    found = {}
    for field in ADMIN_SPECS[kind].fields:
        if field.kind is FieldKind.MULTI:
            found[field.name] = views.options(await service.options(field, list(values.get(field.name) or [])))
        elif field.kind is FieldKind.REF:
            current = values.get(field.name)
            found[field.name] = views.options(await service.options(field, [current] if current else []))
    return found


@admin_route("GET", f"/admin/e/{KIND}/new")
async def entity_form(ctx: Ctx) -> dict[str, Any]:
    """Форма новой записи: варианты полей-выборов. У проведения - ещё курсы для выбора карточками."""
    kind = _kind(ctx)
    data: dict[str, Any] = {"kind": kind.value, "options": await _options(ctx, kind, {})}
    if kind is DictKind.SESSIONS:
        programs = await AdminCourseRepository(ctx.session).list_with_relations(archived=False)
        data["courses"] = [views.course_row(program, 0, 0, ctx.settings.timezone) for program in programs]
    return data


# --- Карточка записи -----------------------------------------------------------------------------------------------

async def _item(ctx: Ctx, kind: DictKind, item_id: int) -> dict[str, Any]:
    service = _service(ctx, kind)
    item = await service.item(item_id)
    if item is None:
        raise _not_found()
    spec = service.spec
    raw = {field.name: service.field_value(item, field) for field in spec.fields}
    data: dict[str, Any] = {
        "kind": kind.value,
        "id": item.id,
        "title": service.title(item),
        "active": service.is_active(item),
        "editable": True,
        "values": {name: views.json_value(spec.field(name), value) for name, value in raw.items()},
        "html": {f.name: to_web_html(raw[f.name]) for f in spec.fields if f.kind in views.RICH_KINDS},
        "photos": {f.name: _photo(ctx, raw[f.name]) for f in spec.fields if f.kind is FieldKind.PHOTO},
        "options": await _options(ctx, kind, raw),
        "display": _display(ctx, spec, raw),
    }
    tz = ctx.settings.timezone
    if isinstance(service, AdminCourseService):
        data["editable"] = service.is_active(item)
        waiting = await _clients(ctx).waiting_count(item.id) if ctx.settings.applications_enabled else 0
        data["course"] = {**views.course_row(item, await service.sessions_in_afisha(item.id), waiting, tz),
                          "sessions_total": await service.sessions_total(item.id)}
    elif isinstance(service, AdminSessionService):
        scope = service.scope(item)
        data["editable"] = scope is SessionScope.ACTIVE
        card = SessionCard(session=item, seats_left=None, deadline_passed=deadline_passed(item))
        participants = (await _clients(ctx).participants_count(item.id)
                        if ctx.settings.applications_enabled else None)
        data["session"] = {
            "scope": views.SCOPE_NAMES[scope],
            "status": item.status.value,
            "status_label": to_plain(texts.SESSION_STATUS_LABELS.get(item.status.value, "")),
            "program": {"id": item.program.id, "title": item.program.title,
                        "archived": item.program.archived_at is not None},
            "past": service.is_past(item),
            "archived_at": views.local_date(item.archived_at, tz),
            "participants": participants,
            "card": serializers.course_details(card, tz, ctx.settings.applications_enabled, None),
        }
    return data


def _display(ctx: Ctx, spec: Any, raw: dict[str, Any]) -> dict[str, Any]:
    """Готовые подписи полей, которые неудобно собирать в браузере: дни, «запись до» (в часовом поясе центра,
    и для поля ввода), тарифы - как их показывает бот."""
    tz = ZoneInfo(ctx.settings.timezone)
    shown: dict[str, Any] = {}
    for field in spec.fields:
        value = raw.get(field.name)
        if not value:
            continue
        if field.kind is FieldKind.DAYS:
            shown[field.name] = " · ".join(
                format_day(date.fromisoformat(day["date"])) + (f", {day['start']}–{day['end']}" if day.get("start") else "")
                for day in value)
        elif field.kind is FieldKind.DATETIME:
            moment = datetime.fromisoformat(value) if isinstance(value, str) else value
            shown[field.name] = format_moment(moment, ctx.settings.timezone)
            shown[f"{field.name}_input"] = moment.astimezone(tz).strftime("%Y-%m-%dT%H:%M")
        elif field.kind is FieldKind.PRICES:
            shown[field.name] = "; ".join(
                f"{price['label']} - {format_rub(price['amount'])}" + (" за двоих" if price["kind"] == "group" else "")
                for price in value)
    return shown


@admin_route("GET", f"/admin/e/{KIND}/{ID}")
async def entity_item(ctx: Ctx) -> dict[str, Any]:
    return await _item(ctx, _kind(ctx), _path_id(ctx))


@admin_route("POST", f"/admin/e/{KIND}/preview")
async def entity_preview(ctx: Ctx) -> dict[str, Any]:
    """Предпросмотр перед сохранением - как курс или проведение увидят клиенты (шаг «Всё верно?» в боте)."""
    kind = _kind(ctx)
    values = _values(ctx, kind, (await _body(ctx)).get("values"))
    tz = ctx.settings.timezone
    if kind is DictKind.SESSIONS:
        card = await _sessions(ctx).preview(values)
        card.session.id = 0
        return {"card": serializers.course_details(card, tz, ctx.settings.applications_enabled, None)}
    if kind is DictKind.COURSES:
        program = await _courses(ctx).preview(values)
        program.id = 0
        return {"course": {**views.course_row(program, 0, 0, tz),
                           "program_html": to_web_html(program.program_html),
                           "description_html": to_web_html(program.description_html)}}
    return {"values": values}


@admin_route("POST", f"/admin/e/{KIND}")
async def entity_create(ctx: Ctx) -> dict[str, Any]:
    kind = _kind(ctx)
    values = _values(ctx, kind, (await _body(ctx)).get("values"))
    result, item = await _service(ctx, kind).create(values)
    if result is not SaveResult.OK:
        raise _save_error(result, ADMIN_SPECS[kind].field(ADMIN_SPECS[kind].title_field))
    await ctx.session.flush()
    return {"item": await _item(ctx, kind, item.id), "message": to_plain(texts.ADMIN_SAVED)}


@admin_route("PATCH", f"/admin/e/{KIND}/{ID}")
async def entity_update(ctx: Ctx) -> dict[str, Any]:
    """Правка одного поля - как «✏️» в карточке бота. Пусто - очистить необязательное поле."""
    kind, item_id = _kind(ctx), _path_id(ctx)
    data = await _body(ctx)
    service = _service(ctx, kind)
    field = service.spec.field(_text(data, "field", 64) or "")
    if field is None or not field.editable:
        raise _bad("bad_request")
    item = await service.item(item_id)
    if item is None:
        raise _not_found()
    value = _value(ctx, field, data.get("value"), current=service.field_value(item, field))
    result = await service.update_field(item_id, field, value)
    if result is not SaveResult.OK:
        raise _save_error(result, field)
    await ctx.session.flush()
    ctx.session.expire_all()  # связи (направления, тарифы) - заново из базы
    return {"item": await _item(ctx, kind, item_id), "message": to_plain(texts.ADMIN_SAVED)}


@admin_route("POST", f"/admin/e/{KIND}/{ID}/archive")
async def entity_archive(ctx: Ctx) -> dict[str, Any]:
    """«В архив» / «Вернуть из архива». Курс с проведениями в афише в архив не уходит; проведение не вернуть,
    пока в архиве его курс (как в боте)."""
    kind, item_id = _kind(ctx), _path_id(ctx)
    archive = _flag(await _body(ctx), "archived")
    service = _service(ctx, kind)
    item = await service.item(item_id)
    if item is None:
        raise _not_found()
    if isinstance(service, AdminCourseService) and archive and (count := await service.sessions_in_afisha(item_id)):
        raise _conflict("has_sessions", texts.ADMIN_COURSE_HAS_SESSIONS.format(count=count))
    if isinstance(service, AdminSessionService):
        # В архив - только из афиши (прошедшее уже не в ней), из архива - только убранное вручную.
        if service.scope(item) is not (SessionScope.ACTIVE if archive else SessionScope.ARCHIVED):
            raise _conflict("unavailable", "Проведение уже в другом разделе - обновите экран.")
        if not archive and item.program.archived_at is not None:
            raise _conflict("course_archived", texts.ADMIN_RESTORE_COURSE_FIRST)
    if service.is_active(item) == archive and await service.set_active(item_id, not archive) is None:
        raise _not_found()
    await ctx.session.flush()
    if archive:
        notice = {DictKind.COURSES: texts.ADMIN_COURSE_ARCHIVED_NOTICE,
                  DictKind.SESSIONS: texts.ADMIN_SESSION_ARCHIVED_NOTICE}.get(kind, texts.ADMIN_ARCHIVED_NOTICE)
    else:
        notice = {DictKind.COURSES: texts.ADMIN_COURSE_RESTORED_NOTICE}.get(kind, texts.ADMIN_RESTORED_NOTICE)
        if isinstance(service, AdminSessionService):
            notice = (texts.ADMIN_SESSION_RESTORED_PAST_NOTICE if service.is_past(item)
                      else texts.ADMIN_SESSION_RESTORED_NOTICE)
    return {"item": await _item(ctx, kind, item_id), "message": to_plain(notice)}


@admin_route("DELETE", f"/admin/e/{KIND}/{ID}")
async def entity_delete(ctx: Ctx) -> dict[str, Any]:
    """«Удалить навсегда» - только курсы и проведения и только из архива (у проведения - и из прошедших)."""
    kind = _kind(ctx)
    if kind not in (DictKind.COURSES, DictKind.SESSIONS):
        raise _bad("bad_request")
    service = _courses(ctx) if kind is DictKind.COURSES else _sessions(ctx)
    result = await service.delete(_path_id(ctx))
    if result is not DeleteResult.OK:
        raise _conflict(result.value, texts.ADMIN_DELETE_ERRORS[result.value])
    return {"message": to_plain(texts.ADMIN_DELETED_NOTICE)}


@admin_route("POST", f"/admin/e/ses/{ID}/status")
async def session_status(ctx: Ctx) -> dict[str, Any]:
    status = _text(await _body(ctx), "status", 32) or ""
    if await _sessions(ctx).set_status(_path_id(ctx), status) is None:
        raise _conflict("unavailable", "Статус не поменять: проведение уже не в афише или статус не тот.")
    await ctx.session.flush()
    return {"item": await _item(ctx, DictKind.SESSIONS, _path_id(ctx)), "message": to_plain(texts.ADMIN_STATUS_SET)}


@admin_route("GET", f"/admin/e/ses/{ID}/copy")
async def session_copy(ctx: Ctx) -> dict[str, Any]:
    """«Скопировать»: всё из проведения, кроме дней (их спросят заново); копия встанет в афишу новым проведением."""
    service = _sessions(ctx)
    item = await service.item(_path_id(ctx))
    if item is None:
        raise _not_found()
    if item.program.archived_at is not None:
        raise _conflict("course_archived", texts.ADMIN_COPY_COURSE_ARCHIVED)
    values = await service.copy_values(item.id)
    values["cover_file_id"] = _photo(ctx, values.get("cover_file_id"))
    return {"values": values, "options": await _options(ctx, DictKind.SESSIONS, values),
            "program": {"id": item.program.id, "title": item.program.title}}


# --- Файлы: обложка и программа курса ------------------------------------------------------------------------------

async def _upload(ctx: Ctx, limit: int) -> tuple[str, bytes]:
    try:
        form = await ctx.request.post()
    except web.HTTPRequestEntityTooLarge as error:
        raise ApiError(413, "file_too_large", "Файл слишком большой.") from error
    except ValueError as error:
        raise _bad("bad_request") from error
    field = form.get("file")
    if not isinstance(field, web.FileField):
        raise _bad("file_required", "Выберите файл.")
    content = field.file.read(limit + 1)
    if not content or len(content) > limit:
        raise ApiError(413, "file_too_large", "Файл слишком большой.")
    return strip_nul(_file_name(field.filename or ""))[:256], content


@admin_route("POST", "/admin/upload/photo", transaction=False)
async def upload_photo(ctx: Ctx) -> dict[str, Any]:
    """Обложка: картинка уходит админу в чат с ботом (file_id - как у фото, присланного боту), сообщение сразу
    удаляется. Ответ - file_id с подписью: сохранить в курс можно только его."""
    _, content = await _upload(ctx, MAX_IMAGE)
    if image_type(content[:16]) not in PHOTO_TYPES:
        raise _bad("not_image", "Нужна картинка JPG, PNG или WebP до 10 МБ.")
    if ctx.bot is None:
        raise ApiError(503, "bot_unavailable", "Картинку сейчас не загрузить - бот недоступен.")
    try:
        name = "cover." + image_type(content[:16]).split("/")[1]
        sent = await ctx.bot.send_photo(ctx.user.id, BufferedInputFile(content, filename=name),
                                        caption="Обложка из приложения", disable_notification=True)
    except TelegramForbiddenError as error:
        raise _conflict("no_chat", "Откройте чат с ботом и нажмите /start - через него загружаются картинки.") \
            from error
    except TelegramBadRequest as error:
        raise _bad("photo_rejected", "Telegram не принял картинку - попробуйте другую.") from error
    file_id = sent.photo[-1].file_id
    try:
        await ctx.bot.delete_message(ctx.user.id, sent.message_id)
    except TelegramAPIError:
        pass
    ctx.request.app[COVERS].store(file_id, content)
    return {"photo": _photo(ctx, file_id)}


@admin_route("POST", "/admin/upload/program")
async def upload_program(ctx: Ctx) -> dict[str, Any]:
    """Программа файлом .docx / .txt (как в боте) → текст с форматированием для редактора."""
    name, content = await _upload(ctx, MAX_FILE_SIZE)
    if not name.lower().endswith(SUPPORTED_EXTENSIONS):
        raise _bad("file_type", texts.ADMIN_FILE_UNSUPPORTED)
    html = file_to_html(name, content)
    if not html:
        raise _bad("unreadable", texts.ADMIN_FILE_UNREADABLE)
    return {"html": to_web_html(html)}


# --- Участники и листы ожидания -----------------------------------------------------------------------------------

@admin_route("GET", f"/admin/e/ses/{ID}/participants")
async def session_participants(ctx: Ctx) -> dict[str, Any]:
    _need_applications(ctx)
    view = await _clients(ctx).participants(_path_id(ctx))
    if view is None:
        raise _not_found()
    return views.participants(view)


@admin_route("GET", "/admin/waitlists")
async def waitlists(ctx: Ctx) -> dict[str, Any]:
    _need_applications(ctx)
    return {"items": [{"program_id": program.id, "title": program.title, "count": count}
                      for program, count in await _clients(ctx).waitlists()]}


@admin_route("GET", f"/admin/waitlists/{ID}")
async def course_waitlist(ctx: Ctx) -> dict[str, Any]:
    _need_applications(ctx)
    program, rows = await _clients(ctx).course_waitlist(_path_id(ctx))
    if program is None:
        raise _not_found()
    tz = ZoneInfo(ctx.settings.timezone)
    return {"program": {"id": program.id, "title": program.title},
            "items": [{**views.client_row(client), "since": since.astimezone(tz).strftime("%d.%m.%Y")}
                      for client, since in rows]}


@admin_route("POST", f"/admin/waitlists/{ID}/invited")
async def waitlist_invited(ctx: Ctx) -> dict[str, Any]:
    """«Приглашены»: все из листа ожидания курса отмечены приглашёнными - лист пустеет (как в боте)."""
    _need_applications(ctx)
    count = await _clients(ctx).mark_invited(_path_id(ctx))
    return {"count": count, "message": to_plain(texts.ADMIN_INVITED_DONE.format(count=count))}


# --- Заказы --------------------------------------------------------------------------------------------------------

async def _order(ctx: Ctx, order_id: int | None = None) -> dict[str, Any]:
    card = await _orders(ctx).card(order_id or _path_id(ctx))
    if card is None:
        raise _not_found(texts.ADMIN_ORDER_UNAVAILABLE)
    return views.order_card(card, ctx.settings.timezone, ctx.now)


@admin_route("GET", "/admin/orders")
async def orders(ctx: Ctx) -> dict[str, Any]:
    _need_applications(ctx)
    try:
        category = OrderCategory(ctx.request.query.get("category", OrderCategory.NEED_INVOICE.value))
    except ValueError as error:
        raise _bad("bad_request") from error
    rows = await _orders(ctx).category(category, ctx.today, ctx.now)
    return {"category": category.value, "title": to_plain(texts.ADMIN_ORDER_CATEGORY_TITLES[category.value]),
            "items": [views.order_row(order, payer, ctx.settings.timezone, ctx.now) for order, payer in rows]}


@admin_route("GET", "/admin/orders/find")
async def find_order(ctx: Ctx) -> dict[str, Any]:
    _need_applications(ctx)
    number = ctx.request.query.get("number", "").strip().lstrip("№").strip()
    if not (number.isdigit() and len(number) == 6):
        raise _bad("bad_number", texts.ADMIN_ORDER_BAD_NUMBER)
    order = await _orders(ctx).find(int(number))
    if order is None:
        raise _not_found(texts.ADMIN_ORDER_NOT_FOUND.format(number=number))
    return {"id": order.id}


@admin_route("GET", f"/admin/orders/{ID}")
async def order(ctx: Ctx) -> dict[str, Any]:
    _need_applications(ctx)
    return await _order(ctx)


def _refused(result: ActionResult) -> ApiError:
    popups = {Outcome.DUPLICATE: texts.ADMIN_ORDER_DUPLICATE, Outcome.NO_ADVANCE: texts.ADMIN_ORDER_NO_ADVANCE,
              Outcome.ALREADY: texts.ADMIN_ORDER_INVOICE_ALREADY}
    return _conflict(result.outcome.value, popups.get(result.outcome, texts.ADMIN_ORDER_UNAVAILABLE))


@admin_route("POST", f"/admin/orders/{ID}/payment")
async def mark_payment(ctx: Ctx) -> dict[str, Any]:
    """Отметить оплату: сумма (или «весь остаток») и способ. Ключ token - уникальный ключ платежа: двойное нажатие
    не создаст второй платёж. Клиенту - «оплата получена», как из бота."""
    _need_applications(ctx)
    data = await _body(ctx)
    method = next((m for m in MANUAL_PAYMENT_METHODS if m.value == data.get("method")), None)
    if method is None:
        raise _bad("bad_request")
    token = _token(data)
    service, order_id = _orders(ctx), _path_id(ctx)
    card = await service.card(order_id)
    if card is None or not card.payable:
        raise _conflict("unavailable", texts.ADMIN_ORDER_UNAVAILABLE)
    amount = card.left if _flag(data, "full") else _kopecks(data, "amount")
    if not amount:
        raise _conflict("unavailable", texts.ADMIN_ORDER_UNAVAILABLE)
    if card.left is not None and amount > card.left:
        raise _bad("too_much", texts.ADMIN_ORDER_AMOUNT_TOO_MUCH.format(left=format_rub(card.left)))
    result = await service.mark_payment(order_id, amount, method, f"web-{token}")
    if result.outcome is not Outcome.DONE:
        raise _refused(result)
    card = await service.card(order_id)
    _to_client(ctx, card.payer, render.payment_received(card.session, card.order, result.paid, result.full),
               "об оплате")
    return {"order": views.order_card(card, ctx.settings.timezone, ctx.now),
            "message": to_plain(texts.ADMIN_ORDER_PAYMENT_SAVED.format(amount=format_rub(result.amount)))}


@admin_route("POST", f"/admin/orders/{ID}/total")
async def set_total(ctx: Ctx) -> dict[str, Any]:
    """Сумма заказа, которую при записи не знали («стоимость уточнит менеджер»): с этого момента - 72 часа на оплату."""
    _need_applications(ctx)
    amount = _kopecks(await _body(ctx), "amount")
    service, order_id = _orders(ctx), _path_id(ctx)
    if await service.set_total(order_id, amount, ctx.now) is None:
        card = await service.card(order_id)
        if card is not None and card.payable and card.order.total_amount is None and amount < card.paid:
            raise _bad("less_than_paid", texts.ADMIN_ORDER_TOTAL_INVALID.format(paid=format_rub(card.paid)))
        raise _conflict("unavailable", texts.ADMIN_ORDER_UNAVAILABLE)
    return {"order": await _order(ctx, order_id),
            "message": to_plain(texts.ADMIN_ORDER_TOTAL_SAVED.format(amount=format_rub(amount)))}


@admin_route("POST", f"/admin/orders/{ID}/invoice-manual")
async def invoice_manual(ctx: Ctx) -> dict[str, Any]:
    """«Счёт отправлен вне бота» (почта, 1С): заказ переходит в «Счёт отправлен»."""
    _need_applications(ctx)
    result = await _orders(ctx).mark_invoice_manual(_path_id(ctx), ctx.now)
    if result.outcome is not Outcome.DONE:
        raise _refused(result)
    return {"order": await _order(ctx), "message": to_plain(texts.ADMIN_ORDER_INVOICE_MARKED)}


@admin_route("DELETE", f"/admin/orders/{ID}/invoice-manual")
async def invoice_unmark(ctx: Ctx) -> dict[str, Any]:
    _need_applications(ctx)
    result = await _orders(ctx).unmark_invoice(_path_id(ctx))
    if result.outcome is not Outcome.DONE:
        raise _refused(result)
    return {"order": await _order(ctx), "message": to_plain(texts.ADMIN_ORDER_INVOICE_UNMARKED)}


@admin_route("GET", f"/admin/orders/{ID}/invoice-caption")
async def invoice_caption(ctx: Ctx) -> dict[str, Any]:
    """Подпись, которую бот сам добавит к счёту: номер заказа, сумма, срок. Подпись админа - ниже неё."""
    _need_applications(ctx)
    card = await _orders(ctx).card(_path_id(ctx))
    if card is None or not card.payable:
        raise _conflict("unavailable", texts.ADMIN_ORDER_UNAVAILABLE)
    return {"html": to_web_html(render.invoice_caption(card.order, card.left, ctx.settings.timezone))}


@admin_route("POST", f"/admin/orders/{ID}/remove")
async def remove_booking(ctx: Ctx) -> dict[str, Any]:
    """Снять бронь: места свободны, заказ отменён. Плательщику и коллеге - сообщение, как из бота."""
    _need_applications(ctx)
    service, order_id = _orders(ctx), _path_id(ctx)
    result = await service.remove_booking(order_id, ctx.now)
    if result.outcome is not Outcome.DONE:
        raise _refused(result)
    card = await service.card(order_id)
    for _, person in result.participants:
        text = (render.booking_removed(card.session, card.order, result.paid) if person.id == card.payer.id
                else render.booking_removed_colleague(card.session))
        _to_client(ctx, person, text, "о снятии брони")
    return {"order": views.order_card(card, ctx.settings.timezone, ctx.now),
            "message": to_plain(texts.ADMIN_ORDER_REMOVED)}


@admin_route("POST", f"/admin/orders/{ID}/credit")
async def credit_advance(ctx: Ctx) -> dict[str, Any]:
    """Деньги отменённого заказа - на аванс плательщика. Клиенту - «зачислено на аванс»."""
    _need_applications(ctx)
    service, order_id = _orders(ctx), _path_id(ctx)
    result = await service.credit_advance(order_id)
    if result.outcome is not Outcome.DONE:
        raise _refused(result)
    card = await service.card(order_id)
    _to_client(ctx, card.payer, render.advance_credited(result.amount), "об авансе")
    return {"order": views.order_card(card, ctx.settings.timezone, ctx.now),
            "message": to_plain(texts.ADMIN_ORDER_CREDITED.format(amount=format_rub(result.amount),
                                                                  balance=format_rub(result.balance)))}


@admin_route("POST", f"/admin/orders/{ID}/refund")
async def mark_refunded(ctx: Ctx) -> dict[str, Any]:
    _need_applications(ctx)
    result = await _orders(ctx).mark_refunded(_path_id(ctx))
    if result.outcome is not Outcome.DONE:
        raise _refused(result)
    return {"order": await _order(ctx),
            "message": to_plain(texts.ADMIN_ORDER_REFUNDED.format(amount=format_rub(result.amount)))}


@admin_route("POST", f"/admin/orders/{ID}/apply-advance")
async def apply_advance(ctx: Ctx) -> dict[str, Any]:
    """Зачесть аванс плательщика: сколько есть, но не больше остатка. Клиенту - «оплата получена»."""
    _need_applications(ctx)
    service, order_id = _orders(ctx), _path_id(ctx)
    result = await service.apply_advance(order_id, ctx.now)
    if result.outcome is not Outcome.DONE:
        raise _refused(result)
    card = await service.card(order_id)
    _to_client(ctx, card.payer, render.payment_received(card.session, card.order, result.paid, result.full,
                                                        from_advance=result.amount), "об оплате из аванса")
    return {"order": views.order_card(card, ctx.settings.timezone, ctx.now),
            "message": to_plain(texts.ADMIN_ORDER_APPLIED.format(amount=format_rub(result.amount)))}


@admin_route("POST", f"/admin/orders/{ID}/requisites", transaction=False)
async def send_requisites(ctx: Ctx) -> dict[str, Any]:
    """Файл реквизитов - админу в чат с ботом (как кнопка «Реквизиты» в боте): открыть и переслать в бухгалтерию."""
    _need_applications(ctx)
    async with ctx.session.begin():
        card = await _orders(ctx).card(_path_id(ctx))
        if card is None or not card.order.requisites_file_id:
            raise _conflict("no_requisites", texts.ADMIN_ORDER_NO_REQUISITES)
        file_id = card.order.requisites_file_id
        caption, markup = admin_orders_views.notify_requisites(card.order, card.payer)
    if ctx.bot is None:
        raise ApiError(503, "bot_unavailable", "Бот сейчас недоступен - попробуйте позже.")
    try:
        try:
            await ctx.bot.send_document(ctx.user.id, file_id, caption=caption, reply_markup=markup)
        except TelegramBadRequest:
            await ctx.bot.send_photo(ctx.user.id, file_id, caption=caption, reply_markup=markup)
    except TelegramForbiddenError as error:
        raise _conflict("no_chat", "Откройте чат с ботом и нажмите /start - файл придёт туда.") from error
    except TelegramBadRequest as error:
        raise _conflict("not_sent", "Telegram не отдал файл - откройте заказ в боте.") from error
    return {"message": "Файл реквизитов - в чате с ботом."}


# --- Счёт и сообщение клиенту --------------------------------------------------------------------------------------

async def _outgoing(ctx: Ctx, need_file: bool) -> tuple[str, tuple[str, bytes] | None, str]:
    """Форма «текст + файл + token»: текст - HTML из редактора → разметка Telegram; файл - документ или картинка
    (те же типы, что принимаются от клиентов: requisites_file_ok)."""
    try:
        form = await ctx.request.post()
    except web.HTTPRequestEntityTooLarge as error:
        raise ApiError(413, "file_too_large", "Файл слишком большой - до 20 МБ.") from error
    except ValueError as error:
        raise _bad("bad_request") from error
    raw_text = form.get("text")
    if raw_text is not None and (not isinstance(raw_text, str) or len(raw_text) > MAX_RICH_INPUT):
        raise _bad("bad_request")
    text = to_telegram_html(strip_nul(raw_text or ""))
    token = _token({"token": form.get("token")})
    upload = None
    field = form.get("file")
    if isinstance(field, web.FileField):
        content = field.file.read()
        name = strip_nul(_file_name(field.filename or ""))[:256]
        if not requisites_file_ok(name, len(content)):
            raise _bad("file_type", "Подойдёт документ (PDF, Word, Excel) или картинка до 20 МБ.")
        upload = (name, content)
    if need_file and upload is None:
        raise _bad("file_required", to_plain(texts.ADMIN_ORDER_INVOICE_FILE_ONLY))
    if not text and upload is None:
        raise _bad("empty", "Напишите сообщение или приложите файл.")
    return text, upload, token


def _check_length(text: str, with_file: bool) -> None:
    over = render.visible_length(text) - (CAPTION_LIMIT if with_file else TEXT_LIMIT)
    if over > 0:
        raise _bad("too_long", texts.ADMIN_ORDER_TOO_LONG.format(over=over))


async def _deliver(ctx: Ctx, telegram_id: int | None, text: str, upload: tuple[str, bytes] | None,
                   markup: InlineKeyboardMarkup | None = None) -> Delivery:
    """От имени бота - как deliver_to_client в боте: заблокировал бота - отмечаем это у клиента."""
    if telegram_id is None:
        return Delivery.NO_TELEGRAM
    if ctx.bot is None:
        raise ApiError(503, "bot_unavailable", "Бот сейчас недоступен - попробуйте позже.")
    try:
        if upload is None:
            await ctx.bot.send_message(telegram_id, text, reply_markup=markup)
        else:
            name, content = upload
            file = BufferedInputFile(content, filename=name)
            photo = PurePath(name).suffix.lower() in PHOTO_EXTENSIONS and len(content) <= MAX_IMAGE
            if photo:
                await ctx.bot.send_photo(telegram_id, file, caption=text or None, reply_markup=markup)
            else:
                await ctx.bot.send_document(telegram_id, file, caption=text or None, reply_markup=markup)
    except TelegramForbiddenError:
        async with ctx.session.begin():
            await ClientService(ctx.session).mark_bot_blocked(telegram_id)
        return Delivery.BLOCKED
    except TelegramBadRequest:
        return Delivery.FAILED
    return Delivery.DELIVERED


def _report(delivery: Delivery, client: Client) -> dict[str, Any]:
    return {"delivery": delivery.value, "delivered": delivery is Delivery.DELIVERED,
            "message": to_plain(admin_orders_views.delivery_report(delivery, client))}


@admin_route("POST", f"/admin/orders/{ID}/invoice", transaction=False)
async def send_invoice(ctx: Ctx) -> dict[str, Any]:
    """Счёт файлом: бот добавит подпись с номером заказа, суммой и сроком; дошёл - заказ в «Счёт отправлен»."""
    _need_applications(ctx)
    order_id = _path_id(ctx)
    text, upload, token = await _outgoing(ctx, need_file=True)
    async with ctx.session.begin():
        card = await _orders(ctx).card(order_id)
        if card is None or not card.payable:
            raise _conflict("unavailable", texts.ADMIN_ORDER_UNAVAILABLE)
        caption = render.invoice_caption(card.order, card.left, ctx.settings.timezone)
        payer, number = card.payer, card.order.number
    caption += f"\n\n{text}" if text else ""
    _check_length(caption, with_file=True)
    if not send_once.claim(f"web:{token}"):
        raise _conflict("expired", texts.ADMIN_ORDER_EXPIRED)
    markup = keyboards.app_link(ctx.settings, texts.BTN_APP_RECORD, f"/my?order={order_id}")
    delivery = await _deliver(ctx, payer.telegram_id, caption, upload, markup)
    if delivery is Delivery.DELIVERED:
        async with ctx.session.begin():
            await _orders(ctx).mark_invoice_sent(order_id, ctx.now)
    log_admin_action(ctx.user.id, LOG_ORDER, "№%s: сообщение клиенту (invoice) - %s", number, delivery.value)
    async with ctx.session.begin():
        data = await _order(ctx, order_id)
    return {**_report(delivery, payer), "order": data}


@admin_route("POST", f"/admin/orders/{ID}/message", transaction=False)
async def message_payer(ctx: Ctx) -> dict[str, Any]:
    _need_applications(ctx)
    order_id = _path_id(ctx)
    text, upload, token = await _outgoing(ctx, need_file=False)
    _check_length(text, with_file=upload is not None)
    async with ctx.session.begin():
        card = await _orders(ctx).card(order_id)
        if card is None:
            raise _not_found(texts.ADMIN_ORDER_UNAVAILABLE)
        payer, number = card.payer, card.order.number
    if not send_once.claim(f"web:{token}"):
        raise _conflict("expired", texts.ADMIN_ORDER_EXPIRED)
    delivery = await _deliver(ctx, payer.telegram_id, text, upload)
    log_admin_action(ctx.user.id, LOG_ORDER, "№%s: сообщение клиенту (message) - %s", number, delivery.value)
    return _report(delivery, payer)


# --- Клиенты -------------------------------------------------------------------------------------------------------

@admin_route("GET", "/admin/clients")
async def search_clients(ctx: Ctx) -> dict[str, Any]:
    """Поиск: часть ФИО от 2 букв или последние цифры телефона от 4 - как в боте, не больше 100 найденных."""
    _need_applications(ctx)
    result = await _clients(ctx).search(strip_nul(ctx.request.query.get("q", ""))[:200])
    if result.query.kind is QueryKind.TOO_SHORT:
        raise _bad("query_short", texts.ADMIN_CLIENT_QUERY_SHORT)
    return {"items": [views.client_row(client) for client in result.clients], "more": result.more}


@admin_route("GET", "/admin/clients/list")
async def client_list(ctx: Ctx) -> dict[str, Any]:
    """Все клиенты по 15 на странице: фильтр, сортировка (новые, старые, по ФИО) и поиск - для выбора любого клиента."""
    _need_applications(ctx)
    query = ctx.request.query
    try:
        client_filter = ClientFilter(query.get("filter", ClientFilter.ALL.value))
        sort = ClientSort(query.get("sort", ClientSort.NEW.value))
        page = int(query.get("page", "1"))
    except ValueError as error:
        raise _bad("bad_request") from error
    result = await _clients(ctx).client_list(strip_nul(query.get("q", ""))[:200], client_filter, sort,
                                             min(max(page, 1), 100_000))
    if result.query is not None and result.query.kind is QueryKind.TOO_SHORT:
        raise _bad("query_short", texts.ADMIN_CLIENT_QUERY_SHORT)
    tz = ctx.settings.timezone
    return {
        "items": [{**views.client_row(client), "since": views.local_date(client.created_at, tz),
                   "blocked": client.is_bot_blocked, "profile_done": client.profile_status is ProfileStatus.COMPLETED,
                   "active": result.active.get(client.id, 0)} for client in result.clients],
        "total": result.total, "page": result.page, "pages": result.pages, "per_page": CLIENTS_PAGE,
    }


async def _client(ctx: Ctx, client_id: int | None = None) -> dict[str, Any]:
    card = await _clients(ctx).card(client_id or _path_id(ctx))
    if card is None:
        raise _not_found(texts.ADMIN_CLIENT_UNAVAILABLE)
    return views.client_card(card, ctx.settings.timezone, ctx.today)


@admin_route("GET", f"/admin/clients/{ID}")
async def client_card(ctx: Ctx) -> dict[str, Any]:
    _need_applications(ctx)
    return await _client(ctx)


@admin_route("POST", f"/admin/clients/{ID}/advance")
async def adjust_advance(ctx: Ctx) -> dict[str, Any]:
    """Правка аванса: + начислить или - списать, с причиной. Та же проверка, что у «+5000 причина» в боте."""
    _need_applications(ctx)
    data = await _body(ctx)
    amount = _kopecks(data, "amount", signed=True)
    reason = _text(data, "reason", 1000) or ""
    token = _token(data)
    change = parse_advance(f"{'+' if amount > 0 else '-'}{abs(amount) // 100} {reason}")
    if isinstance(change, AdvanceInputError):
        message = {AdvanceInputError.NO_REASON: "Добавьте причину - её увидят в журнале.",
                   AdvanceInputError.TOO_LONG: texts.ADMIN_CLIENT_ADVANCE_TOO_LONG.format(limit=ADVANCE_REASON_LIMIT),
                   }.get(change, texts.ADMIN_CLIENT_ADVANCE_INVALID)
        raise _bad(f"advance_{change.value}", message)
    if not send_once.claim(f"advance:web:{token}"):
        raise _conflict("expired", texts.ADMIN_ORDER_EXPIRED)
    result = await _clients(ctx).adjust_advance(_path_id(ctx), change.amount, change.reason)
    if result.outcome is AdvanceOutcome.NEGATIVE:
        raise _bad("advance_negative", texts.ADMIN_CLIENT_ADVANCE_NEGATIVE.format(balance=format_rub(result.balance)))
    if result.outcome is AdvanceOutcome.NOT_FOUND:
        raise _not_found(texts.ADMIN_CLIENT_UNAVAILABLE)
    return {"client": await _client(ctx),
            "message": to_plain(texts.ADMIN_CLIENT_ADVANCE_SAVED.format(balance=format_rub(result.balance)))}


@admin_route("POST", f"/admin/clients/{ID}/message", transaction=False)
async def message_client(ctx: Ctx) -> dict[str, Any]:
    _need_applications(ctx)
    client_id = _path_id(ctx)
    text, upload, token = await _outgoing(ctx, need_file=False)
    _check_length(text, with_file=upload is not None)
    async with ctx.session.begin():
        card = await _clients(ctx).card(client_id)
        if card is None:
            raise _not_found(texts.ADMIN_CLIENT_UNAVAILABLE)
        client = card.client
    if not send_once.claim(f"web:{token}"):
        raise _conflict("expired", texts.ADMIN_ORDER_EXPIRED)
    delivery = await _deliver(ctx, client.telegram_id, text, upload)
    log_admin_action(ctx.user.id, LOG_CLIENTS, "клиент #%s: сообщение - %s", client.id, delivery.value)
    return _report(delivery, client)


@admin_route("POST", "/admin/export", transaction=False)
async def export_clients(ctx: Ctx) -> dict[str, Any]:
    """Все клиенты одним CSV - файлом в чат админа с ботом (в браузер файл с персональными данными не попадает)."""
    tz = ZoneInfo(ctx.settings.timezone)
    async with ctx.session.begin():
        export = await ClientExportService(ctx.session, ctx.user.id).build(ctx.now, tz)
    if ctx.bot is None:
        raise ApiError(503, "bot_unavailable", "Бот сейчас недоступен - попробуйте позже.")
    caption = texts.ADMIN_EXPORT_CAPTION.format(count=export.count, moment=f"{ctx.now.astimezone(tz):%d.%m.%Y %H:%M}")
    try:
        await ctx.bot.send_document(ctx.user.id, BufferedInputFile(export.content, filename=export.filename),
                                    caption=caption)
    except TelegramForbiddenError as error:
        raise _conflict("no_chat", "Откройте чат с ботом и нажмите /start - файл придёт туда.") from error
    except TelegramBadRequest as error:
        raise _conflict("not_sent", "Telegram не принял файл - попробуйте ещё раз.") from error
    return {"count": export.count, "message": f"Файл с клиентами ({export.count}) - в чате с ботом."}
