"""Выбор из списка в админке по номеру (правило пользователя 26.10.2026).

Список - не кнопкой на каждую строку, а пронумерованными строками в тексте, по PAGE_SIZE на странице, со стрелками
◀️ n/N ▶️; нужную строку админ выбирает, написав её номер («17»). Номера сквозные (на второй странице - с 11).
Под списком - только стрелки и действия экрана («Искать ещё», «Назад»).

Список целиком (id по порядку номеров, строки, кнопки) лежит в FSM: номер относится к тому списку, который админ
видел, даже если за это время что-то добавилось. Что открыть по номеру, решает «открывалка» вида списка
(OPENERS, регистрируют модули админки); номер ловит handlers/admin_pick.py.
"""

import secrets
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from stubbot.tg import texts
from stubbot.tg.callbacks import PickCb
from stubbot.tg.screen import send_screen, show_screen
from stubbot.tg.states import AdminPick

PAGE_SIZE = 10
PICK_KEY = "pick"
MAX_NUMBERS = 50  # сколько номеров принимаем за раз в мультивыборе

Button = tuple[str, str]  # текст и callback_data


@dataclass
class PickList:
    kind: str  # вид списка - ключ OPENERS
    ids: list[int]  # что открывать: id по порядку номеров
    title: str  # заголовок экрана (HTML), над строками
    lines: list[str] = field(default_factory=list)  # строки (HTML); пусто - строки уже в тексте экрана
    hint: str = texts.PICK_HINT
    empty: str | None = None  # текст, когда строк нет
    buttons: list[list[Button]] = field(default_factory=list)  # ряды под стрелками
    context: dict[str, Any] = field(default_factory=dict)  # что нужно открывалке (откуда пришли и т. п.)
    token: str = field(default_factory=lambda: secrets.token_hex(4))

    def to_data(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "PickList":
        return cls(**{**data, "buttons": [[tuple(b) for b in row] for row in data.get("buttons", [])]})

    @property
    def pages(self) -> int:
        return max((len(self.lines) + PAGE_SIZE - 1) // PAGE_SIZE, 1)


def render(pick: PickList, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """Страница списка: заголовок, строки с номерами, подсказка; стрелки и кнопки экрана."""
    page = min(max(page, 0), pick.pages - 1)
    if not pick.ids and pick.empty:
        text = pick.empty
    elif not pick.lines:
        text = f"{pick.title}\n\n{pick.hint}"
    else:
        start = page * PAGE_SIZE
        lines = [texts.PICK_LINE.format(number=start + i, line=line)
                 for i, line in enumerate(pick.lines[start:start + PAGE_SIZE], start=1)]
        suffix = texts.PICK_PAGE.format(page=page + 1, pages=pick.pages) if pick.pages > 1 else ""
        text = f"{pick.title}{suffix}\n\n" + "\n".join(lines) + f"\n\n{pick.hint}"
    rows = []
    if pick.pages > 1:
        rows.append(pager(pick.token, page, pick.pages))
    rows += [[InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in pick.buttons]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def pager(token: str, page: int, pages: int) -> list[InlineKeyboardButton]:
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text=texts.BTN_PREV_PAGE, callback_data=PickCb(token=token, page=page - 1).pack()))
    row.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=PickCb(token=token).pack()))
    if page + 1 < pages:
        row.append(InlineKeyboardButton(text=texts.BTN_NEXT_PAGE, callback_data=PickCb(token=token, page=page + 1).pack()))
    return row


async def show(message: Message, state: FSMContext | None, pick: PickList, page: int = 0, in_place: bool = True,
               notice: str | None = None) -> Message:
    """Показать список и ждать номер. Пустой список - просто экран (номер ждать нечего)."""
    text, markup = render(pick, page)
    if notice:
        text = f"{notice}\n\n{text}"
    screen = await (show_screen if in_place else send_screen)(message, text, markup)
    await remember(state, pick)
    return screen


async def remember(state: FSMContext | None, pick: PickList) -> None:
    """Ждать номер из этого списка (строки которого уже показаны - в списке или прямо в карточке)."""
    if pick.ids and state is not None:
        await state.set_state(AdminPick.number)
        await state.update_data({PICK_KEY: pick.to_data()})


async def current(state: FSMContext) -> PickList | None:
    data = (await state.get_data()).get(PICK_KEY)
    return PickList.from_data(data) if data else None


def parse_number(raw: str, count: int) -> int | None:
    """«17» → 17, если в списке столько строк. Иначе None."""
    text = raw.strip().rstrip(".")
    if not text.isdigit():
        return None
    number = int(text)
    return number if 1 <= number <= count else None


def parse_numbers(raw: str, count: int) -> list[int] | None:
    """«1, 3 5» → [1, 3, 5] (без повторов, по порядку ввода). Любой номер вне списка - None."""
    parts = raw.replace(";", ",").replace(",", " ").split()
    if not parts or len(parts) > MAX_NUMBERS:
        return None
    numbers: list[int] = []
    for part in parts:
        number = parse_number(part, count)
        if number is None:
            return None
        if number not in numbers:
            numbers.append(number)
    return numbers


# Открывалки: вид списка → что сделать с выбранным id. Подпись: (message, state, session, client, settings,
# item_id, context). Поиск (context["requery"]) - ещё и «новый запрос», если написан не номер.
Opener = Callable[..., Awaitable[None]]
OPENERS: dict[str, Opener] = {}
REQUERIES: dict[str, Opener] = {}


def opener(kind: str) -> Callable[[Opener], Opener]:
    def register(func: Opener) -> Opener:
        OPENERS[kind] = func
        return func
    return register


def requery(kind: str) -> Callable[[Opener], Opener]:
    def register(func: Opener) -> Opener:
        REQUERIES[kind] = func
        return func
    return register
