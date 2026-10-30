"""Проверка самой обвязки: бот отвечает на /start через диспетчер и базу."""

from tests.e2e.harness import Harness
from stubbot.tg import texts


def test_start_and_registration(e2e) -> None:
    async def scenario(h: Harness) -> None:
        anna = h.person(5001, "Анна")
        await anna.register("+79990000001", "Иванова Анна Сергеевна")
        assert texts.BTN_SCHEDULE in anna.reply_keyboard()

    e2e.run(scenario)
