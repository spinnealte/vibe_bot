"""Экраны раздела «🧾 Заказы» и уведомления админам о записях (полная версия, этап 1, шаг 4).

Текст + клавиатура, как в admin_views.py. Строки клиентов и админов экранируются (safe); суммы - format_rub.
"""

from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from stubbot.db.enums import CANCELLED_ENROLLMENT_STATUSES, MANUAL_PAYMENT_METHODS, OrderStatus, PayerType
from stubbot.db.models import Client, Order, PriceOption
from stubbot.services.admin_orders import AdminOrderCard, OrderCategory
from stubbot.services.enrollments import CancelResult, MyApplication
from stubbot.services.reminders import ReminderJob, ReminderKind
from stubbot.services.schedule import SessionCard
from stubbot.tg import texts
from stubbot.tg.callbacks import (
    AdminAction,
    AdminCb,
    ClientAdminAction,
    ClientAdminCb,
    OrderAdminAction,
    OrderAdminCb,
)
from stubbot.tg.formatting import safe
from stubbot.tg.outgoing import Delivery
from stubbot.tg.picker import PickList
from stubbot.tg.render import dates_text, order_amount, price_label, session_title
from stubbot.utils.dates import format_moment
from stubbot.utils.money import format_rub
from stubbot.utils.names import short_name

NEW_SCREEN = "n"  # value кнопки «Открыть заказ» под уведомлением: карточку - новым сообщением
TOKEN_SEPARATOR = "."  # value кнопки способа оплаты: «invoice.<ключ>»
BACK_ORDER = "o"  # карточка клиента открыта из заказа: value «o<id заказа>» - туда и вернёт «Назад»
PICK_ORDER = "order"  # вид пронумерованного списка (tg/picker.py): номер открывает заказ

Screen = tuple[str, InlineKeyboardMarkup]


def _cb(action: OrderAdminAction, order_id: int = 0, value: str = "", page: int = 0) -> str:
    return OrderAdminCb(action=action, order_id=order_id, value=value, page=page).pack()


def _button(text: str, action: OrderAdminAction, order_id: int = 0, value: str = "",
            page: int = 0) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text, callback_data=_cb(action, order_id, value, page))]


def _markup(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def person(client: Client) -> str:
    return safe(client.full_name)


def phone(client: Client) -> str:
    return safe(client.phone)


def _short(client: Client) -> str:
    return safe(short_name(client.full_name)) if client.full_name else safe(None)


def _to_card(order_id: int) -> list[InlineKeyboardButton]:
    return _button(texts.BTN_ADMIN_ORDER_TO_CARD, OrderAdminAction.VIEW, order_id)


def _cancel(order_id: int) -> list[InlineKeyboardButton]:
    return _button(texts.BTN_ADMIN_CANCEL, OrderAdminAction.CANCEL, order_id)


def _yes_no(yes: str, action: OrderAdminAction, order_id: int) -> InlineKeyboardMarkup:
    return _markup([
        InlineKeyboardButton(text=yes, callback_data=_cb(action, order_id)),
        InlineKeyboardButton(text=texts.BTN_ADMIN_ORDER_NO, callback_data=_cb(OrderAdminAction.VIEW, order_id)),
    ])


# --- Разделы и список ------------------------------------------------------------------------------------------

def orders_menu(counts: dict[OrderCategory, int]) -> Screen:
    late = counts[OrderCategory.OVERDUE]
    text = texts.ADMIN_ORDERS.format(
        wait=counts[OrderCategory.AWAITING], late=texts.ADMIN_ORDERS_LATE.format(count=late) if late else "",
        part=counts[OrderCategory.PARTIAL], paid=counts[OrderCategory.PAID], money=counts[OrderCategory.MONEY],
    )
    rows = [_button(texts.BTN_ADMIN_ORDER_CATEGORIES[category.value].format(count=counts[category]),
                    OrderAdminAction.LIST, value=category.value) for category in OrderCategory]
    rows.append(_button(texts.BTN_ADMIN_ORDER_FIND, OrderAdminAction.FIND))
    rows.append([InlineKeyboardButton(text=texts.BTN_ADMIN_BACK, callback_data=AdminCb(action=AdminAction.MENU).pack())])
    return text, _markup(*rows)


def order_list(category: OrderCategory, rows: list[tuple[Order, Client]]) -> PickList:
    """Заказы раздела - пронумерованным списком «№100001 · Петров П. П. · 80 000 ₽» (правило 26.10.2026):
    заказ открывают номером строки."""
    title = texts.ADMIN_ORDER_CATEGORY_TITLES[category.value]
    hint = texts.ADMIN_ORDER_LIST_PAID_HINT if category is OrderCategory.PAID else ""
    return PickList(
        kind=PICK_ORDER,
        ids=[order.id for order, _ in rows],
        title=texts.ADMIN_ORDER_LIST.format(title=title, count=len(rows), hint=hint),
        lines=[texts.ADMIN_ORDER_ROW.format(
            number=order.number, name=safe(short_name(payer.full_name)) if payer.full_name else texts.NOT_SET,
            amount=format_rub(order.total_amount) if order.total_amount is not None else "?",
        ) for order, payer in rows],
        empty=texts.ADMIN_ORDER_LIST_EMPTY.format(title=title, hint=hint),
        buttons=[[(texts.BTN_ADMIN_TO_ORDERS, _cb(OrderAdminAction.MENU))]],
    )


def ask_number() -> Screen:
    return texts.ADMIN_ORDER_ASK_NUMBER, _markup(_button(texts.BTN_ADMIN_CANCEL, OrderAdminAction.MENU))


# --- Карточка --------------------------------------------------------------------------------------------------

def order_card(card: AdminOrderCard, timezone: str, now: datetime) -> Screen:
    order = card.order
    lines = [
        texts.ADMIN_ORDER_TITLE.format(number=order.number, status=texts.ADMIN_ORDER_STATUS[order.status.value]),
        texts.ADMIN_ORDER_COURSE.format(title=safe(session_title(card.session)), dates=dates_text(card.session)),
        texts.ADMIN_ORDER_PAYER.format(name=person(card.payer), phone=safe(card.payer.phone)),
    ]
    if len(card.participants) > 1:
        names = ", ".join(_short(person) + ("" if person.telegram_id else texts.ADMIN_ORDER_NOT_IN_BOT)
                          for _, person in card.participants)
        lines.append(texts.ADMIN_ORDER_PARTICIPANTS.format(names=names))
    lines += _money_lines(card, timezone, now)
    if order.status is not OrderStatus.CANCELLED:
        lines.append(_payer_type_line(order))
        lines.append(texts.ADMIN_ORDER_INVOICE_SENT.format(moment=format_moment(order.invoice_sent_at, timezone))
                     if order.invoice_sent_at else texts.ADMIN_ORDER_INVOICE_NONE)
    if card.advance > 0:
        lines.append(texts.ADMIN_ORDER_ADVANCE.format(amount=format_rub(card.advance)))
    lines.append(texts.ADMIN_ORDER_CREATED.format(moment=format_moment(order.created_at, timezone)))
    if order.status is OrderStatus.CANCELLED:
        lines += _cancel_lines(card, timezone)
    return "\n".join(lines), _card_markup(card, now)


def _money_lines(card: AdminOrderCard, timezone: str, now: datetime) -> list[str]:
    order = card.order
    lines = [texts.ADMIN_ORDER_PRICE.format(value=safe(price_label(card.price))) if card.price
             else texts.ADMIN_ORDER_PRICE.format(value=format_rub(order.total_amount))
             if order.total_amount is not None else texts.ADMIN_ORDER_TOTAL_OPEN]
    if card.paid:
        if order.total_amount is None:
            lines.append(texts.ADMIN_ORDER_PAID_OPEN.format(paid=format_rub(card.paid)))
        elif card.left:
            lines.append(texts.ADMIN_ORDER_PAID.format(paid=format_rub(card.paid), left=format_rub(card.left)))
        else:
            lines.append(texts.ADMIN_ORDER_PAID_FULL.format(paid=format_rub(card.paid)))
    elif card.payable:
        due = texts.ADMIN_ORDER_DUE.format(due=format_moment(order.due_at, timezone))
        lines.append(due + (texts.ADMIN_ORDER_DUE_LATE if card.overdue(now) else ""))
    return lines


def _payer_type_line(order: Order) -> str:
    if order.payer_type is PayerType.INDIVIDUAL:
        return texts.ADMIN_ORDER_INDIVIDUAL
    if order.payer_type is PayerType.ORGANIZATION:
        return texts.ADMIN_ORDER_ORGANIZATION if order.requisites_file_id else texts.ADMIN_ORDER_ORGANIZATION_WAITING
    return texts.ADMIN_ORDER_PAYER_TYPE_NONE


def _cancel_lines(card: AdminOrderCard, timezone: str) -> list[str]:
    order = card.order
    lines = [texts.ADMIN_ORDER_CANCELLED.format(moment=format_moment(order.cancelled_at, timezone),
                                                reason=safe(order.cancel_reason))]
    if order.cancel_choice is not None:
        lines.append(texts.ADMIN_ORDER_CLIENT_CHOICE.format(choice=texts.CANCEL_CHOICE_LABELS[order.cancel_choice.value]))
    if card.money_pending:
        lines.append(texts.ADMIN_ORDER_MONEY_PENDING)
    elif card.paid:
        lines.append(texts.ADMIN_ORDER_MONEY_SETTLED)
    return lines


def _card_markup(card: AdminOrderCard, now: datetime) -> InlineKeyboardMarkup:
    order = card.order
    rows = []
    if card.payable:
        rows.append(_button(texts.BTN_ADMIN_ORDER_INVOICE, OrderAdminAction.INVOICE, order.id))
        if order.total_amount is None:
            rows.append(_button(texts.BTN_ADMIN_ORDER_TOTAL, OrderAdminAction.TOTAL, order.id))
        rows.append(_button(texts.BTN_ADMIN_ORDER_PAY, OrderAdminAction.PAY, order.id))
        if card.advance_to_apply:
            rows.append(_button(texts.BTN_ADMIN_ORDER_APPLY.format(amount=format_rub(card.advance_to_apply)),
                                OrderAdminAction.APPLY, order.id))
    if order.requisites_file_id:
        rows.append(_button(texts.BTN_ADMIN_ORDER_REQUISITES, OrderAdminAction.REQUISITES, order.id))
    if card.money_pending:
        rows.append(_button(texts.BTN_ADMIN_ORDER_CREDIT.format(amount=format_rub(card.paid)),
                            OrderAdminAction.CREDIT, order.id))
        rows.append(_button(texts.BTN_ADMIN_ORDER_REFUND, OrderAdminAction.REFUND, order.id))
    rows.append([
        InlineKeyboardButton(text=texts.BTN_ADMIN_ORDER_CLIENT, callback_data=ClientAdminCb(
            action=ClientAdminAction.VIEW, item_id=card.payer.id, value=f"{BACK_ORDER}{order.id}").pack()),
        InlineKeyboardButton(text=texts.BTN_ADMIN_ORDER_WRITE, callback_data=_cb(OrderAdminAction.WRITE, order.id)),
    ])
    if order.status is not OrderStatus.CANCELLED:
        rows.append(_button(texts.BTN_ADMIN_ORDER_REMOVE, OrderAdminAction.REMOVE, order.id))
    category = card.category(now)
    rows.append(_button(texts.BTN_ADMIN_TO_ORDERS, OrderAdminAction.LIST, value=category.value) if category
                else _button(texts.BTN_ADMIN_TO_ORDERS, OrderAdminAction.MENU))
    return _markup(*rows)


# --- Оплата и сумма заказа -------------------------------------------------------------------------------------

def ask_amount(card: AdminOrderCard) -> Screen:
    order, paid = card.order, format_rub(card.paid)
    if card.left is None:
        summary = texts.ADMIN_ORDER_AMOUNT_SUMMARY_OPEN.format(paid=paid)
    else:
        summary = texts.ADMIN_ORDER_AMOUNT_SUMMARY.format(total=format_rub(order.total_amount), paid=paid,
                                                          left=format_rub(card.left))
    rows = []
    if card.left:
        rows.append(_button(texts.BTN_ADMIN_ORDER_PAY_FULL.format(amount=format_rub(card.left)),
                            OrderAdminAction.FULL, order.id))
    rows.append(_cancel(order.id))
    hint = texts.ADMIN_ORDER_AMOUNT_HINT_FULL if card.left else texts.ADMIN_ORDER_AMOUNT_HINT
    return texts.ADMIN_ORDER_ASK_AMOUNT.format(number=order.number, summary=summary, hint=hint), _markup(*rows)


def ask_method(order: Order, amount: int, token: str) -> Screen:
    """Способ оплаты - он же подтверждение: нажатие сохраняет оплату. token в кнопке: старый экран с другой
    суммой (админ потом написал новую) уже не сработает."""
    rows = [_button(texts.BTN_PAYMENT_METHODS[method.value], OrderAdminAction.METHOD, order.id,
                    f"{method.value}{TOKEN_SEPARATOR}{token}")
            for method in MANUAL_PAYMENT_METHODS]
    rows.append(_cancel(order.id))
    return texts.ADMIN_ORDER_ASK_METHOD.format(amount=format_rub(amount), number=order.number), _markup(*rows)


def ask_total(order: Order) -> Screen:
    return texts.ADMIN_ORDER_ASK_TOTAL.format(number=order.number), _markup(_cancel(order.id))


# --- Подтверждения ---------------------------------------------------------------------------------------------

def remove_confirm(card: AdminOrderCard) -> Screen:
    active = [person for row, person in card.participants if row.status not in CANCELLED_ENROLLMENT_STATUSES]
    names = ", ".join(_short(person) for person in active) or _short(card.payer)
    note = (texts.ADMIN_ORDER_REMOVE_PAID.format(paid=format_rub(card.paid)) if card.paid
            else texts.ADMIN_ORDER_REMOVE_UNPAID)
    text = texts.ADMIN_ORDER_REMOVE_CONFIRM.format(number=card.order.number, names=names, note=note)
    return text, _yes_no(texts.BTN_ADMIN_ORDER_REMOVE_YES, OrderAdminAction.REMOVE_OK, card.order.id)


def credit_confirm(card: AdminOrderCard) -> Screen:
    text = texts.ADMIN_ORDER_CREDIT_CONFIRM.format(amount=format_rub(card.paid), name=person(card.payer),
                                                   number=card.order.number)
    return text, _yes_no(texts.BTN_ADMIN_ORDER_CREDIT_YES, OrderAdminAction.CREDIT_OK, card.order.id)


def refund_confirm(card: AdminOrderCard) -> Screen:
    text = texts.ADMIN_ORDER_REFUND_CONFIRM.format(amount=format_rub(card.paid), name=person(card.payer),
                                                   number=card.order.number)
    return text, _yes_no(texts.BTN_ADMIN_ORDER_REFUND_YES, OrderAdminAction.REFUND_OK, card.order.id)


def apply_confirm(card: AdminOrderCard) -> Screen:
    amount = card.advance_to_apply
    text = texts.ADMIN_ORDER_APPLY_CONFIRM.format(amount=format_rub(amount), number=card.order.number,
                                                  name=person(card.payer), rest=format_rub(card.advance - amount))
    return text, _yes_no(texts.BTN_ADMIN_ORDER_APPLY_YES, OrderAdminAction.APPLY_OK, card.order.id)


# --- Счёт и сообщение клиенту ----------------------------------------------------------------------------------

def ask_invoice(card: AdminOrderCard) -> Screen:
    text = texts.ADMIN_ORDER_ASK_INVOICE.format(number=card.order.number, name=person(card.payer))
    return text, _markup(_cancel(card.order.id))


def ask_message(card: AdminOrderCard) -> Screen:
    text = texts.ADMIN_ORDER_ASK_MESSAGE.format(number=card.order.number, name=person(card.payer))
    return text, _markup(_cancel(card.order.id))


def preview_note(payer: Client) -> str:
    return texts.ADMIN_ORDER_PREVIEW.format(name=person(payer))


def preview_buttons(order_id: int, token: str) -> InlineKeyboardMarkup:
    """Под предпросмотром. token в кнопке: «Отправить» под старым предпросмотром не отправит новый черновик."""
    return _markup([
        InlineKeyboardButton(text=texts.BTN_ADMIN_SEND, callback_data=_cb(OrderAdminAction.SEND, order_id, token)),
        InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL, callback_data=_cb(OrderAdminAction.CANCEL, order_id)),
    ])


_DELIVERY_REPORTS = {
    Delivery.DELIVERED: texts.ADMIN_ORDER_DELIVERED,
    Delivery.BLOCKED: texts.ADMIN_ORDER_NOT_DELIVERED_BLOCKED,
    Delivery.FAILED: texts.ADMIN_ORDER_NOT_DELIVERED,
    Delivery.NO_TELEGRAM: texts.ADMIN_ORDER_NO_TELEGRAM,
}


def delivery_report(delivery: Delivery, client: Client) -> str:
    """«✅ Доставлено: …» или почему не дошло - с телефоном, чтобы связаться иначе."""
    return _DELIVERY_REPORTS[delivery].format(name=person(client), phone=phone(client))


def to_card(order_id: int) -> InlineKeyboardMarkup:
    return _markup(_to_card(order_id))


def open_order(order_id: int) -> InlineKeyboardMarkup:
    """Под уведомлением: «🧾 Открыть заказ» - карточка в админке новым сообщением."""
    return _markup(_button(texts.BTN_ADMIN_OPEN_ORDER, OrderAdminAction.VIEW, order_id, NEW_SCREEN))


# --- Уведомления админам ---------------------------------------------------------------------------------------

def _colleague_line(colleague: Client | None) -> str:
    if colleague is None:
        return ""
    return texts.ADMIN_NOTIFY_COLLEAGUE.format(
        name=person(colleague), phone=safe(colleague.phone),
        not_in_bot="" if colleague.telegram_id else texts.ADMIN_NOTIFY_NOT_IN_BOT,
    )


def notify_enrolled(card: SessionCard, order: Order | None, payer: Client, colleague: Client | None,
                    price: PriceOption | None, timezone: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """Новая запись (заказ создан) или лист ожидания (мест нет, заказа нет)."""
    fields = dict(title=safe(session_title(card.session)), dates=dates_text(card.session), name=person(payer),
                  phone=safe(payer.phone), colleague=_colleague_line(colleague))
    if order is None:
        return texts.ADMIN_NOTIFY_WAITLIST.format(**fields), None
    text = texts.ADMIN_NOTIFY_ENROLLED.format(
        number=order.number, price=safe(price_label(price)) if price else texts.APPLY_NO_PRICE,
        due=format_moment(order.due_at, timezone), **fields,
    )
    return text, open_order(order.id)


def notify_payer_type(order: Order, payer: Client) -> tuple[str, InlineKeyboardMarkup]:
    template = (texts.ADMIN_NOTIFY_INDIVIDUAL if order.payer_type is PayerType.INDIVIDUAL
                else texts.ADMIN_NOTIFY_ORGANIZATION)
    return template.format(number=order.number, name=person(payer)), open_order(order.id)


def notify_requisites(order: Order, payer: Client) -> tuple[str, InlineKeyboardMarkup]:
    """Подпись к файлу реквизитов, который бот пересылает админам."""
    text = texts.ADMIN_NOTIFY_REQUISITES.format(number=order.number, name=person(payer), phone=safe(payer.phone))
    return text, open_order(order.id)


def reminder_notice(job: ReminderJob, timezone: str) -> tuple[str, InlineKeyboardMarkup]:
    """Админам: «сутки без оплаты» или «просрочена оплата» - с кнопкой «Снять бронь» (сначала спросит)."""
    order, payer = job.order, job.payer
    fields = dict(number=order.number, title=safe(session_title(job.session)), dates=dates_text(job.session),
                  name=person(payer), phone=phone(payer), amount=order_amount(order),
                  due=format_moment(order.due_at, timezone),
                  with_colleague=texts.ADMIN_NOTIFY_WITH_COLLEAGUE if job.participants > 1 else "")
    if job.kind is ReminderKind.OVERDUE:
        markup = _markup(_button(texts.BTN_ADMIN_ORDER_REMOVE, OrderAdminAction.REMOVE, order.id, NEW_SCREEN),
                         _button(texts.BTN_ADMIN_OPEN_ORDER, OrderAdminAction.VIEW, order.id, NEW_SCREEN))
        return texts.ADMIN_REMINDER_OVERDUE.format(**fields), markup
    invoice = (texts.ADMIN_ORDER_INVOICE_SENT.format(moment=format_moment(order.invoice_sent_at, timezone))
               if order.invoice_sent_at else texts.ADMIN_ORDER_INVOICE_NONE)
    return texts.ADMIN_REMINDER_DAY.format(invoice=invoice, **fields), open_order(order.id)


def notify_cancelled(app: MyApplication, result: CancelResult,
                     payer: Client) -> tuple[str, InlineKeyboardMarkup | None]:
    """Клиент сам отменил запись или вышел из листа ожидания."""
    pair = bool(result.colleagues)
    fields = dict(title=safe(app.title), dates=dates_text(app.session), name=person(payer), phone=safe(payer.phone),
                  with_colleague=texts.ADMIN_NOTIFY_WITH_COLLEAGUE if pair else "")
    if result.waitlist:
        return texts.ADMIN_NOTIFY_LEFT_WAITLIST.format(**fields), None
    order = result.order
    if result.paid and result.choice is not None:
        money = texts.ADMIN_NOTIFY_CANCEL_PAID.format(paid=format_rub(result.paid),
                                                      choice=texts.CANCEL_CHOICE_LABELS[result.choice.value])
    elif result.course_waitlist:
        money = texts.ADMIN_NOTIFY_CANCEL_UNPAID.format(
            who=texts.ADMIN_NOTIFY_CANCEL_UNPAID_PAIR if pair else texts.ADMIN_NOTIFY_CANCEL_UNPAID_ONE)
    else:
        money = ""
    text = texts.ADMIN_NOTIFY_CANCELLED.format(
        order=texts.ADMIN_NOTIFY_ORDER.format(number=order.number) if order else "", money=money, **fields,
    ).rstrip("\n")
    return text, open_order(order.id) if order else None
