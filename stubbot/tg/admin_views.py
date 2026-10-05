"""Экраны админки: текст + клавиатура. Только inline-кнопки, все они меняют то же сообщение (tg/screen.py).

Значения экранируются; поля FieldKind.HTML / DOCUMENT — это HTML, который собрал aiogram из форматирования
сообщения админа (message.html_text) или utils/documents из файла, он уже безопасен.

Списки проведений можно открыть для одного курса: id курса едет в кнопках в поле value («родитель»).
"""

from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from stubbot.config import get_settings
from stubbot.services.admin_dictionaries import DictField, DictionarySpec, DictKind, FieldKind
from stubbot.tg import texts
from stubbot.tg.callbacks import AdminAction, AdminCb, DictAction, DictCb
from stubbot.tg.formatting import safe
from stubbot.tg.render import plain_excerpt, short
from stubbot.utils.dates import format_month
from stubbot.utils.money import format_rub
from stubbot.utils.schedule_input import format_days, format_deadline

PAGE_SIZE = 8
CURRENT_EXCERPT = 200  # сколько текущей программы показывать при правке

# Запись в списке: id, название, действует ли (не в архиве / не скрыта).
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


def _parent(parent_id: int | None) -> str:
    return str(parent_id) if parent_id else ""


# --- Меню ------------------------------------------------------------------------------------------------------

def menu() -> tuple[str, InlineKeyboardMarkup]:
    builder = InlineKeyboardBuilder()
    for kind in (DictKind.COURSES, DictKind.SESSIONS, DictKind.LECTURERS, DictKind.VENUES, DictKind.SPECIALTIES,
                 DictKind.POSITIONS):
        builder.button(text=texts.ADMIN_DICT_TITLES[kind.value], callback_data=_dict(kind, DictAction.LIST))
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_EXPORT, callback_data=_admin(AdminAction.EXPORT)))
    builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_CLOSE, callback_data=_admin(AdminAction.CLOSE)))
    return texts.ADMIN_MENU, builder.as_markup()


# --- Список ----------------------------------------------------------------------------------------------------

def dict_list(spec: DictionarySpec, entries: list[ListEntry], page: int) -> tuple[str, InlineKeyboardMarkup]:
    """Список записей справочника или курсов. Проведения показываются иначе — по месяцам (session_months)."""
    kind = spec.kind
    title = texts.ADMIN_DICT_TITLES[kind.value]
    columns = spec.list_columns
    page_size = PAGE_SIZE * columns  # строк на странице столько же, кнопок — по числу столбцов
    pages = max((len(entries) + page_size - 1) // page_size, 1)
    page = min(max(page, 0), pages - 1)
    hidden = sum(1 for _, _, active in entries if not active)
    if entries:
        text = texts.ADMIN_LIST.format(title=title, total=len(entries),
                                       archived=texts.ADMIN_LIST_ARCHIVED.format(count=hidden) if hidden else "")
    else:
        text = texts.ADMIN_LIST_EMPTY.format(title=title)

    items = [InlineKeyboardButton(text=("" if active else texts.ADMIN_ARCHIVED_MARK) + short(item_title),
                                  callback_data=_dict(kind, DictAction.VIEW, item_id, page=page))
             for item_id, item_title, active in entries[page * page_size:(page + 1) * page_size]]
    rows = [items[i:i + columns] for i in range(0, len(items), columns)]
    if pages > 1:
        rows.append(_pager(page, pages, _dict(kind, DictAction.LIST, page=page - 1),
                           _dict(kind, DictAction.LIST, page=page + 1)))
    rows.append([InlineKeyboardButton(text=texts.BTN_ADMIN_ADD, callback_data=_dict(kind, DictAction.ADD))])
    rows.append([InlineKeyboardButton(text=texts.BTN_ADMIN_BACK, callback_data=_admin(AdminAction.MENU))])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def session_months(months: list[tuple[int, int]], current_month: int, past: bool = False, page: int = 0,
                   parent_id: int | None = None, heading: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    """Проведения по месяцам: кнопки «Октябрь 2026 · 3» в два столбца, только месяцы, где проведения есть.

    months — [(ГГГГММ, сколько проведений)]. Основной экран — текущий месяц и будущие; прошедшие месяцы — на
    отдельном экране (past), свежие первыми. parent_id — проведения одного курса (heading — его название).
    Внутри месяца проведения листаются стрелками, как афиша (session_card_markup).
    """
    kind = DictKind.SESSIONS
    title = heading or texts.ADMIN_DICT_TITLES[kind.value]
    parent = _parent(parent_id)
    upcoming = [(month, count) for month, count in months if month >= current_month]
    older = [(month, count) for month, count in reversed(months) if month < current_month]
    shown = older if past else upcoming
    total = sum(count for _, count in shown)
    if past:
        text = texts.ADMIN_SESSION_MONTHS_PAST.format(title=title, total=total)
    elif upcoming:
        text = texts.ADMIN_SESSION_MONTHS.format(title=title, total=total)
    else:
        text = texts.ADMIN_SESSION_MONTHS_NONE.format(title=title)

    page_size = PAGE_SIZE * 2
    pages = max((len(shown) + page_size - 1) // page_size, 1)
    page = min(max(page, 0), pages - 1)
    field = "past" if past else ""
    buttons = [InlineKeyboardButton(text=texts.BTN_ADMIN_MONTH.format(month=format_month(month), count=count),
                                    callback_data=_dict(kind, DictAction.MONTH, page=month, value=parent))
               for month, count in shown[page * page_size:(page + 1) * page_size]]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    if pages > 1:
        rows.append(_pager(page, pages, _dict(kind, DictAction.LIST, field=field, page=page - 1, value=parent),
                           _dict(kind, DictAction.LIST, field=field, page=page + 1, value=parent)))
    if past:
        rows.append([InlineKeyboardButton(text=texts.BTN_ADMIN_UPCOMING_MONTHS,
                                          callback_data=_dict(kind, DictAction.LIST, value=parent))])
    else:
        if older:
            rows.append([InlineKeyboardButton(
                text=texts.BTN_ADMIN_PAST_MONTHS.format(count=sum(count for _, count in older)),
                callback_data=_dict(kind, DictAction.LIST, field="past", value=parent))])
        rows.append([InlineKeyboardButton(text=texts.BTN_ADMIN_ADD,
                                          callback_data=_dict(kind, DictAction.ADD, value=parent))])
    if parent_id:
        rows.append([InlineKeyboardButton(text=texts.BTN_ADMIN_BACK_TO_COURSE,
                                          callback_data=_dict(DictKind.COURSES, DictAction.VIEW, parent_id))])
    rows.append([InlineKeyboardButton(text=texts.BTN_ADMIN_BACK, callback_data=_admin(AdminAction.MENU))])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


# --- Значения полей --------------------------------------------------------------------------------------------

def _value(field: DictField, value: Any, options: list[tuple[int, str]] | None = None) -> str:
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
        case FieldKind.REF:
            return safe(dict(options or []).get(value, value))
        case FieldKind.DAYS:
            return f"\n<code>{format_days(value)}</code>"
        case FieldKind.DATETIME:
            return format_deadline(value, ZoneInfo(get_settings().timezone))
        case FieldKind.PRICES:
            return "".join(f"\n• {safe(p['label'])} — {format_rub(p['amount'])}" for p in value)
    return safe(value)


def card_text(spec: DictionarySpec, values: Mapping[str, Any], is_active: bool = True) -> str:
    lines = [f"<b>{texts.ADMIN_DICT_ITEM[spec.kind.value]}</b>", ""]
    lines += [f"<b>{texts.ADMIN_FIELD_LABELS[f.name]}:</b> {_value(f, values.get(f.name))}" for f in spec.fields]
    if not is_active:
        lines += ["", texts.ADMIN_CARD_ARCHIVED]
    return "\n".join(lines)


# --- Карточки --------------------------------------------------------------------------------------------------

def _edit_buttons(spec: DictionarySpec, item_id: int, page: int, parent: str = "") -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for field in spec.fields:
        if field.editable:
            builder.button(text=texts.BTN_ADMIN_EDIT_FIELD.format(label=texts.ADMIN_FIELD_LABELS[field.name]),
                           callback_data=_dict(spec.kind, DictAction.EDIT, item_id, field.name, page, parent))
    builder.adjust(2)
    return builder


def _back_to_list(builder: InlineKeyboardBuilder, kind: DictKind, page: int) -> None:
    builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_TO_LIST, callback_data=_dict(kind, DictAction.LIST, page=page)))


def _archive_button(kind: DictKind, item_id: int, active: bool, page: int, parent: str = "") -> InlineKeyboardButton:
    if kind is DictKind.SESSIONS:
        action, label = ((DictAction.ARCHIVE, texts.BTN_ADMIN_HIDE) if active
                         else (DictAction.RESTORE, texts.BTN_ADMIN_SHOW))
    else:
        action, label = ((DictAction.ARCHIVE, texts.BTN_ADMIN_ARCHIVE) if active
                         else (DictAction.RESTORE, texts.BTN_ADMIN_RESTORE))
    return InlineKeyboardButton(text=label, callback_data=_dict(kind, action, item_id, page=page, value=parent))


def card(spec: DictionarySpec, item_id: int, values: Mapping[str, Any], active: bool,
         page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """Карточка записи справочника: поля текстом, «✏️» у каждого, архив, к списку."""
    builder = _edit_buttons(spec, item_id, page)
    builder.row(_archive_button(spec.kind, item_id, active, page))
    _back_to_list(builder, spec.kind, page)
    return card_text(spec, values, active), builder.as_markup()


def course_card_markup(spec: DictionarySpec, item_id: int, active: bool, has_program: bool, show_without_dates: bool,
                       page: int = 0) -> InlineKeyboardMarkup:
    """Под карточкой курса (фото + подпись как в афише): поля, программа, проведения, показ без дат, архив."""
    builder = _edit_buttons(spec, item_id, page)
    if has_program:
        builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_PROGRAM,
                                         callback_data=_dict(spec.kind, DictAction.PROGRAM, item_id, page=0)))
    builder.row(
        InlineKeyboardButton(text=texts.BTN_ADMIN_SESSIONS_OF_COURSE,
                             callback_data=_dict(DictKind.SESSIONS, DictAction.LIST, value=str(item_id))),
        InlineKeyboardButton(text=texts.BTN_ADMIN_NEW_SESSION,
                             callback_data=_dict(DictKind.SESSIONS, DictAction.ADD, value=str(item_id))),
    )
    tbd_label, tbd_value = ((texts.BTN_ADMIN_TBD_HIDE, "0") if show_without_dates else (texts.BTN_ADMIN_TBD_SHOW, "1"))
    builder.row(InlineKeyboardButton(text=tbd_label,
                                     callback_data=_dict(spec.kind, DictAction.SHOW_TBD, item_id, page=page,
                                                         value=tbd_value)))
    builder.row(_archive_button(spec.kind, item_id, active, page))
    _back_to_list(builder, spec.kind, page)
    return builder.as_markup()


def session_card_markup(spec: DictionarySpec, item_id: int, status: str, active: bool,
                        parent_id: int | None = None, index: int = 0, total: int = 1,
                        prev_id: int = 0, next_id: int = 0, past_month: bool = False) -> InlineKeyboardMarkup:
    """Под карточкой проведения (как в афише): ◀️ n/N ▶️ по проведениям того же месяца, поля, статус в одно
    нажатие, копия, скрыть, к месяцам.

    index/total — место проведения среди проведений месяца, prev_id/next_id — соседи (0 — края).
    past_month — месяц уже прошёл: «К месяцам» вернёт на экран прошедших.
    """
    parent = _parent(parent_id)
    builder = InlineKeyboardBuilder()
    builder.row(*_pager(index, total, _dict(spec.kind, DictAction.VIEW, prev_id, value=parent),
                        _dict(spec.kind, DictAction.VIEW, next_id, value=parent)))
    builder.attach(_edit_buttons(spec, item_id, 0, parent))
    status_field = spec.field("status")
    statuses = InlineKeyboardBuilder()
    for choice in status_field.choices:
        mark = "✅ " if choice == status else ""
        statuses.button(text=mark + texts.ADMIN_CHOICE_LABELS["status"][choice],
                        callback_data=_dict(spec.kind, DictAction.STATUS, item_id,
                                            value=f"{choice}~{parent}"))  # «:» — разделитель callback_data
    statuses.adjust(2)
    builder.attach(statuses)
    builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_COPY,
                                     callback_data=_dict(spec.kind, DictAction.COPY, item_id, value=parent)))
    builder.row(_archive_button(spec.kind, item_id, active, 0, parent))
    builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_TO_MONTHS,
                                     callback_data=_dict(spec.kind, DictAction.LIST, field="past" if past_month else "",
                                                         value=parent)))
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
                 selected: list[Any] | None = None) -> tuple[str, InlineKeyboardMarkup]:
    """Вопрос в мастере новой записи (item_id=0: «Пропустить») или при правке поля (item_id: «Очистить»).

    Выбор и запись справочника — кнопки вариантов (✅ у текущего); мультивыбор и категории тарифов —
    кнопки с ✅ у selected и «Готово».
    """
    kind = spec.kind
    shows_current = current not in (None, "", []) and field.kind is not FieldKind.MULTI
    text = texts.ADMIN_FIELD_PROMPT.format(
        header=header,
        label=texts.ADMIN_FIELD_LABELS[field.name],
        optional="" if field.required else texts.ADMIN_FIELD_OPTIONAL,
        hint=_hint(spec, field),
        current=texts.ADMIN_CURRENT_VALUE.format(value=_value(field, current, options)) if shows_current else "",
    )
    builder = InlineKeyboardBuilder()
    if field.kind is FieldKind.CHOICE:
        for choice in field.choices:
            mark = "✅ " if choice == current else ""
            builder.button(text=mark + texts.ADMIN_CHOICE_LABELS[field.name][choice],
                           callback_data=_dict(kind, DictAction.PICK, item_id, field.name, page, value=choice))
        builder.adjust(2)
    elif field.kind is FieldKind.REF:
        for option_id, title in options or []:
            mark = "✅ " if option_id == current else ""
            builder.button(text=mark + short(title),
                           callback_data=_dict(kind, DictAction.PICK, item_id, field.name, page, str(option_id)))
        builder.adjust(1)
    elif field.kind in (FieldKind.MULTI, FieldKind.PRICES):
        chosen = set(selected or [])
        if field.kind is FieldKind.PRICES:
            options = list(texts.PRICE_KIND_LABELS.items())
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


def price_label_prompt(spec: DictionarySpec, header: str, n: int, total: int, price_kind: str,
                       item_id: int = 0, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """Тарифы, шаг «подпись»: своя подпись текстом или стандартная кнопкой."""
    standard = texts.PRICE_KIND_LABELS[price_kind]
    text = texts.ADMIN_PRICE_LABEL_PROMPT.format(header=header, n=n, total=total, category=standard)
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.BTN_ADMIN_PRICE_STD.format(label=standard),
                              callback_data=_dict(spec.kind, DictAction.PICK, item_id, "prices", page, "std"))],
        [InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL,
                              callback_data=_dict(spec.kind, DictAction.CANCEL, item_id, page=page))],
    ])
    return text, markup


def price_amount_prompt(spec: DictionarySpec, header: str, n: int, total: int, label: str, unit: str,
                        item_id: int = 0, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    text = texts.ADMIN_PRICE_AMOUNT_PROMPT.format(header=header, n=n, total=total, label=safe(label),
                                                  unit=texts.ADMIN_PRICE_UNIT[unit])
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL,
                             callback_data=_dict(spec.kind, DictAction.CANCEL, item_id, page=page)),
    ]])
    return text, markup


def preview(spec: DictionarySpec, values: Mapping[str, Any]) -> tuple[str, InlineKeyboardMarkup]:
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_ADMIN_SAVE, callback_data=_dict(spec.kind, DictAction.SAVE)),
        InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL, callback_data=_dict(spec.kind, DictAction.CANCEL)),
    ]])
    return texts.ADMIN_PREVIEW.format(card=card_text(spec, values)), markup


def publish_markup(kind: DictKind) -> InlineKeyboardMarkup:
    """Курсы и проведения публикуются сразу (черновиков нет)."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_ADMIN_PUBLISH, callback_data=_dict(kind, DictAction.SAVE)),
        InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL, callback_data=_dict(kind, DictAction.CANCEL)),
    ]])
