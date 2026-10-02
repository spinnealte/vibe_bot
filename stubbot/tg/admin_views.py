"""Экраны админки: текст + клавиатура. Только inline-кнопки, все они меняют то же сообщение (tg/screen.py).

Значения экранируются; поля FieldKind.HTML / DOCUMENT — это HTML, который собрал aiogram из форматирования
сообщения админа (message.html_text) или utils/documents из файла, он уже безопасен.
"""

from collections.abc import Mapping
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from stubbot.services.admin_dictionaries import DictField, DictionarySpec, DictKind, FieldKind
from stubbot.tg import texts
from stubbot.tg.callbacks import AdminAction, AdminCb, DictAction, DictCb
from stubbot.tg.formatting import safe
from stubbot.tg.render import plain_excerpt, short

PAGE_SIZE = 8
CURRENT_EXCERPT = 200  # сколько текущей программы показывать при правке

# Запись в списке: id, название, действует ли (не в архиве).
ListEntry = tuple[int, str, bool]


def _dict(kind: DictKind, action: DictAction, item_id: int = 0, field: str = "", page: int = 0,
          value: str = "") -> str:
    return DictCb(kind=kind, action=action, item_id=item_id, field=field, page=page, value=value).pack()


def _admin(action: AdminAction) -> str:
    return AdminCb(action=action).pack()


def _pager(page: int, pages: int, prev_data: str, next_data: str) -> list[InlineKeyboardButton]:
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text=texts.BTN_PREV_PAGE, callback_data=prev_data))
    row.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=_admin(AdminAction.NOOP)))
    if page + 1 < pages:
        row.append(InlineKeyboardButton(text=texts.BTN_NEXT_PAGE, callback_data=next_data))
    return row


# --- Меню ------------------------------------------------------------------------------------------------------

def menu() -> tuple[str, InlineKeyboardMarkup]:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.ADMIN_DICT_TITLES[DictKind.COURSES.value],
                   callback_data=_dict(DictKind.COURSES, DictAction.LIST))
    for kind in (DictKind.LECTURERS, DictKind.VENUES, DictKind.SPECIALTIES, DictKind.POSITIONS):
        builder.button(text=texts.ADMIN_DICT_TITLES[kind.value], callback_data=_dict(kind, DictAction.LIST))
    builder.adjust(1, 2)
    builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_CLOSE, callback_data=_admin(AdminAction.CLOSE)))
    return texts.ADMIN_MENU, builder.as_markup()


# --- Список ----------------------------------------------------------------------------------------------------

def dict_list(spec: DictionarySpec, entries: list[ListEntry], page: int) -> tuple[str, InlineKeyboardMarkup]:
    kind = spec.kind
    title = texts.ADMIN_DICT_TITLES[kind.value]
    pages = max((len(entries) + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = min(max(page, 0), pages - 1)
    archived = sum(1 for _, _, active in entries if not active)
    if entries:
        text = texts.ADMIN_LIST.format(
            title=title, total=len(entries),
            archived=texts.ADMIN_LIST_ARCHIVED.format(count=archived) if archived else "",
        )
    else:
        text = texts.ADMIN_LIST_EMPTY.format(title=title)

    rows = []
    for item_id, item_title, active in entries[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        mark = "" if active else texts.ADMIN_ARCHIVED_MARK
        rows.append([InlineKeyboardButton(text=mark + short(item_title),
                                          callback_data=_dict(kind, DictAction.VIEW, item_id, page=page))])
    if pages > 1:
        rows.append(_pager(page, pages, _dict(kind, DictAction.LIST, page=page - 1),
                           _dict(kind, DictAction.LIST, page=page + 1)))
    rows.append([InlineKeyboardButton(text=texts.BTN_ADMIN_ADD, callback_data=_dict(kind, DictAction.ADD))])
    rows.append([InlineKeyboardButton(text=texts.BTN_ADMIN_BACK, callback_data=_admin(AdminAction.MENU))])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


# --- Карточка --------------------------------------------------------------------------------------------------

def _value(field: DictField, value: Any) -> str:
    """Значение поля для карточки справочника и «Сейчас: …» при правке."""
    if value is None or value == "" or value == []:
        return "—"
    match field.kind:
        case FieldKind.HTML:
            return f"\n{value}"  # многострочный текст — с новой строки
        case FieldKind.DOCUMENT:
            return f"\n{plain_excerpt(value, CURRENT_EXCERPT)}"
        case FieldKind.CHOICE:
            return texts.ADMIN_CHOICE_LABELS[field.name].get(value, safe(value))
        case FieldKind.PHOTO:
            return texts.ADMIN_CURRENT_PHOTO
    return safe(value)


def card_text(spec: DictionarySpec, values: Mapping[str, Any], is_active: bool = True) -> str:
    lines = [f"<b>{texts.ADMIN_DICT_ITEM[spec.kind.value]}</b>", ""]
    lines += [f"<b>{texts.ADMIN_FIELD_LABELS[f.name]}:</b> {_value(f, values.get(f.name))}" for f in spec.fields]
    if not is_active:
        lines += ["", texts.ADMIN_CARD_ARCHIVED]
    return "\n".join(lines)


def _edit_buttons(spec: DictionarySpec, item_id: int, page: int) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for field in spec.fields:
        builder.button(text=texts.BTN_ADMIN_EDIT_FIELD.format(label=texts.ADMIN_FIELD_LABELS[field.name]),
                       callback_data=_dict(spec.kind, DictAction.EDIT, item_id, field.name, page))
    builder.adjust(2)
    return builder


def _archive_and_back(builder: InlineKeyboardBuilder, kind: DictKind, item_id: int, active: bool, page: int) -> None:
    action, label = ((DictAction.ARCHIVE, texts.BTN_ADMIN_ARCHIVE) if active
                     else (DictAction.RESTORE, texts.BTN_ADMIN_RESTORE))
    builder.row(InlineKeyboardButton(text=label, callback_data=_dict(kind, action, item_id, page=page)))
    builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_TO_LIST,
                                     callback_data=_dict(kind, DictAction.LIST, page=page)))


def card(spec: DictionarySpec, item_id: int, values: Mapping[str, Any], active: bool,
         page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """Карточка записи справочника: поля текстом, «✏️» у каждого, архив, к списку."""
    builder = _edit_buttons(spec, item_id, page)
    _archive_and_back(builder, spec.kind, item_id, active, page)
    return card_text(spec, values, active), builder.as_markup()


def course_card_markup(spec: DictionarySpec, item_id: int, active: bool, has_program: bool,
                       page: int = 0) -> InlineKeyboardMarkup:
    """Под карточкой курса (фото + подпись как в афише): «✏️» у каждого поля, программа, архив, к списку."""
    builder = _edit_buttons(spec, item_id, page)
    if has_program:
        builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_PROGRAM,
                                         callback_data=_dict(spec.kind, DictAction.PROGRAM, item_id, page=0)))
    _archive_and_back(builder, spec.kind, item_id, active, page)
    return builder.as_markup()


def course_program(item_id: int, page: int, pages: int) -> InlineKeyboardMarkup:
    rows = []
    if pages > 1:
        rows.append(_pager(page, pages, _dict(DictKind.COURSES, DictAction.PROGRAM, item_id, page=page - 1),
                           _dict(DictKind.COURSES, DictAction.PROGRAM, item_id, page=page + 1)))
    rows.append([InlineKeyboardButton(text=texts.BTN_ADMIN_BACK_TO_COURSE,
                                      callback_data=_dict(DictKind.COURSES, DictAction.VIEW, item_id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- Вопрос по полю и предпросмотр -----------------------------------------------------------------------------

def _hint(spec: DictionarySpec, field: DictField) -> str:
    return texts.ADMIN_FIELD_HINTS.get(f"{spec.kind.value}.{field.name}", texts.ADMIN_FIELD_HINTS[field.name])


def field_prompt(spec: DictionarySpec, field: DictField, header: str, current: Any,
                 item_id: int = 0, page: int = 0, options: list[tuple[int, str]] | None = None,
                 selected: list[int] | None = None) -> tuple[str, InlineKeyboardMarkup]:
    """Вопрос в мастере новой записи (item_id=0: «Пропустить») или при правке поля (item_id: «Очистить»).

    Выбор — кнопки вариантов (✅ у текущего); мультивыбор — options с ✅ у selected и «Готово».
    """
    kind = spec.kind
    shows_current = current not in (None, "", []) and field.kind is not FieldKind.MULTI
    text = texts.ADMIN_FIELD_PROMPT.format(
        header=header,
        label=texts.ADMIN_FIELD_LABELS[field.name],
        optional="" if field.required else texts.ADMIN_FIELD_OPTIONAL,
        hint=_hint(spec, field),
        current=texts.ADMIN_CURRENT_VALUE.format(value=_value(field, current)) if shows_current else "",
    )
    builder = InlineKeyboardBuilder()
    if field.kind is FieldKind.CHOICE:
        for choice in field.choices:
            mark = "✅ " if choice == current else ""
            builder.button(text=mark + texts.ADMIN_CHOICE_LABELS[field.name][choice],
                           callback_data=_dict(kind, DictAction.PICK, item_id, field.name, page, value=choice))
        builder.adjust(2)
    elif field.kind is FieldKind.MULTI:
        chosen = set(selected or [])
        for option_id, title in options or []:
            mark = "✅ " if option_id in chosen else ""
            builder.button(text=mark + short(title),
                           callback_data=_dict(kind, DictAction.TOGGLE, item_id, field.name, page, str(option_id)))
        builder.adjust(2)
        builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_DONE,
                                         callback_data=_dict(kind, DictAction.DONE, item_id, field.name, page)))

    controls = []
    if not field.required and not item_id:
        controls.append(InlineKeyboardButton(text=texts.BTN_ADMIN_SKIP,
                                             callback_data=_dict(kind, DictAction.SKIP, field=field.name)))
    if not field.required and item_id and current not in (None, "", []):
        controls.append(InlineKeyboardButton(text=texts.BTN_ADMIN_CLEAR,
                                             callback_data=_dict(kind, DictAction.CLEAR, item_id, field.name, page)))
    controls.append(InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL,
                                         callback_data=_dict(kind, DictAction.CANCEL, item_id, page=page)))
    builder.row(*controls)
    return text, builder.as_markup()


def preview(spec: DictionarySpec, values: Mapping[str, Any]) -> tuple[str, InlineKeyboardMarkup]:
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_ADMIN_SAVE, callback_data=_dict(spec.kind, DictAction.SAVE)),
        InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL, callback_data=_dict(spec.kind, DictAction.CANCEL)),
    ]])
    return texts.ADMIN_PREVIEW.format(card=card_text(spec, values)), markup


def course_preview_markup() -> InlineKeyboardMarkup:
    """Курс публикуется сразу (черновиков нет)."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_ADMIN_PUBLISH, callback_data=_dict(DictKind.COURSES, DictAction.SAVE)),
        InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL, callback_data=_dict(DictKind.COURSES, DictAction.CANCEL)),
    ]])
