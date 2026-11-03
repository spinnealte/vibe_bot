// Каталог: курсы (библиотека - афишу не меняет), справочники (лекторы, площадки, направления, должности), архив.
// Карточка записи - все поля строками, нажатие - правка; «В архив» / «Вернуть»; удалить - только из архива.

import { useState, type ReactNode } from "react";

import { Icon } from "../icons";
import { navigate } from "../router";
import { Badge, Button, Cover, Empty, ErrorState, Loading, Rich, useLoad } from "../ui";
import { admin, type Item, type Kind } from "./api";
import { AdminHeader, HeaderAction, NavRow, Section, Thumb, useAdmin, useRun } from "./common";
import { FieldRows } from "./editors";

const DICTS: { kind: Kind; icon: () => ReactNode; text: string }[] = [
  { kind: "lec", icon: Icon.user, text: "ФИО, регалии, биография" },
  { kind: "ven", icon: Icon.pin, text: "Где проходят занятия" },
  { kind: "spec", icon: Icon.wave, text: "Для кого курсы - выбирают при регистрации" },
  { kind: "pos", icon: Icon.card, text: "Выбирают при регистрации" },
];

export function CatalogScreen(): ReactNode {
  const { specs, summary, refreshSummary } = useAdmin();
  const [busy, run] = useRun();
  const archived = summary.archive.courses + summary.archive.sessions + summary.archive.past;
  return (
    <div className="page">
      <AdminHeader over="Админка" title="Каталог" text="Курсы и справочники - из них собирается афиша" />
      <Section>
        <div className="list">
          <NavRow icon={<Icon.book />} title="Курсы" text="Программа, лекторы, обложка" count={summary.courses} to="/admin/courses" />
        </div>
      </Section>
      <Section title="Справочники">
        <div className="list">
          {DICTS.map((dict) => (
            <NavRow
              key={dict.kind}
              icon={dict.icon()}
              title={specs.kinds[dict.kind].title}
              text={dict.text}
              count={summary.dictionaries[dict.kind as "lec"]}
              to={`/admin/dict/${dict.kind}`}
            />
          ))}
        </div>
      </Section>
      <Section title="Ещё">
        <div className="list">
          <NavRow icon={<Icon.box />} title="Архив" text="Убранные курсы и проведения, прошедшие" count={archived} to="/admin/archive" />
          {summary.applications && (
            <NavRow icon={<Icon.clock />} title="Листы ожидания" count={summary.waitlists ?? 0} to="/admin/waitlists" />
          )}
          <NavRow
            icon={<Icon.share />}
            title="Выгрузка клиентов"
            text={busy ? "Готовлю файл…" : "CSV для Excel - файлом в чат с ботом"}
            onClick={() => void run(() => admin.exportClients().then((r) => (refreshSummary(), r.message)))}
          />
        </div>
      </Section>
    </div>
  );
}

export function ArchiveScreen(): ReactNode {
  const { summary } = useAdmin();
  return (
    <div className="page">
      <AdminHeader over="Каталог" title="Архив" text="Здесь ничего не правится: вернуть, скопировать или удалить навсегда" />
      <div className="list">
        <NavRow icon={<Icon.book />} title="Курсы в архиве" count={summary.archive.courses} to="/admin/courses?scope=archived" />
        <NavRow icon={<Icon.calendar />} title="Проведения в архиве" text="Убраны из афиши вручную" count={summary.archive.sessions} to="/admin/afisha?scope=archived" />
        <NavRow icon={<Icon.clock />} title="Прошедшие мероприятия" text="Уходят из афиши в день начала" count={summary.archive.past} to="/admin/afisha?scope=past" />
      </div>
    </div>
  );
}

export function CoursesScreen({ scope }: { scope: "active" | "archived" }): ReactNode {
  const loaded = useLoad(() => admin.courses(scope), [scope]);
  return (
    <div className="page">
      <AdminHeader
        over={scope === "archived" ? "Архив" : "Каталог"}
        title={scope === "archived" ? "Курсы в архиве" : "Курсы"}
        text={scope === "archived" ? undefined : "Библиотека курсов. В афишу курс попадает проведением с датами."}
        action={scope === "active" ? <HeaderAction icon={<Icon.plus />} label="Новый" onClick={() => navigate("/admin/new/crs")} /> : undefined}
      />
      {loaded.error && <ErrorState error={loaded.error} onRetry={loaded.reload} />}
      {loaded.loading && !loaded.data && <Loading rows={2} />}
      {loaded.data && !loaded.data.items.length && (
        <Empty icon={<Icon.book />} title="Курсов пока нет">
          {scope === "active" && (
            <Button size="small" onClick={() => navigate("/admin/new/crs")}>
              <Icon.plus />
              Новый курс
            </Button>
          )}
        </Empty>
      )}
      {loaded.data && loaded.data.items.length > 0 && (
        <div className="list">
          {loaded.data.items.map((course) => (
            <button key={course.id} className="list-item a-session-row" onClick={() => navigate(`/admin/course/${course.id}`)}>
              <Thumb src={course.cover_url} />
              <span className="grow">
                <span className="a-nav-title">{course.title}</span>
                <span className="a-nav-text">{[course.lecturers.join(", "), course.specialties.join(", ")].filter(Boolean).join(" · ")}</span>
                <span className="a-order-tags">
                  {course.sessions_in_afisha > 0 ? (
                    <Badge tone="ok">в афише: {course.sessions_in_afisha}</Badge>
                  ) : (
                    scope === "active" && <Badge>нет в афише</Badge>
                  )}
                  {course.waiting > 0 && <Badge tone="warn">ждут: {course.waiting}</Badge>}
                  {!course.has_program && <Badge tone="danger">без программы</Badge>}
                  {course.archived_at && <span className="muted small">в архиве с {course.archived_at}</span>}
                </span>
              </span>
              <Icon.chevron />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function CourseAdminScreen({ id }: { id: number }): ReactNode {
  const loaded = useLoad(() => admin.item("crs", id), [id]);
  const { summary, refreshSummary } = useAdmin();
  const [busy, run] = useRun();
  const [program, setProgram] = useState(false);
  const item = loaded.data;
  if (loaded.error) return <div className="page"><ErrorState error={loaded.error} onRetry={loaded.reload} /></div>;
  if (!item?.course) return <div className="page"><Loading rows={2} /></div>;
  const course = item.course;
  const changed = (fresh: Item) => {
    loaded.setData(fresh);
    refreshSummary();
  };
  return (
    <div className="page">
      <div className="card a-hero">
        <Cover src={course.cover_url} alt={course.title} full />
        <div className="card-body">
          <div className="a-over">Курс{item.active ? "" : " · в архиве"}</div>
          <h1 className="a-hero-title">{course.title}</h1>
          {course.subtitle && <div className="muted">{course.subtitle}</div>}
          <div className="a-order-tags">
            <Badge>{course.format_label}</Badge>
            {course.level_label && <Badge>{course.level_label}</Badge>}
            {course.duration_hours && <Badge>{course.duration_hours} ак. ч.</Badge>}
          </div>
        </div>
      </div>
      {!item.active && <div className="notice warn">В архиве с {course.archived_at}. Правка - после возвращения из архива.</div>}

      <Section>
        <div className="list">
          {item.active && (
            <NavRow icon={<Icon.plus />} title="Новое проведение этого курса" text="Даты, площадка, тарифы" to={`/admin/new/ses?course=${item.id}`} />
          )}
          <NavRow icon={<Icon.calendar />} title="В афише" count={course.sessions_in_afisha} to="/admin/afisha" />
          {summary.applications && (
            <NavRow icon={<Icon.clock />} title="Лист ожидания" text="Просили сообщить о наборе" count={course.waiting} to={`/admin/waitlist/${item.id}`} />
          )}
        </div>
      </Section>

      {item.html.program_html && (
        <Section title="Программа">
          <div className={`card card-body a-program ${program ? "open" : ""}`}>
            <Rich html={item.html.program_html} />
          </div>
          <Button variant="ghost" block onClick={() => setProgram((v) => !v)}>
            {program ? "Свернуть" : "Показать полностью"}
          </Button>
        </Section>
      )}

      <Section title={item.editable ? "Поля - нажмите, чтобы изменить" : "Поля"}>
        <FieldRows item={item} onSaved={changed} />
      </Section>

      <Section title="Действия">
        <div className="a-actions">
          {item.active ? (
            <Button
              variant="secondary"
              busy={busy}
              onClick={() =>
                run(async () => {
                  const result = await admin.archive("crs", item.id, true);
                  changed(result.item);
                  return result.message;
                }, "Отправить курс в архив? В новых проведениях его не будет.")
              }
            >
              <Icon.box />
              В архив
            </Button>
          ) : (
            <>
              <Button
                variant="secondary"
                busy={busy}
                onClick={() =>
                  run(async () => {
                    const result = await admin.archive("crs", item.id, false);
                    changed(result.item);
                    return result.message;
                  })
                }
              >
                Вернуть из архива
              </Button>
              <Button
                variant="danger"
                busy={busy}
                onClick={() =>
                  run(async () => {
                    const result = await admin.remove("crs", item.id);
                    refreshSummary();
                    navigate("/admin/courses?scope=archived", { replace: true });
                    return result.message;
                  }, `Удалить курс навсегда${course.sessions_total ? ` вместе с проведениями (${course.sessions_total})` : ""}? Вернуть не получится.`)
                }
              >
                <Icon.trash />
                Удалить навсегда
              </Button>
            </>
          )}
        </div>
      </Section>
    </div>
  );
}

export function DictScreen({ kind }: { kind: Kind }): ReactNode {
  const { specs } = useAdmin();
  const loaded = useLoad(() => admin.dictionary(kind), [kind]);
  const items = loaded.data?.items ?? [];
  const active = items.filter((i) => i.active);
  const archived = items.filter((i) => !i.active);
  return (
    <div className="page">
      <AdminHeader
        over="Справочник"
        title={specs.kinds[kind].title}
        text={archived.length ? `В архиве: ${archived.length} - их не предлагают в новых курсах и при регистрации` : undefined}
        action={<HeaderAction icon={<Icon.plus />} label="Добавить" onClick={() => navigate(`/admin/new/${kind}`)} />}
      />
      {loaded.error && <ErrorState error={loaded.error} onRetry={loaded.reload} />}
      {loaded.loading && !loaded.data && <Loading rows={1} />}
      {loaded.data && !items.length && <Empty icon={<Icon.layers />} title="Пока пусто" text="Нажмите «Добавить»." />}
      {active.length > 0 && (
        <div className="list">
          {active.map((row) => (
            <NavRow key={row.id} title={row.title} text={row.subtitle ?? undefined} to={`/admin/dict/${kind}/${row.id}`} />
          ))}
        </div>
      )}
      {archived.length > 0 && (
        <Section title="В архиве">
          <div className="list muted-list">
            {archived.map((row) => (
              <NavRow key={row.id} icon={<Icon.box />} title={row.title} text={row.subtitle ?? undefined} to={`/admin/dict/${kind}/${row.id}`} />
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}

export function EntryScreen({ kind, id }: { kind: Kind; id: number }): ReactNode {
  const { specs, refreshSummary } = useAdmin();
  const loaded = useLoad(() => admin.item(kind, id), [kind, id]);
  const [busy, run] = useRun();
  const item = loaded.data;
  if (loaded.error) return <div className="page"><ErrorState error={loaded.error} onRetry={loaded.reload} /></div>;
  if (!item) return <div className="page"><Loading rows={1} /></div>;
  return (
    <div className="page">
      <AdminHeader over={specs.kinds[kind].title} title={item.title} text={item.active ? undefined : <Badge tone="warn">в архиве</Badge>} />
      {!item.active && (
        <div className="notice warn">В архиве: не предлагается в новых курсах и при регистрации. У старых данных остаётся.</div>
      )}
      <Section title="Поля - нажмите, чтобы изменить">
        <FieldRows item={item} onSaved={loaded.setData} />
      </Section>
      <Section>
        <Button
          variant="secondary"
          block
          busy={busy}
          onClick={() =>
            run(async () => {
              const result = await admin.archive(kind, item.id, item.active);
              loaded.setData(result.item);
              refreshSummary();
              return result.message;
            })
          }
        >
          {item.active ? (
            <>
              <Icon.box />В архив
            </>
          ) : (
            "Вернуть из архива"
          )}
        </Button>
      </Section>
    </div>
  );
}
