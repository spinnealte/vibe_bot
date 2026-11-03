// Новая запись - как мастер «➕ Добавить» в боте, только одной формой: поля по порядку шагов, лишние к выбранному
// (площадка у онлайна) скрыты. У проведения сначала курс - карточками; копия проведения спрашивает только дни.
// Перед сохранением - предпросмотр «как увидят клиенты».

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { ApiError, type CourseDetails } from "../api";
import { Icon } from "../icons";
import { navigate } from "../router";
import { CourseCardView } from "../screens/Schedule";
import { ActionBar, ActionButton, Button, Cover, ErrorState, Field, Loading, Rich, Sheet, useApp, useLoad } from "../ui";
import { admin, type CourseRow, type FieldSpec, type Kind, type Option } from "./api";
import { AdminHeader, Section, Thumb, useAdmin } from "./common";
import { FieldInput, applies, emptyValue, fromValue, hintOf, toApi } from "./editors";

interface Loaded {
  options: Record<string, Option[]>;
  courses: CourseRow[];
  values: Record<string, unknown>;
  copyOf: Option | null;
}

type Preview = { card?: CourseDetails; course?: CourseRow; values?: Record<string, unknown> };

const ITEM_PATHS: Record<Kind, (id: number) => string> = {
  crs: (id) => `/admin/course/${id}`,
  ses: (id) => `/admin/session/${id}`,
  lec: (id) => `/admin/dict/lec/${id}`,
  ven: (id) => `/admin/dict/ven/${id}`,
  spec: (id) => `/admin/dict/spec/${id}`,
  pos: (id) => `/admin/dict/pos/${id}`,
};

export function NewScreen({ kind, copy, course }: { kind: Kind; copy?: number; course?: number }): ReactNode {
  const { specs } = useAdmin();
  const spec = specs.kinds[kind];
  const loaded = useLoad<Loaded>(async () => {
    const form = await admin.newForm(kind);
    const values: Record<string, unknown> = {};
    for (const field of spec.fields) values[field.name] = emptyValue(field);
    if (kind === "ses") values.status = "registration_open";
    let copyOf: Option | null = null;
    if (copy) {
      const source = await admin.copy(copy);
      for (const field of spec.fields) if (field.name in source.values) values[field.name] = fromValue(field, source.values[field.name]);
      copyOf = source.program;
      Object.assign(form.options, source.options);
    } else if (course) {
      values.program_id = course;
    }
    return { options: form.options, courses: form.courses ?? [], values, copyOf };
  }, [kind, copy, course]);

  if (loaded.error) return <div className="page"><ErrorState error={loaded.error} onRetry={loaded.reload} /></div>;
  if (!loaded.data) return <div className="page"><Loading rows={2} /></div>;
  return <EntityForm kind={kind} initial={loaded.data} copy={Boolean(copy)} />;
}

function EntityForm({ kind, initial, copy }: { kind: Kind; initial: Loaded; copy: boolean }): ReactNode {
  const { specs, refreshSummary } = useAdmin();
  const { toast, fail } = useApp();
  const spec = specs.kinds[kind];
  const [values, setValues] = useState<Record<string, unknown>>(initial.values);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const refs = useRef<Record<string, HTMLDivElement | null>>({});

  const course = initial.courses.find((c) => c.id === values.program_id);
  const choosingCourse = kind === "ses" && !values.program_id;
  const visible = spec.fields.filter((f) => f.name !== "program_id" && applies(f, values));
  const ordered = copy ? [...visible.filter((f) => f.name === "days"), ...visible.filter((f) => f.name !== "days")] : visible;

  const payload = () => {
    const body: Record<string, unknown> = {};
    for (const field of spec.fields) if (applies(field, values)) body[field.name] = toApi(field, values[field.name]);
    return body;
  };
  const showError = (error: unknown) => {
    if (error instanceof ApiError && typeof error.extra.field === "string") {
      const field = error.extra.field;
      setErrors({ [field]: error.message });
      refs.current[field]?.scrollIntoView({ behavior: "smooth", block: "center" });
      toast(error.message, "error");
    } else fail(error);
  };
  const check = async () => {
    setBusy(true);
    setErrors({});
    try {
      setPreview(await admin.preview(kind, payload()));
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (copy) refs.current.days?.scrollIntoView({ block: "center" });
  }, [copy]);

  if (choosingCourse) {
    return <CoursePicker courses={initial.courses} onPick={(id) => setValues((v) => ({ ...v, program_id: id }))} />;
  }
  const title = copy ? "Копия проведения" : spec.new_title;
  return (
    <div className="page">
      <AdminHeader over={copy ? "Новые даты - остальное как в исходном" : "Новая запись"} title={title} />
      {kind === "ses" && (
        <div className="list a-picked">
          <div className="list-item static">
            <Thumb src={course?.cover_url ?? "/api/files/default"} />
            <span className="grow">
              <span className="label">Курс</span>
              <span className="value">{course?.title ?? initial.copyOf?.title ?? "Курс"}</span>
            </span>
            {!copy && (
              <Button variant="ghost" size="small" onClick={() => setValues((v) => ({ ...v, program_id: null }))}>
                Другой
              </Button>
            )}
          </div>
        </div>
      )}
      <div className="stack a-form">
        {ordered.map((field) => (
          <div key={field.name} ref={(el) => { refs.current[field.name] = el; }}>
            <Field
              label={`${field.label}${field.required ? "" : " · необязательно"}`}
              hint={hintOf(kind, field)}
              error={errors[field.name]}
            >
              <FieldInput
                kind={kind}
                field={field}
                value={values[field.name]}
                options={initial.options[field.name]}
                invalid={Boolean(errors[field.name])}
                onChange={(value) => {
                  setValues((v) => ({ ...v, [field.name]: value }));
                  if (errors[field.name]) setErrors({});
                }}
              />
            </Field>
          </div>
        ))}
      </div>
      <ActionBar>
        <Button block busy={busy} onClick={check}>
          Посмотреть, как будет
        </Button>
      </ActionBar>
      {preview && (
        <Sheet open onClose={() => setPreview(null)}>
          <h2>Всё верно?</h2>
          <PreviewBody kind={kind} preview={preview} fields={spec.fields} />
          <div className="stack">
            <ActionButton
              block
              onClick={async () => {
                try {
                  const saved = await admin.create(kind, payload());
                  toast(saved.message, "ok");
                  refreshSummary();
                  navigate(ITEM_PATHS[kind](saved.item.id), { replace: true });
                } catch (error) {
                  setPreview(null);
                  showError(error);
                }
              }}
            >
              <Icon.check />
              Сохранить
            </ActionButton>
            <Button variant="ghost" block onClick={() => setPreview(null)}>
              Изменить
            </Button>
          </div>
        </Sheet>
      )}
    </div>
  );
}

function CoursePicker({ courses, onPick }: { courses: CourseRow[]; onPick: (id: number) => void }): ReactNode {
  const [query, setQuery] = useState("");
  const shown = useMemo(
    () => courses.filter((c) => c.title.toLowerCase().includes(query.trim().toLowerCase())),
    [courses, query],
  );
  return (
    <div className="page">
      <AdminHeader over="Новое проведение" title="Какой курс проводим?" text="Курса нет в списке - сначала добавьте его в «Каталог → Курсы»." />
      {courses.length > 5 && (
        <div className="a-search">
          <Icon.search />
          <input className="input" placeholder="Название курса" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
      )}
      {!courses.length && (
        <Button block onClick={() => navigate("/admin/new/crs", { replace: true })}>
          <Icon.plus />
          Новый курс
        </Button>
      )}
      <div className="a-course-grid">
        {shown.map((course) => (
          <button key={course.id} className="card a-course-pick" onClick={() => onPick(course.id)}>
            <Cover src={course.cover_url} alt={course.title} />
            <div className="card-body">
              <div className="a-nav-title">{course.title}</div>
              <div className="a-nav-text">{course.lecturers.join(", ")}</div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function PreviewBody({ kind, preview, fields }: { kind: Kind; preview: Preview; fields: FieldSpec[] }): ReactNode {
  if (kind === "ses" && preview.card) {
    const card = preview.card;
    return (
      <div className="stack a-preview">
        <div className="a-preview-card">
          <CourseCardView item={card} preview />
        </div>
        <Section title="Дни">
          {card.days.length ? (
            <div className="order-box">
              {card.days.map((day) => (
                <div className="order-line" key={day.date}>
                  <span>{day.label}</span>
                  <b>{day.time ?? "время уточняется"}</b>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted small">Даты уточняются.</p>
          )}
        </Section>
        {card.prices.length > 0 && (
          <Section title="Тарифы">
            <div className="order-box">
              {card.prices.map((price) => (
                <div className="order-line" key={price.label}>
                  <span>{price.label}</span>
                  <b>{price.amount_text}</b>
                </div>
              ))}
            </div>
          </Section>
        )}
      </div>
    );
  }
  if (kind === "crs" && preview.course) {
    const course = preview.course;
    return (
      <div className="stack a-preview">
        <div className="card">
          <Cover src={course.cover_url} alt={course.title} />
          <div className="card-body">
            <div className="course-title">{course.title}</div>
            {course.subtitle && <div className="course-sub">{course.subtitle}</div>}
            <div className="muted small">{[course.lecturers.join(", "), course.specialties.join(", ")].filter(Boolean).join(" · ")}</div>
          </div>
        </div>
        {course.program_html && (
          <div className="card card-body a-program">
            <Rich html={course.program_html} />
          </div>
        )}
      </div>
    );
  }
  const values = preview.values ?? {};
  return (
    <div className="order-box a-preview">
      {fields.map((field) => {
        const value = values[field.name];
        if (value === null || value === undefined || value === "") return null;
        return (
          <div className="order-line" key={field.name}>
            <span className="muted">{field.label}</span>
            <b>{field.kind === "html" ? "есть" : String(value)}</b>
          </div>
        );
      })}
    </div>
  );
}
