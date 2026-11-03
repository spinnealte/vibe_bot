// Поля админки: ввод каждого вида (как шаги мастера в боте, только формой), правка одного поля в окне снизу,
// строки карточки со значениями. Проверяет ввод сервер - теми же функциями, что бот.

import { useEffect, useRef, useState, type ReactNode } from "react";

import { ApiError } from "../api";
import { Icon } from "../icons";
import { haptic } from "../telegram";
import { ActionButton, Button, Field, Sheet, Spinner, useApp } from "../ui";
import { admin, type Day, type FieldSpec, type Item, type Kind, type Option, type Photo, type PriceValue } from "./api";
import { InfoRow, excerpt, useAdmin } from "./common";

export interface PriceDraft {
  kind: string;
  label: string;
  rubles: string;
}

/** Подсказки к полям - для формы в приложении (у бота свои: «пришлите сообщением» и т. п.). */
const HINTS: Record<string, string> = {
  full_name: "Фамилия, имя и отчество - как в сертификате.",
  regalia: "Степень, специализация, стаж - одной строкой.",
  bio_html: "Пара абзацев о лекторе.",
  name: "Как называть площадку в расписании.",
  address: "Например: Санкт-Петербург, Невский пр., 1",
  map_url: "Ссылка на Яндекс или Google Карты.",
  directions_text: "Как пройти: вход, этаж, парковка.",
  "spec.title": "Так направление увидят клиенты при регистрации.",
  "pos.title": "Так должность увидят клиенты при регистрации.",
  "crs.title": "Как курс будет называться в афише.",
  short_description: "Одна строка под названием.",
  specialties: "Для кого курс.",
  lecturers: "Нужного нет - сначала добавьте его в «Лекторы».",
  level: "Можно не указывать.",
  default_format: "Как обычно проходит курс.",
  duration_hours: "Академических часов, числом.",
  program_html: "Наберите здесь или загрузите файл .docx / .txt - жирный, курсив и списки сохранятся.",
  description_html: "Показывается в афише, если у курса нет программы.",
  cover_file_id: "Лучше горизонтальная картинка. Без обложки - стандартная.",
  "ses.cover_file_id": "Своя обложка этого проведения. Без неё - обложка курса.",
  days: "Даты - с завтрашнего дня, время можно не указывать.",
  format: "Как пройдёт это проведение.",
  venue_id: "Нужной нет - сначала добавьте её в «Площадки».",
  online_url: "Ссылка на трансляцию для участников. В афише не показывается.",
  capacity: "Пусто - без ограничения мест.",
  prices: "Отметьте категории и укажите цену. Без тарифов в афише - «стоимость уточняется».",
  registration_deadline: "До какого момента принимаем записи. Пусто - до начала.",
  status: "Что показать в афише.",
  title_override: "Пусто - будет название курса.",
};

export function hintOf(kind: Kind, field: FieldSpec): string | undefined {
  return HINTS[`${kind}.${field.name}`] ?? HINTS[field.name];
}

/** Подходит ли поле к уже выбранному (площадка - только у очного формата) - как пропуск шага в мастере бота. */
export function applies(field: FieldSpec, values: Record<string, unknown>): boolean {
  return !field.when || field.when.values.includes(String(values[field.when.field] ?? ""));
}

// --- Значения: из карточки и копии → форма → запрос ----------------------------------------------------------------

export function fromItem(field: FieldSpec, item: Item): unknown {
  const value = item.values[field.name];
  switch (field.kind) {
    case "html":
    case "document":
      return item.html[field.name] ?? "";
    case "photo":
      return item.photos[field.name] ?? null;
    case "datetime":
      return item.display[`${field.name}_input`] ?? "";
    default:
      return fromValue(field, value);
  }
}

/** Значение из данных сервера (копия проведения) в вид формы. */
export function fromValue(field: FieldSpec, value: unknown): unknown {
  switch (field.kind) {
    case "number":
      return value === null || value === undefined ? "" : String(value);
    case "multi":
      return (value as number[] | null) ?? [];
    case "days":
      return (value as Day[] | null) ?? [];
    case "prices":
      return ((value as PriceValue[] | null) ?? []).map((p) => ({ kind: p.kind, label: p.label, rubles: String(p.amount / 100) }));
    case "choice":
    case "ref":
    case "course":
    case "photo":
      return value ?? null;
    case "datetime":
      return typeof value === "string" ? value.slice(0, 16) : "";
    default:
      return (value as string | null) ?? "";
  }
}

export function emptyValue(field: FieldSpec): unknown {
  return fromValue(field, null);
}

export function toApi(field: FieldSpec, value: unknown): unknown {
  switch (field.kind) {
    case "prices":
      return (value as PriceDraft[]).map((p) => ({
        kind: p.kind,
        label: p.label,
        amount: Math.round(Number(p.rubles.replace(/[\s ₽]/g, "").replace(",", ".")) * 100),
      }));
    case "multi":
    case "days":
      return value;
    default:
      return typeof value === "string" ? value.trim() || null : value ?? null;
  }
}

// --- Ввод -------------------------------------------------------------------------------------------------------

export function FieldInput({ kind, field, value, onChange, options, invalid }: {
  kind: Kind;
  field: FieldSpec;
  value: unknown;
  onChange: (value: unknown) => void;
  options?: Option[];
  invalid?: boolean;
}): ReactNode {
  const cls = `input ${invalid ? "invalid" : ""}`;
  switch (field.kind) {
    case "full_name":
    case "line":
      return (
        <input
          className={cls}
          value={value as string}
          maxLength={field.max_length ?? undefined}
          autoComplete="off"
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case "url":
      return (
        <input
          className={cls}
          type="url"
          inputMode="url"
          placeholder="https://"
          value={value as string}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case "number":
      return (
        <input
          className={cls}
          inputMode="numeric"
          value={value as string}
          onChange={(e) => onChange(e.target.value.replace(/\D/g, ""))}
        />
      );
    case "html":
      return <RichEditor value={value as string} onChange={onChange} invalid={invalid} />;
    case "document":
      return <ProgramInput value={value as string} onChange={onChange} invalid={invalid} />;
    case "choice":
      return (
        <ChipsInput
          options={field.choices.map((c) => ({ id: c.value, title: c.label }))}
          value={value as string | null}
          onChange={onChange}
          optional={!field.required}
        />
      );
    case "ref":
      return (
        <ChipsInput
          options={(options ?? []).map((o) => ({ id: o.id, title: o.title }))}
          value={value as number | null}
          onChange={onChange}
          optional={!field.required}
          empty="Сначала добавьте площадку в каталоге."
        />
      );
    case "multi":
      return <MultiInput options={options ?? []} value={value as number[]} onChange={onChange} />;
    case "photo":
      return <PhotoInput kind={kind} value={value as Photo | null} onChange={onChange} optional={!field.required} />;
    case "days":
      return <DaysEditor value={value as Day[]} onChange={onChange} />;
    case "datetime":
      return (
        <div className="row">
          <input
            className={`${cls} grow`}
            type="datetime-local"
            value={value as string}
            onChange={(e) => onChange(e.target.value)}
          />
          {!field.required && Boolean(value) && (
            <button className="a-icon-btn" onClick={() => onChange("")} aria-label="Очистить">
              <Icon.close />
            </button>
          )}
        </div>
      );
    case "prices":
      return <PricesEditor value={value as PriceDraft[]} onChange={onChange} />;
    default:
      return null;
  }
}

function ChipsInput<T extends string | number>({ options, value, onChange, optional, empty }: {
  options: { id: T; title: string }[];
  value: T | null;
  onChange: (value: T | null) => void;
  optional?: boolean;
  empty?: string;
}): ReactNode {
  if (!options.length) return <div className="field-hint">{empty ?? "Вариантов пока нет."}</div>;
  return (
    <div className="chips">
      {optional && (
        <button className={`chip ${value === null ? "on" : ""}`} onClick={() => onChange(null)}>
          Не указано
        </button>
      )}
      {options.map((option) => (
        <button
          key={String(option.id)}
          className={`chip ${option.id === value ? "on" : ""}`}
          onClick={() => {
            haptic("select");
            onChange(option.id);
          }}
        >
          {option.title}
        </button>
      ))}
    </div>
  );
}

function MultiInput({ options, value, onChange }: {
  options: Option[];
  value: number[];
  onChange: (value: number[]) => void;
}): ReactNode {
  if (!options.length) return <div className="field-hint">Список пока пуст - сначала заполните его в каталоге.</div>;
  return (
    <div className="chips">
      {options.map((option) => {
        const on = value.includes(option.id);
        return (
          <button
            key={option.id}
            className={`chip ${on ? "on" : ""}`}
            onClick={() => {
              haptic("select");
              onChange(on ? value.filter((id) => id !== option.id) : [...value, option.id]);
            }}
          >
            {on && <Icon.check />}
            {option.title}
          </button>
        );
      })}
    </div>
  );
}

/** Текст с форматированием: жирный, курсив, подчёркнутый, зачёркнутый, ссылка - то, что умеет Telegram. */
export function RichEditor({ value, onChange, invalid, placeholder = "Текст…", compact }: {
  value: string;
  onChange: (html: string) => void;
  invalid?: boolean;
  placeholder?: string;
  compact?: boolean;
}): ReactNode {
  const ref = useRef<HTMLDivElement>(null);
  const range = useRef<Range | null>(null);
  const [link, setLink] = useState<string | null>(null);

  useEffect(() => {
    if (ref.current && ref.current.innerHTML !== value) ref.current.innerHTML = value;
  }, [value]);

  const changed = () => onChange(ref.current?.innerHTML ?? "");
  const command = (name: string, argument?: string) => {
    ref.current?.focus();
    document.execCommand(name, false, argument);
    changed();
  };
  const saveSelection = () => {
    const selection = window.getSelection();
    range.current = selection && selection.rangeCount ? selection.getRangeAt(0).cloneRange() : null;
  };
  const applyLink = () => {
    const url = (link ?? "").trim();
    setLink(null);
    if (!/^(https?:\/\/|tg:\/\/)\S+$/.test(url) || !range.current) return;
    ref.current?.focus();
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range.current);
    if (range.current.collapsed) document.execCommand("insertHTML", false, `<a href="${encodeURI(url)}">${url}</a>`);
    else document.execCommand("createLink", false, url);
    changed();
  };
  const tools: { label: ReactNode; title: string; run: () => void }[] = [
    { label: <b>Ж</b>, title: "Жирный", run: () => command("bold") },
    { label: <i>К</i>, title: "Курсив", run: () => command("italic") },
    { label: <u>Ч</u>, title: "Подчёркнутый", run: () => command("underline") },
    { label: <s>З</s>, title: "Зачёркнутый", run: () => command("strikeThrough") },
    {
      label: <Icon.link />,
      title: "Ссылка",
      run: () => {
        saveSelection();
        setLink("https://");
      },
    },
    { label: <Icon.close />, title: "Убрать оформление", run: () => command("removeFormat") },
  ];
  return (
    <div className={`rich-editor ${invalid ? "invalid" : ""} ${compact ? "compact" : ""}`}>
      <div className="rich-tools">
        {tools.map((tool) => (
          <button
            key={tool.title}
            title={tool.title}
            aria-label={tool.title}
            onMouseDown={(e) => e.preventDefault()}
            onClick={tool.run}
          >
            {tool.label}
          </button>
        ))}
      </div>
      {link !== null && (
        <div className="rich-link">
          <input
            className="input"
            type="url"
            inputMode="url"
            value={link}
            autoFocus
            onChange={(e) => setLink(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyLink()}
          />
          <Button size="small" onClick={applyLink}>
            Ок
          </Button>
        </div>
      )}
      <div
        ref={ref}
        className="rich rich-area"
        contentEditable
        role="textbox"
        aria-multiline="true"
        data-placeholder={placeholder}
        onInput={changed}
        onBlur={changed}
        onPaste={(e) => {
          // Из Word и сайтов - только текст: лишняя разметка всё равно не дойдёт до Telegram.
          e.preventDefault();
          document.execCommand("insertText", false, e.clipboardData.getData("text/plain"));
          changed();
        }}
      />
    </div>
  );
}

function ProgramInput({ value, onChange, invalid }: {
  value: string;
  onChange: (html: string) => void;
  invalid?: boolean;
}): ReactNode {
  const { fail } = useApp();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="stack tight">
      <RichEditor value={value} onChange={onChange} invalid={invalid} placeholder="Программа курса…" />
      <input
        ref={input}
        type="file"
        accept=".docx,.txt"
        hidden
        onChange={async (e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (!file) return;
          setBusy(true);
          try {
            onChange((await admin.uploadProgram(file)).html);
          } catch (error) {
            fail(error);
          } finally {
            setBusy(false);
          }
        }}
      />
      <Button variant="secondary" size="small" busy={busy} onClick={() => input.current?.click()}>
        <Icon.upload />
        Загрузить .docx или .txt
      </Button>
    </div>
  );
}

function PhotoInput({ kind, value, onChange, optional }: {
  kind: Kind;
  value: Photo | null;
  onChange: (value: Photo | null) => void;
  optional: boolean;
}): ReactNode {
  const { fail } = useApp();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="photo-input">
      <div className="photo-frame" onClick={() => !busy && input.current?.click()}>
        {busy ? (
          <Spinner />
        ) : value ? (
          <img src={value.url} alt="" />
        ) : (
          <span className="photo-empty">
            <Icon.image />
            {kind === "ses" ? "Будет обложка курса" : "Стандартная обложка"}
          </span>
        )}
      </div>
      <input
        ref={input}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        hidden
        onChange={async (e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (!file) return;
          setBusy(true);
          try {
            onChange((await admin.uploadPhoto(file)).photo);
          } catch (error) {
            fail(error);
          } finally {
            setBusy(false);
          }
        }}
      />
      <div className="row">
        <Button variant="secondary" size="small" busy={busy} onClick={() => input.current?.click()}>
          <Icon.upload />
          {value ? "Заменить" : "Загрузить картинку"}
        </Button>
        {optional && value && (
          <Button variant="ghost" size="small" onClick={() => onChange(null)}>
            Убрать
          </Button>
        )}
      </div>
    </div>
  );
}

function addDays(iso: string, days: number): string {
  const date = new Date(`${iso}T12:00:00`);
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

function tomorrow(): string {
  return addDays(new Date().toISOString().slice(0, 10), 1);
}

/** Дни проведения: дата и время каждого дня. Без дней - «даты уточняются» (так и будет в афише). */
function DaysEditor({ value, onChange }: { value: Day[]; onChange: (value: Day[]) => void }): ReactNode {
  const { specs } = useAdmin();
  const set = (index: number, patch: Partial<Day>) =>
    onChange(value.map((day, i) => (i === index ? { ...day, ...patch } : day)));
  const add = () => {
    const last = value[value.length - 1];
    onChange([
      ...value,
      last
        ? { date: addDays(last.date, 1), start: last.start, end: last.end }
        : { date: tomorrow(), start: "10:00", end: "18:00" },
    ]);
  };
  return (
    <div className="days-editor">
      {value.length === 0 && <div className="notice">Даты уточняются - так и будет написано в афише.</div>}
      {value.map((day, index) => (
        <div className="day-row" key={index}>
          <input
            className="input"
            type="date"
            min={tomorrow()}
            value={day.date}
            onChange={(e) => set(index, { date: e.target.value })}
          />
          <input
            className="input time"
            type="time"
            value={day.start ?? ""}
            onChange={(e) => set(index, { start: e.target.value || null })}
            aria-label="Начало"
          />
          <span className="muted">–</span>
          <input
            className="input time"
            type="time"
            value={day.end ?? ""}
            onChange={(e) => set(index, { end: e.target.value || null })}
            aria-label="Конец"
          />
          <button className="a-icon-btn" onClick={() => onChange(value.filter((_, i) => i !== index))} aria-label="Убрать день">
            <Icon.close />
          </button>
        </div>
      ))}
      <div className="row">
        {value.length < specs.max_days && (
          <Button variant="secondary" size="small" onClick={add}>
            <Icon.plus />
            Добавить день
          </Button>
        )}
        {value.length > 0 && (
          <Button variant="ghost" size="small" onClick={() => onChange([])}>
            Даты уточняются
          </Button>
        )}
      </div>
    </div>
  );
}

/** Тарифы: категории (как в боте) - у каждой подпись в карточке и цена в рублях. */
function PricesEditor({ value, onChange }: { value: PriceDraft[]; onChange: (value: PriceDraft[]) => void }): ReactNode {
  const { specs } = useAdmin();
  return (
    <div className="prices-editor">
      {specs.price_kinds.map((kind) => {
        const draft = value.find((p) => p.kind === kind.value);
        const toggle = () => {
          haptic("select");
          onChange(
            draft
              ? value.filter((p) => p.kind !== kind.value)
              : specs.price_kinds
                  .map((k) => (k.value === kind.value ? { kind: k.value, label: k.label, rubles: "" } : value.find((p) => p.kind === k.value)))
                  .filter((p): p is PriceDraft => Boolean(p)),
          );
        };
        const set = (patch: Partial<PriceDraft>) =>
          onChange(value.map((p) => (p.kind === kind.value ? { ...p, ...patch } : p)));
        return (
          <div className={`price-draft ${draft ? "on" : ""}`} key={kind.value}>
            <button className="price-draft-head" onClick={toggle}>
              <span className="check-box">
                <Icon.check />
              </span>
              <span className="grow">{kind.label}</span>
              {kind.per_group && <span className="muted small">за двоих</span>}
            </button>
            {draft && (
              <div className="price-draft-body">
                <input
                  className="input"
                  value={draft.label}
                  maxLength={specs.max_price_label}
                  placeholder="Подпись в карточке"
                  onChange={(e) => set({ label: e.target.value })}
                />
                <div className="money-input">
                  <input
                    className="input"
                    inputMode="numeric"
                    value={draft.rubles}
                    placeholder="Цена"
                    onChange={(e) => set({ rubles: e.target.value.replace(/[^\d]/g, "") })}
                  />
                  <span>₽</span>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// --- Строки карточки и правка поля ---------------------------------------------------------------------------------

export function displayValue(field: FieldSpec, item: Item): ReactNode {
  const value = item.values[field.name];
  switch (field.kind) {
    case "multi": {
      const ids = (value as number[] | null) ?? [];
      const titles = (item.options[field.name] ?? []).filter((o) => ids.includes(o.id)).map((o) => o.title);
      return titles.join(", ") || null;
    }
    case "ref":
      return (item.options[field.name] ?? []).find((o) => o.id === value)?.title ?? null;
    case "course":
      return item.session?.program.title ?? null;
    case "choice":
      return field.choices.find((c) => c.value === value)?.label ?? null;
    case "html":
    case "document":
      return excerpt(item.html[field.name]) || null;
    case "photo": {
      const photo = item.photos[field.name];
      return photo ? <img className="a-photo-thumb" src={photo.url} alt="" /> : null;
    }
    case "days":
      return item.display[field.name] ?? "Даты уточняются";
    case "datetime":
    case "prices":
      return item.display[field.name] ?? null;
    default:
      return value === null || value === undefined ? null : String(value);
  }
}

/** Все поля записи строками; нажатие - правка поля (если запись ещё правится: не в архиве, не прошла). */
export function FieldRows({ item, onSaved, skip = [] }: {
  item: Item;
  onSaved: (item: Item) => void;
  skip?: string[];
}): ReactNode {
  const { specs } = useAdmin();
  const [editing, setEditing] = useState<FieldSpec | null>(null);
  const fields = specs.kinds[item.kind].fields.filter(
    (field) => !skip.includes(field.name) && applies(field, item.values),
  );
  return (
    <>
      <div className="list">
        {fields.map((field) => (
          <InfoRow
            key={field.name}
            label={field.label}
            value={displayValue(field, item)}
            empty={field.kind === "photo" ? "Стандартная" : "—"}
            onClick={item.editable && field.editable ? () => setEditing(field) : undefined}
          />
        ))}
      </div>
      {editing && (
        <FieldSheet
          item={item}
          field={editing}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            setEditing(null);
            onSaved(saved);
          }}
        />
      )}
    </>
  );
}

export function FieldSheet({ item, field, onClose, onSaved }: {
  item: Item;
  field: FieldSpec;
  onClose: () => void;
  onSaved: (item: Item) => void;
}): ReactNode {
  const { toast } = useApp();
  const [value, setValue] = useState<unknown>(() => fromItem(field, item));
  const [error, setError] = useState<string | null>(null);
  const save = async (next: unknown) => {
    setError(null);
    try {
      const result = await admin.update(item.kind, item.id, field.name, next);
      toast(result.message, "ok");
      onSaved(result.item);
    } catch (e) {
      if (e instanceof ApiError && e.status < 500) setError(e.message);
      else throw e;
    }
  };
  return (
    <Sheet open onClose={onClose}>
      <h2>{field.label}</h2>
      <p className="muted small">{item.title}</p>
      <div className="stack">
        <Field label={field.required ? "Обязательное поле" : "Можно оставить пустым"} hint={hintOf(item.kind, field)} error={error}>
          <FieldInput
            kind={item.kind}
            field={field}
            value={value}
            onChange={setValue}
            options={item.options[field.name]}
            invalid={Boolean(error)}
          />
        </Field>
        <ActionButton block onClick={() => save(toApi(field, value))}>
          Сохранить
        </ActionButton>
        {!field.required && field.kind !== "days" && (
          <ActionButton variant="ghost" block onClick={() => save(null)}>
            Очистить поле
          </ActionButton>
        )}
      </div>
    </Sheet>
  );
}
