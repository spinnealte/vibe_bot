// Заказы: сводка по разделам (как «🧾 Заказы» в боте), список раздела, карточка заказа с оплатой, счётом,
// сообщением клиенту, снятием брони и авансом. Деньги и правила - на сервере, здесь - кнопки и формы.

import { useMemo, useState, type ReactNode } from "react";

import { Icon } from "../icons";
import { navigate } from "../router";
import { ActionButton, Badge, Button, Empty, ErrorState, Field, Loading, Sheet, useApp, useLoad } from "../ui";
import { admin, newToken, rub, type OrderCard, type OrderRow } from "./api";
import { AdminHeader, InfoRow, NavRow, Section, useAdmin, useRun } from "./common";
import { ComposeSheet } from "./Compose";

const CATEGORIES: { key: string; title: string; text: string; icon: () => ReactNode; tone?: "danger" | "warn" | "ok" }[] = [
  { key: "inv", title: "Нужен счёт", text: "Ждут оплаты, счёт ещё не отправлен", icon: Icon.file },
  { key: "sent", title: "Счёт отправлен", text: "Ждём оплату", icon: Icon.send },
  { key: "late", title: "Просрочены", text: "Срок оплаты вышел", icon: Icon.clock, tone: "danger" },
  { key: "part", title: "Оплачены частично", text: "Место закреплено, остаток на контроле", icon: Icon.money, tone: "warn" },
  { key: "paid", title: "Оплачены", text: "Курсы, которые ещё не прошли", icon: Icon.check, tone: "ok" },
  { key: "money", title: "Деньги после отмены", text: "На аванс или вернуть", icon: Icon.refresh, tone: "warn" },
];

export function OrdersHome(): ReactNode {
  const { summary, refreshSummary } = useAdmin();
  const { me, fail } = useApp();
  const [number, setNumber] = useState("");
  const [busy, setBusy] = useState(false);
  const counts = summary.orders ?? {};
  const urgent = (counts.inv ?? 0) + (counts.late ?? 0);

  const find = async () => {
    setBusy(true);
    try {
      navigate(`/admin/order/${(await admin.findOrder(number.trim())).id}`);
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <AdminHeader
        over="Админка"
        title={`Здравствуйте${me.first_name ? `, ${me.first_name}` : ""}`}
        text={urgent ? `Нужно внимание: ${urgent}` : "Срочных заказов нет"}
        action={
          <button className="a-icon-btn" onClick={refreshSummary} aria-label="Обновить">
            <Icon.refresh />
          </button>
        }
      />
      <div className="a-tiles">
        <Tile label="В афише" value={summary.afisha} onClick={() => navigate("/admin/afisha")} />
        <Tile label="Клиентов" value={summary.clients ?? 0} onClick={() => navigate("/admin/clients")} />
        <Tile label="Ждут набора" value={summary.waitlists ?? 0} onClick={() => navigate("/admin/waitlists")} />
      </div>
      <Section title="Заказы">
        <div className="list">
          {CATEGORIES.map((category) => (
            <NavRow
              key={category.key}
              icon={category.icon()}
              title={category.title}
              text={
                category.key === "inv" && summary.orders_waiting_client
                  ? `Из них ждут клиента: ${summary.orders_waiting_client}`
                  : category.text
              }
              count={counts[category.key] ?? 0}
              tone={(counts[category.key] ?? 0) > 0 ? category.tone ?? undefined : undefined}
              to={`/admin/orders/${category.key}`}
            />
          ))}
        </div>
      </Section>
      <Section title="Заказ по номеру">
        <div className="row">
          <input
            className="input grow"
            inputMode="numeric"
            placeholder="100001"
            value={number}
            maxLength={7}
            onChange={(e) => setNumber(e.target.value.replace(/[^\d]/g, ""))}
            onKeyDown={(e) => e.key === "Enter" && number.length === 6 && void find()}
          />
          <Button busy={busy} disabled={number.length !== 6} onClick={find}>
            <Icon.search />
          </Button>
        </div>
      </Section>
    </div>
  );
}

function Tile({ label, value, onClick }: { label: string; value: number; onClick: () => void }): ReactNode {
  return (
    <button className="a-tile" onClick={onClick}>
      <span className="a-tile-value">{value}</span>
      <span className="a-tile-label">{label}</span>
    </button>
  );
}

function payerMark(row: OrderRow): { text: string; tone?: "ok" | "warn" | "danger" } | null {
  if (row.payer_type === null) return { text: "не выбрал физ/юрлицо", tone: "warn" };
  if (row.payer_type === "individual") return { text: "физлицо" };
  return row.requisites ? { text: "юрлицо" } : { text: "ждём реквизиты", tone: "warn" };
}

export function OrderList({ category }: { category: string }): ReactNode {
  const loaded = useLoad(() => admin.orders(category), [category]);
  const meta = CATEGORIES.find((c) => c.key === category);
  return (
    <div className="page">
      <AdminHeader over="Заказы" title={meta?.title ?? loaded.data?.title ?? "Заказы"} text={meta?.text} />
      {loaded.error && <ErrorState error={loaded.error} onRetry={loaded.reload} />}
      {loaded.loading && !loaded.data && <Loading rows={2} />}
      {loaded.data && !loaded.data.items.length && (
        <Empty icon={<Icon.receipt />} title="Здесь пока пусто" text="Новые заказы появятся сами." />
      )}
      {loaded.data && loaded.data.items.length > 0 && (
        <div className="list">
          {loaded.data.items.map((row) => {
            const mark = category === "inv" ? payerMark(row) : null;
            return (
              <button key={row.id} className="list-item a-order-row" onClick={() => navigate(`/admin/order/${row.id}`)}>
                <span className="grow">
                  <span className="a-order-top">
                    <b>№{row.number}</b>
                    <span>{row.total?.text ?? "сумма не указана"}</span>
                  </span>
                  <span className="a-order-name">{row.payer}</span>
                  <span className="a-order-tags">
                    {mark && <Badge tone={mark.tone}>{mark.text}</Badge>}
                    {row.invoice_sent && <Badge>счёт {row.invoice_sent}</Badge>}
                    {row.overdue && <Badge tone="danger">просрочен</Badge>}
                    {!row.overdue && row.due && category !== "paid" && category !== "money" && (
                      <span className="muted small">до {row.due}</span>
                    )}
                  </span>
                </span>
                <Icon.chevron />
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

type Dialog = "pay" | "total" | "invoice" | "message" | null;

export function OrderScreen({ id }: { id: number }): ReactNode {
  const loaded = useLoad(() => admin.order(id), [id]);
  const { refreshSummary } = useAdmin();
  const [dialog, setDialog] = useState<Dialog>(null);
  const [caption, setCaption] = useState<string | null>(null);
  const [busy, run] = useRun();
  const order = loaded.data;

  const apply = (result: { order: OrderCard; message: string }) => {
    loaded.setData(result.order);
    refreshSummary();
    return result.message;
  };

  if (loaded.error) return <div className="page"><ErrorState error={loaded.error} onRetry={loaded.reload} /></div>;
  if (!order) return <div className="page"><Loading rows={2} /></div>;
  const a = order.actions;
  const tone = order.status === "paid" ? "ok" : order.status === "cancelled" ? "danger" : order.overdue ? "danger" : "warn";

  return (
    <div className="page">
      <AdminHeader over="Заказ" title={`№${order.number}`} text={<Badge tone={tone}>{order.status_label}</Badge>} />

      <div className="list">
        <InfoRow label="Курс" value={`${order.session.title} · ${order.session.dates}`} />
        <NavRow
          icon={<Icon.user />}
          title={order.payer.name}
          text={[order.payer.phone, order.payer.username ? `@${order.payer.username}` : null, !order.payer.in_bot ? "не в боте" : null, order.payer.blocked ? "заблокировал бота" : null]
            .filter(Boolean)
            .join(" · ")}
          to={`/admin/client/${order.payer.id}`}
        />
        <NavRow icon={<Icon.calendar />} title="Проведение" text="Карточка и участники" to={`/admin/session/${order.session.id}`} />
      </div>

      {order.participants.length > 1 && (
        <Section title="Участники">
          <div className="list">
            {order.participants.map((p) => (
              <NavRow key={p.id} title={p.name} text={`${p.status_label}${p.in_bot ? "" : " · не в боте"}`} to={`/admin/client/${p.id}`} />
            ))}
          </div>
        </Section>
      )}

      <Section title="Деньги">
        <div className="order-box a-money">
          {order.price_label && <Line label="Тариф" value={order.price_label} />}
          <Line label="Сумма" value={order.total?.text ?? "уточнит менеджер"} />
          {order.paid && <Line label="Оплачено" value={order.paid.text} />}
          {order.left && order.paid && !order.cancelled && <Line label="Осталось" value={order.left.text} />}
          {order.due && <Line label="Оплатить до" value={order.due} warn={order.overdue} />}
          {order.advance && <Line label="Аванс клиента" value={order.advance.text} />}
          <Line label="Создан" value={order.created} />
        </div>
        {order.online_pending && (
          <div className="notice warn">
            Клиент начал онлайн-оплату {order.online_pending.amount.text} ({order.online_pending.created}) - итога ещё нет.
          </div>
        )}
        {order.cancelled && (
          <div className="notice danger">
            <div className="grow">
              Отменён {order.cancelled.at ?? ""}. {order.cancelled.reason ?? ""}
              {order.cancelled.choice && <div>Клиент выбрал: {order.cancelled.choice}.</div>}
              {order.money_pending && <div><b>Деньги не распределены: {order.money_pending.text}.</b></div>}
              {order.money_settled && <div>Деньги по заказу распределены.</div>}
            </div>
          </div>
        )}
      </Section>

      {!order.cancelled && (
        <Section title="Плательщик и счёт">
          <div className="list">
            <InfoRow
              label="Кто платит"
              value={
                order.payer_type === "individual"
                  ? "Физлицо"
                  : order.payer_type === "organization"
                    ? order.requisites
                      ? `Юрлицо · реквизиты: ${order.requisites.name ?? "файл"}`
                      : "Юрлицо · ждём реквизиты"
                    : null
              }
              empty="Клиент ещё не выбрал"
            />
            <InfoRow
              label="Счёт"
              value={order.invoice ? `${order.invoice.manual ? "Отмечен вне бота" : "Отправлен через бота"} · ${order.invoice.sent_at}` : null}
              empty="Ещё не отправлен"
            />
          </div>
        </Section>
      )}

      <Section title="Действия">
        <div className="a-actions">
          {a.pay && (
            <Button onClick={() => setDialog("pay")}>
              <Icon.money />
              Отметить оплату
            </Button>
          )}
          {a.total && (
            <Button variant="secondary" onClick={() => setDialog("total")}>
              Указать сумму заказа
            </Button>
          )}
          {a.invoice && (
            <Button
              variant="secondary"
              onClick={async () => {
                setCaption(null);
                setDialog("invoice");
                try {
                  setCaption((await admin.invoiceCaption(order.id)).html);
                } catch {
                  /* подпись покажем без предпросмотра */
                }
              }}
            >
              <Icon.file />
              Отправить счёт
            </Button>
          )}
          {a.apply_advance && (
            <Button
              variant="secondary"
              busy={busy}
              onClick={() =>
                run(async () => apply(await admin.applyAdvance(order.id)), `Зачесть из аванса ${a.apply_advance!.text} в заказ №${order.number}?`)
              }
            >
              Зачесть аванс {a.apply_advance.text}
            </Button>
          )}
          {a.invoice_manual && (
            <Button variant="secondary" busy={busy} onClick={() => run(async () => apply(await admin.invoiceManual(order.id, true)))}>
              Счёт отправлен вне бота
            </Button>
          )}
          {a.invoice_undo && (
            <Button variant="ghost" busy={busy} onClick={() => run(async () => apply(await admin.invoiceManual(order.id, false)))}>
              Снять отметку о счёте
            </Button>
          )}
          {a.requisites && (
            <Button variant="secondary" busy={busy} onClick={() => run(() => admin.requisites(order.id).then((r) => r.message))}>
              Реквизиты - в чат с ботом
            </Button>
          )}
          <Button variant="secondary" onClick={() => setDialog("message")}>
            <Icon.chat />
            Написать клиенту
          </Button>
          {a.settle && order.money_pending && (
            <>
              <Button
                busy={busy}
                onClick={() =>
                  run(async () => apply(await admin.credit(order.id)), `Зачислить ${order.money_pending!.text} на аванс клиента? Клиенту придёт сообщение.`)
                }
              >
                На аванс {order.money_pending.text}
              </Button>
              <Button
                variant="secondary"
                busy={busy}
                onClick={() => run(async () => apply(await admin.refund(order.id)), `Отметить, что ${order.money_pending!.text} вернули клиенту?`)}
              >
                Отметить возврат
              </Button>
            </>
          )}
          {a.remove && (
            <Button
              variant="danger"
              busy={busy}
              onClick={() =>
                run(
                  async () => apply(await admin.removeBooking(order.id)),
                  order.paid
                    ? `Снять бронь? Места освободятся, заказ отменится. Оплачено ${order.paid.text} - потом решите: на аванс или вернуть.`
                    : "Снять бронь? Места освободятся, заказ отменится, участники попадут в лист ожидания курса.",
                )
              }
            >
              Снять бронь
            </Button>
          )}
        </div>
      </Section>

      {dialog === "pay" && <PaySheet order={order} onClose={() => setDialog(null)} onDone={(r) => { apply(r); setDialog(null); }} />}
      {dialog === "total" && <TotalSheet order={order} onClose={() => setDialog(null)} onDone={(r) => { apply(r); setDialog(null); }} />}
      {(dialog === "invoice" || dialog === "message") && (
        <ComposeSheet
          title={dialog === "invoice" ? `Счёт по заказу №${order.number}` : "Сообщение клиенту"}
          to={order.payer.name}
          invoice={dialog === "invoice"}
          captionHtml={caption}
          onSend={(text, token, file) =>
            dialog === "invoice" ? admin.sendInvoice(order.id, text, token, file) : admin.messagePayer(order.id, text, token, file)
          }
          onClose={(result) => {
            setDialog(null);
            if (result?.order) {
              loaded.setData(result.order);
              refreshSummary();
            }
          }}
        />
      )}
    </div>
  );
}

function Line({ label, value, warn }: { label: string; value: string; warn?: boolean }): ReactNode {
  return (
    <div className="order-line">
      <span className="muted">{label}</span>
      <b className={warn ? "danger-text" : ""}>{value}</b>
    </div>
  );
}

function PaySheet({ order, onClose, onDone }: {
  order: OrderCard;
  onClose: () => void;
  onDone: (result: { order: OrderCard; message: string }) => void;
}): ReactNode {
  const { specs } = useAdmin();
  const { toast } = useApp();
  const [rubles, setRubles] = useState(order.left ? String(order.left.amount / 100) : "");
  const [method, setMethod] = useState<string | null>(null);
  const token = useMemo(() => newToken(), []);
  const amount = Number(rubles) * 100;
  const full = order.left !== null && amount === order.left.amount;
  return (
    <Sheet open onClose={onClose}>
      <h2>Оплата по заказу №{order.number}</h2>
      <p className="muted small">Любая оплата закрепляет место. Клиенту придёт «оплата получена».</p>
      <div className="stack">
        <Field label="Сколько поступило" hint={order.left ? `Осталось оплатить: ${order.left.text}` : "Сумма заказа не указана"}>
          <div className="money-input">
            <input className="input" inputMode="numeric" value={rubles} onChange={(e) => setRubles(e.target.value.replace(/\D/g, ""))} />
            <span>₽</span>
          </div>
        </Field>
        {order.left && !full && (
          <button className="chip" onClick={() => setRubles(String(order.left!.amount / 100))}>
            Весь остаток {order.left.text}
          </button>
        )}
        <Field label="Как оплатили">
          <div className="chips">
            {specs.payment_methods.map((m) => (
              <button key={m.value} className={`chip ${method === m.value ? "on" : ""}`} onClick={() => setMethod(m.value)}>
                {m.label.replace(/^[^\p{L}]+/u, "")}
              </button>
            ))}
          </div>
        </Field>
        <ActionButton
          block
          disabled={!method || !amount}
          onClick={async () => {
            const result = await admin.pay(order.id, full ? { method: method!, token, full: true } : { method: method!, token, amount });
            toast(result.message, "ok");
            onDone(result);
          }}
        >
          Отметить оплату {amount ? rub(amount) : ""}
        </ActionButton>
      </div>
    </Sheet>
  );
}

function TotalSheet({ order, onClose, onDone }: {
  order: OrderCard;
  onClose: () => void;
  onDone: (result: { order: OrderCard; message: string }) => void;
}): ReactNode {
  const { toast } = useApp();
  const [rubles, setRubles] = useState("");
  return (
    <Sheet open onClose={onClose}>
      <h2>Сумма заказа №{order.number}</h2>
      <p className="muted small">С этого момента у клиента 72 часа на оплату.</p>
      <div className="stack">
        <div className="money-input">
          <input className="input" inputMode="numeric" autoFocus value={rubles} onChange={(e) => setRubles(e.target.value.replace(/\D/g, ""))} />
          <span>₽</span>
        </div>
        <ActionButton
          block
          disabled={!rubles}
          onClick={async () => {
            const result = await admin.setTotal(order.id, Number(rubles) * 100);
            toast(result.message, "ok");
            onDone(result);
          }}
        >
          Сохранить
        </ActionButton>
      </div>
    </Sheet>
  );
}
