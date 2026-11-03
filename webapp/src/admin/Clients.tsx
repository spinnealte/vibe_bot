// Клиенты: полный список по страницам (поиск по ФИО и телефону - как в боте: от 2 букв или 4 цифр, фильтры,
// сортировка), карточка клиента с записями и заказами, сообщение от имени бота, правка аванса; листы ожидания
// курсов; выгрузка всех клиентов в чат с ботом.

import { useEffect, useMemo, useState, type ReactNode } from "react";

import { ApiError } from "../api";
import { Icon } from "../icons";
import { navigate } from "../router";
import { ActionButton, Badge, Button, DragScroll, Empty, ErrorState, Field, Loading, Sheet, Spinner, statusTone, useApp, useLoad } from "../ui";
import { admin, newToken, rub, type ClientCard, type ClientFilter, type ClientList, type ClientListRow, type ClientSort } from "./api";
import { AdminHeader, InfoRow, NavRow, Section, Segmented, useAdmin, useRun } from "./common";
import { ComposeSheet } from "./Compose";

function searchable(query: string): boolean {
  const text = query.trim();
  if (/^[\d\s+()-]+$/.test(text)) return text.replace(/\D/g, "").length >= 4;
  return (text.match(/\p{L}/gu) ?? []).length >= 2;
}

const FILTERS: { value: ClientFilter; label: string }[] = [
  { value: "all", label: "Все" },
  { value: "profile", label: "С профилем" },
  { value: "enrolled", label: "Записаны на курсы" },
  { value: "blocked", label: "Заблокировали бота" },
];

const SORTS: { value: ClientSort; label: string }[] = [
  { value: "new", label: "Новые" },
  { value: "old", label: "Старые" },
  { value: "name", label: "По ФИО" },
];

// Список остаётся как был (страница, фильтр, поиск), когда возвращаешься из карточки клиента.
let saved: { q: string; filter: ClientFilter; sort: ClientSort; page: number } = { q: "", filter: "all", sort: "new", page: 1 };

/** Все клиенты по 15 на странице: поиск по ФИО или телефону, фильтр, сортировка, листание стрелками. Нажатие -
 * карточка клиента: записи, заказы, сообщение от имени бота, аванс. */
export function ClientsScreen(): ReactNode {
  const { summary } = useAdmin();
  const [q, setQ] = useState(saved.q);
  const [filter, setFilter] = useState<ClientFilter>(saved.filter);
  const [sort, setSort] = useState<ClientSort>(saved.sort);
  const [page, setPage] = useState(saved.page);
  const [list, setList] = useState<ClientList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, run] = useRun();
  const query = searchable(q) ? q.trim() : "";
  const waiting = q.trim() !== "" && !query; // набрали мало букв - ждём, список прежний

  useEffect(() => {
    saved = { q, filter, sort, page };
  }, [q, filter, sort, page]);

  useEffect(() => {
    if (waiting) return;
    let alive = true;
    const timer = window.setTimeout(
      () => {
        setLoading(true);
        admin
          .clientList({ q: query, filter, sort, page })
          .then((result) => {
            if (!alive) return;
            setList(result);
            setError(null);
            if (result.page !== page) setPage(result.page);
          })
          .catch((e: unknown) => alive && setError(e instanceof ApiError ? e.message : String(e)))
          .finally(() => alive && setLoading(false));
      },
      query ? 350 : 0,
    );
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [query, filter, sort, page, waiting]);

  const go = (next: number) => {
    setPage(next);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <div className="page">
      <AdminHeader over="Админка" title="Клиенты" text={`Всего в базе: ${summary.clients ?? 0}`} />
      <div className="a-actions two" style={{ marginBottom: 12 }}>
        <Button variant="secondary" size="small" onClick={() => navigate("/admin/waitlists")}>
          <Icon.clock />
          Листы ожидания · {summary.waitlists ?? 0}
        </Button>
        <Button variant="secondary" size="small" busy={busy} onClick={() => run(() => admin.exportClients().then((r) => r.message))}>
          <Icon.share />
          Выгрузка CSV
        </Button>
      </div>
      <div className="a-search">
        <Icon.search />
        <input
          className="input"
          placeholder="ФИО или последние цифры телефона"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(1);
          }}
          autoComplete="off"
        />
        {loading && <Spinner />}
      </div>
      {waiting && <p className="muted small">Часть ФИО - от 2 букв, телефон - от 4 последних цифр.</p>}
      <DragScroll className="filters" role="tablist">
        {FILTERS.map((item) => (
          <button
            key={item.value}
            role="tab"
            className={`chip ${filter === item.value ? "on" : ""}`}
            onClick={() => {
              setFilter(item.value);
              setPage(1);
            }}
          >
            {item.label}
          </button>
        ))}
      </DragScroll>
      <Segmented
        value={sort}
        options={SORTS}
        onChange={(value) => {
          setSort(value);
          setPage(1);
        }}
      />
      <div className="a-gap" />
      {error && <div className="field-error">{error}</div>}
      {!list && !error && <Loading rows={2} />}
      {list && (
        <>
          <p className="muted small" style={{ margin: "8px 2px" }}>
            {query ? "Найдено" : "Клиентов"}: {list.total}
            {list.pages > 1 && ` · страница ${list.page} из ${list.pages}`}
          </p>
          {list.items.length === 0 ? (
            <Empty
              icon={<Icon.search />}
              title="Никого не нашли"
              text={query ? "Проверьте написание или ищите по телефону." : "С таким фильтром клиентов пока нет."}
            />
          ) : (
            <div className={`list ${loading ? "a-loading" : ""}`}>
              {list.items.map((client) => (
                <ClientLine key={client.id} client={client} />
              ))}
            </div>
          )}
          {list.pages > 1 && <Pager page={list.page} pages={list.pages} onGo={go} />}
        </>
      )}
    </div>
  );
}

function ClientLine({ client }: { client: ClientListRow }): ReactNode {
  const details = [
    client.phone_tail ? `…${client.phone_tail}` : null,
    client.username ? `@${client.username}` : null,
    client.since ? `с ${client.since}` : null,
  ].filter(Boolean);
  const tags = client.active > 0 || !client.in_bot || client.blocked || !client.profile_done;
  return (
    <button className="list-item a-session-row" onClick={() => navigate(`/admin/client/${client.id}`)}>
      <span className="a-nav-icon">
        <Icon.user />
      </span>
      <span className="grow">
        <span className="a-nav-title">{client.name}</span>
        <span className="a-nav-text">{details.join(" · ")}</span>
        {tags && (
          <span className="a-order-tags">
            {client.active > 0 && <Badge tone="ok">записей: {client.active}</Badge>}
            {!client.in_bot && <Badge>не в боте</Badge>}
            {client.blocked && <Badge tone="danger">заблокировал бота</Badge>}
            {client.in_bot && !client.profile_done && <Badge tone="warn">профиль не заполнен</Badge>}
          </span>
        )}
      </span>
      <Icon.chevron />
    </button>
  );
}

/** Листание страниц: ◀ 3 / 12 ▶. */
function Pager({ page, pages, onGo }: { page: number; pages: number; onGo: (page: number) => void }): ReactNode {
  return (
    <div className="a-pager">
      <button className="a-icon-btn" disabled={page <= 1} onClick={() => onGo(page - 1)} aria-label="Предыдущая страница">
        <Icon.back />
      </button>
      <span>
        {page} / {pages}
      </span>
      <button className="a-icon-btn" disabled={page >= pages} onClick={() => onGo(page + 1)} aria-label="Следующая страница">
        <Icon.chevron />
      </button>
    </div>
  );
}

export function ClientScreen({ id }: { id: number }): ReactNode {
  const loaded = useLoad(() => admin.client(id), [id]);
  const [dialog, setDialog] = useState<"message" | "advance" | null>(null);
  const client = loaded.data;
  if (loaded.error) return <div className="page"><ErrorState error={loaded.error} onRetry={loaded.reload} /></div>;
  if (!client) return <div className="page"><Loading rows={2} /></div>;
  const active = client.enrollments.filter((e) => !e.cancelled);
  const cancelled = client.enrollments.filter((e) => e.cancelled);
  const telegram = !client.in_bot ? "Нет в боте" : client.blocked ? "Заблокировал бота" : client.username ? `@${client.username}` : "Без @username";

  return (
    <div className="page">
      <AdminHeader over="Клиент" title={client.full_name ?? client.telegram_name ?? "Без имени"} text={client.since ? `С нами с ${client.since}` : undefined} />
      <div className="a-actions two">
        <Button onClick={() => setDialog("message")} disabled={!client.in_bot}>
          <Icon.chat />
          Написать
        </Button>
        <Button variant="secondary" onClick={() => setDialog("advance")}>
          <Icon.money />
          Аванс: {client.advance.text}
        </Button>
      </div>
      <Section title="Профиль">
        <div className="list">
          <InfoRow label="Телефон" value={client.phone} />
          <InfoRow label="Telegram" value={telegram} />
          <InfoRow label="E-mail" value={client.email} />
          <InfoRow label="Город · место работы" value={[client.city, client.workplace].filter(Boolean).join(" · ")} />
          <InfoRow label="Стаж" value={client.experience_years !== null ? `${client.experience_years} лет` : null} />
          <InfoRow label="Дата рождения" value={client.birth_date} />
          <InfoRow label="Направления" value={client.specialties.join(", ")} />
          <InfoRow label="Рассылки" value={client.marketing ? "Согласие есть" : "Без рассылок"} />
          <InfoRow label="Последний визит" value={client.last_seen} />
        </div>
      </Section>
      <Section title={`Записи · ${active.length}`}>
        {active.length ? (
          <div className="list">
            {active.map((e) => (
              <NavRow
                key={e.id}
                title={e.title}
                text={
                  <>
                    {e.dates} · <Badge tone={statusTone(e.status)}>{e.status_label}</Badge>
                    {e.order && ` · №${e.order.number}`}
                  </>
                }
                to={e.order ? `/admin/order/${e.order.id}` : `/admin/session/${e.session_id}`}
              />
            ))}
          </div>
        ) : (
          <p className="muted small">Действующих записей нет.</p>
        )}
      </Section>
      {client.orders.length > 0 && (
        <Section title="Заказы, которые он оплачивает">
          <div className="list">
            {client.orders.map((o) => (
              <NavRow key={o.id} title={`№${o.number} · ${o.total?.text ?? "сумма не указана"}`} text={o.status_label} to={`/admin/order/${o.id}`} />
            ))}
          </div>
        </Section>
      )}
      {client.waiting.length > 0 && (
        <Section title="Ждёт нового проведения">
          <div className="list">
            {client.waiting.map((w) => (
              <NavRow key={w.program_id} title={w.title} text={w.since ? `с ${w.since}` : undefined} to={`/admin/waitlist/${w.program_id}`} />
            ))}
          </div>
        </Section>
      )}
      {cancelled.length > 0 && (
        <Section title={`Отменённые · ${cancelled.length}`}>
          <div className="list">
            {cancelled.map((e) => (
              <NavRow key={e.id} title={e.title} text={`${e.dates} · ${e.status_label}`} to={`/admin/session/${e.session_id}`} />
            ))}
          </div>
        </Section>
      )}
      {dialog === "message" && (
        <ComposeSheet
          title="Сообщение клиенту"
          to={client.full_name ?? "клиенту"}
          onSend={(text, token, file) => admin.messageClient(client.id, text, token, file)}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "advance" && (
        <AdvanceSheet
          client={client}
          onClose={() => setDialog(null)}
          onDone={(fresh) => {
            loaded.setData(fresh);
            setDialog(null);
          }}
        />
      )}
    </div>
  );
}

function AdvanceSheet({ client, onClose, onDone }: {
  client: ClientCard;
  onClose: () => void;
  onDone: (client: ClientCard) => void;
}): ReactNode {
  const { toast } = useApp();
  const [sign, setSign] = useState<"plus" | "minus">("plus");
  const [rubles, setRubles] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const token = useMemo(() => newToken(), []);
  const change = Number(rubles || 0) * 100 * (sign === "plus" ? 1 : -1);
  return (
    <Sheet open onClose={onClose}>
      <h2>Аванс клиента</h2>
      <p className="muted small">Сейчас: {client.advance.text}. Причина попадёт в журнал.</p>
      <div className="stack">
        <Segmented value={sign} onChange={setSign} options={[{ value: "plus", label: "Начислить" }, { value: "minus", label: "Списать" }]} />
        <div className="money-input">
          <input className="input" inputMode="numeric" placeholder="Сумма" value={rubles} onChange={(e) => setRubles(e.target.value.replace(/\D/g, ""))} />
          <span>₽</span>
        </div>
        <Field label="Причина" error={error}>
          <input className="input" maxLength={200} placeholder="Например: подарок за отзыв" value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        {change !== 0 && <div className="notice">Будет: {rub(client.advance.amount + change)}</div>}
        <ActionButton
          block
          disabled={!rubles || !reason.trim()}
          onClick={async () => {
            setError(null);
            try {
              const result = await admin.advance(client.id, change, reason.trim(), token);
              toast(result.message, "ok");
              onDone(result.client);
            } catch (e) {
              if (e instanceof ApiError && e.status === 400) setError(e.message);
              else throw e;
            }
          }}
        >
          Сохранить
        </ActionButton>
      </div>
    </Sheet>
  );
}

export function WaitlistsScreen(): ReactNode {
  const loaded = useLoad(() => admin.waitlists(), []);
  return (
    <div className="page">
      <AdminHeader over="Клиенты" title="Листы ожидания" text="Кто ждёт нового проведения курса" />
      {loaded.error && <ErrorState error={loaded.error} onRetry={loaded.reload} />}
      {loaded.loading && !loaded.data && <Loading rows={1} />}
      {loaded.data && !loaded.data.items.length && <Empty icon={<Icon.clock />} title="Никто не ждёт" text="Здесь появятся курсы, на которые просят сообщить о наборе." />}
      {loaded.data && loaded.data.items.length > 0 && (
        <div className="list">
          {loaded.data.items.map((row) => (
            <NavRow key={row.program_id} icon={<Icon.book />} title={row.title} count={row.count} to={`/admin/waitlist/${row.program_id}`} />
          ))}
        </div>
      )}
    </div>
  );
}

export function WaitlistScreen({ id }: { id: number }): ReactNode {
  const loaded = useLoad(() => admin.waitlist(id), [id]);
  const { refreshSummary } = useAdmin();
  const [busy, run] = useRun();
  const data = loaded.data;
  return (
    <div className="page">
      <AdminHeader over="Лист ожидания" title={data?.program.title ?? "Курс"} text={data ? `Ждут: ${data.items.length}` : undefined} />
      {loaded.error && <ErrorState error={loaded.error} onRetry={loaded.reload} />}
      {!data && !loaded.error && <Loading rows={1} />}
      {data && !data.items.length && <Empty icon={<Icon.clock />} title="Лист пуст" text="Все приглашены или никто не ждёт." />}
      {data && data.items.length > 0 && (
        <>
          <div className="list">
            {data.items.map((row) => (
              <NavRow key={row.id} icon={<Icon.user />} title={row.name} text={`ждёт с ${row.since}${row.in_bot ? "" : " · не в боте"}`} to={`/admin/client/${row.id}`} />
            ))}
          </div>
          <p className="muted small">Позвали на новое проведение - отметьте, и лист опустеет. Сообщения клиентам это не отправляет.</p>
          <Button
            block
            variant="secondary"
            busy={busy}
            onClick={() =>
              run(async () => {
                const result = await admin.invited(id);
                loaded.reload();
                refreshSummary();
                return result.message;
              }, `Отметить приглашёнными всех из листа (${data.items.length})?`)
            }
          >
            Отметить приглашёнными
          </Button>
        </>
      )}
      <Section>
        <div className="list">
          <NavRow icon={<Icon.book />} title="Карточка курса" to={`/admin/course/${id}`} />
        </div>
      </Section>
    </div>
  );
}
