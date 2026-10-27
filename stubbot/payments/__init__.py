"""Онлайн-оплата: платёжные сервисы (полная версия, этап 1, шаг 7 - каркас; выключено до договора с ЮKassa).

create_gateway(settings) - по ONLINE_PAYMENTS: нет оплаты (off), тестовая без сервиса (fake) или ЮKassa (yookassa).
Настройки проверяются при запуске бота: ошибка в них - бот не стартует и пишет в лог, что не так (лучше, чем
кнопка «Оплатить», которая молча не работает).
"""

import logging

from stubbot.config import OnlinePayments, Settings
from stubbot.payments.fake import FakeGateway
from stubbot.payments.gateway import PaymentGateway, Receipt
from stubbot.payments.yookassa import YooKassaGateway

logger = logging.getLogger(__name__)

LOCAL_DB_HOSTS = ("localhost", "127.0.0.1", "::1")
VAT_CODES = range(1, 13)  # 1 - без НДС … 11 - 22%, 12 - 22/122 (с 01.01.2026); значение даёт бухгалтер
TAX_SYSTEM_CODES = range(1, 7)
# Чеки от ЮKassa не принимают частичную предоплату и аванс - онлайн всегда платится весь остаток.
PAYMENT_MODES = ("full_prepayment", "full_payment")


class GatewayConfigError(Exception):
    """Онлайн-оплата включена, но настроена неверно. Текст - для лога: что исправить в .env."""


def create_gateway(settings: Settings) -> PaymentGateway | None:
    match settings.online_payments:
        case OnlinePayments.OFF:
            return None
        case _ if not settings.applications_enabled:
            raise GatewayConfigError("ONLINE_PAYMENTS без записи на курсы не работает: включите APPLICATIONS_ENABLED "
                                     "или поставьте ONLINE_PAYMENTS=off")
        case OnlinePayments.FAKE:
            if settings.db_host not in LOCAL_DB_HOSTS:
                raise GatewayConfigError("ONLINE_PAYMENTS=fake - только на локальной базе: кнопка «Оплата прошла» "
                                         "закрепляет место без денег. На сервере - off или yookassa")
            logger.warning("ONLINE_PAYMENTS=fake: тестовая оплата без ЮKassa - кнопка «Оплата прошла» без денег")
            return FakeGateway()
        case OnlinePayments.YOOKASSA:
            if not settings.yookassa_shop_id or settings.yookassa_secret_key is None:
                raise GatewayConfigError("ONLINE_PAYMENTS=yookassa: заполните YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY")
            receipt_settings(settings)  # ошибка в настройках чека - сразу при запуске, а не на первой оплате
            return YooKassaGateway(settings.yookassa_shop_id, settings.yookassa_secret_key)


def receipt_settings(settings: Settings) -> dict | None:
    """Поля чека 54-ФЗ из настроек (без e-mail и названия - их даёт заказ). None - чек не передаём."""
    if not settings.yookassa_receipts:
        return None
    if settings.yookassa_vat_code not in VAT_CODES:
        raise GatewayConfigError("YOOKASSA_RECEIPTS=true: укажите YOOKASSA_VAT_CODE (1-12, ставку даёт бухгалтер)")
    if settings.yookassa_tax_system_code is not None and settings.yookassa_tax_system_code not in TAX_SYSTEM_CODES:
        raise GatewayConfigError("YOOKASSA_TAX_SYSTEM_CODE - число 1-6 или пусто")
    if settings.yookassa_payment_mode not in PAYMENT_MODES:
        raise GatewayConfigError(f"YOOKASSA_PAYMENT_MODE - одно из: {', '.join(PAYMENT_MODES)}")
    return dict(vat_code=settings.yookassa_vat_code, payment_mode=settings.yookassa_payment_mode,
                payment_subject=settings.yookassa_payment_subject,
                tax_system_code=settings.yookassa_tax_system_code)


def build_receipt(settings: Settings, email: str, item: str) -> Receipt | None:
    """Чек для запроса платежа. Вызывать при receipts_enabled: в режиме fake настройки чека не проверялись."""
    fields = receipt_settings(settings)
    return Receipt(email=email, item=item, **fields) if fields is not None else None


def receipts_enabled(settings: Settings) -> bool:
    """Чек 54-ФЗ передаётся с каждым платежом ЮKassa. Он уходит покупателю на e-mail: без почты в профиле
    онлайн-оплату не начинаем - сначала спросим её."""
    return settings.online_payments is OnlinePayments.YOOKASSA and settings.yookassa_receipts
