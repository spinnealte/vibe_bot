"""Админка: вход кнопкой «⚙️ Админка» в главном меню (видна только owner и ADMIN_IDS), дальше — только inline-кнопки.

Фильтр IsAdmin стоит на весь роутер (message и callback_query): чужие нажатия и текст уходят в fallback.
Один общий редактор для курсов, проведений и справочников (services/admin_registry.py): список → карточка →
правка поля / архив; «➕ Добавить» — мастер по полям с предпросмотром. Ответ сообщением — новым экраном, у вопроса
убираем кнопки; ответ кнопкой — в том же сообщении.

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
    DictField,
    DictionarySpec,
    DictKind,
    FieldKind,
    SaveResult,
    parse_field,
)
from stubbot.services.admin_registry import ADMIN_SPECS, admin_service
from stubbot.services.admin_sessions import COPY_FIELDS, MAX_PRICE_LABEL, PRICE_KINDS, AdminSessionService
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
    return admin_service(session, kind, client.id)


def _sessions(session: AsyncSession, client: Client) -> AdminSessionService:
    """Сервис проведений — когда нужны его особые действия (статус, копия, предпросмотр)."""
    return AdminSessionService(session, client.id, local_today(get_settings().timezone))


def _parent(value: str) -> int | None:
    """id курса из кнопки («родитель» списка проведений) или None."""
    return int(value) if value.isdigit() else None


async def _screen(message: Message, text: str, markup: InlineKeyboardMarkup, in_place: bool,
                  photo: Photo = None) -> Message:
    return await (show_screen if in_place else send_screen)(message, text, markup, photo)


# --- Меню ------------------------------------------------------------------------------------------------------

@router.message(F.text == texts.BTN_ADMIN)
async def open_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
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
    export = await ClientExportService(session, client.id).build(now, tz)
    await delete_quietly(callback.message)
    await callback.message.answer_document(
        BufferedInputFile(export.content, filename=export.filename),
        caption=texts.ADMIN_EXPORT_CAPTION.format(count=export.count, moment=f"{now.astimezone(tz):%d.%m.%Y %H:%M}"),
    )
    await send_screen(callback.message, *admin_views.menu())


# --- Список, карточка, архив, быстрые действия -----------------------------------------------------------------

@router.callback_query(DictCb.filter(F.action == DictAction.LIST))
async def show_list(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                    client: Client) -> None:
    await callback.answer()
    await state.clear()
    await _show_list(callback.message, session, client, callback_data.kind, callback_data.page,
                     parent_id=_parent(callback_data.value))


@router.callback_query(DictCb.filter(F.action == DictAction.VIEW))
async def show_item(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                    client: Client) -> None:
    """Карточка записи. Для курса сюда ведёт и «✏️ Править» под карточкой в афише."""
    await state.clear()
    if not await _show_card(callback.message, session, client, callback_data.kind, callback_data.item_id,
                            callback_data.page, parent_id=_parent(callback_data.value)):
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    await callback.answer()


@router.callback_query(DictCb.filter(F.action.in_({DictAction.ARCHIVE, DictAction.RESTORE})))
async def toggle_archive(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                         client: Client) -> None:
    """Архив курса и справочника; у проведения — «скрыть из афиши» / «показать»."""
    await state.clear()
    active = callback_data.action is DictAction.RESTORE
    if await _service(session, callback_data.kind, client).set_active(callback_data.item_id, active) is None:
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    await callback.answer()
    if callback_data.kind is DictKind.SESSIONS:
        notice = texts.ADMIN_SESSION_SHOWN if active else texts.ADMIN_SESSION_HIDDEN
    else:
        notice = texts.ADMIN_RESTORED_NOTICE if active else texts.ADMIN_ARCHIVED_NOTICE
    await _show_card(callback.message, session, client, callback_data.kind, callback_data.item_id,
                     callback_data.page, notice=notice, parent_id=_parent(callback_data.value))


@router.callback_query(DictCb.filter((F.action == DictAction.STATUS) & (F.kind == DictKind.SESSIONS)))
async def set_status(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                     client: Client) -> None:
    """Статус проведения в одно нажатие прямо в карточке. value — «статус~id курса-родителя»."""
    await state.clear()
    status, _, parent = callback_data.value.partition("~")
    if await _sessions(session, client).set_status(callback_data.item_id, status) is None:
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    await callback.answer()
    await _show_card(callback.message, session, client, DictKind.SESSIONS, callback_data.item_id,
                     callback_data.page, notice=texts.ADMIN_STATUS_SET, parent_id=_parent(parent))


@router.callback_query(DictCb.filter((F.action == DictAction.SHOW_TBD) & (F.kind == DictKind.COURSES)))
async def set_show_without_dates(callback: CallbackQuery, callback_data: DictCb, state: FSMContext,
                                 session: AsyncSession, client: Client) -> None:
    """Курс без проведений: показывать в афише как «даты уточняются» или нет (решение 14)."""
    await state.clear()
    show = callback_data.value == "1"
    if await AdminCourseService(session, client.id).set_show_without_dates(callback_data.item_id, show) is None:
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    await callback.answer()
    await _show_card(callback.message, session, client, DictKind.COURSES, callback_data.item_id,
                     callback_data.page, notice=texts.ADMIN_TBD_SHOWN if show else texts.ADMIN_TBD_HIDDEN)


@router.callback_query(DictCb.filter((F.action == DictAction.PROGRAM) & (F.kind == DictKind.COURSES)))
async def show_program(callback: CallbackQuery, callback_data: DictCb, session: AsyncSession, client: Client) -> None:
    program = await AdminCourseService(session, client.id).item(callback_data.item_id)
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
    """Новая запись. Новое проведение из карточки курса (value = id курса) — курс уже выбран, его не спрашиваем."""
    spec = ADMIN_SPECS[callback_data.kind]
    values: dict[str, Any] = {}
    ask = [f.name for f in spec.fields]
    parent_id = _parent(callback_data.value)
    if callback_data.kind is DictKind.SESSIONS and parent_id is not None:
        if await AdminCourseService(session, client.id).item(parent_id) is None:
            await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
            return
        values["program_id"] = parent_id
        ask.remove("program_id")
    await callback.answer()
    await _start_wizard(callback.message, state, session, client, spec, values, ask)


@router.callback_query(DictCb.filter((F.action == DictAction.COPY) & (F.kind == DictKind.SESSIONS)))
async def start_copy(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                     client: Client) -> None:
    """«📋 Скопировать»: всё из исходного проведения, спрашиваем только новые дни и время."""
    service = _sessions(session, client)
    values = await service.copy_values(callback_data.item_id)
    if values is None:
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    await callback.answer()
    await _start_wizard(callback.message, state, session, client, service.spec, values, list(COPY_FIELDS), copy=True)


@router.callback_query(DictCb.filter(F.action == DictAction.EDIT))
async def start_edit(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                     client: Client) -> None:
    field = ADMIN_SPECS[callback_data.kind].field(callback_data.field)
    if (field is None or not field.editable
            or await _service(session, callback_data.kind, client).item(callback_data.item_id) is None):
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.set_state(AdminDict.value)
    await state.update_data(kind=callback_data.kind.value, mode="edit", item_id=callback_data.item_id,
                            field=field.name, page=callback_data.page, parent=callback_data.value)
    await _ask_field(callback.message, state, session, client, in_place=True)


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
    elif field.kind is FieldKind.REF and callback_data.value.isdigit():
        value = int(callback_data.value)
        options = await _service(session, callback_data.kind, client).options(field, data.get("selected", []))
        if value not in {option_id for option_id, _ in options}:
            await callback.answer(texts.UNKNOWN_CALLBACK)  # подделанная кнопка
            return
    else:
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    await _answer_result(callback, await _accept(callback.message, state, session, client, value, in_place=True))


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
    parent_id = _parent((await state.get_data()).get("parent", ""))
    await callback.answer()
    await state.clear()
    if callback_data.item_id and await _show_card(callback.message, session, client, callback_data.kind,
                                                  callback_data.item_id, callback_data.page,
                                                  notice=texts.ADMIN_CANCELLED, parent_id=parent_id):
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
                            notice=texts.ADMIN_SAVED, in_place=in_place, parent_id=_parent(data.get("parent", ""))):
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
        # Курс — ровно как его увидят в афише: обложка + подпись; кнопка «🚀 Опубликовать».
        program = await AdminCourseService(session, client.id).preview(values)
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

async def _show_list(message: Message, session: AsyncSession, client: Client, kind: DictKind, page: int,
                     notice: str | None = None, in_place: bool = True, parent_id: int | None = None) -> None:
    service = _service(session, kind, client)
    heading = None
    if parent_id is not None:
        course = await AdminCourseService(session, client.id).item(parent_id)
        heading = f"{texts.ADMIN_DICT_TITLES[kind.value]}: {safe(course.title)}" if course else None
    entries = []
    for item in await service.items(parent_id):
        title = service.title(item)
        if kind is DictKind.SESSIONS and service.is_past(item):
            title = texts.ADMIN_PAST_MARK + title
        entries.append((item.id, title, service.is_active(item)))
    text, markup = admin_views.dict_list(service.spec, entries, page, parent_id, heading)
    await _screen(message, with_notice(notice, text), markup, in_place)


async def _show_card(message: Message, session: AsyncSession, client: Client, kind: DictKind, item_id: int, page: int, notice: str | None = None, in_place: bool = True,
                     parent_id: int | None = None) -> bool:
    """Карточка записи; курс и проведение — ровно как в афише (обложка + подпись). False — записи нет."""
    service = _service(session, kind, client)
    item = await service.item(item_id)
    if item is None:
        return False
    if kind is DictKind.COURSES:
        markup = admin_views.course_card_markup(service.spec, item.id, service.is_active(item),
                                                bool(item.program_html), item.show_without_dates, page)
        await _screen(message, _fit_notice(notice, render.program_caption(item)), markup, in_place,
                      photo=item.cover_file_id or DEFAULT_COVER)
        return True
    if kind is DictKind.SESSIONS:
        card = SessionCard(session=item, seats_left=None)
        caption = _session_caption(card)
        if item.program.archived_at is not None:
            caption = _fit_notice(texts.ADMIN_COURSE_ARCHIVED_HINT, caption)
        markup = admin_views.session_card_markup(service.spec, item.id, item.status.value, item.is_visible, page,
                                                 parent_id)
        await _screen(message, _fit_notice(notice, caption), markup, in_place, photo=_session_photo(card))
        return True
    values = {f.name: service.field_value(item, f) for f in service.spec.fields}
    text, markup = admin_views.card(service.spec, item.id, values, service.is_active(item), page)
    await _screen(message, with_notice(notice, text), markup, in_place)
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
