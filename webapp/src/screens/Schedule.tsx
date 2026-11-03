// «Расписание»: все проведения из афиши карточками, фильтры сверху. Нажатие - страница курса.

import { useMemo, useState, type ReactNode } from "react";

import { api, type CourseCard } from "../api";
import { Icon } from "../icons";
import { navigate } from "../router";
import { Badge, Cover, DragScroll, EditSessionButton, Empty, ErrorState, Loading, ManagerButton, clean, plural, statusTone, useApp, useLoad } from "../ui";

type Filter = "all" | "open" | "mine" | string;

export function Brand(): ReactNode {
  return (
    <div className="brand">
      <div className="brand-mark">
        <Icon.tooth />
      </div>
      <div>
        <div className="brand-name">АкваСтом</div>
        <div className="brand-sub">Учебный центр для стоматологов</div>
      </div>
    </div>
  );
}

export function ScheduleScreen(): ReactNode {
  const { me } = useApp();
  const schedule = useLoad(api.schedule);
  const [filter, setFilter] = useState<Filter>("all");
  const items = schedule.data?.items ?? [];

  const specialties = useMemo(
    () => [...new Set(items.flatMap((item) => item.specialties))].sort((a, b) => a.localeCompare(b, "ru")),
    [items],
  );
  const shown = items.filter((item) => {
    if (filter === "all") return true;
    if (filter === "open") return item.accepts_applications;
    if (filter === "mine") return item.mine !== null;
    return item.specialties.includes(filter);
  });
  const hasMine = items.some((item) => item.mine);

  return (
    <div className="page">
      <Brand />
      <div className="page-header" style={{ paddingTop: 0 }}>
        <h1>Расписание курсов</h1>
      </div>
      {schedule.loading && !schedule.data ? (
        <Loading />
      ) : schedule.error ? (
        <ErrorState error={schedule.error} onRetry={schedule.reload} />
      ) : items.length === 0 ? (
        <Empty icon={<Icon.calendar />} title="Скоро здесь появятся курсы" text="Новые даты уже готовятся">
          <ManagerButton url={schedule.data?.manager_url ?? null} label="Узнать о ближайших курсах" />
        </Empty>
      ) : (
        <>
          <DragScroll className="filters" role="tablist">
            <FilterChip value="all" current={filter} onSelect={setFilter}>
              Все · {items.length}
            </FilterChip>
            {me.config.applications && (
              <FilterChip value="open" current={filter} onSelect={setFilter}>
                Запись открыта
              </FilterChip>
            )}
            {hasMine && (
              <FilterChip value="mine" current={filter} onSelect={setFilter}>
                Мои курсы
              </FilterChip>
            )}
            {specialties.map((title) => (
              <FilterChip key={title} value={title} current={filter} onSelect={setFilter}>
                {title}
              </FilterChip>
            ))}
          </DragScroll>
          <div className="stack">
            {shown.map((item) => (
              <CourseCardView key={item.id} item={item} />
            ))}
            {shown.length === 0 && (
              <div className="state">
                {filter === "open" ? "Курсов с открытой записью пока нет" : "По этому направлению обучения скоро появятся"}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function FilterChip({ value, current, onSelect, children }: {
  value: Filter;
  current: Filter;
  onSelect: (value: Filter) => void;
  children: ReactNode;
}): ReactNode {
  return (
    <button className={`chip ${value === current ? "on" : ""}`} onClick={() => onSelect(value)} role="tab">
      {children}
    </button>
  );
}

/** Карточка проведения в расписании. preview - предпросмотр в мастере админки: проведения ещё нет, «Править» не нужна. */
export function CourseCardView({ item, preview }: { item: CourseCard; preview?: boolean }): ReactNode {
  const { me } = useApp();
  const editable = me.is_admin && !preview;
  return (
    <div className={`course-card-wrap ${editable ? "editable" : ""}`}>
      <button className="card course-card" onClick={() => navigate(`/course/${item.id}`)}>
        <Cover src={item.cover_url} alt={item.title}>
          <div className="badges">
            {item.mine ? (
              <Badge tone="mine">✓ {clean(item.mine.label)}</Badge>
            ) : (
              item.status_label && <Badge tone={statusTone(item.status)}>{clean(item.status_label)}</Badge>
            )}
            <Badge>{clean(item.format_label)}</Badge>
          </div>
        </Cover>
        <div className="card-body">
          <h3 className="course-title">{item.title}</h3>
          {item.subtitle && <p className="course-sub">{item.subtitle}</p>}
          <div className="meta">
            <span>
              <Icon.calendar />
              {item.dates}
            </span>
            {item.venue && (
              <span>
                <Icon.pin />
                {item.venue.name}
              </span>
            )}
            {item.lecturers.length > 0 && (
              <span>
                <Icon.user />
                {item.lecturers.join(", ")}
              </span>
            )}
          </div>
          <div className="card-footer">
            <div className="price">
              {item.price_from ? (
                <>
                  {item.prices_count > 1 && <small>от </small>}
                  {item.price_from.text}
                </>
              ) : (
                <small>Стоимость уточнит менеджер</small>
              )}
            </div>
            {me.config.applications && item.seats_left !== null && item.seats_left > 0 && item.seats_left <= 5 ? (
              <Badge tone="warn">
                Осталось {item.seats_left} {plural(item.seats_left, "место", "места", "мест")}
              </Badge>
            ) : (
              <Icon.chevron />
            )}
          </div>
        </div>
      </button>
      {editable && <EditSessionButton id={item.id} />}
    </div>
  );
}
