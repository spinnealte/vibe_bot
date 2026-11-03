// Страница проведения: даты по дням, площадка, лекторы, тарифы, описание, программа - и «Записаться».

import type { ReactNode } from "react";

import { api, type CourseDetails } from "../api";
import { Icon } from "../icons";
import { navigate } from "../router";
import { openUrl } from "../telegram";
import {
  ActionBar,
  ActionButton,
  Badge,
  Button,
  Cover,
  EditSessionButton,
  ErrorState,
  Loading,
  ManagerButton,
  Rich,
  clean,
  plural,
  statusTone,
  useApp,
  useLoad,
} from "../ui";

export function CourseScreen({ id }: { id: number }): ReactNode {
  const course = useLoad(() => api.course(id), [id]);
  if (course.loading && !course.data) {
    return (
      <div className="page">
        <Loading rows={2} />
      </div>
    );
  }
  if (course.error || !course.data) {
    return (
      <div className="page">
        <ErrorState error={course.error!} onRetry={course.reload} />
      </div>
    );
  }
  return <CourseView course={course.data} />;
}

function CourseView({ course }: { course: CourseDetails }): ReactNode {
  const { me } = useApp();
  return (
    <div className="page">
      <div className="hero">
        <Cover src={course.cover_url} alt={course.title} full />
        <EditSessionButton id={course.id} />
      </div>
      <div className="hero-body">
        <div className="row wrap" style={{ marginBottom: 10 }}>
          {course.mine ? (
            <Badge tone="accent">✓ {clean(course.mine.label)}</Badge>
          ) : (
            course.status_label && <Badge tone={statusTone(course.status)}>{clean(course.status_label)}</Badge>
          )}
          <Badge>{clean(course.format_label)}</Badge>
          {course.level_label && <Badge>{clean(course.level_label)}</Badge>}
        </div>
        <h1>{course.title}</h1>
        {course.subtitle && <p className="course-sub" style={{ fontSize: 15 }}>{course.subtitle}</p>}

        <div className="facts">
          {course.duration_hours && <Fact label="Объём (академ/час)" value={String(course.duration_hours)} />}
          {me.config.applications && course.seats_left !== null && (
            <Fact
              label="Свободно"
              value={course.seats_left > 0 ? `${course.seats_left} ${plural(course.seats_left, "место", "места", "мест")}` : "мест нет"}
            />
          )}
          {course.deadline && <Fact label="Запись до" value={course.deadline} />}
          {course.specialties.length > 0 && (
            <Fact
              label={course.specialties.length > 1 ? "Направления обучения" : "Направление обучения"}
              value={course.specialties.join(", ")}
              wide
            />
          )}
        </div>
      </div>

      {/* Дни проведения - блоками по два в ряд (двухдневный курс - слева и справа поровну). Дат ещё нет -
          так и пишем: дни - единственное место на странице, где видны даты. */}
      <div className="section-title">Дни проведения</div>
      {course.days.length > 0 ? (
        <div className="days-grid">
          {course.days.map((day) => {
            const date = new Date(`${day.date}T12:00:00`);
            const weekday = date.toLocaleDateString("ru-RU", { weekday: "long" });
            return (
              <div className="card day-card" key={`${day.date}-${day.time}`}>
                <div className="day-card-date">{date.toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}</div>
                <div className="day-card-weekday">{weekday.charAt(0).toUpperCase() + weekday.slice(1)}</div>
                {day.time && (
                  <div className="day-card-time">
                    <Icon.clock />
                    {day.time}
                  </div>
                )}
                {day.topic && <div className="small">{day.topic}</div>}
                {day.venue && <div className="muted small">{day.venue.name}, {day.venue.address}</div>}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="card day-card wide">
          <div className="day-card-date">Даты уточняются</div>
        </div>
      )}

      {course.venue && (
        <>
          <div className="section-title">Место проведения</div>
          <div className="card card-body">
            <div style={{ fontWeight: 600 }}>{course.venue.name}</div>
            <div className="muted">{course.venue.address}</div>
            {course.venue.directions_html && <Rich html={course.venue.directions_html} className="small" />}
            {course.venue.map_url && (
              <div style={{ marginTop: 10 }}>
                <Button size="small" variant="secondary" onClick={() => openUrl(course.venue!.map_url!)}>
                  <Icon.pin />
                  Открыть на карте
                </Button>
              </div>
            )}
          </div>
        </>
      )}

      {course.lecturers_full.length > 0 && (
        <>
          <div className="section-title">{course.lecturers_full.length > 1 ? "Лекторы" : "Лектор"}</div>
          <div className="list">
            {course.lecturers_full.map((lecturer) => (
              <div className="lecturer" key={lecturer.id}>
                {lecturer.photo_url ? (
                  <img className="avatar" src={lecturer.photo_url} alt="" loading="lazy" />
                ) : (
                  <div className="avatar">{initials(lecturer.name)}</div>
                )}
                <div className="grow">
                  <div style={{ fontWeight: 600 }}>{lecturer.name}</div>
                  {lecturer.regalia && <div className="muted small">{lecturer.regalia}</div>}
                  {lecturer.bio_html && <Rich html={lecturer.bio_html} className="small" />}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {course.prices.length > 0 && (
        <>
          <div className="section-title">Стоимость</div>
          <div className="list">
            {course.prices.map((price) => (
              <div className="price-option" key={price.id}>
                <span>{price.label}</span>
                <b style={{ whiteSpace: "nowrap" }}>{price.amount_text}</b>
              </div>
            ))}
          </div>
        </>
      )}

      {course.description_html && (
        <>
          <div className="section-title">О курсе</div>
          <div className="card card-body">
            <Rich html={course.description_html} />
          </div>
        </>
      )}

      <div className="stack" style={{ marginTop: 16 }}>
        {course.has_program && (
          <Button variant="secondary" block onClick={() => navigate(`/program/${course.program_id}?course=${course.id}`)}>
            <Icon.book />
            Программа курса
          </Button>
        )}
        <ManagerButton url={course.manager_url} label="Задать вопрос менеджеру" />
      </div>

      <CourseAction course={course} />
    </div>
  );
}

function CourseAction({ course }: { course: CourseDetails }): ReactNode {
  const { me, toast } = useApp();
  if (!me.config.applications) {
    // Афиша без записи в приложении (как MVP бота): запись - через менеджера.
    if (!course.manager_url) return null;
    return (
      <ActionBar>
        <Button block onClick={() => openUrl(course.manager_url!)}>
          Записаться через менеджера
        </Button>
      </ActionBar>
    );
  }
  // Уже записан - кнопки внизу нет: статус записи - значком на обложке, сама запись - во вкладке «Мои записи».
  if (course.mine) return null;
  if (course.accepts_applications) {
    const enroll = () =>
      navigate(me.step === "done" ? `/enroll/${course.id}` : `/start?next=${encodeURIComponent(`/enroll/${course.id}`)}`);
    return (
      <ActionBar note={course.goes_to_waitlist ? "Свободных мест нет - запишем в лист ожидания" : undefined}>
        <Button block onClick={enroll}>
          {course.goes_to_waitlist ? "Встать в лист ожидания" : "Записаться"}
        </Button>
      </ActionBar>
    );
  }
  if (course.announced) {
    return (
      <ActionBar note="Даты ещё уточняются">
        <ActionButton
          block
          onClick={async () => {
            const result = await api.interest(course.program_id);
            toast(result.message, "ok");
          }}
        >
          <Icon.bell />
          Сообщить о наборе
        </ActionButton>
      </ActionBar>
    );
  }
  return (
    <ActionBar note="Запись на это проведение закрыта">
      {course.manager_url ? (
        <Button block variant="secondary" onClick={() => openUrl(course.manager_url!)}>
          Спросить менеджера
        </Button>
      ) : (
        <Button block variant="secondary" onClick={() => navigate("/")}>
          Другие даты
        </Button>
      )}
    </ActionBar>
  );
}

function Fact({ label, value, wide }: { label: string; value: string; wide?: boolean }): ReactNode {
  return (
    <div className={`fact ${wide ? "wide" : ""}`}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  );
}

export function initials(name: string | null | undefined): string {
  if (!name) return "?";
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

export function ProgramScreen({ id, course }: { id: number; course?: number }): ReactNode {
  const program = useLoad(() => api.program(id), [id]);
  if (program.loading && !program.data) {
    return (
      <div className="page">
        <Loading rows={1} />
      </div>
    );
  }
  if (program.error || !program.data) {
    return (
      <div className="page">
        <ErrorState error={program.error!} onRetry={program.reload} />
      </div>
    );
  }
  const data = program.data;
  return (
    <div className="page">
      <div className="page-header">
        <div className="muted small" style={{ marginBottom: 4 }}>
          Программа курса
        </div>
        <h1>{data.title}</h1>
        {data.subtitle && <p>{data.subtitle}</p>}
      </div>
      <div className="card card-body">
        {data.html ? <Rich html={data.html} /> : <div className="muted">Программа курса скоро будет известна</div>}
      </div>
      <div className="stack" style={{ marginTop: 16 }}>
        {course && (
          <Button variant="secondary" block onClick={() => navigate(`/course/${course}`)}>
            К датам и записи
          </Button>
        )}
        <ManagerButton url={data.manager_url} label="Задать вопрос менеджеру" />
      </div>
    </div>
  );
}
