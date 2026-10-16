"""Админка: вход кнопкой «⚙️ Админка» в главном меню (видна только owner и ADMIN_IDS), дальше — только inline-кнопки.

Фильтр IsAdmin стоит на весь роутер (message и callback_query): чужие нажатия и текст уходят в fallback.
Один общий редактор для курсов, проведений и справочников (services/admin_registry.py): список → карточка →
правка поля / архив; «➕ Добавить» — мастер по полям с предпросмотром. Ответ сообщением — новым экраном, у вопроса
убираем кнопки; ответ кнопкой — в том же сообщении. Курсы и проведения списком кнопок не показываются: карточки
как в афише листаются стрелками ◀️ n/N ▶️ (курсы — все подряд, проведения — внутри выбранного месяца). Так же,
карточками со стрелками, выбирают курс на первом шаге мастера «Новое проведение».

Три раздела (решение 16): «Курсы» — библиотека, афишу не меняет; «Проведения» — это и есть афиша (с датами или
без дат), убрать из неё — отправить в архив; «Архив» — убранные курсы и проведения и прошедшие мероприятия,
только там есть «Удалить навсегда».

Состояние мастера в FSM: kind, mode (add/edit), ask (какие поля спрашивать), step, values; при правке — item_id,
field. Тарифы проведения — вложенный мини-мастер: категории кнопками → для каждой подпись → цена.
"""

from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import get_settings
from stubbot.db.models import Client
from stubbot.services.admin_courses import AdminCourseService
from stubbot.services.admin_dictionaries import (
    BUTTON_KINDS,
    AdminEntityService,
    DeleteResult,
    DictField,
    DictionarySpec,
    DictKind,
    FieldKind,
    SaveResult,
    parse_field,
)
from stubbot.services.admin_log import log_admin_action
from stubbot.services.admin_registry import ADMIN_SPECS, admin_service
from stubbot.services.admin_sessions import (
    COPY_FIELDS,
    MAX_PRICE_LABEL,
    PRICE_KINDS,
    AdminSessionService,
    SessionScope,
)
from stubbot.services.client_export import ClientExportService
from stubbot.services.schedule import SessionCard
from stubbot.tg import admin_views, catalog_flow, render, texts
from stubbot.tg.callbacks import AdminAction, AdminCb, DictAction, DictCb
from stubbot.tg.filters import IsAdmin
from stubbot.tg.flows import with_notice
from stubbot.tg.formatting import safe
from stubbot.tg.screen import DEFAULT_COVER, Photo, delete_quietly, send_screen, show_screen, strip_keyboard
from stubbot.tg.states import AdminDict
from stubbot.utils.dates import local_today
from stubbot.utils.documents import MAX_FILE_SIZE, SUPPORTED_EXTENSIONS, file_to_html
from stubbot.utils.schedule_input import parse_rubles

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

PROMPT_KEY = "admin_prompt_id"

_SAVE_ERRORS = {
    SaveResult.DUPLICATE: texts.ADMIN_DUPLICATE,
    SaveResult.MISSING_REQUIRED: texts.ADMIN_REQUIRED,
    SaveResult.NOT_FOUND: texts.ADMIN_NOT_FOUND,
    SaveResult.BAD_VALUE: texts.UNKNOWN_CALLBACK,
}


def _service(session: AsyncSession, kind: DictKind, client: Client) -> AdminEntityService:
    # Telegram ID админа — для журнала действий (logs/admin.log).
    return admin_service(session, kind, client.telegram_id)


def _courses(session: AsyncSession, client: Client) -> AdminCourseService:
    """Сервис курсов — когда нужны его особые действия (предпросмотр, архив, удаление)."""
    return AdminCourseService(session, client.telegram_id, local_today(get_settings().timezone))


def _sessions(session: AsyncSession, client: Client) -> AdminSessionService:
    """Сервис проведений — когда нужны его особые действия (статус, копия, предпросмотр, разделы)."""
    return AdminSessionService(session, client.telegram_id, local_today(get_settings().timezone))


def _scope(value: str) -> SessionScope:
    """Раздел из кнопки: афиша, архив или прошедшие. Непонятное значение (подделанная кнопка) — афиша."""
    try:
        return SessionScope(value)
    except ValueError:
        return SessionScope.ACTIVE


def _local_date(moment: datetime) -> str:
    return f"{moment.astimezone(ZoneInfo(get_settings().timezone)):%d.%m.%Y}"


async def _screen(message: Message, text: str, markup: InlineKeyboardMarkup, in_place: bool,
                  photo: Photo = None) -> Message:
    return await (show_screen if in_place else send_screen)(message, text, markup, photo)


# --- Меню ------------------------------------------------------------------------------------------------------

@router.message(F.text == texts.BTN_ADMIN)
async def open_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    log_admin_action(message.from_user.id, "админка", "вход")
    text, markup = admin_views.menu()
    await message.answer(text, reply_markup=markup)


@router.callback_query(AdminCb.filter(F.action == AdminAction.MENU))
async def back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await show_screen(callback.message, *admin_views.menu())


@router.callback_query(AdminCb.filter(F.action == AdminAction.CLOSE))
async def close_admin(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await delete_quietly(callback.message)


@router.callback_query(AdminCb.filter(F.action == AdminAction.NOOP))
async def on_counter(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(AdminCb.filter(F.action == AdminAction.EXPORT))
async def export_clients(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client) -> None:
    """Все клиенты одним CSV-файлом. Файл остаётся в чате, меню присылаем заново под ним — оно снова внизу."""
    await callback.answer(texts.ADMIN_EXPORT_WAIT)
    await state.clear()
    tz = ZoneInfo(get_settings().timezone)
    now = datetime.now(UTC)
    export = await ClientExportService(session, client.telegram_id).build(now, tz)
    await delete_quietly(callback.message)
    await callback.message.answer_document(
        BufferedInputFile(export.content, filename=export.filename),
        caption=texts.ADMIN_EXPORT_CAPTION.format(count=export.count, moment=f"{now.astimezone(tz):%d.%m.%Y %H:%M}"),
    )
    await send_screen(callback.message, *admin_views.menu())


@router.callback_query(AdminCb.filter(F.action == AdminAction.ARCHIVE))
async def open_archive(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client) -> None:
    """Раздел «Архив»: убранные курсы и проведения, прошедшие мероприятия."""
    await callback.answer()
    await state.clear()
    await _show_archive(callback.message, session, client)


# --- Список, карточка, архив, удаление, быстрые действия -------------------------------------------------------

@router.callback_query(DictCb.filter(F.action == DictAction.LIST))
async def show_list(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                    client: Client) -> None:
    await callback.answer()
    await state.clear()
    await _show_list(callback.message, session, client, callback_data.kind, callback_data.page,
                     scope=_scope(callback_data.value))


@router.callback_query(DictCb.filter((F.action == DictAction.MONTH) & (F.kind == DictKind.SESSIONS)))
async def show_month(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                     client: Client) -> None:
    """Проведения месяца (page — ГГГГММ, 0 — без дат; value — раздел): открываем первое, дальше — стрелками."""
    await state.clear()
    scope = _scope(callback_data.value)
    items = await _sessions(session, client).month_items(callback_data.page, scope)
    if not items:
        # Проведения месяца перенесли, убрали или кнопка подделана — показываем актуальные месяцы.
        await callback.answer(texts.ADMIN_MONTH_EMPTY)
        await _show_list(callback.message, session, client, DictKind.SESSIONS, 0, scope=scope)
        return
    await callback.answer()
    await _show_card(callback.message, session, client, DictKind.SESSIONS, items[0].id, 0)


@router.callback_query(DictCb.filter(F.action == DictAction.VIEW))
async def show_item(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                    client: Client) -> None:
    """Карточка записи. Для проведения сюда ведёт и «✏️ Править» под карточкой в афише."""
    await state.clear()
    if not await _show_card(callback.message, session, client, callback_data.kind, callback_data.item_id,
                            callback_data.page):
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    await callback.answer()


@router.callback_query(DictCb.filter(F.action.in_({DictAction.ARCHIVE, DictAction.RESTORE})))
async def toggle_archive(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                         client: Client) -> None:
    """«В архив» / «Вернуть из архива». Курс и проведение при этом переезжают в другой раздел — показываем
    соседнюю карточку прежнего; запись справочника остаётся на месте с пометкой."""
    await state.clear()
    active = callback_data.action is DictAction.RESTORE
    if callback_data.kind is DictKind.COURSES:
        await _move_course(callback, session, client, callback_data.item_id, active)
        return
    if callback_data.kind is DictKind.SESSIONS:
        await _move_session(callback, session, client, callback_data.item_id, active)
        return
    if await _service(session, callback_data.kind, client).set_active(callback_data.item_id, active) is None:
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    await callback.answer()
    await _show_card(callback.message, session, client, callback_data.kind, callback_data.item_id,
                     callback_data.page,
                     notice=texts.ADMIN_RESTORED_NOTICE if active else texts.ADMIN_ARCHIVED_NOTICE)


async def _move_course(callback: CallbackQuery, session: AsyncSession, client: Client, item_id: int,
                       active: bool) -> None:
    """Курс в архив или обратно. В архив — только если у курса нет проведений в афише: из раздела «Курсы»
    афиша не меняется, сначала в архив уходят сами проведения."""
    service = _courses(session, client)
    item = await service.item(item_id)
    if item is None:
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    if service.is_active(item) == active:  # кнопка от старой карточки: курс уже там, куда просят
        await callback.answer()
        await _show_card(callback.message, session, client, DictKind.COURSES, item_id, 0)
        return
    if not active and (count := await service.sessions_in_afisha(item_id)):
        await callback.answer(texts.ADMIN_COURSE_HAS_SESSIONS.format(count=count), show_alert=True)
        return
    siblings = await service.siblings(item)  # раздел, из которого курс уходит
    await service.set_active(item_id, active)
    await callback.answer()
    await _show_neighbour(callback.message, session, client, DictKind.COURSES, siblings, item_id,
                          texts.ADMIN_COURSE_RESTORED_NOTICE if active else texts.ADMIN_COURSE_ARCHIVED_NOTICE,
                          SessionScope.ARCHIVED if active else SessionScope.ACTIVE)


async def _move_session(callback: CallbackQuery, session: AsyncSession, client: Client, item_id: int,
                        active: bool) -> None:
    """Проведение в архив (убрать из афиши) или из архива обратно."""
    service = _sessions(session, client)
    item = await service.item(item_id)
    if item is None:
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    scope = service.scope(item)
    if scope is not (SessionScope.ARCHIVED if active else SessionScope.ACTIVE):
        await callback.answer()  # кнопка от старой карточки: проведение уже в другом разделе
        await _show_card(callback.message, session, client, DictKind.SESSIONS, item_id, 0)
        return
    if active and item.program.archived_at is not None:
        await callback.answer(texts.ADMIN_RESTORE_COURSE_FIRST, show_alert=True)
        return
    siblings = await service.siblings(item)  # месяц раздела, из которого проведение уходит
    await service.set_active(item_id, active)
    await callback.answer()
    if not active:
        notice = texts.ADMIN_SESSION_ARCHIVED_NOTICE
    else:
        notice = (texts.ADMIN_SESSION_RESTORED_PAST_NOTICE if service.is_past(item)
                  else texts.ADMIN_SESSION_RESTORED_NOTICE)
    await _show_neighbour(callback.message, session, client, DictKind.SESSIONS, siblings, item_id, notice, scope)


@router.callback_query(DictCb.filter((F.action == DictAction.DELETE)
                                     & F.kind.in_({DictKind.COURSES, DictKind.SESSIONS})))
async def ask_delete(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                     client: Client) -> None:
    """«🗑 Удалить навсегда» под карточкой в архиве: сначала вопрос. Если удалить нельзя — сразу говорим почему."""
    await state.clear()
    kind, item_id = callback_data.kind, callback_data.item_id
    if kind is DictKind.COURSES:
        courses = _courses(session, client)
        result = await courses.can_delete(item_id)
        if result is DeleteResult.OK:
            count = await courses.sessions_total(item_id)
            text = texts.ADMIN_DELETE_COURSE_CONFIRM.format(
                title=texts.CAPTION_TITLE.format(title=safe((await courses.item(item_id)).title)),
                sessions=texts.ADMIN_DELETE_COURSE_SESSIONS.format(count=count) if count else "",
            )
    else:
        sessions = _sessions(session, client)
        result = await sessions.can_delete(item_id)
        if result is DeleteResult.OK:
            title = sessions.title(await sessions.item(item_id))
            text = texts.ADMIN_DELETE_SESSION_CONFIRM.format(title=f"🗓 <b>{safe(title)}</b>")
    if result is not DeleteResult.OK:
        await callback.answer(texts.ADMIN_DELETE_ERRORS[result.value], show_alert=True)
        return
    await callback.answer()
    await show_screen(callback.message, text, admin_views.delete_confirm_markup(kind, item_id))


@router.callback_query(DictCb.filter((F.action == DictAction.PURGE)
                                     & F.kind.in_({DictKind.COURSES, DictKind.SESSIONS})))
async def purge(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                client: Client) -> None:
    """«Да, удалить навсегда»: курс (вместе с его проведениями) или проведение — только из архива."""
    await state.clear()
    kind, item_id = callback_data.kind, callback_data.item_id
    service = _courses(session, client) if kind is DictKind.COURSES else _sessions(session, client)
    item = await service.item(item_id)
    # Раздел и соседей запоминаем до удаления: после него спросить будет не у кого.
    siblings = await service.siblings(item) if item is not None else []
    scope = service.scope(item) if item is not None and kind is DictKind.SESSIONS else SessionScope.ARCHIVED
    result = await service.delete(item_id)
    if result is not DeleteResult.OK:
        await callback.answer(texts.ADMIN_DELETE_ERRORS[result.value], show_alert=True)
        return
    await callback.answer(texts.ADMIN_DELETED_NOTICE)
    await _show_neighbour(callback.message, session, client, kind, siblings, item_id, texts.ADMIN_DELETED_NOTICE, scope)


@router.callback_query(DictCb.filter((F.action == DictAction.STATUS) & (F.kind == DictKind.SESSIONS)))
async def set_status(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                     client: Client) -> None:
    """Статус проведения в одно нажатие прямо в карточке. value — статус."""
    await state.clear()
    if await _sessions(session, client).set_status(callback_data.item_id, callback_data.value) is None:
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    await callback.answer()
    await _show_card(callback.message, session, client, DictKind.SESSIONS, callback_data.item_id,
                     callback_data.page, notice=texts.ADMIN_STATUS_SET)


@router.callback_query(DictCb.filter((F.action == DictAction.PROGRAM) & (F.kind == DictKind.COURSES)))
async def show_program(callback: CallbackQuery, callback_data: DictCb, session: AsyncSession, client: Client) -> None:
    program = await _courses(session, client).item(callback_data.item_id)
    if program is None or not program.program_html:
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    await callback.answer()
    pages = render.program_pages(program)
    page = min(max(callback_data.page, 0), len(pages) - 1)
    await show_screen(callback.message, pages[page], admin_views.course_program(program.id, page, len(pages)))


# --- Мастер новой записи, копия, правка поля: начало -----------------------------------------------------------

@router.callback_query(DictCb.filter(F.action == DictAction.ADD))
async def start_add(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                    client: Client) -> None:
    """Новая запись: мастер по всем полям сущности."""
    spec = ADMIN_SPECS[callback_data.kind]
    await callback.answer()
    await _start_wizard(callback.message, state, session, client, spec, {}, [f.name for f in spec.fields])


@router.callback_query(DictCb.filter((F.action == DictAction.COPY) & (F.kind == DictKind.SESSIONS)))
async def start_copy(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                     client: Client) -> None:
    """«📋 Скопировать»: всё из исходного проведения, спрашиваем только новые дни и время. Работает и из
    архива, и из прошедших — так повторяют прошлое мероприятие; копия сразу встаёт в афишу."""
    service = _sessions(session, client)
    item = await service.item(callback_data.item_id)
    if item is None:
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    if item.program.archived_at is not None:
        await callback.answer(texts.ADMIN_COPY_COURSE_ARCHIVED, show_alert=True)
        return
    values = await service.copy_values(item.id)
    await callback.answer()
    await _start_wizard(callback.message, state, session, client, service.spec, values, list(COPY_FIELDS), copy=True)


@router.callback_query(DictCb.filter(F.action == DictAction.EDIT))
async def start_edit(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                     client: Client) -> None:
    field = ADMIN_SPECS[callback_data.kind].field(callback_data.field)
    service = _service(session, callback_data.kind, client)
    item = await service.item(callback_data.item_id) if field is not None and field.editable else None
    if item is None or not _editable(service, item):
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.set_state(AdminDict.value)
    await state.update_data(kind=callback_data.kind.value, mode="edit", item_id=callback_data.item_id,
                            field=field.name, page=callback_data.page)
    await _ask_field(callback.message, state, session, client, in_place=True)


def _editable(service: AdminEntityService, item: Any) -> bool:
    """Курс в архиве и проведение из архива или прошедших не правятся: карточка там — «как было»."""
    if isinstance(service, AdminSessionService):
        return service.scope(item) is SessionScope.ACTIVE
    if isinstance(service, AdminCourseService):
        return service.is_active(item)
    return True


async def _start_wizard(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                        spec: DictionarySpec, values: dict[str, Any], ask: list[str], copy: bool = False) -> None:
    await state.clear()
    await state.set_state(AdminDict.value)
    await state.update_data(kind=spec.kind.value, mode="add", ask=ask, step=0, values=values, copy=copy)
    await _ask_field(message, state, session, client, in_place=True)


# --- Ответы кнопками -------------------------------------------------------------------------------------------

@router.callback_query(AdminDict.value, DictCb.filter(F.action == DictAction.PICK))
async def on_pick(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                  client: Client) -> None:
    """Вариант выбора, запись справочника (курс, площадка) или стандартная подпись тарифа."""
    data = await state.get_data()
    field = _matching_field(data, callback_data)
    if field is None:
        await callback.answer(texts.UNKNOWN_CALLBACK)  # кнопка от прошлого вопроса
        return
    if field.kind is FieldKind.PRICES:
        if data.get("price_stage") != "label" or callback_data.value != "std":
            await callback.answer(texts.UNKNOWN_CALLBACK)
            return
        await callback.answer()
        price_kind = data["price_kinds"][data["price_i"]]
        await _price_label_given(callback.message, state, session, client, texts.PRICE_KIND_LABELS[price_kind],
                                 in_place=True)
        return
    if field.kind is FieldKind.CHOICE and callback_data.value in field.choices:
        value: Any = callback_data.value
    elif field.kind in (FieldKind.REF, FieldKind.COURSE) and callback_data.value.isdigit():
        value = int(callback_data.value)
        options = await _service(session, callback_data.kind, client).options(field, data.get("selected", []))
        if value not in {option_id for option_id, _ in options}:
            await callback.answer(texts.UNKNOWN_CALLBACK)  # подделанная кнопка
            return
    else:
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    await _answer_result(callback, await _accept(callback.message, state, session, client, value, in_place=True))


@router.callback_query(AdminDict.value, DictCb.filter(F.action == DictAction.BROWSE))
async def on_browse(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                    client: Client) -> None:
    """◀️ ▶️ на шаге «Курс» мастера проведения: соседняя карточка курса в том же сообщении."""
    field = _matching_field(await state.get_data(), callback_data)
    if field is None or field.kind is not FieldKind.COURSE or not callback_data.value.isdigit():
        await callback.answer(texts.UNKNOWN_CALLBACK)  # кнопка от прошлого вопроса или подделанная
        return
    await callback.answer()
    await _ask_course(callback.message, state, session, client, int(callback_data.value), in_place=True)


@router.callback_query(AdminDict.value, DictCb.filter(F.action == DictAction.TOGGLE))
async def on_toggle(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                    client: Client) -> None:
    """Галочка в мультивыборе (направления, лекторы) или в категориях тарифов."""
    data = await state.get_data()
    field = _matching_field(data, callback_data)
    current = list(data.get("selected", []))
    spec = ADMIN_SPECS[callback_data.kind]
    service = _service(session, spec.kind, client)
    if field is not None and field.kind is FieldKind.MULTI and callback_data.value.isdigit():
        toggled: Any = int(callback_data.value)
        allowed = {option_id for option_id, _ in await service.options(field, current)}
    elif field is not None and field.kind is FieldKind.PRICES and not data.get("price_stage"):
        toggled, allowed = callback_data.value, set(PRICE_KINDS)
    else:
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    if toggled not in allowed:
        await callback.answer(texts.UNKNOWN_CALLBACK)  # не из списка вариантов — подделанная кнопка
        return
    await callback.answer()
    selected = sorted(set(current) ^ {toggled}, key=str)
    await state.update_data(selected=selected)
    options = await service.options(field, selected) if field.kind is FieldKind.MULTI else None
    # Меняем только клавиатуру. Сохранённое значение нужно ей, чтобы при правке не пропала кнопка «Очистить».
    saved = None
    if data["mode"] == "edit":
        saved = service.field_value(await service.item(data["item_id"]), field)
    _, markup = admin_views.field_prompt(spec, field, "", saved, callback_data.item_id, callback_data.page,
                                        options, selected)
    await callback.message.edit_reply_markup(reply_markup=markup)


@router.callback_query(AdminDict.value, DictCb.filter(F.action == DictAction.DONE))
async def on_done(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                  client: Client) -> None:
    data = await state.get_data()
    field = _matching_field(data, callback_data)
    if field is None or field.kind not in (FieldKind.MULTI, FieldKind.PRICES) or data.get("price_stage"):
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    selected = list(data.get("selected", []))
    if field.required and not selected:
        await callback.answer(texts.ADMIN_MULTI_EMPTY, show_alert=True)
        return
    if field.kind is FieldKind.PRICES and selected:
        # Категории выбраны — по каждой спросим подпись и цену (в каноническом порядке, как в афише).
        await callback.answer()
        kinds = [kind for kind in PRICE_KINDS if kind in selected]
        await state.update_data(price_kinds=kinds, price_i=0, price_stage="label", prices_acc=[])
        await _ask_price(callback.message, state, session, client, in_place=True)
        return
    value = selected or None
    await _answer_result(callback, await _accept(callback.message, state, session, client, value, in_place=True))


@router.callback_query(AdminDict.value, DictCb.filter(F.action.in_({DictAction.SKIP, DictAction.CLEAR})))
async def on_skip_or_clear(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                           client: Client) -> None:
    """«Пропустить» в мастере и «Очистить» при правке — пустое значение необязательного поля."""
    data = await state.get_data()
    field = _matching_field(data, callback_data)
    expected_mode = "add" if callback_data.action is DictAction.SKIP else "edit"
    if field is None or field.required or data.get("mode") != expected_mode or data.get("price_stage"):
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    await _answer_result(callback, await _accept(callback.message, state, session, client, None, in_place=True))


@router.callback_query(AdminDict.confirm, DictCb.filter(F.action == DictAction.SAVE))
async def on_save(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                  client: Client) -> None:
    data = await state.get_data()
    if data.get("kind") != callback_data.kind.value:
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    result, item = await _service(session, callback_data.kind, client).create(data["values"])
    if result is not SaveResult.OK:
        await callback.answer(_SAVE_ERRORS[result], show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await _show_card(callback.message, session, client, callback_data.kind, item.id, page=0,
                     notice=texts.ADMIN_SAVED)


@router.callback_query(DictCb.filter(F.action == DictAction.CANCEL))
async def on_cancel(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                    client: Client) -> None:
    """Отмена мастера — к списку, отмена правки — к карточке записи."""
    await callback.answer()
    await state.clear()
    if callback_data.item_id and await _show_card(callback.message, session, client, callback_data.kind,
                                                  callback_data.item_id, callback_data.page,
                                                  notice=texts.ADMIN_CANCELLED):
        return
    await _show_list(callback.message, session, client, callback_data.kind, callback_data.page,
                     notice=texts.ADMIN_CANCELLED)


# --- Ответы сообщением: текст, фото, файл ----------------------------------------------------------------------

@router.message(AdminDict.value, F.text)
async def on_text(message: Message, state: FSMContext, session: AsyncSession, client: Client) -> None:
    data = await state.get_data()
    field = _current_field(data)
    if field.kind is FieldKind.PRICES and data.get("price_stage"):
        await _price_text(message, state, session, client, data)
        return
    if field.kind in BUTTON_KINDS or field.kind in (FieldKind.PHOTO, FieldKind.PRICES):
        await message.answer(texts.ADMIN_FIELD_ERRORS[field.kind.value])
        return
    # Многострочные поля — с форматированием, которое админ сделал в Telegram (жирный, курсив, ссылки).
    rich = field.kind in (FieldKind.HTML, FieldKind.DOCUMENT)
    value = parse_field(field, message.html_text if rich else message.text, datetime.now(UTC),
                        ZoneInfo(get_settings().timezone))
    if value is None:
        await message.answer(_input_error(field) + texts.ADMIN_ERROR_HINT)
        return
    await _reply_result(message, await _accept(message, state, session, client, value, in_place=False))


@router.message(AdminDict.value, F.photo)
async def on_photo(message: Message, state: FSMContext, session: AsyncSession, client: Client) -> None:
    field = _current_field(await state.get_data())
    if field.kind is not FieldKind.PHOTO:
        await message.answer(_wrong_input(field))
        return
    file_id = message.photo[-1].file_id  # самый большой размер
    await _reply_result(message, await _accept(message, state, session, client, file_id, in_place=False))


@router.message(AdminDict.value, F.document)
async def on_document(message: Message, state: FSMContext, session: AsyncSession, client: Client) -> None:
    """Программа курса файлом .docx / .txt."""
    field = _current_field(await state.get_data())
    if field.kind is not FieldKind.DOCUMENT:
        await message.answer(_wrong_input(field))
        return
    document = message.document
    name = document.file_name or ""
    if not name.lower().endswith(SUPPORTED_EXTENSIONS) or (document.file_size or 0) > MAX_FILE_SIZE:
        await message.answer(texts.ADMIN_FILE_UNSUPPORTED + texts.ADMIN_ERROR_HINT)
        return
    buffer = BytesIO()
    await message.bot.download(document, destination=buffer)
    html = file_to_html(name, buffer.getvalue())
    if not html:
        await message.answer(texts.ADMIN_FILE_UNREADABLE + texts.ADMIN_ERROR_HINT)
        return
    value = parse_field(field, html)
    if value is None:
        await message.answer(_input_error(field) + texts.ADMIN_ERROR_HINT)
        return
    await _reply_result(message, await _accept(message, state, session, client, value, in_place=False))


@router.message(AdminDict.value)
async def value_wrong_input(message: Message, state: FSMContext) -> None:
    """Стикер, голосовое и прочее, что не подходит ни одному полю."""
    await message.answer(_wrong_input(_current_field(await state.get_data())))


@router.message(AdminDict.confirm)
async def confirm_expects_buttons(message: Message) -> None:
    await message.answer(texts.REG_USE_BUTTONS)


# --- Тарифы: подпись и цена каждой выбранной категории ---------------------------------------------------------

async def _price_text(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                      data: dict[str, Any]) -> None:
    if data["price_stage"] == "label":
        label = " ".join(message.text.split())
        if not 0 < len(label) <= MAX_PRICE_LABEL:
            await message.answer(texts.ADMIN_PRICE_LABEL_ERROR + texts.ADMIN_ERROR_HINT)
            return
        await _price_label_given(message, state, session, client, label, in_place=False)
        return
    amount = parse_rubles(message.text)
    if amount is None:
        await message.answer(texts.ADMIN_PRICE_ERROR + texts.ADMIN_ERROR_HINT)
        return
    kinds, i = data["price_kinds"], data["price_i"]
    prices = [*data["prices_acc"], {"kind": kinds[i], "label": data["price_label"], "amount": amount}]
    if i + 1 < len(kinds):
        await _strip_prompt(message, state)
        await state.update_data(prices_acc=prices, price_i=i + 1, price_stage="label")
        await _ask_price(message, state, session, client, in_place=False)
        return
    await state.update_data(prices_acc=[], price_stage=None)
    await _reply_result(message, await _accept(message, state, session, client, prices, in_place=False))


async def _price_label_given(message: Message, state: FSMContext, session: AsyncSession, client: Client, label: str,
                             in_place: bool) -> None:
    if not in_place:
        await _strip_prompt(message, state)
    await state.update_data(price_label=label, price_stage="amount")
    await _ask_price(message, state, session, client, in_place)


async def _ask_price(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                     in_place: bool) -> None:
    data = await state.get_data()
    spec = ADMIN_SPECS[DictKind(data["kind"])]
    header = await _header(spec, data, session, client)
    kinds, i = data["price_kinds"], data["price_i"]
    item_id, page = (data["item_id"], data.get("page", 0)) if data["mode"] == "edit" else (0, 0)
    if data["price_stage"] == "label":
        text, markup = admin_views.price_label_prompt(spec, header, i + 1, len(kinds), kinds[i], item_id, page)
    else:
        unit = "per_group" if kinds[i] == "group" else "per_person"
        text, markup = admin_views.price_amount_prompt(spec, header, i + 1, len(kinds), data["price_label"], unit,
                                                       item_id, page)
    prompt = await _screen(message, text, markup, in_place)
    await state.update_data({PROMPT_KEY: prompt.message_id})


# --- Движок мастера и правки -----------------------------------------------------------------------------------

def _current_field(data: dict[str, Any]) -> DictField:
    spec = ADMIN_SPECS[DictKind(data["kind"])]
    if data["mode"] == "add":
        return spec.field(data["ask"][data["step"]])
    return spec.field(data["field"])


def _matching_field(data: dict[str, Any], callback_data: DictCb) -> DictField | None:
    """Поле текущего вопроса, если кнопка относится именно к нему (а не к прошлому вопросу или другой записи)."""
    if data.get("kind") != callback_data.kind.value or "mode" not in data:
        return None
    if data["mode"] == "edit" and data.get("item_id") != callback_data.item_id:
        return None
    field = _current_field(data)
    return field if field.name == callback_data.field else None


async def _header(spec: DictionarySpec, data: dict[str, Any], session: AsyncSession, client: Client) -> str:
    if data["mode"] == "edit":
        service = _service(session, spec.kind, client)
        return texts.ADMIN_EDIT_HEADER.format(item=safe(service.title(await service.item(data["item_id"]))))
    if data.get("copy"):
        return texts.ADMIN_COPY_HEADER
    return texts.ADMIN_WIZARD_HEADER.format(title=texts.ADMIN_DICT_NEW[spec.kind.value], step=data["step"] + 1,
                                            steps=len(data["ask"]))


async def _ask_field(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                     in_place: bool) -> None:
    """Вопрос по текущему полю: шаг мастера или правка поля существующей записи."""
    data = await state.get_data()
    spec = ADMIN_SPECS[DictKind(data["kind"])]
    service = _service(session, spec.kind, client)
    field = _current_field(data)
    if field.kind is FieldKind.COURSE:
        await _ask_course(message, state, session, client, course_id=0, in_place=in_place)
        return
    header = await _header(spec, data, session, client)
    if data["mode"] == "add":
        current, item_id = None, 0
        selected = list(data["values"].get(field.name) or []) if field.kind is FieldKind.MULTI else []
    else:
        item = await service.item(data["item_id"])
        current, item_id = service.field_value(item, field), item.id
        if field.kind is FieldKind.MULTI:
            selected = list(current or [])
        elif field.kind is FieldKind.PRICES:
            selected = [price["kind"] for price in current or []]
        elif field.kind is FieldKind.REF and current is not None:
            selected = [current]  # текущая запись (даже архивная) остаётся среди вариантов
        else:
            selected = []
    options = None
    if field.kind is FieldKind.MULTI:
        options = await service.options(field, selected)
    elif field.kind is FieldKind.REF:
        options = await service.options(field, [current] if current else [])
    text, markup = admin_views.field_prompt(spec, field, header, current, item_id, data.get("page", 0),
                                            options, selected)
    prompt = await _screen(message, text, markup, in_place)
    await state.update_data({"selected": selected, PROMPT_KEY: prompt.message_id})


async def _ask_course(message: Message, state: FSMContext, session: AsyncSession, client: Client, course_id: int,
                      in_place: bool) -> None:
    """Шаг «Курс» мастера проведения: карточка курса из библиотеки, как в разделе «Курсы», со стрелками
    ◀️ n/N ▶️ и «✅ Выбрать этот курс». course_id — какой курс показать; 0 или курса уже нет в библиотеке — первый."""
    data = await state.get_data()
    spec = ADMIN_SPECS[DictKind(data["kind"])]
    header = await _header(spec, data, session, client)
    courses = _courses(session, client)
    ids = [course.id for course in await courses.items()]
    if not ids:
        prompt = await _screen(message, *admin_views.course_pick_empty(spec, header), in_place)
    else:
        index = ids.index(course_id) if course_id in ids else 0
        course = await courses.item(ids[index])
        intro, markup = admin_views.course_pick(
            spec, _current_field(data), header, course.id, index, len(ids),
            prev_id=ids[index - 1] if index > 0 else 0,
            next_id=ids[index + 1] if index + 1 < len(ids) else 0,
        )
        prompt = await _screen(message, render.program_caption(course, intro), markup, in_place,
                               photo=course.cover_file_id or DEFAULT_COVER)
    await state.update_data({"selected": [], PROMPT_KEY: prompt.message_id})


async def _accept(message: Message, state: FSMContext, session: AsyncSession, client: Client, value: Any,
                  in_place: bool) -> SaveResult:
    """Значение текущего поля принято. Мастер — следующий шаг или предпросмотр; правка — сохранить и карточку.

    in_place=False — ответ пришёл сообщением: у вопроса убираем кнопки, следующий экран — новым сообщением.
    """
    data = await state.get_data()
    spec = ADMIN_SPECS[DictKind(data["kind"])]
    field = _current_field(data)
    if data["mode"] == "add":
        if not in_place:
            await _strip_prompt(message, state)
        values = {**data["values"], field.name: value}
        await state.update_data(values=values, step=_next_index(spec, data["ask"], data["step"] + 1, values))
        await _next_step(message, state, session, client, spec, in_place)
        return SaveResult.OK

    result = await _service(session, spec.kind, client).update_field(data["item_id"], field, value)
    if result is not SaveResult.OK:
        return result
    if not in_place:
        await _strip_prompt(message, state)
    await state.clear()
    if not await _show_card(message, session, client, spec.kind, data["item_id"], data.get("page", 0),
                            notice=texts.ADMIN_SAVED, in_place=in_place):
        await _show_list(message, session, client, spec.kind, data.get("page", 0), in_place=in_place)
    return SaveResult.OK


def _next_index(spec: DictionarySpec, ask: list[str], step: int, values: dict[str, Any]) -> int:
    """Следующий шаг мастера, пропуская поля, которые не подходят к уже выбранному (площадка у онлайна и т. п.)."""
    while step < len(ask) and not spec.field(ask[step]).applies(values):
        step += 1
    return step


async def _next_step(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                     spec: DictionarySpec, in_place: bool) -> None:
    """Следующее поле мастера или предпросмотр, если поля кончились."""
    data = await state.get_data()
    if data["step"] < len(data["ask"]):
        await _ask_field(message, state, session, client, in_place)
        return
    await state.set_state(AdminDict.confirm)
    values = data["values"]
    if spec.kind is DictKind.COURSES:
        # Карточка курса для библиотеки: обложка + подпись; кнопка «✅ Сохранить» (в афишу курс сам не попадает).
        program = await _courses(session, client).preview(values)
        await _screen(message, render.program_caption(program), admin_views.publish_markup(spec.kind), in_place,
                      photo=program.cover_file_id or DEFAULT_COVER)
        return
    if spec.kind is DictKind.SESSIONS:
        card = await _sessions(session, client).preview(values)
        await _screen(message, _session_caption(card), admin_views.publish_markup(spec.kind), in_place,
                      photo=_session_photo(card))
        return
    await _screen(message, *admin_views.preview(spec, values), in_place)


# --- Вспомогательное -------------------------------------------------------------------------------------------

async def _show_archive(message: Message, session: AsyncSession, client: Client, notice: str | None = None,
                        in_place: bool = True) -> None:
    """Экран «Архив» с числом записей в каждом из трёх разделов."""
    sessions = _sessions(session, client)
    text, markup = admin_views.archive_menu(
        await _courses(session, client).archived_count(),
        await sessions.count(SessionScope.ARCHIVED),
        await sessions.count(SessionScope.PAST),
    )
    await _screen(message, with_notice(notice, text), markup, in_place)


async def _show_list(message: Message, session: AsyncSession, client: Client, kind: DictKind, page: int,
                     notice: str | None = None, in_place: bool = True,
                     scope: SessionScope = SessionScope.ACTIVE) -> None:
    """Список записей. Проведения — не списком, а по месяцам своего раздела (scope: афиша, архив, прошедшие).
    Курсы — тоже не списком: сразу карточка первого курса с листанием (scope не «афиша» — курсы из архива)."""
    if kind is DictKind.SESSIONS:
        text, markup = admin_views.session_months(await _sessions(session, client).months(scope), scope, page)
        # После мастера или карточки с фото этот текстовый экран заменит фото: screen.py удалит и пришлёт заново.
        await _screen(message, with_notice(notice, text), markup, in_place)
        return
    if kind is DictKind.COURSES:
        courses = _courses(session, client)
        archived = scope is not SessionScope.ACTIVE
        items = await courses.archived_items() if archived else await courses.items()
        if items:
            await _show_card(message, session, client, kind, items[0].id, 0, notice=notice, in_place=in_place)
            return
        if archived:
            text, markup = admin_views.archive_section_empty(texts.ADMIN_ARCHIVE_COURSES_TITLE)
            await _screen(message, with_notice(notice, text), markup, in_place)
            return
    service = _service(session, kind, client)
    entries = [(item.id, service.title(item), service.is_active(item)) for item in await service.items()]
    text, markup = admin_views.dict_list(service.spec, entries, page)
    await _screen(message, with_notice(notice, text), markup, in_place)


async def _show_neighbour(message: Message, session: AsyncSession, client: Client, kind: DictKind,
                          siblings: list[int], gone_id: int, notice: str, scope: SessionScope) -> None:
    """Запись ушла из раздела (в архив, из архива, удалена): показываем соседнюю карточку того же раздела,
    а если карточек там не осталось — экран раздела. siblings — карточки, среди которых она листалась."""
    place = siblings.index(gone_id) if gone_id in siblings else 0
    rest = [item_id for item_id in siblings if item_id != gone_id]
    if rest and await _show_card(message, session, client, kind, rest[min(place, len(rest) - 1)], 0, notice=notice):
        return
    if scope is SessionScope.ACTIVE:
        await _show_list(message, session, client, kind, 0, notice=notice)
    elif kind is DictKind.SESSIONS and await _sessions(session, client).count(scope):
        await _show_list(message, session, client, kind, 0, notice=notice, scope=scope)  # остались другие месяцы
    else:
        await _show_archive(message, session, client, notice=notice)


async def _show_card(message: Message, session: AsyncSession, client: Client, kind: DictKind, item_id: int, page: int,
                     notice: str | None = None, in_place: bool = True) -> bool:
    """Карточка записи; курс и проведение — фото с подписью, как в афише. False — записи нет."""
    if kind is DictKind.SESSIONS:
        return await _show_session_card(message, session, client, item_id, notice, in_place)
    if kind is DictKind.COURSES:
        return await _show_course_card(message, session, client, item_id, notice, in_place)
    service = _service(session, kind, client)
    item = await service.item(item_id)
    if item is None:
        return False
    values = {f.name: service.field_value(item, f) for f in service.spec.fields}
    text, markup = admin_views.card(service.spec, item.id, values, service.is_active(item), page)
    await _screen(message, with_notice(notice, text), markup, in_place)
    return True


async def _show_course_card(message: Message, session: AsyncSession, client: Client, item_id: int,
                            notice: str | None, in_place: bool) -> bool:
    """Карточка курса со стрелками ◀️ n/N ▶️ по курсам своего раздела: «Курсы» (с правкой) или архив
    («как было»: вернуть или удалить). False — курса нет."""
    service = _courses(session, client)
    item = await service.item(item_id)
    if item is None:
        return False
    archived = not service.is_active(item)
    siblings = await service.siblings(item)
    index = siblings.index(item.id) if item.id in siblings else 0
    markup = admin_views.course_card_markup(
        service.spec, item.id, bool(item.program_html),
        index=index, total=max(len(siblings), 1),
        prev_id=siblings[index - 1] if index > 0 else 0,
        next_id=siblings[index + 1] if index + 1 < len(siblings) else 0,
        archived=archived,
    )
    hint = texts.ADMIN_ARCHIVED_SINCE_HINT.format(date=_local_date(item.archived_at)) if archived else None
    caption = _fit_notice(hint, render.program_caption(item))
    await _screen(message, _fit_notice(notice, caption), markup, in_place, photo=item.cover_file_id or DEFAULT_COVER)
    return True


async def _show_session_card(message: Message, session: AsyncSession, client: Client, item_id: int,
                             notice: str | None, in_place: bool) -> bool:
    """Карточка проведения как в афише, со стрелками ◀️ n/N ▶️ по проведениям того же раздела и месяца
    (или по всем проведениям без дат). False — проведения нет."""
    service = _sessions(session, client)
    item = await service.item(item_id)
    if item is None:
        return False
    scope = service.scope(item)
    siblings = await service.siblings(item)
    index = siblings.index(item.id) if item.id in siblings else 0
    markup = admin_views.session_card_markup(
        service.spec, item.id, item.status.value, scope,
        index=index, total=max(len(siblings), 1),
        prev_id=siblings[index - 1] if index > 0 else 0,
        next_id=siblings[index + 1] if index + 1 < len(siblings) else 0,
    )
    card = SessionCard(session=item, seats_left=None)
    caption = _session_caption(card)
    # Пометки над подписью — то, чего по самой карточке не видно.
    hints = []
    if scope is SessionScope.ACTIVE and item.program.archived_at is not None:
        hints.append(texts.ADMIN_COURSE_ARCHIVED_HINT)
    if item.archived_at is not None:
        hints.append(texts.ADMIN_ARCHIVED_SINCE_HINT.format(date=_local_date(item.archived_at)))
    if service.is_past(item):
        hints.append(texts.ADMIN_SESSION_PAST_HINT)
    caption = _fit_notice("\n".join(hints) or None, caption)
    await _screen(message, _fit_notice(notice, caption), markup, in_place, photo=_session_photo(card))
    return True


def _session_caption(card: SessionCard) -> str:
    """Подпись проведения как в афише: тот же рендер, тот же режим счётчика мест."""
    settings = get_settings()
    return render.session_caption(card, catalog_flow.deadline_text(card, settings.timezone),
                                  show_seats=settings.applications_enabled)


def _session_photo(card: SessionCard) -> Photo:
    return card.session.cover_file_id or card.program.cover_file_id or DEFAULT_COVER


def _fit_notice(notice: str | None, caption: str) -> str:
    """Отметка («✅ Сохранено.») над подписью к фото — только если подпись не вылезет за лимит Telegram."""
    if notice and render.visible_length(with_notice(notice, caption)) <= render.CAPTION_LIMIT:
        return with_notice(notice, caption)
    return caption


async def _strip_prompt(message: Message, state: FSMContext) -> None:
    prompt_id = (await state.get_data()).get(PROMPT_KEY)
    if prompt_id:
        await strip_keyboard(message, prompt_id)


def _input_error(field: DictField) -> str:
    if field.kind is FieldKind.NUMBER:
        return texts.ADMIN_FIELD_ERRORS["number"].format(min=field.min_value, max=field.max_value)
    return texts.ADMIN_FIELD_ERRORS[field.kind.value].format(max=field.max_length)


def _wrong_input(field: DictField) -> str:
    """Пришло не то, что ждёт поле: кнопки, фото или текст."""
    if field.kind in BUTTON_KINDS or field.kind in (FieldKind.PHOTO, FieldKind.PRICES):
        return texts.ADMIN_FIELD_ERRORS[field.kind.value]
    return texts.ADMIN_TEXT_ONLY


async def _answer_result(callback: CallbackQuery, result: SaveResult) -> None:
    if result is SaveResult.OK:
        await callback.answer()
    else:
        await callback.answer(_SAVE_ERRORS[result], show_alert=True)


async def _reply_result(message: Message, result: SaveResult) -> None:
    if result is not SaveResult.OK:
        await message.answer(f"❗️ {_SAVE_ERRORS[result]}." + texts.ADMIN_ERROR_HINT)
