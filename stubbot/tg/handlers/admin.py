"""Админка: вход кнопкой «⚙️ Админка» в главном меню (видна только owner и ADMIN_IDS), дальше — только inline-кнопки.

Фильтр IsAdmin стоит на весь роутер (message и callback_query): чужие нажатия и текст уходят в fallback.
Справочники — один общий редактор (services/admin_dictionaries.py): список → карточка → правка поля / архив;
«➕ Добавить» — мастер по полям с предпросмотром. Ответ текстом — новым сообщением, у вопроса убираем кнопки.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.models import Client
from stubbot.services.admin_dictionaries import (
    SPECS,
    AdminDictionaryService,
    DictionarySpec,
    DictKind,
    FieldKind,
    SaveResult,
    parse_field,
)
from stubbot.tg import admin_views, texts
from stubbot.tg.callbacks import AdminAction, AdminCb, DictAction, DictCb
from stubbot.tg.filters import IsAdmin
from stubbot.tg.flows import with_notice
from stubbot.tg.formatting import safe
from stubbot.tg.screen import delete_quietly, send_screen, show_screen, strip_keyboard
from stubbot.tg.states import AdminDict

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

PROMPT_KEY = "admin_prompt_id"


def _service(session: AsyncSession, kind: DictKind, client: Client) -> AdminDictionaryService:
    return AdminDictionaryService(session, SPECS[kind], client.id)


async def _screen(message: Message, text: str, markup: InlineKeyboardMarkup, in_place: bool) -> Message:
    return await (show_screen if in_place else send_screen)(message, text, markup)


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


# --- Список, карточка, архив -----------------------------------------------------------------------------------

@router.callback_query(DictCb.filter(F.action == DictAction.LIST))
async def show_list(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                    client: Client) -> None:
    await callback.answer()
    await state.clear()
    await _show_list(callback.message, session, client, callback_data.kind, callback_data.page)


@router.callback_query(DictCb.filter(F.action == DictAction.VIEW))
async def show_item(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                    client: Client) -> None:
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


# --- Новая запись: мастер по полям -----------------------------------------------------------------------------

@router.callback_query(DictCb.filter(F.action == DictAction.ADD))
async def start_add(callback: CallbackQuery, callback_data: DictCb, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await state.set_state(AdminDict.value)
    await state.update_data(kind=callback_data.kind.value, mode="add", step=0, values={})
    await _ask_step(callback.message, state, SPECS[callback_data.kind], step=0, in_place=True)


@router.callback_query(AdminDict.value, DictCb.filter(F.action == DictAction.SKIP))
async def on_skip(callback: CallbackQuery, callback_data: DictCb, state: FSMContext) -> None:
    data = await state.get_data()
    spec = SPECS[callback_data.kind]
    if (data.get("mode") != "add" or data.get("kind") != spec.kind.value
            or spec.fields[data["step"]].name != callback_data.field or spec.fields[data["step"]].required):
        await callback.answer(texts.UNKNOWN_CALLBACK)  # кнопка от прошлого вопроса
        return
    await callback.answer()
    await state.update_data(values={**data["values"], callback_data.field: None})
    await _next_step(callback.message, state, spec, data["step"] + 1, in_place=True)


@router.callback_query(AdminDict.confirm, DictCb.filter(F.action == DictAction.SAVE))
async def on_save(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                  client: Client) -> None:
    data = await state.get_data()
    if data.get("kind") != callback_data.kind.value:
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    spec = SPECS[callback_data.kind]
    result, item = await _service(session, spec.kind, client).create(data["values"])
    if result is not SaveResult.OK:
        await callback.answer(texts.ADMIN_DUPLICATE if result is SaveResult.DUPLICATE else texts.ADMIN_REQUIRED,
                              show_alert=True)
        return
    await callback.answer()
    await state.clear()
    text, markup = admin_views.card(spec, item)
    await show_screen(callback.message, with_notice(texts.ADMIN_SAVED, text), markup)


async def _ask_step(message: Message, state: FSMContext, spec: DictionarySpec, step: int, in_place: bool) -> None:
    header = texts.ADMIN_WIZARD_HEADER.format(title=texts.ADMIN_DICT_NEW[spec.kind.value], step=step + 1,
                                              steps=len(spec.fields))
    prompt = await _screen(message, *admin_views.field_prompt(spec, spec.fields[step], header, current=None),
                           in_place=in_place)
    await state.update_data({"step": step, PROMPT_KEY: prompt.message_id})


async def _next_step(message: Message, state: FSMContext, spec: DictionarySpec, step: int, in_place: bool) -> None:
    """Следующее поле или предпросмотр, если поля кончились."""
    if step < len(spec.fields):
        await _ask_step(message, state, spec, step, in_place)
        return
    await state.set_state(AdminDict.confirm)
    values = (await state.get_data())["values"]
    await _screen(message, *admin_views.preview(spec, values), in_place=in_place)


# --- Правка поля -----------------------------------------------------------------------------------------------

@router.callback_query(DictCb.filter(F.action == DictAction.EDIT))
async def start_edit(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                     client: Client) -> None:
    spec = SPECS[callback_data.kind]
    field = spec.field(callback_data.field)
    item = await _service(session, spec.kind, client).item(callback_data.item_id)
    if field is None or item is None:
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.set_state(AdminDict.value)
    await state.update_data(kind=spec.kind.value, mode="edit", item_id=item.id, field=field.name,
                            page=callback_data.page)
    header = texts.ADMIN_EDIT_HEADER.format(item=safe(admin_views.item_title(spec, item)))
    prompt = await show_screen(callback.message, *admin_views.field_prompt(
        spec, field, header, current=getattr(item, field.name), item_id=item.id, page=callback_data.page))
    await state.update_data({PROMPT_KEY: prompt.message_id})


@router.callback_query(AdminDict.value, DictCb.filter(F.action == DictAction.CLEAR))
async def on_clear(callback: CallbackQuery, callback_data: DictCb, state: FSMContext, session: AsyncSession,
                   client: Client) -> None:
    data = await state.get_data()
    spec = SPECS[callback_data.kind]
    field = spec.field(callback_data.field)
    if (data.get("mode") != "edit" or data.get("kind") != spec.kind.value or field is None
            or data.get("item_id") != callback_data.item_id or data.get("field") != field.name):
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    result = await _service(session, spec.kind, client).update_field(callback_data.item_id, field, None)
    if result is not SaveResult.OK:
        await callback.answer(texts.ADMIN_REQUIRED if result is SaveResult.MISSING_REQUIRED else texts.ADMIN_NOT_FOUND,
                              show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await _show_card(callback.message, session, client, spec.kind, callback_data.item_id, callback_data.page,
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


# --- Ответ текстом: значение поля (и в мастере, и при правке) --------------------------------------------------

@router.message(AdminDict.value, F.text)
async def on_value(message: Message, state: FSMContext, session: AsyncSession, client: Client) -> None:
    data = await state.get_data()
    spec = SPECS[DictKind(data["kind"])]
    field = spec.fields[data["step"]] if data["mode"] == "add" else spec.field(data["field"])
    # Многострочные поля — с форматированием, которое админ сделал в Telegram (жирный, курсив, ссылки).
    value = parse_field(field, message.html_text if field.kind is FieldKind.HTML else message.text)
    if value is None:
        error = texts.ADMIN_FIELD_ERRORS[field.kind.value].format(max=field.max_length)
        await message.answer(error + texts.ADMIN_ERROR_HINT)
        return

    if data["mode"] == "add":
        await _strip_prompt(message, state)
        await state.update_data(values={**data["values"], field.name: value})
        await _next_step(message, state, spec, data["step"] + 1, in_place=False)
        return

    result = await _service(session, spec.kind, client).update_field(data["item_id"], field, value)
    if result is SaveResult.DUPLICATE:
        await message.answer(f"❗️ {texts.ADMIN_DUPLICATE}." + texts.ADMIN_ERROR_HINT)
        return
    await _strip_prompt(message, state)
    await state.clear()
    if not await _show_card(message, session, client, spec.kind, data["item_id"], data.get("page", 0),
                            notice=texts.ADMIN_SAVED, in_place=False):
        await _show_list(message, session, client, spec.kind, data.get("page", 0), in_place=False)


@router.message(AdminDict.value)
async def value_expects_text(message: Message) -> None:
    await message.answer(texts.ADMIN_TEXT_ONLY)


@router.message(AdminDict.confirm)
async def confirm_expects_buttons(message: Message) -> None:
    await message.answer(texts.REG_USE_BUTTONS)


# --- Вспомогательное -------------------------------------------------------------------------------------------

async def _show_list(message: Message, session: AsyncSession, client: Client, kind: DictKind, page: int,
                     notice: str | None = None, in_place: bool = True) -> None:
    items = await _service(session, kind, client).items()
    text, markup = admin_views.dict_list(SPECS[kind], items, page)
    await _screen(message, with_notice(notice, text), markup, in_place)


async def _show_card(message: Message, session: AsyncSession, client: Client, kind: DictKind, item_id: int,
                     page: int, notice: str | None = None, in_place: bool = True) -> bool:
    """False — записи нет."""
    item = await _service(session, kind, client).item(item_id)
    if item is None:
        return False
    text, markup = admin_views.card(SPECS[kind], item, page)
    await _screen(message, with_notice(notice, text), markup, in_place)
    return True


async def _strip_prompt(message: Message, state: FSMContext) -> None:
    prompt_id = (await state.get_data()).get(PROMPT_KEY)
    if prompt_id:
        await strip_keyboard(message, prompt_id)
