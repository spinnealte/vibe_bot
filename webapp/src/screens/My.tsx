// «Мои записи»: записи с заказом и оплатой, «Как будете оплачивать?», реквизиты, онлайн-оплата и отмена.
// Плательщик видит заказ; коллега из пары - кто его записал.

import { useEffect, useState, type ReactNode } from "react";

import { ApiError, api, type Application } from "../api";
import { Icon } from "../icons";
import { navigate } from "../router";
import { openUrl } from "../telegram";
import {
  ActionButton,
  Badge,
  Button,
  Empty,
  ErrorState,
  Loading,
  PageHeader,
  Rich,
  Sheet,
  clean,
  statusTone,
  useApp,
  useLoad,
} from "../ui";
import { PaymentActions } from "./Payment";

/** focus - запись (id), order - заказ: так открывает запись кнопка под уведомлением бота (напоминание, счёт). */
export function MyScreen({ focus, order }: { focus?: number; order?: number }): ReactNode {
  const { me } = useApp();
  const list = useLoad(api.applications);

  useEffect(() => {
    if (!list.data) return;
    const target = focus ?? list.data.items.find((app) => order && app.order?.id === order)?.id;
    if (target) document.getElementById(`app-${target}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [focus, order, list.data]);

  if (me.step === "consent") {
    return (
      <div className="page">
        <PageHeader title="Мои записи" />
        <Empty icon={<Icon.ticket />} title="Здесь будут Ваши записи на обучение" text="Запишитесь на курс в расписании">
          <Button onClick={() => navigate("/")}>К расписанию</Button>
        </Empty>
      </div>
    );
  }

  const replace = (fresh: Application) =>
    list.data && list.setData({ ...list.data, items: list.data.items.map((a) => (a.id === fresh.id ? fresh : a)) });

  return (
    <div className="page">
      <PageHeader title="Мои записи" />
      {list.loading && !list.data ? (
        <Loading rows={2} />
      ) : list.error ? (
        <ErrorState error={list.error} onRetry={list.reload} />
      ) : (
        <div className="stack">
          {list.data?.advance && (
            <div className="notice ok">
              <span>💰</span>
              <span>
                Аванс: <b>{list.data.advance.text}</b>
              </span>
            </div>
          )}
          {list.data?.items.length ? (
            list.data.items.map((app) => (
              <ApplicationCard key={app.id} app={app} onChange={replace} onCancelled={list.reload} />
            ))
          ) : (
            <Empty icon={<Icon.ticket />} title="Записей пока нет" text="Выберите курс в расписании и запишитесь">
              <Button onClick={() => navigate("/")}>К расписанию</Button>
            </Empty>
          )}
        </div>
      )}
    </div>
  );
}

function ApplicationCard({ app, onChange, onCancelled }: {
  app: Application;
  onChange: (app: Application) => void;
  onCancelled: () => void;
}): ReactNode {
  const [cancelling, setCancelling] = useState(false);
  const order = app.order;
  return (
    <div className="card" id={`app-${app.id}`}>
      <div className="card-body">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
          <Badge tone={statusTone(app.status)}>{clean(app.status_label)}</Badge>
          {order?.overdue && <Badge tone="danger">Срок оплаты прошёл</Badge>}
        </div>
        <button
          className="btn ghost"
          style={{ padding: 0, minHeight: 0, marginTop: 10, textAlign: "left", color: "var(--text)", display: "block" }}
          onClick={() => navigate(`/course/${app.session_id}`)}
        >
          <h3 className="course-title">{app.title}</h3>
        </button>
        <div className="meta">
          <span>
            <Icon.calendar />
            {app.dates}
          </span>
          {app.venue && (
            <span>
              <Icon.pin />
              {app.venue.name}
            </span>
          )}
        </div>
        {app.price && <div className="muted small" style={{ marginTop: 6 }}>Тариф: {app.price.label} · {app.price.amount_text}</div>}
        {app.partner && (
          <div className="small" style={{ marginTop: 6 }}>
            {app.is_payer ? `👥 Вместе с Вами: ${app.partner}` : `🤝 Вас записал(а): ${app.partner}, статус оплаты - ${app.payment_label}`}
          </div>
        )}

        {order && (
          <div className="order-box">
            <div className="order-line">
              <span className="muted">Заказ</span>
              <b>№{order.number}</b>
            </div>
            <div className="order-line">
              <span className="muted">Сумма</span>
              <b>{order.total?.text ?? "уточнит менеджер"}</b>
            </div>
            {order.paid && (
              <div className="order-line">
                <span className="muted">Оплачено</span>
                <b>
                  {order.paid.text}
                  {order.left && order.left.amount > 0 ? `, осталось ${order.left.text}` : order.total ? " - полностью" : ""}
                </b>
              </div>
            )}
            {order.due && (
              <div className="order-line">
                <span className="muted">Оплатить до</span>
                <b style={{ color: order.overdue ? "var(--danger)" : undefined }}>{order.due}</b>
              </div>
            )}
            {!order.total && order.payable && (
              <div className="muted small" style={{ marginTop: 4 }}>
                Срок оплаты - 72 часа с момента, когда менеджер назовёт сумму.
              </div>
            )}
            {order.invoice_sent && <div className="muted small" style={{ marginTop: 4 }}>📨 Счёт отправлен</div>}
          </div>
        )}
        {order?.overdue && (
          <div className="notice danger" style={{ marginTop: 10 }}>
            Срок оплаты прошёл - менеджер может снять бронь. Оплатите, пожалуйста, или напишите менеджеру для продления брони.
          </div>
        )}

        {app.active && app.is_payer && <PaymentActions app={app} onChange={onChange} />}

        {app.active && (
          <div className="actions">
            {app.can_cancel && (
              <Button variant="danger" size="small" onClick={() => setCancelling(true)}>
                {app.waitlist ? "Выйти из листа ожидания" : "Отменить запись"}
              </Button>
            )}
            {app.manager_cancel && app.manager_url && (
              <Button variant="secondary" size="small" onClick={() => openUrl(app.manager_url!)}>
                <Icon.chat />
                Отменить через менеджера
              </Button>
            )}
          </div>
        )}
        {app.active && app.can_cancel && app.cancel_until && !app.waitlist && (
          <div className="muted small" style={{ marginTop: 8 }}>
            Отменить запись здесь можно до {app.cancel_until} включительно, позже - через менеджера.
          </div>
        )}
        {app.active && !app.is_payer && (
          <div className="muted small" style={{ marginTop: 8 }}>Отказаться от участия можно через менеджера.</div>
        )}
      </div>
      {cancelling && <CancelSheet app={app} onClose={() => setCancelling(false)} onDone={onCancelled} />}
    </div>
  );
}

const CHOICES = [
  { value: "transfer", title: "Перенести на другое мероприятие", text: "Выберите другое проведение - оплату засчитает менеджер в ближайшее время." },
  { value: "advance", title: "Оставить на аванс", text: "Деньги останутся у Вас на балансе - для следующих курсов." },
  { value: "refund", title: "Вернуть деньги", text: "Возврат оформит менеджер." },
];

function CancelSheet({ app, onClose, onDone }: { app: Application; onClose: () => void; onDone: () => void }) {
  const { fail } = useApp();
  const [result, setResult] = useState<{ html: string; manager: string | null; transfer: boolean } | null>(null);
  const [paid, setPaid] = useState(app.paid > 0);

  const cancel = async (choice: string | null) => {
    try {
      const outcome = await api.cancel(app.id, choice);
      setResult({ html: outcome.message_html, manager: outcome.manager_url, transfer: outcome.choice === "transfer" });
    } catch (error) {
      if (error instanceof ApiError && error.code === "needs_choice") {
        setPaid(true); // пока решали, пришла оплата - сначала выбрать, что сделать с деньгами
        return;
      }
      fail(error);
    }
  };

  const close = () => {
    onClose();
    if (result) onDone();
  };

  return (
    <Sheet open onClose={close}>
      {result ? (
        <div className="stack">
          <h2>Запись отменена</h2>
          <Rich html={result.html} />
          {result.transfer && (
            <Button block onClick={() => { close(); navigate("/"); }}>
              Выбрать другое проведение
            </Button>
          )}
          {result.manager && (
            <Button block variant="secondary" onClick={() => openUrl(result.manager!)}>
              <Icon.chat />
              Связаться с менеджером
            </Button>
          )}
          <Button block variant="secondary" onClick={close}>
            Готово
          </Button>
        </div>
      ) : paid ? (
        <div className="stack">
          <h2>Отменить запись?</h2>
          <p className="muted" style={{ margin: 0 }}>
            {app.title} · {app.dates}. По заказу уже есть оплата - что сделать с деньгами?
          </p>
          {CHOICES.map((choice) => (
            <ActionButton key={choice.value} variant="secondary" block onClick={() => cancel(choice.value)}>
              <span style={{ display: "block", textAlign: "left", width: "100%" }}>
                {choice.title}
                <span className="muted small" style={{ display: "block", fontWeight: 400 }}>{choice.text}</span>
              </span>
            </ActionButton>
          ))}
          <Button block variant="ghost" onClick={close}>
            Нет, оставить запись
          </Button>
        </div>
      ) : (
        <div className="stack">
          <h2>{app.waitlist ? "Выйти из листа ожидания?" : "Отменить запись?"}</h2>
          <p className="muted" style={{ margin: 0 }}>
            {app.title} · {app.dates}
          </p>
          {app.partner && app.is_payer && (
            <div className="notice warn">👥 Отменится и запись коллеги: {app.partner}</div>
          )}
          {!app.waitlist && (
            <div className="notice">
              Место освободится. Сообщим, когда откроется новый набор на этот курс.
            </div>
          )}
          <ActionButton block variant="danger" onClick={() => cancel(null)}>
            Да, отменить
          </ActionButton>
          <Button block variant="ghost" onClick={close}>
            Нет, оставить
          </Button>
        </div>
      )}
    </Sheet>
  );
}
