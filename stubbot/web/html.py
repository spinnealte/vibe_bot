"""HTML Telegram (программа курса, «О курсе», тексты согласий, «О центре») → HTML для веб-страницы.

В Telegram текст - это HTML с переводами строк. На странице оставляем только безопасные теги той же разметки
(жирный, курсив, ссылки http/https/tg, код, цитата, спойлер), переводы строк - в <br>. Всё остальное - текстом:
даже если в программу курса попадёт <script>, страница покажет его как буквы.

Обратно (to_telegram_html) - текст, который админ набрал в приложении: из него остаётся только разметка Telegram.
"""

import re
from html import escape
from html.parser import HTMLParser

_SIMPLE = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "blockquote"}
_SAFE_LINK = ("https://", "http://", "tg://")


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str | None] = []  # что открыто на странице для каждого открывающего тега (None - выброшен)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        emitted = None
        if tag in _SIMPLE:
            emitted = tag
            self.out.append(f"<{tag}>")
        elif tag == "a":
            href = (dict(attrs).get("href") or "").strip()
            if href.startswith(_SAFE_LINK):
                emitted = "a"
                self.out.append(f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">')
        elif tag == "tg-spoiler" or (tag == "span" and dict(attrs).get("class") == "tg-spoiler"):
            emitted = "span"
            self.out.append('<span class="spoiler">')
        self.open.append(emitted)

    def handle_endtag(self, tag: str) -> None:
        if self.open:
            emitted = self.open.pop()
            if emitted:
                self.out.append(f"</{emitted}>")

    def handle_data(self, data: str) -> None:
        self.out.append(escape(data, quote=False).replace("\n", "<br>"))

    def result(self) -> str:
        self.close()
        tail = [f"</{tag}>" for tag in reversed(self.open) if tag]  # незакрытые теги - закрываем
        return "".join(self.out + tail)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def to_web_html(telegram_html: str | None) -> str:
    """Безопасный HTML для страницы. Пусто - пустая строка."""
    if not telegram_html:
        return ""
    sanitizer = _Sanitizer()
    sanitizer.feed(telegram_html)
    return sanitizer.result()


def to_plain(telegram_html: str | None) -> str:
    """Текст бота без разметки (сообщения об ошибках, всплывающие подсказки приложения)."""
    if not telegram_html:
        return ""
    parser = _Text()
    parser.feed(telegram_html)
    parser.close()
    return "".join(parser.parts).strip()


# --- Обратно: редактор админки → HTML Telegram -------------------------------------------------------------------

_TO_TELEGRAM = {"b": "b", "strong": "b", "i": "i", "em": "i", "u": "u", "ins": "u", "s": "s", "strike": "s",
                "del": "s", "code": "code", "pre": "pre", "blockquote": "blockquote"}
_BLOCKS = {"div", "p", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "section", "article"}
_VOID = {"br", "hr", "img", "input", "meta", "link", "wbr", "col", "area", "base", "embed", "source", "track",
         "param"}
_HIDDEN = {"script", "style", "template", "head", "title"}  # их содержимое - не текст
_SPACES = re.compile(r"[ \t\r\n\f]+")
_BLANK = re.compile(r"\n{3,}")


class _ToTelegram(HTMLParser):
    """Разметка из поля с форматированием (contenteditable) → HTML, который понимает Telegram: жирный, курсив,
    подчёркнутый, зачёркнутый, код, цитата, спойлер, ссылки http/https/tg; абзацы и <br> - переводы строк.
    Теги всегда закрыты и не перекрещиваются - иначе бот не смог бы отправить такой текст."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[tuple[str, str | None]] = []  # (тег из редактора, что вывели или None)
        self.lists: list[list] = []  # [«ol»/«ul», номер пункта]
        self.line_start = True
        self.pending = False  # перед следующим текстом нужен перевод строки (кончился абзац)
        self.hidden = 0  # внутри <script>, <style>…

    def _emitted(self) -> set[str]:
        return {emitted for _, emitted in self.open if emitted}

    def _flush(self) -> None:
        if self.pending and not self.line_start:
            self.out.append("\n")
            self.line_start = True
        self.pending = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID:
            self.handle_startendtag(tag, attrs)
            return
        if tag in _HIDDEN:
            self.hidden += 1
            self.open.append((tag, None))
            return
        emitted = None
        inside = self._emitted()
        if tag in _BLOCKS or tag == "blockquote":
            self.pending = True
        if tag in ("ul", "ol"):
            self.lists.append([tag, 0])
        elif tag == "li":
            self._flush()
            marker = "• "
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] += 1
                marker = f"{self.lists[-1][1]}. "
            self.out.append(marker)
            self.line_start = False
        literal = inside & {"pre", "code"}  # внутри кода Telegram разметку не принимает
        if not literal:
            classes = (dict(attrs).get("class") or "").split()
            if tag in _TO_TELEGRAM and _TO_TELEGRAM[tag] not in inside:
                emitted = _TO_TELEGRAM[tag]
                self._flush()
                self.out.append(f"<{emitted}>")
            elif tag == "a" and "a" not in inside:
                href = (dict(attrs).get("href") or "").strip()
                if href.startswith(_SAFE_LINK) and " " not in href:
                    emitted = "a"
                    self._flush()
                    self.out.append(f'<a href="{escape(href, quote=True)}">')
            elif (tag == "tg-spoiler" or (tag == "span" and ({"spoiler", "tg-spoiler"} & set(classes)))) \
                    and "tg-spoiler" not in inside:
                emitted = "tg-spoiler"
                self._flush()
                self.out.append("<tg-spoiler>")
        self.open.append((tag, emitted))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("br", "hr"):
            self._flush()
            self.out.append("\n")
            self.line_start = True

    def handle_endtag(self, tag: str) -> None:
        if tag in _HIDDEN and self.hidden:
            self.hidden -= 1
        for index in range(len(self.open) - 1, -1, -1):
            if self.open[index][0] == tag:
                for _, emitted in reversed(self.open[index:]):
                    if emitted:
                        self.out.append(f"</{emitted}>")
                del self.open[index:]
                break
        if tag in ("ul", "ol") and self.lists:
            self.lists.pop()
        if tag in _BLOCKS or tag == "blockquote":
            self.pending = True

    def handle_data(self, data: str) -> None:
        if self.hidden:
            return
        data = data.replace("\xa0", " ")
        if not ({"pre", "code"} & self._emitted()):
            data = _SPACES.sub(" ", data)
            if self.line_start or self.pending:
                data = data.lstrip(" ")
        if not data:
            return
        self._flush()
        self.out.append(escape(data, quote=False))
        self.line_start = data.endswith("\n")

    def result(self) -> str:
        self.close()
        tail = [f"</{emitted}>" for _, emitted in reversed(self.open) if emitted]
        text = "".join(self.out + tail)
        lines = [line.rstrip(" ") for line in text.split("\n")]
        return _BLANK.sub("\n\n", "\n".join(lines)).strip()


def to_telegram_html(web_html: str | None) -> str:
    """HTML из поля с форматированием в приложении → HTML Telegram (так его хранит и шлёт бот). Пусто - ""."""
    if not web_html:
        return ""
    parser = _ToTelegram()
    parser.feed(web_html)
    return parser.result()
