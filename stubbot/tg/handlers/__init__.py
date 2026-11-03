from aiogram import F, Router
from aiogram.enums import ChatType

from stubbot.tg.handlers import (
    admin,
    admin_clients,
    admin_orders,
    admin_pick,
    app_only,
    application,
    cabinet,
    chats,
    consent,
    fallback,
    menu,
    payment,
    registration,
    schedule,
    selection,
    start,
)


def build_root_router(applications_enabled: bool, webapp_only: bool = False) -> Router:
    root = Router(name="root")
    # Только личные чаты: фильтр корневого роутера действует на все вложенные. В группе бот молчит,
    # иначе кабинет, списки админки и выгрузка клиентов оказались бы на виду у всех участников.
    root.message.filter(F.chat.type == ChatType.PRIVATE)
    root.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    root.include_router(chats.router)  # если бота добавили в группу - выходит из неё
    if webapp_only:
        # Всё - в приложении (WEBAPP_ONLY): /start - приветствие с кнопкой приложения, на остальное - подсказка.
        # Разделы бота (меню, кабинет, афиша, запись, админка) не подключаются, их код остаётся.
        root.include_router(start.router)
        root.include_router(app_only.router)
        return root
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
        # Онлайн-оплата: без ONLINE_PAYMENTS кнопок нет, а нажатие старой отвечает «недоступна».
        root.include_router(payment.router)
    # Фильтр «только админ» - на всём роутере; не-админам он не мешает: их апдейты идут дальше, в fallback.
    root.include_router(admin.router)
    # Номер строки из пронумерованного списка админки - после admin: «⚙️ Админка» открывает меню.
    root.include_router(admin_pick.router)
    if applications_enabled:
        # Заказы и клиенты есть только вместе с записью на курсы. После admin: «⚙️ Админка» посреди ввода суммы
        # или поиска открывает меню, а не считается ответом.
        root.include_router(admin_orders.router)
        root.include_router(admin_clients.router)
    # Всегда последним: ловит то, что не подошло остальным.
    root.include_router(fallback.router)
    return root
