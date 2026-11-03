// Афиша в админке: проведения по месяцам (в афише, в архиве, прошедшие), карточка проведения - как её видят
// клиенты, статус одним нажатием, правка полей, копия, архив, удаление; участники проведения.

import { useState, type ReactNode } from "react";

import { Icon } from "../icons";
import { navigate } from "../router";
import { Badge, Button, Cover, Empty, ErrorState, Loading, clean, plural, statusTone, useLoad } from "../ui";
import { admin, type Item, type ParticipantRow, type Scope, type SessionRow } from "./api";
import { AdminHeader, HeaderAction, NavRow, Section, Segmented, Thumb, useAdmin, useRun } from "./common";
import { FieldRows } from "./editors";

const SCOPES: { value: Scope; label: string }[] = [
  { value: "active", label: "В афише" },
  { value: "archived", label: "Архив" },
  { value: "past", label: "Прошедшие" },
];

export function AfishaScreen({ scope }: { scope: Scope }): ReactNode {
  const loaded = useLoad(() => admin.sessions(scope), [scope]);
  const groups: { label: string; items: SessionRow[] }[] = [];
  for (const row of loaded.data?.items ?? []) {
    const last = groups[groups.length - 1];
    if (last && last.label === row.month_label) last.items.push(row);
    else groups.push({ label: row.month_label, items: [row] });
  }
  return (
    <div className="page">
      <AdminHeader
        over="Админка"
        title="Афиша"
        text="Проведения курсов: с датами или «даты уточняются»"
        action={<HeaderAction icon={<Icon.plus />} label="Новое" onClick={() => navigate("/admin/new/ses")} />}
      />
      <Segmented
        value={scope}
        options={SCOPES}
        onChange={(next) => navigate(next === "active" ? "/admin/afisha" : `/admin/afisha?scope=${next}`, { replace: true })}
      />
      <div className="a-gap" />
      {loaded.error && <ErrorState error={loaded.error} onRetry={loaded.reload} />}
      {loaded.loading && !loaded.data && <Loading rows={2} />}
      {loaded.data && !loaded.data.items.length && (
        <Empty
          icon={<Icon.calendar />}
          title={scope === "active" ? "В афише пусто" : "Здесь пусто"}
          text={scope === "active" ? "Добавьте проведение курса - оно сразу появится в расписании." : undefined}
        >
          {scope === "active" && (
            <Button size="small" onClick={() => navigate("/admin/new/ses")}>
              <Icon.plus />
              Новое проведение
            </Button>
          )}
        </Empty>
      )}
      {groups.map((group) => (
        <Section key={group.label} title={group.label}>
          <div className="list">
            {group.items.map((row) => (
              <SessionLine key={row.id} row={row} />
            ))}
          </div>
        </Section>
      ))}
    </div>
  );
}

function SessionLine({ row }: { row: SessionRow }): ReactNode {
  const seats = row.capacity ? `${row.taken} из ${row.capacity}` : row.taken ? `записано ${row.taken}` : null;
  return (
    <button className="list-item a-session-row" onClick={() => navigate(`/admin/session/${row.id}`)}>
      <Thumb src={row.cover_url} />
      <span className="grow">
        <span className="a-nav-title">{row.title}</span>
        <span className="a-nav-text">
          {row.dates}
          {row.venue ? ` · ${row.venue}` : ""}
        </span>
        <span className="a-order-tags">
          <Badge tone={statusTone(row.status)}>{clean(row.status_label)}</Badge>
          {seats && (
            <span className="muted small">
              <Icon.people /> {seats}
            </span>
          )}
          {row.price_from && <span className="muted small">от {row.price_from.text}</span>}
          {row.program_archived && <Badge tone="danger">курс в архиве</Badge>}
        </span>
      </span>
      <Icon.chevron />
    </button>
  );
}

export function SessionScreen({ id }: { id: number }): ReactNode {
  const loaded = useLoad(() => admin.item("ses", id), [id]);
  const { specs, summary, refreshSummary } = useAdmin();
  const [busy, run] = useRun();
  const item = loaded.data;
  if (loaded.error) return <div className="page"><ErrorState error={loaded.error} onRetry={loaded.reload} /></div>;
  if (!item?.session) return <div className="page"><Loading rows={2} /></div>;
  const s = item.session;
  const card = s.card;
  const changed = (fresh: Item) => {
    loaded.setData(fresh);
    refreshSummary();
  };

  return (
    <div className="page">
      <div className="card a-hero">
        <Cover src={card.cover_url} alt={card.title} full>
          <div className="cover-badges">
            <Badge tone={statusTone(card.status)}>{clean(card.status_label)}</Badge>
          </div>
        </Cover>
        <div className="card-body">
          <div className="a-over">Проведение</div>
          <h1 className="a-hero-title">{card.title}</h1>
          <div className="muted">{card.dates}</div>
          {card.venue && <div className="muted small">{card.venue.name}</div>}
        </div>
      </div>

      {s.scope !== "active" && (
        <div className="notice warn">
          {s.scope === "archived" ? `В архиве с ${s.archived_at ?? "…"} - в афише его нет.` : "Уже началось или прошло - в афише его нет."}
          {" "}Править нельзя: можно скопировать на новые даты.
        </div>
      )}
      {s.program.archived && s.scope === "active" && <div className="notice danger">Курс этого проведения в архиве.</div>}

      {item.editable && (
        <Section title="Статус в афише">
          <div className="chips">
            {specs.statuses.map((status) => (
              <button
                key={status.value}
                className={`chip ${s.status === status.value ? "on" : ""}`}
                disabled={busy}
                onClick={() =>
                  s.status !== status.value &&
                  run(async () => {
                    const result = await admin.setStatus(item.id, status.value);
                    changed(result.item);
                    return result.message;
                  })
                }
              >
                {clean(status.label)}
              </button>
            ))}
          </div>
        </Section>
      )}

      <Section>
        <div className="list">
          {s.participants !== null && (
            <NavRow
              icon={<Icon.people />}
              title="Участники"
              text={item.values.capacity ? `Мест: ${item.values.capacity}` : "Без ограничения мест"}
              count={s.participants}
              to={`/admin/session/${item.id}/people`}
            />
          )}
          <NavRow icon={<Icon.book />} title={s.program.title} text="Курс - программа, лекторы" to={`/admin/course/${s.program.id}`} />
          {s.scope === "active" && <NavRow icon={<Icon.calendar />} title="Как видят клиенты" to={`/course/${item.id}`} />}
        </div>
      </Section>

      <Section title={item.editable ? "Поля - нажмите, чтобы изменить" : "Поля"}>
        <FieldRows item={item} onSaved={changed} skip={["status"]} />
      </Section>

      <Section title="Действия">
        <div className="a-actions">
          {!s.program.archived && (
            <Button variant="secondary" onClick={() => navigate(`/admin/new/ses?copy=${item.id}`)}>
              <Icon.copy />
              Скопировать на новые даты
            </Button>
          )}
          {s.scope === "active" && (
            <Button
              variant="secondary"
              busy={busy}
              onClick={() =>
                run(async () => {
                  const result = await admin.archive("ses", item.id, true);
                  changed(result.item);
                  return result.message;
                }, "Убрать проведение из афиши в архив? Записи клиентов останутся.")
              }
            >
              <Icon.box />
              В архив
            </Button>
          )}
          {s.scope === "archived" && (
            <Button
              variant="secondary"
              busy={busy}
              onClick={() =>
                run(async () => {
                  const result = await admin.archive("ses", item.id, false);
                  changed(result.item);
                  return result.message;
                })
              }
            >
              Вернуть из архива
            </Button>
          )}
          {s.scope !== "active" && (
            <Button
              variant="danger"
              busy={busy}
              onClick={() =>
                run(async () => {
                  const result = await admin.remove("ses", item.id);
                  refreshSummary();
                  navigate(`/admin/afisha?scope=${s.scope}`, { replace: true });
                  return result.message;
                }, "Удалить проведение навсегда? Вернуть не получится.")
              }
            >
              <Icon.trash />
              Удалить навсегда
            </Button>
          )}
        </div>
        {summary.applications && s.scope === "active" && (
          <p className="muted small">Удалить можно только из архива и без записей клиентов.</p>
        )}
      </Section>
    </div>
  );
}

export function PeopleScreen({ id }: { id: number }): ReactNode {
  const loaded = useLoad(() => admin.participants(id), [id]);
  const [showCancelled, setShowCancelled] = useState(false);
  const data = loaded.data;
  if (loaded.error) return <div className="page"><ErrorState error={loaded.error} onRetry={loaded.reload} /></div>;
  if (!data) return <div className="page"><Loading rows={2} /></div>;
  const total = data.confirmed.length + data.awaiting.length + data.waitlist.length;
  const seats = data.seats_left !== null ? `свободно ${data.seats_left} из ${data.session.capacity}` : "без ограничения мест";
  return (
    <div className="page">
      <AdminHeader over="Участники" title={data.session.title} text={`${data.session.dates} · ${seats}`} />
      {!total && <Empty icon={<Icon.people />} title="Пока никто не записался" />}
      <Group title="Место закреплено" rows={data.confirmed} />
      <Group title="Записаны, ждём оплату" rows={data.awaiting} />
      <Group title="Лист ожидания" rows={data.waitlist} />
      {data.cancelled.length > 0 && (
        <Section>
          <Button variant="ghost" block onClick={() => setShowCancelled((v) => !v)}>
            {showCancelled ? "Скрыть" : "Показать"} отменивших · {data.cancelled.length}
          </Button>
          {showCancelled && <Group rows={data.cancelled} />}
        </Section>
      )}
    </div>
  );
}

function Group({ title, rows }: { title?: string; rows: ParticipantRow[] }): ReactNode {
  if (!rows.length) return null;
  return (
    <Section title={title ? `${title} · ${rows.length}` : undefined}>
      <div className="list">
        {rows.map((row) => (
          <NavRow
            key={row.enrollment_id}
            icon={<Icon.user />}
            title={row.name}
            text={[
              row.phone_tail ? `…${row.phone_tail}` : null,
              row.seats > 1 ? `${row.seats} ${plural(row.seats, "место", "места", "мест")}` : null,
              row.order ? `№${row.order.number}` : null,
              row.in_bot ? null : "не в боте",
            ]
              .filter(Boolean)
              .join(" · ")}
            to={`/admin/client/${row.id}`}
          />
        ))}
      </div>
    </Section>
  );
}
