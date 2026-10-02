"""Админка: вход кнопкой «⚙️ Админка» в главном меню (видна только owner и ADMIN_IDS), дальше — только inline-кнопки.

Фильтр IsAdmin стоит на весь роутер (message и callback_query): чужие нажатия и текст уходят в fallback.
Один общий редактор для курсов и справочников (services/admin_registry.py): список → карточка → правка поля / архив;
«➕ Добавить» — мастер по полям с предпросмотром. Поля бывают текстовые, числовые, с выбором кнопками, фото и
«текст или файл». Ответ сообщением — новым экраном, у вопроса убираем кнопки; ответ кнопкой — в том же сообщении.
"""

from io import BytesIO
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

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
from stubbot.tg import admin_views, render, texts
from stubbot.tg.callbacks import AdminAction, AdminCb, DictAction, DictCb
from stubbot.tg.filters import IsAdmin
from stubbot.tg.flows import with_notice
from stubbot.tg.formatting import safe
from stubbot.tg.screen import DEFAULT_COVER, Photo, delete_quietly, send_screen, show_screen, strip_keyboard
from stubbot.tg.states import AdminDict
from stubbot.utils.documents import MAX_FILE_SIZE, SUPPORTED_EXTENSIONS, file_to_html

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


# --- Список, карточка, архив, программа курса ------------------------------------------------------------------

@router.callback_query(DictCb.filter(F.action == DictAction.LIST))
async def show_list(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                    client: Client) -> None:
    await callback.answer()
    await state.clear()
    await _show_list(callback.message, session, client, callback_data.kind, callback_data.page)


@router.callback_query(DictCb.filter(F.action == DictAction.VIEW))
async def show_item(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                    client: Client) -> None:
    """Карточка записи. Для курса сюда ведёт и «✏️ Править» под карточкой в афише."""
    await state.clear()
    if not await _show_card(callback.message, session, client, callback_data.kind, callback_data.item_id,
                            callback_data.page):
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    await callback.answer()


@router.callback_query(DictCb.filter(F.action.in_({DictAction.ARCHIVE, DictAction.RESTORE})))
async def toggle_archive(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                         client: Client) -> None:
    await state.clear()
    active = callback_data.action is DictAction.RESTORE
    if await _service(session, callback_data.kind, client).set_active(callback_data.item_id, active) is None:
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    await callback.answer()
    await _show_card(callback.message, session, client, callback_data.kind, callback_data.item_id, callback_data.page,
                     notice=texts.ADMIN_RESTORED_NOTICE if active else texts.ADMIN_ARCHIVED_NOTICE)


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


# --- Мастер новой записи и правка поля: начало -----------------------------------------------------------------

@router.callback_query(DictCb.filter(F.action == DictAction.ADD))
async def start_add(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                    client: Client) -> None:
    await callback.answer()
    await state.clear()
    await state.set_state(AdminDict.value)
    await state.update_data(kind=callback_data.kind.value, mode="add", step=0, values={})
    await _ask_field(callback.message, state, session, client, in_place=True)


@router.callback_query(DictCb.filter(F.action == DictAction.EDIT))
async def start_edit(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                     client: Client) -> None:
    field = ADMIN_SPECS[callback_data.kind].field(callback_data.field)
    if field is None or await _service(session, callback_data.kind, client).item(callback_data.item_id) is None:
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.set_state(AdminDict.value)
    await state.update_data(kind=callback_data.kind.value, mode="edit", item_id=callback_data.item_id,
                            field=field.name, page=callback_data.page)
    await _ask_field(callback.message, state, session, client, in_place=True)


# --- Ответы кнопками -------------------------------------------------------------------------------------------

@router.callback_query(AdminDict.value, DictCb.filter(F.action == DictAction.PICK))
async def on_pick(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                  client: Client) -> None:
    field = _matching_field(await state.get_data(), callback_data)
    if field is None or field.kind is not FieldKind.CHOICE or callback_data.value not in field.choices:
        await callback.answer(texts.UNKNOWN_CALLBACK)  # кнопка от прошлого вопроса
        return
    await _answer_result(callback, await _accept(callback.message, state, session, client, callback_data.value,
                                                 in_place=True))


@router.callback_query(AdminDict.value, DictCb.filter(F.action == DictAction.TOGGLE))
async def on_toggle(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                    client: Client) -> None:
    data = await state.get_data()
    field = _matching_field(data, callback_data)
    if field is None or field.kind is not FieldKind.MULTI or not callback_data.value.isdigit():
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    spec = ADMIN_SPECS[callback_data.kind]
    service = _service(session, spec.kind, client)
    current = list(data.get("selected", []))
    toggled = int(callback_data.value)
    if toggled not in {option_id for option_id, _ in await service.options(field, current)}:
        await callback.answer(texts.UNKNOWN_CALLBACK)  # id не из списка вариантов — подделанная кнопка
        return
    await callback.answer()
    selected = sorted(set(current) ^ {toggled})
    await state.update_data(selected=selected)
    options = await service.options(field, selected)
    _, markup = admin_views.field_prompt(spec, field, "", selected, callback_data.item_id, callback_data.page,
                                        options, selected)
    await callback.message.edit_reply_markup(reply_markup=markup)


@router.callback_query(AdminDict.value, DictCb.filter(F.action == DictAction.DONE))
async def on_done(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                  client: Client) -> None:
    data = await state.get_data()
    field = _matching_field(data, callback_data)
    if field is None or field.kind is not FieldKind.MULTI:
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    selected = list(data.get("selected", []))
    if field.required and not selected:
        await callback.answer(texts.ADMIN_MULTI_EMPTY, show_alert=True)
        return
    await _answer_result(callback, await _accept(callback.message, state, session, client, selected, in_place=True))


@router.callback_query(AdminDict.value, DictCb.filter(F.action.in_({DictAction.SKIP, DictAction.CLEAR})))
async def on_skip_or_clear(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                           client: Client) -> None:
    """«Пропустить» в мастере и «Очистить» при правке — пустое значение необязательного поля."""
    data = await state.get_data()
    field = _matching_field(data, callback_data)
    expected_mode = "add" if callback_data.action is DictAction.SKIP else "edit"
    if field is None or field.required or data.get("mode") != expected_mode:
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
    await _show_card(callback.message, session, client, callback_data.kind, item.id, page=0, notice=texts.ADMIN_SAVED)


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
    field = _current_field(await state.get_data())
    if field.kind in BUTTON_KINDS or field.kind is FieldKind.PHOTO:
        await message.answer(texts.ADMIN_FIELD_ERRORS[field.kind.value])
        return
    # Многострочные поля — с форматированием, которое админ сделал в Telegram (жирный, курсив, ссылки).
    rich = field.kind in (FieldKind.HTML, FieldKind.DOCUMENT)
    value = parse_field(field, message.html_text if rich else message.text)
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


# --- Движок мастера и правки -----------------------------------------------------------------------------------

def _current_field(data: dict[str, Any]) -> DictField:
    spec = ADMIN_SPECS[DictKind(data["kind"])]
    if data["mode"] == "add":
        return spec.fields[data["step"]]
    return spec.field(data["field"])


def _matching_field(data: dict[str, Any], callback_data: DictCb) -> DictField | None:
    """Поле текущего вопроса, если кнопка относится именно к нему (а не к прошлому вопросу или другой записи)."""
    if data.get("kind") != callback_data.kind.value or "mode" not in data:
        return None
    if data["mode"] == "edit" and data.get("item_id") != callback_data.item_id:
        return None
    field = _current_field(data)
    return field if field.name == callback_data.field else None


async def _ask_field(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                     in_place: bool) -> None:
    """Вопрос по текущему полю: шаг мастера или правка поля существующей записи."""
    data = await state.get_data()
    spec = ADMIN_SPECS[DictKind(data["kind"])]
    service = _service(session, spec.kind, client)
    field = _current_field(data)
    if data["mode"] == "add":
        header = texts.ADMIN_WIZARD_HEADER.format(title=texts.ADMIN_DICT_NEW[spec.kind.value],
                                                  step=data["step"] + 1, steps=len(spec.fields))
        current, item_id = None, 0
        selected = list(data["values"].get(field.name) or [])
    else:
        item = await service.item(data["item_id"])
        header = texts.ADMIN_EDIT_HEADER.format(item=safe(service.title(item)))
        current, item_id = service.field_value(item, field), item.id
        selected = list(current or []) if field.kind is FieldKind.MULTI else []
    options = await service.options(field, selected) if field.kind is FieldKind.MULTI else None
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
        await state.update_data(values={**data["values"], field.name: value}, step=data["step"] + 1)
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


async def _next_step(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                     spec: DictionarySpec, in_place: bool) -> None:
    """Следующее поле мастера или предпросмотр, если поля кончились."""
    data = await state.get_data()
    if data["step"] < len(spec.fields):
        await _ask_field(message, state, session, client, in_place)
        return
    await state.set_state(AdminDict.confirm)
    if spec.kind is DictKind.COURSES:
        # Курс — ровно как его увидят в афише: обложка + подпись; кнопка «🚀 Опубликовать».
        program = await AdminCourseService(session, client.id).preview(data["values"])
        await _screen(message, render.program_caption(program), admin_views.course_preview_markup(), in_place,
                      photo=program.cover_file_id or DEFAULT_COVER)
        return
    await _screen(message, *admin_views.preview(spec, data["values"]), in_place)


# --- Вспомогательное -------------------------------------------------------------------------------------------

async def _show_list(message: Message, session: AsyncSession, client: Client, kind: DictKind, page: int,
                     notice: str | None = None, in_place: bool = True) -> None:
    service = _service(session, kind, client)
    entries = [(item.id, service.title(item), service.is_active(item)) for item in await service.items()]
    text, markup = admin_views.dict_list(service.spec, entries, page)
    await _screen(message, with_notice(notice, text), markup, in_place)


async def _show_card(message: Message, session: AsyncSession, client: Client, kind: DictKind, item_id: int,
                     page: int, notice: str | None = None, in_place: bool = True) -> bool:
    """Карточка записи; курс — ровно как в афише (обложка + подпись). False — записи нет."""
    service = _service(session, kind, client)
    item = await service.item(item_id)
    if item is None:
        return False
    if kind is DictKind.COURSES:
        caption = render.program_caption(item)
        if notice and render.visible_length(with_notice(notice, caption)) <= render.CAPTION_LIMIT:
            caption = with_notice(notice, caption)
        markup = admin_views.course_card_markup(service.spec, item.id, service.is_active(item),
                                                bool(item.program_html), page)
        await _screen(message, caption, markup, in_place, photo=item.cover_file_id or DEFAULT_COVER)
        return True
    values = {f.name: service.field_value(item, f) for f in service.spec.fields}
    text, markup = admin_views.card(service.spec, item.id, values, service.is_active(item), page)
    await _screen(message, with_notice(notice, text), markup, in_place)
    return True


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
    if field.kind in BUTTON_KINDS or field.kind is FieldKind.PHOTO:
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
