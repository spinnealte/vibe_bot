"""Telegram вместо настоящего: принимает запросы бота, хранит сообщения чатов и проверяет каждое так же строго,
как Bot API (разметка HTML, длина текста, данные кнопок, правка и удаление сообщений, ответ на нажатие).

Что Telegram отклонил бы, бот получает как TelegramBadRequest / TelegramForbiddenError - как в жизни, а нарушение
ещё и записывается в violations: тест падает, даже если бот проглотил ошибку (strip_keyboard, delete_quietly).
"""

import itertools
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import unescape
from typing import Any

from aiogram import Bot
from aiogram.client.default import Default
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageCaption,
    EditMessageMedia,
    EditMessageReplyMarkup,
    EditMessageText,
    GetFile,
    GetMe,
    LeaveChat,
    SendChatAction,
    SendDocument,
    SendMessage,
    SendPhoto,
    TelegramMethod,
)
from aiogram.types import (
    BufferedInputFile,
    Chat,
    Document,
    File,
    FSInputFile,
    InlineKeyboardMarkup,
    InputFile,
    Message,
    PhotoSize,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    User,
)

BOT_ID = 123456
BOT_TOKEN = f"{BOT_ID}:" + "A" * 35
BOT_USERNAME = "aquastom_e2e_bot"

TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024
CALLBACK_DATA_LIMIT = 64  # байт UTF-8
CALLBACK_ANSWER_LIMIT = 200
# Теги HTML, которые понимает Telegram (Bot API, «HTML style»).
ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "span", "tg-spoiler", "a", "code", "pre",
                "blockquote", "tg-emoji"}
_TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)((?:\s+[^<>]*)?)\s*>")
_ENTITY = re.compile(r"&(lt|gt|amp|quot|#\d+|#x[0-9a-fA-F]+);")

_ids = itertools.count(1)


def new_file_id(kind: str) -> str:
    """file_id, который «выдал Telegram»: сквозной на весь прогон (бот кэширует file_id заглушки обложки)."""
    return f"{kind}-{next(_ids)}"


KNOWN_FILES: dict[str, bytes] = {}  # file_id → содержимое (файлы, которые «прислали» люди или бот)


def parse_html(html: str) -> str:
    """Текст, который увидит человек. ValueError - Telegram ответил бы «can't parse entities»."""
    stack: list[str] = []
    plain: list[str] = []
    position = 0
    for match in _TAG.finditer(html):
        plain.append(_check_text(html[position:match.start()]))
        closing, name, attrs = match.group(1), match.group(2).lower(), match.group(3) or ""
        if name not in ALLOWED_TAGS:
            raise ValueError(f"unsupported start tag {name!r}")
        if closing:
            if not stack or stack[-1] != name:
                raise ValueError(f"unexpected end tag {name!r}")
            stack.pop()
        else:
            if name == "a" and not re.search(r'href\s*=\s*"[^"]+"', attrs):
                raise ValueError("<a> без href")
            stack.append(name)
        position = match.end()
    plain.append(_check_text(html[position:]))
    if stack:
        raise ValueError(f"unclosed start tag {stack[-1]!r}")
    return "".join(plain)


def _check_text(chunk: str) -> str:
    if "<" in chunk or ">" in chunk:
        raise ValueError(f"символ < или > вне тега: {chunk[:60]!r}")
    for amp in re.finditer("&", chunk):
        if not _ENTITY.match(chunk, amp.start()):
            raise ValueError(f"& вне сущности: {chunk[amp.start():amp.start() + 20]!r}")
    return unescape(chunk)


@dataclass
class StoredMessage:
    """Сообщение в чате, как его хранит Telegram."""

    chat_id: int
    message_id: int
    from_bot: bool
    html: str | None = None  # текст или подпись в разметке бота
    plain: str | None = None  # что видит человек
    photo: str | None = None  # file_id
    document: str | None = None
    document_name: str | None = None
    markup: Any = None  # InlineKeyboardMarkup, ReplyKeyboardMarkup...
    deleted: bool = False
    date: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_text(self) -> bool:
        return self.photo is None and self.document is None

    @property
    def inline(self) -> InlineKeyboardMarkup | None:
        return self.markup if isinstance(self.markup, InlineKeyboardMarkup) else None

    def buttons(self) -> list:
        markup = self.inline
        return [button for row in markup.inline_keyboard for button in row] if markup else []

    def to_message(self, bot: Bot) -> Message:
        user = User(id=BOT_ID, is_bot=True, first_name="АкваСтом", username=BOT_USERNAME)
        extra: dict[str, Any] = {}
        if self.photo is not None:
            extra["photo"] = [PhotoSize(file_id=self.photo, file_unique_id=self.photo, width=800, height=600)]
            extra["caption"] = self.plain
        elif self.document is not None:
            extra["document"] = Document(file_id=self.document, file_unique_id=self.document,
                                         file_name=self.document_name)
            extra["caption"] = self.plain
        else:
            extra["text"] = self.plain
        return Message(message_id=self.message_id, date=self.date, chat=Chat(id=self.chat_id, type="private"),
                       from_user=user, reply_markup=self.inline, **extra).as_(bot)


class FakeTelegram(BaseSession):
    """Сессия бота без сети. started - кто начинал диалог с ботом (иначе Telegram не даст ему написать)."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod] = []
        self.violations: list[str] = []
        self.chats: dict[int, list[StoredMessage]] = {}
        self.started: set[int] = set()
        self.blocked: set[int] = set()
        self.answers: dict[str, AnswerCallbackQuery] = {}
        self.sent_files: list[tuple[int, str, bytes]] = []  # (чат, имя файла, содержимое) - файлы от бота
        self.left_chats: list[int] = []
        self._message_ids = itertools.count(1000)

    # --- То, что тест читает -------------------------------------------------------------------------------------

    def messages(self, chat_id: int, from_bot: bool = True) -> list[StoredMessage]:
        return [m for m in self.chats.get(chat_id, []) if not m.deleted and m.from_bot == from_bot]

    def get(self, chat_id: int, message_id: int) -> StoredMessage | None:
        return next((m for m in self.chats.get(chat_id, []) if m.message_id == message_id), None)

    def add_user_message(self, chat_id: int, **fields: Any) -> StoredMessage:
        message = StoredMessage(chat_id=chat_id, message_id=next(self._message_ids), from_bot=False, **fields)
        self.chats.setdefault(chat_id, []).append(message)
        return message

    # --- BaseSession ---------------------------------------------------------------------------------------------

    async def close(self) -> None:
        pass

    async def stream_content(self, url: str, headers: dict | None = None, timeout: int = 30,
                             chunk_size: int = 65536, raise_for_status: bool = True):
        file_id = url.rsplit("/", 1)[-1]
        yield KNOWN_FILES[file_id]

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Any:
        self.requests.append(method)
        handler = getattr(self, f"_on_{type(method).__name__}", None)
        if handler is None:
            self._refuse(method, f"метод {type(method).__name__} не поддержан подставным Telegram")
        return handler(bot, method)

    # --- Проверки ------------------------------------------------------------------------------------------------

    def _refuse(self, method: TelegramMethod, reason: str, record: bool = True) -> None:
        if record:
            self.violations.append(f"{type(method).__name__}: {reason}")
        raise TelegramBadRequest(method=method, message=f"Bad Request: {reason}")

    def _check_chat(self, method: TelegramMethod, chat_id: int | str) -> int:
        chat_id = int(chat_id)
        if chat_id in self.blocked:
            raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
        if chat_id not in self.started:
            raise TelegramForbiddenError(method=method, message="Forbidden: bot can't initiate conversation with a user")
        return chat_id

    def _render(self, bot: Bot, method: TelegramMethod, text: str | None, limit: int, required: bool,
                source: Any = None) -> str | None:
        """source - где лежит parse_mode (у правки фото - в самом InputMediaPhoto)."""
        if text is None or text == "":
            if required:
                self._refuse(method, "message text is empty")
            return None
        parse_mode = getattr(source or method, "parse_mode", None)
        if isinstance(parse_mode, Default):
            parse_mode = bot.default[parse_mode.name]
        if parse_mode == "HTML":
            try:
                plain = parse_html(text)
            except ValueError as error:
                self._refuse(method, f"can't parse entities: {error}")
        else:
            plain = text
        if not plain.strip() and required:
            self._refuse(method, "message text is empty")
        if len(plain) > limit:
            self._refuse(method, f"message is too long ({len(plain)} > {limit})")
        return plain

    def _check_markup(self, method: TelegramMethod, markup: Any) -> None:
        if isinstance(markup, InlineKeyboardMarkup):
            for row in markup.inline_keyboard:
                for button in row:
                    if not button.text:
                        self._refuse(method, "пустой текст кнопки")
                    kinds = [k for k in ("callback_data", "url") if getattr(button, k) is not None]
                    if len(kinds) != 1:
                        self._refuse(method, f"кнопка {button.text!r}: нужен ровно один из callback_data / url")
                    if button.callback_data is not None:
                        size = len(button.callback_data.encode("utf-8"))
                        if not 1 <= size <= CALLBACK_DATA_LIMIT:
                            self._refuse(method, f"BUTTON_DATA_INVALID: {button.callback_data!r} ({size} байт)")
                    if button.url is not None and not button.url.startswith(("https://", "http://", "tg://")):
                        self._refuse(method, f"неверная ссылка кнопки: {button.url!r}")
        elif isinstance(markup, ReplyKeyboardMarkup):
            for row in markup.keyboard:
                for button in row:
                    if not button.text:
                        self._refuse(method, "пустой текст кнопки клавиатуры")

    def _file(self, method: TelegramMethod, value: str | InputFile, kind: str, chat_id: int) -> tuple[str, str | None]:
        if isinstance(value, BufferedInputFile):
            file_id = new_file_id(kind)
            KNOWN_FILES[file_id] = value.data
            self.sent_files.append((chat_id, value.filename, value.data))
            return file_id, value.filename
        if isinstance(value, FSInputFile):
            file_id = new_file_id(kind)
            KNOWN_FILES[file_id] = b""
            return file_id, value.filename
        if isinstance(value, str) and value in KNOWN_FILES:
            return value, None
        self._refuse(method, f"wrong file identifier {value!r}")

    def _store(self, chat_id: int, **fields: Any) -> StoredMessage:
        message = StoredMessage(chat_id=chat_id, message_id=next(self._message_ids), from_bot=True, **fields)
        self.chats.setdefault(chat_id, []).append(message)
        return message

    def _editable(self, method: TelegramMethod) -> StoredMessage:
        if getattr(method, "inline_message_id", None):
            self._refuse(method, "inline-сообщений у бота нет")
        message = self.get(int(method.chat_id), method.message_id)
        if message is None or message.deleted:
            self._refuse(method, "message to edit not found", record=False)
        if not message.from_bot:
            self._refuse(method, "message can't be edited")
        return message

    # --- Методы --------------------------------------------------------------------------------------------------

    def _on_GetMe(self, bot: Bot, method: GetMe) -> User:
        return User(id=BOT_ID, is_bot=True, first_name="АкваСтом", username=BOT_USERNAME)

    def _on_SendChatAction(self, bot: Bot, method: SendChatAction) -> bool:
        return True

    def _on_LeaveChat(self, bot: Bot, method: LeaveChat) -> bool:
        self.left_chats.append(int(method.chat_id))
        return True

    def _on_SendMessage(self, bot: Bot, method: SendMessage) -> Message:
        chat_id = self._check_chat(method, method.chat_id)
        plain = self._render(bot, method, method.text, TEXT_LIMIT, required=True)
        self._check_markup(method, method.reply_markup)
        markup = method.reply_markup
        return self._store(chat_id, html=method.text, plain=plain,
                           markup=None if isinstance(markup, ReplyKeyboardRemove) else markup).to_message(bot)

    def _on_SendPhoto(self, bot: Bot, method: SendPhoto) -> Message:
        chat_id = self._check_chat(method, method.chat_id)
        plain = self._render(bot, method, method.caption, CAPTION_LIMIT, required=False)
        self._check_markup(method, method.reply_markup)
        file_id, _ = self._file(method, method.photo, "photo", chat_id)
        return self._store(chat_id, html=method.caption, plain=plain, photo=file_id,
                           markup=method.reply_markup).to_message(bot)

    def _on_SendDocument(self, bot: Bot, method: SendDocument) -> Message:
        chat_id = self._check_chat(method, method.chat_id)
        plain = self._render(bot, method, method.caption, CAPTION_LIMIT, required=False)
        self._check_markup(method, method.reply_markup)
        file_id, name = self._file(method, method.document, "doc", chat_id)
        if file_id.startswith("photo-"):
            self._refuse(method, "type of file mismatch")  # file_id фото отправили как документ
        return self._store(chat_id, html=method.caption, plain=plain, document=file_id, document_name=name,
                           markup=method.reply_markup).to_message(bot)

    def _on_EditMessageText(self, bot: Bot, method: EditMessageText) -> Message:
        message = self._editable(method)
        if not message.is_text:
            self._refuse(method, "there is no text in the message to edit", record=False)
        plain = self._render(bot, method, method.text, TEXT_LIMIT, required=True)
        self._check_markup(method, method.reply_markup)
        if message.html == method.text and _same_markup(message.markup, method.reply_markup):
            self._refuse(method, "message is not modified", record=False)
        message.html, message.plain, message.markup = method.text, plain, method.reply_markup
        return message.to_message(bot)

    def _on_EditMessageCaption(self, bot: Bot, method: EditMessageCaption) -> Message:
        message = self._editable(method)
        if message.is_text:
            self._refuse(method, "there is no caption in the message to edit", record=False)
        plain = self._render(bot, method, method.caption, CAPTION_LIMIT, required=False)
        self._check_markup(method, method.reply_markup)
        message.html, message.plain, message.markup = method.caption, plain, method.reply_markup
        return message.to_message(bot)

    def _on_EditMessageMedia(self, bot: Bot, method: EditMessageMedia) -> Message:
        message = self._editable(method)
        if message.photo is None:
            self._refuse(method, "message can't be edited", record=False)  # текст в фото правкой не превратить
        media = method.media
        plain = self._render(bot, method, media.caption, CAPTION_LIMIT, required=False, source=media)
        self._check_markup(method, method.reply_markup)
        file_id, _ = self._file(method, media.media, "photo", message.chat_id)
        if (file_id == message.photo and message.html == media.caption
                and _same_markup(message.markup, method.reply_markup)):
            self._refuse(method, "message is not modified", record=False)
        message.photo, message.html, message.plain, message.markup = file_id, media.caption, plain, method.reply_markup
        return message.to_message(bot)

    def _on_EditMessageReplyMarkup(self, bot: Bot, method: EditMessageReplyMarkup) -> Message:
        message = self._editable(method)
        self._check_markup(method, method.reply_markup)
        if _same_markup(message.markup, method.reply_markup):
            self._refuse(method, "message is not modified", record=False)
        message.markup = method.reply_markup
        return message.to_message(bot)

    def _on_DeleteMessage(self, bot: Bot, method: DeleteMessage) -> bool:
        message = self.get(int(method.chat_id), method.message_id)
        if message is None or message.deleted:
            self._refuse(method, "message to delete not found", record=False)
        message.deleted = True
        return True

    def _on_AnswerCallbackQuery(self, bot: Bot, method: AnswerCallbackQuery) -> bool:
        if method.callback_query_id in self.answers:
            # Telegram принимает один ответ на нажатие; второй - ошибка «query is too old ... or query ID is invalid».
            self._refuse(method, "query ID is invalid (второй ответ на то же нажатие)")
        if method.text is not None and len(method.text) > CALLBACK_ANSWER_LIMIT:
            self._refuse(method, f"MESSAGE_TOO_LONG: ответ на нажатие {len(method.text)} > {CALLBACK_ANSWER_LIMIT}")
        self.answers[method.callback_query_id] = method
        return True

    def _on_GetFile(self, bot: Bot, method: GetFile) -> File:
        if method.file_id not in KNOWN_FILES:
            self._refuse(method, "wrong file_id")
        return File(file_id=method.file_id, file_unique_id=method.file_id, file_size=len(KNOWN_FILES[method.file_id]),
                    file_path=f"documents/{method.file_id}")


def _same_markup(old: Any, new: Any) -> bool:
    def dump(markup: Any) -> Any:
        if markup is None or (isinstance(markup, InlineKeyboardMarkup) and not markup.inline_keyboard):
            return None
        return markup.model_dump(exclude_none=True) if hasattr(markup, "model_dump") else markup

    return dump(old) == dump(new)
