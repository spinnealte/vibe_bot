from aiogram import Router

from stubbot.tg.handlers import (
    application,
    cabinet,
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
    # Всегда последним: ловит то, что не подошло остальным.
    root.include_router(fallback.router)
    return root
