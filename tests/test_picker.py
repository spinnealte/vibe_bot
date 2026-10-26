"""Выбор из списка в админке по номеру (tg/picker.py, правило 26.10.2026): страницы, номера, открывалки.
Без БД и Telegram."""

import asyncio
from types import SimpleNamespace

from stubbot.services.admin_dictionaries import DictKind, FieldKind
from stubbot.tg import picker, texts
from stubbot.tg.callbacks import PickCb
from stubbot.tg.handlers import admin, admin_pick
from stubbot.tg.picker import PICK_KEY, PickList
from stubbot.tg.states import AdminPick


def _pick(count: int = 23, **kw) -> PickList:
    fields = dict(kind="test", ids=list(range(101, 101 + count)), title="<b>Список</b>",
                  lines=[f"Строка {i}" for i in range(1, count + 1)], buttons=[[("Назад", "back")]],
                  context={"back": "f"}, token="tok")
    return PickList(**{**fields, **kw})


def _rows(markup) -> list[list[str]]:
    return [[b.text for b in row] for row in markup.inline_keyboard]


def test_numbers_are_parsed_strictly() -> None:
    assert picker.parse_number("17", 23) == 17 and picker.parse_number(" 3. ", 23) == 3
    for raw in ("0", "24", "-1", "3а", "", "1 2"):
        assert picker.parse_number(raw, 23) is None
    assert picker.parse_numbers("1, 3 5;3", 6) == [1, 3, 5]  # без повторов, по порядку ввода
    assert picker.parse_numbers("1, 9", 6) is None and picker.parse_numbers(" ", 6) is None


def test_pages_with_global_numbers_and_only_navigation_buttons() -> None:
    text, markup = picker.render(_pick(), page=0)
    assert text.startswith("<b>Список</b>\n<i>Страница 1 из 3</i>\n\n<b>1.</b> Строка 1")
    assert "<b>10.</b> Строка 10" in text and "11." not in text and text.endswith(texts.PICK_HINT)
    assert _rows(markup) == [["1/3", "▶️"], ["Назад"]]  # на каждую строку кнопки нет
    text, markup = picker.render(_pick(), page=2)
    assert "<b>21.</b> Строка 21" in text and "<b>23.</b> Строка 23" in text  # номера сквозные
    assert _rows(markup)[0] == ["◀️", "3/3"]
    assert PickCb.unpack(markup.inline_keyboard[0][0].callback_data) == PickCb(token="tok", page=1)
    assert "Страница 3 из 3" in picker.render(_pick(), page=99)[0]  # страница за краем - последняя
    text, markup = picker.render(_pick(count=5))
    assert "Страница" not in text and _rows(markup) == [["Назад"]]  # одна страница - без стрелок


def test_empty_and_embedded_lists() -> None:
    text, _ = picker.render(_pick(count=0, empty="Пусто"))
    assert text == "Пусто"
    embedded = PickList(kind="order", ids=[7], title="Карточка")  # строки уже в тексте экрана
    assert picker.render(embedded)[0] == f"Карточка\n\n{texts.PICK_HINT}"
    assert PickList.from_data(_pick().to_data()) == _pick()


class _State:
    def __init__(self, data: dict | None = None, state=None) -> None:
        self.data, self.state = dict(data or {}), state

    async def get_data(self) -> dict:
        return dict(self.data)

    async def update_data(self, values: dict) -> None:
        self.data.update(values)

    async def set_state(self, state) -> None:
        self.state = state

    async def clear(self) -> None:
        self.data, self.state = {}, None


def test_remember_waits_for_a_number_only_when_there_is_something_to_pick() -> None:
    state = _State()
    asyncio.run(picker.remember(state, _pick()))
    assert state.state is AdminPick.number and state.data[PICK_KEY]["ids"][0] == 101
    empty = _State()
    asyncio.run(picker.remember(empty, _pick(count=0)))
    assert empty.state is None and PICK_KEY not in empty.data
    asyncio.run(picker.remember(None, _pick()))  # без состояния (курсы, проведения) - ничего не ломается


class _Message:
    def __init__(self, text: str) -> None:
        self.text, self.answers = text, []

    async def answer(self, text, reply_markup=None):
        self.answers.append(text)


def _on_number(monkeypatch, text: str, pick: PickList | None = _pick(), requery: bool = False):
    opened, searched = [], []

    async def opener(message, state, session, client, settings, item_id, context):
        opened.append((item_id, context))

    async def search(message, state, session, client, settings, query, context):
        searched.append(query)

    monkeypatch.setitem(picker.OPENERS, "test", opener)
    monkeypatch.setitem(picker.REQUERIES, "test", search)
    if pick is not None and requery:
        pick = PickList(**{**pick.to_data(), "context": {**pick.context, "requery": True}})
    state = _State({PICK_KEY: pick.to_data()} if pick else {}, AdminPick.number)
    message = _Message(text)
    asyncio.run(admin_pick.on_number(message, state, None, None, None))
    return opened, searched, message.answers, state


def test_number_opens_that_line_with_its_page(monkeypatch) -> None:
    opened, _, answers, _ = _on_number(monkeypatch, "17")
    assert opened == [(117, {"back": "f", "page": 1})] and not answers  # 17-я строка - на второй странице


def test_wrong_number_and_new_search_query(monkeypatch) -> None:
    opened, searched, answers, _ = _on_number(monkeypatch, "24")
    assert not opened and not searched and answers == [texts.PICK_INVALID.format(count=23)]
    opened, searched, answers, _ = _on_number(monkeypatch, "Иванов", requery=True)
    assert searched == ["Иванов"] and not opened and not answers  # в поиске не номер - новый запрос
    _, _, answers, state = _on_number(monkeypatch, "5", pick=None)
    assert answers == [texts.PICK_EXPIRED] and state.state is None  # список потерялся - просим открыть заново


class _Callback:
    def __init__(self) -> None:
        self.answered: list = []
        self.message = SimpleNamespace()

    async def answer(self, text=None, show_alert=False):
        self.answered.append(text)


def test_arrows_of_an_old_list_do_not_page_the_new_one() -> None:
    callback = _Callback()
    state = _State({PICK_KEY: _pick(token="new").to_data()}, AdminPick.number)
    asyncio.run(admin_pick.on_page(callback, PickCb(token="old", page=1), state))
    assert callback.answered == [texts.PICK_EXPIRED]
    counter = _Callback()
    asyncio.run(admin_pick.on_page(counter, PickCb(token="new"), state))
    assert counter.answered == [None]  # счётчик «2/3» - просто ответ на нажатие


# --- Мастер: площадка и мультивыбор номерами ----------------------------------------------------------------------

def _wizard(monkeypatch, kind: FieldKind, text: str, options=((5, "Класс"), (6, "Клиника"), (7, "Зал"))):
    accepted, answers = [], []
    field = SimpleNamespace(kind=kind, name="venue_id")

    class _Service:
        async def options(self, field, selected):
            return list(options)

    async def accept(message, state, session, client, value, in_place):
        accepted.append(value)
        return "ok"

    async def reply(message, result):
        return None

    monkeypatch.setattr(admin, "_service", lambda session, kind, client: _Service())
    monkeypatch.setattr(admin, "_accept", accept)
    monkeypatch.setattr(admin, "_reply_result", reply)
    message = _Message(text)
    data = {"kind": DictKind.SESSIONS.value, admin.CHOICE_IDS_KEY: [5, 6, 7], "selected": []}
    asyncio.run(admin._numbers_given(message, _State(data), None, None, data, field))
    return accepted, message.answers


def test_wizard_takes_numbers_from_the_list_in_the_question(monkeypatch) -> None:
    assert _wizard(monkeypatch, FieldKind.REF, "2") == ([6], [])
    assert _wizard(monkeypatch, FieldKind.MULTI, "3, 1") == ([[7, 5]], [])
    accepted, answers = _wizard(monkeypatch, FieldKind.REF, "4")
    assert not accepted and answers == [texts.PICK_FIELD_INVALID_ONE.format(count=3)]
    accepted, answers = _wizard(monkeypatch, FieldKind.MULTI, "1, нет")
    assert not accepted and answers == [texts.PICK_FIELD_INVALID_MANY.format(count=3)]
    # Вариант ушёл в архив, пока админ отвечал, - номер больше не годится.
    accepted, answers = _wizard(monkeypatch, FieldKind.REF, "3", options=((5, "Класс"), (6, "Клиника")))
    assert not accepted and answers == [texts.ADMIN_NOT_FOUND]
