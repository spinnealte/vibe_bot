from aiogram import Router

from stubbot.tg.handlers import consent, fallback, menu, registration, start


def build_root_router() -> Router:
    root = Router(name="root")
    # Порядок важен: /start и меню работают в любом состоянии и должны перехватывать ввод раньше сценариев.
    root.include_router(start.router)
    root.include_router(menu.router)
    root.include_router(consent.router)
    root.include_router(registration.router)
    # Всегда последним: ловит то, что не подошло остальным.
    root.include_router(fallback.router)
    return root
