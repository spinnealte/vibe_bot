from aiogram import F, Router
from aiogram.enums import ChatType

from stubbot.tg.handlers import (
    admin,
    application,
    cabinet,
    chats,
    consent,
    fallback,
    menu,
    registration,
    schedule,
    selection,
    start,
)


def build_root_router(applications_enabled: bool) -> Router:
    root = Router(name="root")
    # Только личные чаты: фильтр корневого роутера действует на все вложенные. В группе бот молчит,
    # иначе кабинет, списки админки и выгрузка клиентов оказались бы на виду у всех участников.
    root.message.filter(F.chat.type == ChatType.PRIVATE)
    root.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    root.include_router(chats.router)  # если бота добавили в группу — выходит из неё
    # Порядок важен: /start и меню работают в любом состоянии и должны перехватывать ввод раньше сценариев.
    root.include_router(start.router)
    root.include_router(menu.router)
    root.include_router(consent.router)
    root.include_router(selection.router)
    root.include_router(registration.router)
    root.include_router(cabinet.router)
    root.include_router(schedule.router)
    if applications_enabled:
        root.include_router(application.router)
    # Фильтр «только админ» — на всём роутере; не-админам он не мешает: их апдейты идут дальше, в fallback.
    root.include_router(admin.router)
    # Всегда последним: ловит то, что не подошло остальным.
    root.include_router(fallback.router)
    return root
