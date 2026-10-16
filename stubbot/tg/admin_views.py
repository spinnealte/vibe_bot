"""Экраны админки: текст + клавиатура. Только inline-кнопки, все они меняют то же сообщение (tg/screen.py).

Значения экранируются; поля FieldKind.HTML / DOCUMENT - это HTML, который собрал aiogram из форматирования
сообщения админа (message.html_text) или utils/documents из файла, он уже безопасен.

Курсы и проведения списком кнопок не показываются: карточки как в афише листаются стрелками. У проведений три
раздела (SessionScope): афиша, архив и прошедшие - раздел едет в кнопках месяцев в поле value.
"""

from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from stubbot.config import get_settings
from stubbot.services.admin_dictionaries import DictField, DictionarySpec, DictKind, FieldKind
from stubbot.services.admin_sessions import NO_DATES, SessionScope
from stubbot.tg import texts
from stubbot.tg.callbacks import AdminAction, AdminCb, DictAction, DictCb
from stubbot.tg.formatting import safe
from stubbot.tg.render import plain_excerpt, short
from stubbot.utils.dates import format_month
from stubbot.utils.money import format_rub
from stubbot.utils.schedule_input import format_days, format_deadline

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
    for kind in (DictKind.COURSES, DictKind.SESSIONS, DictKind.LECTURERS, DictKind.VENUES, DictKind.SPECIALTIES,
                 DictKind.POSITIONS):
        builder.button(text=texts.ADMIN_DICT_TITLES[kind.value], callback_data=_dict(kind, DictAction.LIST))
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_ARCHIVE_MENU, callback_data=_admin(AdminAction.ARCHIVE)))
    builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_EXPORT, callback_data=_admin(AdminAction.EXPORT)))
    builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_CLOSE, callback_data=_admin(AdminAction.CLOSE)))
    return texts.ADMIN_MENU, builder.as_markup()


def archive_menu(courses: int, sessions: int, past: int) -> tuple[str, InlineKeyboardMarkup]:
    """Раздел «Архив»: убранные курсы, убранные из афиши проведения и прошедшие мероприятия - с числом записей."""
    archived, gone = SessionScope.ARCHIVED.value, SessionScope.PAST.value
    rows = [
        [InlineKeyboardButton(text=texts.BTN_ADMIN_ARCHIVE_COURSES.format(count=courses),
                              callback_data=_dict(DictKind.COURSES, DictAction.LIST, value=archived))],
        [InlineKeyboardButton(text=texts.BTN_ADMIN_ARCHIVE_SESSIONS.format(count=sessions),
                              callback_data=_dict(DictKind.SESSIONS, DictAction.LIST, value=archived))],
        [InlineKeyboardButton(text=texts.BTN_ADMIN_ARCHIVE_PAST.format(count=past),
                              callback_data=_dict(DictKind.SESSIONS, DictAction.LIST, value=gone))],
        [InlineKeyboardButton(text=texts.BTN_ADMIN_BACK, callback_data=_admin(AdminAction.MENU))],
    ]
    return (texts.ADMIN_ARCHIVE.format(courses=courses, sessions=sessions, past=past),
            InlineKeyboardMarkup(inline_keyboard=rows))


def archive_section_empty(title: str) -> tuple[str, InlineKeyboardMarkup]:
    """Пустой раздел архива (например, курсов в архиве нет) - только путь назад."""
    return texts.ADMIN_ARCHIVE_SECTION_EMPTY.format(title=title), InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_ADMIN_TO_ARCHIVE, callback_data=_admin(AdminAction.ARCHIVE)),
    ]])


# --- Список ----------------------------------------------------------------------------------------------------

def dict_list(spec: DictionarySpec, entries: list[ListEntry], page: int) -> tuple[str, InlineKeyboardMarkup]:
    """Список записей справочника. Курсы и проведения показываются иначе: курсы листаются стрелками
    (course_card_markup; сюда попадает только пустой раздел курсов), проведения - по месяцам (session_months)."""
    kind = spec.kind
    title = texts.ADMIN_DICT_TITLES[kind.value]
    columns = spec.list_columns
    page_size = PAGE_SIZE * columns  # строк на странице столько же, кнопок - по числу столбцов
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


def session_months(months: list[tuple[int, int]], scope: SessionScope = SessionScope.ACTIVE,
                   page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """Проведения раздела по месяцам: кнопки «Октябрь 2026 · 3» в два столбца, только месяцы, где проведения
    есть, и отдельная кнопка «⏳ Без дат · N».

    months - [(ГГГГММ, сколько проведений)], без дат - под ключом NO_DATES. Раздел «Проведения» (афиша) -
    месяцы по порядку и «➕ Добавить»; в архиве и прошедших свежие месяцы первыми, назад - «К архиву».
    Внутри месяца проведения листаются стрелками, как афиша (session_card_markup).
    """
    kind = DictKind.SESSIONS
    total = sum(count for _, count in months)
    dated = [(month, count) for month, count in months if month != NO_DATES]
    without_dates = dict(months).get(NO_DATES, 0)
    if scope is SessionScope.ACTIVE:
        template = texts.ADMIN_SESSION_MONTHS if months else texts.ADMIN_SESSION_MONTHS_NONE
        text = template.format(title=texts.ADMIN_DICT_TITLES[kind.value], total=total)
    else:
        dated.reverse()
        title = (texts.ADMIN_ARCHIVE_PAST_TITLE if scope is SessionScope.PAST else texts.ADMIN_ARCHIVE_SESSIONS_TITLE)
        template = texts.ADMIN_ARCHIVE_MONTHS if months else texts.ADMIN_ARCHIVE_SECTION_EMPTY
        text = template.format(title=title, total=total)

    page_size = PAGE_SIZE * 2
    pages = max((len(dated) + page_size - 1) // page_size, 1)
    page = min(max(page, 0), pages - 1)
    buttons = [InlineKeyboardButton(text=texts.BTN_ADMIN_MONTH.format(month=format_month(month), count=count),
                                    callback_data=_dict(kind, DictAction.MONTH, page=month, value=scope.value))
               for month, count in dated[page * page_size:(page + 1) * page_size]]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    if pages > 1:
        rows.append(_pager(page, pages, _dict(kind, DictAction.LIST, page=page - 1, value=scope.value),
                           _dict(kind, DictAction.LIST, page=page + 1, value=scope.value)))
    if without_dates:
        rows.append([InlineKeyboardButton(text=texts.BTN_ADMIN_MONTH_NO_DATES.format(count=without_dates),
                                          callback_data=_dict(kind, DictAction.MONTH, page=NO_DATES,
                                                              value=scope.value))])
    if scope is SessionScope.ACTIVE:
        rows.append([InlineKeyboardButton(text=texts.BTN_ADMIN_ADD, callback_data=_dict(kind, DictAction.ADD))])
        rows.append([InlineKeyboardButton(text=texts.BTN_ADMIN_BACK, callback_data=_admin(AdminAction.MENU))])
    else:
        rows.append([InlineKeyboardButton(text=texts.BTN_ADMIN_TO_ARCHIVE, callback_data=_admin(AdminAction.ARCHIVE))])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


# --- Значения полей --------------------------------------------------------------------------------------------

def _value(field: DictField, value: Any, options: list[tuple[int, str]] | None = None) -> str:
    """Значение поля для карточки справочника и «Сейчас: …» при правке."""
    if value is None or value == "" or value == []:
        return "-"
    match field.kind:
        case FieldKind.HTML:
            return f"\n{value}"  # многострочный текст - с новой строки
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
            return "".join(f"\n• {safe(p['label'])} - {format_rub(p['amount'])}" for p in value)
    return safe(value)


def card_text(spec: DictionarySpec, values: Mapping[str, Any], is_active: bool = True) -> str:
    lines = [f"<b>{texts.ADMIN_DICT_ITEM[spec.kind.value]}</b>", ""]
    lines += [f"<b>{texts.ADMIN_FIELD_LABELS[f.name]}:</b> {_value(f, values.get(f.name))}" for f in spec.fields]
    if not is_active:
        lines += ["", texts.ADMIN_CARD_ARCHIVED]
    return "\n".join(lines)


# --- Карточки --------------------------------------------------------------------------------------------------

def _edit_buttons(spec: DictionarySpec, item_id: int, page: int) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for field in spec.fields:
        if field.editable:
            builder.button(text=texts.BTN_ADMIN_EDIT_FIELD.format(label=texts.ADMIN_FIELD_LABELS[field.name]),
                           callback_data=_dict(spec.kind, DictAction.EDIT, item_id, field.name, page))
    builder.adjust(2)
    return builder


def _back_to_list(builder: InlineKeyboardBuilder, kind: DictKind, page: int) -> None:
    builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_TO_LIST, callback_data=_dict(kind, DictAction.LIST, page=page)))


def _archive_button(kind: DictKind, item_id: int, active: bool, page: int = 0) -> InlineKeyboardButton:
    """«🗄 В архив» у действующей записи, «♻️ Вернуть из архива» - у архивной."""
    action, label = ((DictAction.ARCHIVE, texts.BTN_ADMIN_ARCHIVE) if active
                     else (DictAction.RESTORE, texts.BTN_ADMIN_RESTORE))
    return InlineKeyboardButton(text=label, callback_data=_dict(kind, action, item_id, page=page))


def _delete_button(kind: DictKind, item_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=texts.BTN_ADMIN_DELETE, callback_data=_dict(kind, DictAction.DELETE, item_id))


def card(spec: DictionarySpec, item_id: int, values: Mapping[str, Any], active: bool,
         page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """Карточка записи справочника: поля текстом, «✏️» у каждого, архив, к списку."""
    builder = _edit_buttons(spec, item_id, page)
    builder.row(_archive_button(spec.kind, item_id, active, page))
    _back_to_list(builder, spec.kind, page)
    return card_text(spec, values, active), builder.as_markup()


def course_card_markup(spec: DictionarySpec, item_id: int, has_program: bool, index: int = 0, total: int = 1,
                       prev_id: int = 0, next_id: int = 0, archived: bool = False) -> InlineKeyboardMarkup:
    """Под карточкой курса (фото + подпись): ◀️ n/N ▶️ по курсам раздела и действия.

    Раздел «Курсы» (библиотека): «✏️» у каждого поля, программа, «В архив», «➕ Новый курс». Афиша отсюда не
    меняется - проведений, дат и «показывать без дат» здесь нет. В архиве (archived) карточка «как была»:
    без правки, только «Вернуть из архива» и «Удалить навсегда». index/total - место курса среди курсов
    своего раздела, prev_id/next_id - соседи (0 - края).
    """
    builder = InlineKeyboardBuilder()
    builder.row(*_pager(index, total, _dict(spec.kind, DictAction.VIEW, prev_id),
                        _dict(spec.kind, DictAction.VIEW, next_id)))
    if not archived:
        builder.attach(_edit_buttons(spec, item_id, 0))
    if has_program:
        builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_PROGRAM,
                                         callback_data=_dict(spec.kind, DictAction.PROGRAM, item_id, page=0)))
    builder.row(_archive_button(spec.kind, item_id, not archived))
    if archived:
        builder.row(_delete_button(spec.kind, item_id))
        builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_TO_ARCHIVE, callback_data=_admin(AdminAction.ARCHIVE)))
    else:
        builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_NEW_COURSE,
                                         callback_data=_dict(spec.kind, DictAction.ADD)))
        builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_BACK, callback_data=_admin(AdminAction.MENU)))
    return builder.as_markup()


def session_card_markup(spec: DictionarySpec, item_id: int, status: str,
                        scope: SessionScope = SessionScope.ACTIVE, index: int = 0, total: int = 1,
                        prev_id: int = 0, next_id: int = 0) -> InlineKeyboardMarkup:
    """Под карточкой проведения (как в афише): ◀️ n/N ▶️ по проведениям того же месяца и раздела и действия.

    В афише (ACTIVE): «✏️» полей, статус в одно нажатие, «Скопировать», «В архив» - это и есть «убрать из афиши».
    В архиве (ARCHIVED): «Вернуть из архива», «Скопировать», «Удалить навсегда». В прошедших (PAST) вернуть
    нечего - только «Скопировать» (повторить с новыми датами) и «Удалить навсегда».
    index/total - место проведения среди проведений месяца, prev_id/next_id - соседи (0 - края).
    """
    kind = spec.kind
    builder = InlineKeyboardBuilder()
    builder.row(*_pager(index, total, _dict(kind, DictAction.VIEW, prev_id), _dict(kind, DictAction.VIEW, next_id)))
    copy = InlineKeyboardButton(text=texts.BTN_ADMIN_COPY, callback_data=_dict(kind, DictAction.COPY, item_id))
    if scope is SessionScope.ACTIVE:
        builder.attach(_edit_buttons(spec, item_id, 0))
        statuses = InlineKeyboardBuilder()
        for choice in spec.field("status").choices:
            mark = "✅ " if choice == status else ""
            statuses.button(text=mark + texts.ADMIN_CHOICE_LABELS["status"][choice],
                            callback_data=_dict(kind, DictAction.STATUS, item_id, value=choice))
        statuses.adjust(2)
        builder.attach(statuses)
        builder.row(copy)
        builder.row(_archive_button(kind, item_id, active=True))
    else:
        if scope is SessionScope.ARCHIVED:
            builder.row(_archive_button(kind, item_id, active=False))
        builder.row(copy)
        builder.row(_delete_button(kind, item_id))
    builder.row(InlineKeyboardButton(text=texts.BTN_ADMIN_TO_MONTHS,
                                     callback_data=_dict(kind, DictAction.LIST, value=scope.value)))
    return builder.as_markup()


def delete_confirm_markup(kind: DictKind, item_id: int) -> InlineKeyboardMarkup:
    """«Удалить навсегда?» - да или назад к карточке."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.BTN_ADMIN_DELETE_YES, callback_data=_dict(kind, DictAction.PURGE, item_id))],
        [InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL, callback_data=_dict(kind, DictAction.VIEW, item_id))],
    ])


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

    Выбор и запись справочника - кнопки вариантов (✅ у текущего); мультивыбор и категории тарифов -
    кнопки с ✅ у selected и «Готово». Курс проведения спрашивается не здесь, а карточками: course_pick.
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
        controls.append(InlineKeyboardButton(text=texts.ADMIN_SKIP_LABELS.get(field.name, texts.BTN_ADMIN_SKIP),
                                             callback_data=_dict(kind, DictAction.SKIP, field=field.name)))
    if not field.required and item_id and current not in (None, "", []):
        controls.append(InlineKeyboardButton(text=texts.ADMIN_CLEAR_LABELS.get(field.name, texts.BTN_ADMIN_CLEAR),
                                             callback_data=_dict(kind, DictAction.CLEAR, item_id, field.name, page)))
    controls.append(InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL,
                                         callback_data=_dict(kind, DictAction.CANCEL, item_id, page=page)))
    builder.row(*controls)
    return text, builder.as_markup()


def course_pick(spec: DictionarySpec, field: DictField, header: str, course_id: int, index: int, total: int,
                prev_id: int, next_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Шаг «Курс» мастера проведения. Курсы списком кнопок не показываем: карточка курса (фото + подпись, как
    в разделе «Курсы») листается стрелками ◀️ n/N ▶️, «✅ Выбрать этот курс» берёт показанный.

    Возвращает вступление к подписи карточки и кнопки под ней. index/total - место курса среди курсов
    библиотеки, prev_id/next_id - соседи (0 - края).
    """
    kind = spec.kind
    template = texts.ADMIN_COURSE_PICK if total > 1 else texts.ADMIN_COURSE_PICK_ONE
    rows = [
        _pager(index, total, _dict(kind, DictAction.BROWSE, field=field.name, value=str(prev_id)),
               _dict(kind, DictAction.BROWSE, field=field.name, value=str(next_id))),
        [InlineKeyboardButton(text=texts.BTN_ADMIN_PICK_COURSE,
                              callback_data=_dict(kind, DictAction.PICK, field=field.name, value=str(course_id)))],
        [InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL, callback_data=_dict(kind, DictAction.CANCEL))],
    ]
    return template.format(header=header, hint=_hint(spec, field)), InlineKeyboardMarkup(inline_keyboard=rows)


def course_pick_empty(spec: DictionarySpec, header: str) -> tuple[str, InlineKeyboardMarkup]:
    """Шаг «Курс», когда в библиотеке нет ни одного курса: выбирать не из чего - только «Отмена»."""
    return texts.ADMIN_COURSE_PICK_EMPTY.format(header=header), InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL, callback_data=_dict(spec.kind, DictAction.CANCEL)),
    ]])


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
    """Под предпросмотром курса и проведения (черновиков нет). Проведение сразу встаёт в афишу - «Опубликовать»;
    курс просто сохраняется в библиотеку."""
    label = texts.BTN_ADMIN_PUBLISH if kind is DictKind.SESSIONS else texts.BTN_ADMIN_SAVE
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=_dict(kind, DictAction.SAVE)),
        InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL, callback_data=_dict(kind, DictAction.CANCEL)),
    ]])
