"""Веб-приложение без базы: очистка HTML, подпись Telegram, ограничение частоты, картинки каталога."""

import json
import re
import time
from datetime import UTC, datetime, timedelta

import pytest

from stubbot.web.app import RateLimiter
from stubbot.web.auth import AuthError, phone_from_contact, user_from_init_data
from stubbot.web.files import image_type
from stubbot.web.html import to_plain, to_telegram_html, to_web_html
from stubbot.web.server import STATIC_DIR
from tests.e2e.telegram_stub import BOT_TOKEN, parse_html
from tests.e2e.web_client import contact, init_data, sign

TOKEN = "123456:" + "A" * 35


def test_web_html_keeps_telegram_markup_and_drops_the_rest() -> None:
    html = ('<b>День 1</b>\n<i>теория</i> <a href="https://example.com/a?b=1&c=2">сайт</a>'
            '<a href="javascript:alert(1)">плохая</a><script>alert(1)</script><img src=x onerror=alert(1)>'
            '<tg-spoiler>секрет</tg-spoiler><blockquote>цитата')
    result = to_web_html(html)
    assert result.startswith("<b>День 1</b><br><i>теория</i> ")
    assert '<a href="https://example.com/a?b=1&amp;c=2" target="_blank" rel="noopener noreferrer">сайт</a>' in result
    assert "javascript" not in result and "<script" not in result and "<img" not in result and "onerror" not in result
    assert "плохая" in result  # текст ссылки остаётся, сама ссылка - нет
    assert '<span class="spoiler">секрет</span>' in result
    assert result.endswith("<blockquote>цитата</blockquote>")  # незакрытый тег закрыт
    assert to_web_html("1 < 2 & 3 > 2") == "1 &lt; 2 &amp; 3 &gt; 2"
    assert to_web_html(None) == "" and to_web_html("") == ""
    assert to_web_html('<a href="https://x.ru" onclick="steal()">x</a>') == (
        '<a href="https://x.ru" target="_blank" rel="noopener noreferrer">x</a>')
    assert to_web_html('<a href="https://x.ru/&quot;onmouseover=&quot;steal()">x</a>').count('"') == 6


def test_editor_html_becomes_telegram_markup() -> None:
    """Текст из редактора админки: только разметка Telegram, теги закрыты и не перекрещиваются (иначе бот
    не отправил бы его), абзацы - переводы строк."""
    cases = {
        "Строка 1<div>Строка 2</div><div><br></div><div>Строка 4</div>": "Строка 1\nСтрока 2\n\nСтрока 4",
        "<p><strong>Жирный</strong> и <em>курсив</em></p><p>Второй&nbsp;абзац</p>":
            "<b>Жирный</b> и <i>курсив</i>\nВторой абзац",
        '<a href="https://x.ru/?a=1&amp;b=2" onclick="x()">сайт</a> <a href="javascript:alert(1)">плохая</a>':
            '<a href="https://x.ru/?a=1&amp;b=2">сайт</a> плохая',
        "<ul><li>раз</li><li>два</li></ul><ol><li>один</li><li>два</li></ol>": "• раз\n• два\n1. один\n2. два",
        "<b>жир <i>курсив</b> хвост</i>": "<b>жир <i>курсив</i></b> хвост",
        "<b><b>двойной</b></b>": "<b>двойной</b>",
        '<span class="spoiler">тайна</span> 1 < 2 & 3': "<tg-spoiler>тайна</tg-spoiler> 1 &lt; 2 &amp; 3",
        "<pre><b>код</b>  с пробелами</pre>": "<pre>код  с пробелами</pre>",
        "<script>alert(1)</script><style>b{}</style><img src=x onerror=alert(1)>текст": "текст",
        "<div>   </div><div></div>": "",
        "<b>незакрытый": "<b>незакрытый</b>",
    }
    for source, expected in cases.items():
        result = to_telegram_html(source)
        assert result == expected, source
        parse_html(result)  # Telegram принял бы такую разметку
    assert to_telegram_html(None) == "" and to_telegram_html("") == ""
    # Туда и обратно: текст бота → редактор → тот же текст бота.
    telegram = '<b>День 1</b>\nТеория\n\n<i>День 2</i> <a href="https://x.ru">ссылка</a>'
    assert to_telegram_html(to_web_html(telegram)) == telegram


def test_plain_text_of_bot_messages() -> None:
    assert to_plain("✅ <b>Реквизиты получили!</b> Заказ &amp; счёт") == "✅ Реквизиты получили! Заказ & счёт"
    assert to_plain(None) == ""


def test_init_data_signature_and_freshness() -> None:
    now = datetime.now(UTC)
    user = user_from_init_data(init_data(42, "Анна", start_param="ref_abc", token=TOKEN), TOKEN)
    assert (user.id, user.first_name, user.start_param, user.dev) == (42, "Анна", "ref_abc", False)
    with pytest.raises(AuthError):
        user_from_init_data(init_data(42, token=TOKEN), "999:" + "B" * 35)  # подписано другим ботом
    with pytest.raises(AuthError):
        user_from_init_data(init_data(42, token=TOKEN).replace("42", "43"), TOKEN)
    old = int((now - timedelta(days=2)).timestamp())
    with pytest.raises(AuthError):
        user_from_init_data(init_data(42, auth_date=old, token=TOKEN), TOKEN)
    future = int((now + timedelta(hours=1)).timestamp())
    with pytest.raises(AuthError):
        user_from_init_data(init_data(42, auth_date=future, token=TOKEN), TOKEN)
    with pytest.raises(AuthError):
        user_from_init_data(sign({"auth_date": str(int(now.timestamp()))}, TOKEN), TOKEN)  # без пользователя
    with pytest.raises(AuthError):
        user_from_init_data("", TOKEN)


def test_contact_must_be_signed_and_own() -> None:
    assert phone_from_contact(_contact(7, "+79990001122"), TOKEN, 7) == "+79990001122"
    with pytest.raises(AuthError):
        phone_from_contact(_contact(7, "+79990001122", owner=8), TOKEN, 7)  # чужой контакт
    with pytest.raises(AuthError):
        phone_from_contact(_contact(7, "+79990001122").replace("1122", "3344"), TOKEN, 7)


def _contact(user_id: int, phone: str, owner: int | None = None) -> str:
    """Контакт, подписанный тестовым токеном этого файла (web_client.contact подписывает токеном сценариев)."""
    data = {"user_id": owner or user_id, "phone_number": phone}
    return sign({"auth_date": str(int(time.time())), "contact": json.dumps(data, separators=(",", ":"))}, TOKEN)


def test_rate_limiter_window() -> None:
    clock = [0.0]
    limiter = RateLimiter(limit=2, window=10, clock=lambda: clock[0])
    assert limiter.allow(1) and limiter.allow(1) and not limiter.allow(1)
    assert limiter.allow(2)  # у каждого своё окно
    clock[0] = 10.5
    assert limiter.allow(1)


def test_only_images_are_served_as_covers() -> None:
    assert image_type(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert image_type(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert image_type(b"%PDF-1.4") is None
    assert image_type(b"<svg onload=alert(1)>") is None


def test_web_client_contact_helper_is_signed() -> None:
    assert phone_from_contact(contact(5, "+79990000000"), BOT_TOKEN, 5) == "+79990000000"


def test_built_interface_is_in_repo() -> None:
    """Amvera собирает только Python: сборка интерфейса лежит в репозитории, и её index.html ссылается на свои файлы.
    Иначе по адресу приложения - пустая страница."""
    index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assets = re.findall(r'(?:src|href)="/(assets/[^"]+)"', index)
    assert len(assets) >= 2 and all((STATIC_DIR / asset).is_file() for asset in assets)
