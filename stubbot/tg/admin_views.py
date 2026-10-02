"""Экраны админки: текст + клавиатура. Только inline-кнопки, все они меняют то же сообщение (tg/screen.py).

Значения из справочников экранируются; поля FieldKind.HTML — это HTML, который собрал aiogram из форматирования
сообщения админа (message.html_text), он уже безопасен.
"""

from collections.abc import Mapping

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from stubbot.repositories.admin_dictionaries import DictionaryItem
from stubbot.services.admin_dictionaries import DictField, DictionarySpec, DictKind, FieldKind
from stubbot.tg import texts
from stubbot.tg.callbacks import AdminAction, AdminCb, DictAction, DictCb
from stubbot.tg.formatting import safe
from stubbot.tg.render import short

PAGE_SIZE = 8


def _dict(kind: DictKind, action: DictAction, item_id: int = 0, field: str = "", page: int = 0) -> str:
    return DictCb(kind=kind, action=action, item_id=item_id, field=field, page=page).pack()


def _admin(action: AdminAction) -> str:
    return AdminCb(action=action).pack()


# --- Меню ------------------------------------------------------------------------------------------------------

def menu() -> tuple[str, InlineKeyboardMarkup]:
    builder = InlineKeyboardBuilder()
    for kind in DictKind:
        builder.button(text=texts.ADMIN_DICT_TITLES[kind.value], callback_data=_dict(kind, DictAction.LIST))
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_CLOSE, callback_data=_admin(AdminAction.CLOSE)))
    return texts.ADMIN_MENU, builder.as_markup()


# --- Список ----------------------------------------------------------------------------------------------------

def item_title(spec: DictionarySpec, item: DictionaryItem) -> str:
    return getattr(item, spec.title_field)


def dict_list(spec: DictionarySpec, items: list[DictionaryItem], page: int) -> tuple[str, InlineKeyboardMarkup]:
    kind = spec.kind
    title = texts.ADMIN_DICT_TITLES[kind.value]
    pages = max((len(items) + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = min(max(page, 0), pages - 1)
    archived = sum(1 for item in items if not item.is_active)
    if items:
        text = texts.ADMIN_LIST.format(
            title=title, total=len(items),
            archived=texts.ADMIN_LIST_ARCHIVED.format(count=archived) if archived else "",
        )
    else:
        text = texts.ADMIN_LIST_EMPTY.format(title=title)

    rows = []
    for item in items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        mark = "" if item.is_active else texts.ADMIN_ARCHIVED_MARK
        rows.append([InlineKeyboardButton(text=mark + short(item_title(spec, item)),
                                          callback_data=_dict(kind, DictAction.VIEW, item.id, page=page))])
    if pages > 1:
        pager = []
        if page > 0:
            pager.append(InlineKeyboardButton(text=texts.BTN_PREV_PAGE,
                                              callback_data=_dict(kind, DictAction.LIST, page=page - 1)))
        pager.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=_admin(AdminAction.NOOP)))
        if page + 1 < pages:
            pager.append(InlineKeyboardButton(text=texts.BTN_NEXT_PAGE,
                                              callback_data=_dict(kind, DictAction.LIST, page=page + 1)))
        rows.append(pager)
    rows.append([InlineKeyboardButton(text=texts.BTN_ADMIN_ADD, callback_data=_dict(kind, DictAction.ADD))])
    rows.append([InlineKeyboardButton(text=texts.BTN_ADMIN_BACK, callback_data=_admin(AdminAction.MENU))])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


# --- Карточка --------------------------------------------------------------------------------------------------

def _value(field: DictField, value: str | None) -> str:
    if not value:
        return "—"
    if field.kind is FieldKind.HTML:
        return f"\n{value}"  # многострочный текст — с новой строки
    return safe(value)


def card_text(spec: DictionarySpec, values: Mapping[str, str | None], is_active: bool = True) -> str:
    lines = [f"<b>{texts.ADMIN_DICT_ITEM[spec.kind.value]}</b>", ""]
    lines += [f"<b>{texts.ADMIN_FIELD_LABELS[f.name]}:</b> {_value(f, values.get(f.name))}" for f in spec.fields]
    if not is_active:
        lines += ["", texts.ADMIN_CARD_ARCHIVED]
    return "\n".join(lines)


def item_values(spec: DictionarySpec, item: DictionaryItem) -> dict[str, str | None]:
    return {f.name: getattr(item, f.name) for f in spec.fields}


def card(spec: DictionarySpec, item: DictionaryItem, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    kind = spec.kind
    builder = InlineKeyboardBuilder()
    for field in spec.fields:
        builder.button(text=texts.BTN_ADMIN_EDIT_FIELD.format(label=texts.ADMIN_FIELD_LABELS[field.name]),
                       callback_data=_dict(kind, DictAction.EDIT, item.id, field.name, page))
    builder.adjust(2)
    action, label = ((DictAction.ARCHIVE, texts.BTN_ADMIN_ARCHIVE) if item.is_active
                     else (DictAction.RESTORE, texts.BTN_ADMIN_RESTORE))
    builder.row(InlineKeyboardButton(text=label, callback_data=_dict(kind, action, item.id, page=page)))
    builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_TO_LIST, callback_data=_dict(kind, DictAction.LIST, page=page)))
    return card_text(spec, item_values(spec, item), item.is_active), builder.as_markup()


# --- Вопрос по полю и предпросмотр -----------------------------------------------------------------------------

def field_prompt(spec: DictionarySpec, field: DictField, header: str, current: str | None,
                 item_id: int = 0, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """Вопрос в мастере новой записи (item_id=0: «Пропустить») или при правке поля (item_id: «Очистить»)."""
    text = texts.ADMIN_FIELD_PROMPT.format(
        header=header,
        label=texts.ADMIN_FIELD_LABELS[field.name],
        optional="" if field.required else texts.ADMIN_FIELD_OPTIONAL,
        hint=texts.ADMIN_FIELD_HINTS[field.name],
        current=texts.ADMIN_CURRENT_VALUE.format(value=_value(field, current)) if current else "",
    )
    row = []
    if not field.required and not item_id:
        row.append(InlineKeyboardButton(text=texts.BTN_ADMIN_SKIP,
                                        callback_data=_dict(spec.kind, DictAction.SKIP, field=field.name)))
    if not field.required and item_id and current:
        row.append(InlineKeyboardButton(text=texts.BTN_ADMIN_CLEAR,
                                        callback_data=_dict(spec.kind, DictAction.CLEAR, item_id, field.name, page)))
    row.append(InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL,
                                    callback_data=_dict(spec.kind, DictAction.CANCEL, item_id, page=page)))
    return text, InlineKeyboardMarkup(inline_keyboard=[row])


def preview(spec: DictionarySpec, values: Mapping[str, str | None]) -> tuple[str, InlineKeyboardMarkup]:
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_ADMIN_SAVE, callback_data=_dict(spec.kind, DictAction.SAVE)),
        InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL, callback_data=_dict(spec.kind, DictAction.CANCEL)),
    ]])
    return texts.ADMIN_PREVIEW.format(card=card_text(spec, values)), markup
