// Общие детали интерфейса: кнопки, карточки, окна снизу, уведомления, загрузка данных.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ApiError, type Me } from "./api";
import { Icon } from "./icons";
import { navigate } from "./router";
import { haptic, openUrl } from "./telegram";

// --- Данные с сервера ------------------------------------------------------------------------------------------------

export interface Loaded<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
  setData: (data: T) => void;
}

export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    load()
      .then((result) => alive && setData(result))
      .catch((e: unknown) => alive && setError(e instanceof ApiError ? e : new ApiError(0, "unknown", String(e))))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload, setData };
}

// --- Кто открыл приложение и уведомления ---------------------------------------------------------------------------

interface AppContextValue {
  me: Me;
  refreshMe: () => Promise<Me>;
  toast: (text: string, kind?: "info" | "error" | "ok") => void;
  fail: (error: unknown) => void;
}

export const AppContext = createContext<AppContextValue | null>(null);

export function useApp(): AppContextValue {
  const value = useContext(AppContext);
  if (!value) throw new Error("AppContext");
  return value;
}

export function useToast(): [ReactNode, (text: string, kind?: "info" | "error" | "ok") => void] {
  const [toast, setToast] = useState<{ text: string; kind: string; id: number } | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const show = useCallback((text: string, kind: "info" | "error" | "ok" = "info") => {
    window.clearTimeout(timer.current);
    setToast({ text, kind, id: Date.now() });
    if (kind === "error") haptic("error");
    if (kind === "ok") haptic("success");
    timer.current = window.setTimeout(() => setToast(null), kind === "error" ? 5000 : 3200);
  }, []);
  const node = toast ? (
    <div key={toast.id} className={`toast ${toast.kind}`} role="status" onClick={() => setToast(null)}>
      {toast.text}
    </div>
  ) : null;
  return [node, show];
}

// --- Детали ----------------------------------------------------------------------------------------------------------

export function Spinner(): ReactNode {
  return <div className="spinner" aria-label="Загрузка" />;
}

export function Loading({ rows = 3 }: { rows?: number }): ReactNode {
  return (
    <div className="stack" aria-busy="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ height: i === 0 ? 240 : 180 }} />
      ))}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: ApiError; onRetry?: () => void }): ReactNode {
  return (
    <div className="state">
      <div className="big-icon">
        <Icon.info />
      </div>
      <h3>Не получилось загрузить</h3>
      <p>{error.message}</p>
      {onRetry && (
        <button className="btn secondary small" onClick={onRetry}>
          Попробовать ещё раз
        </button>
      )}
    </div>
  );
}

export function Empty({ icon, title, text, children }: {
  icon: ReactNode;
  title: string;
  text?: string;
  children?: ReactNode;
}): ReactNode {
  return (
    <div className="state">
      <div className="big-icon">{icon}</div>
      <h3>{title}</h3>
      {text && <p>{text}</p>}
      {children}
    </div>
  );
}

export function Button({
  children,
  onClick,
  variant = "primary",
  size,
  block,
  busy,
  disabled,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => unknown;
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "small";
  block?: boolean;
  busy?: boolean;
  disabled?: boolean;
  type?: "button" | "submit";
}): ReactNode {
  const classes = ["btn", variant !== "primary" && variant, size, block && "block"].filter(Boolean).join(" ");
  return (
    <button
      type={type}
      className={classes}
      disabled={disabled || busy}
      onClick={() => {
        haptic("tap");
        void onClick?.();
      }}
    >
      {busy ? <Spinner /> : children}
    </button>
  );
}

/** Кнопка, которая сама показывает «загрузку», пока идёт действие. Ошибка - во всплывающем уведомлении. */
export function ActionButton(props: Omit<Parameters<typeof Button>[0], "busy" | "onClick"> & {
  onClick: () => Promise<void>;
}): ReactNode {
  const { fail } = useApp();
  const [busy, setBusy] = useState(false);
  return (
    <Button
      {...props}
      busy={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await props.onClick();
        } catch (error) {
          fail(error);
        } finally {
          setBusy(false);
        }
      }}
    />
  );
}

export function Badge({ children, tone }: { children: ReactNode; tone?: "ok" | "warn" | "danger" | "accent" | "mine" }) {
  return <span className={`badge ${tone ?? ""}`}>{children}</span>;
}

/** HTML с сервера - уже очищен (stubbot/web/html.py): только жирный, курсив, ссылки, цитаты. */
export function Rich({ html, className = "" }: { html: string; className?: string }): ReactNode {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const onClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      const spoiler = target.closest(".spoiler");
      if (spoiler) spoiler.classList.add("shown");
      const link = target.closest("a");
      if (link?.href) {
        event.preventDefault();
        openUrl(link.href);
      }
    };
    root.addEventListener("click", onClick);
    return () => root.removeEventListener("click", onClick);
  }, [html]);
  return <div ref={ref} className={`rich ${className}`} dangerouslySetInnerHTML={{ __html: html }} />;
}

const DEFAULT_COVER = "/api/files/default";

/** Обложка курса. Своей нет (заглушка бота) или не загрузилась - фирменный фон со значком.
 * full - картинка целиком (страница курса): высота - по самой картинке, но не больше 55% экрана; если картинка
 * узкая, по бокам - её же размытая копия. В списках обложка обрезается до 16:9, как карточки в Telegram. */
export function Cover({ src, alt, full, children }: {
  src: string;
  alt: string;
  full?: boolean;
  children?: ReactNode;
}): ReactNode {
  const [failed, setFailed] = useState(false);
  const placeholder = failed || src === DEFAULT_COVER;
  return (
    <div className={`cover ${placeholder ? "placeholder" : ""} ${full && !placeholder ? "full" : ""}`}>
      {placeholder ? (
        <div className="cover-art" aria-hidden="true">
          <Icon.tooth />
        </div>
      ) : full ? (
        <>
          <img className="cover-backdrop" src={src} alt="" aria-hidden="true" />
          <img className="cover-main" src={src} alt={alt} onError={() => setFailed(true)} />
        </>
      ) : (
        <img src={src} alt={alt} loading="lazy" onError={() => setFailed(true)} />
      )}
      {children}
    </div>
  );
}

/** Только админам: «Править» поверх обложки в расписании и на странице курса - сразу в карточку проведения
 * в админке (статус, поля, участники), без захода через переключатель режима. «Назад» вернёт сюда же. */
export function EditSessionButton({ id }: { id: number }): ReactNode {
  const { me } = useApp();
  if (!me.is_admin) return null;
  return (
    <button
      type="button"
      className="edit-pill"
      onClick={(event) => {
        event.stopPropagation();
        haptic("tap");
        navigate(`/admin/session/${id}`);
      }}
    >
      <Icon.pencil />
      Править
    </button>
  );
}

export function Sheet({ open, onClose, children }: { open: boolean; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
        <div className="sheet-handle" />
        {children}
      </div>
    </div>
  );
}

export function Check({ on, onToggle, children, radio }: {
  on: boolean;
  onToggle: () => void;
  children: ReactNode;
  radio?: boolean;
}): ReactNode {
  // div, а не button: внутри бывают ссылки (документы согласия) - их нажатие галочку не переключает.
  const toggle = () => {
    haptic("select");
    onToggle();
  };
  return (
    <div
      className={`check ${on ? "on" : ""} ${radio ? "radio" : ""}`}
      role={radio ? "radio" : "checkbox"}
      aria-checked={on}
      tabIndex={0}
      onClick={(event) => {
        if (!(event.target as HTMLElement).closest("a")) toggle();
      }}
      onKeyDown={(event) => {
        if (event.key === " " || event.key === "Enter") {
          event.preventDefault();
          toggle();
        }
      }}
    >
      <span className="check-box">
        <Icon.check />
      </span>
      <span className="grow">{children}</span>
    </div>
  );
}

/** Горизонтальная лента (фильтры): на телефоне листается пальцем, на компьютере - мышью с зажатой левой кнопкой
 * и колесом. Если ленту тянули, щелчок по фильтру под курсором не срабатывает. */
export function DragScroll({ className, children, ...rest }: {
  className: string;
  children: ReactNode;
  role?: string;
}): ReactNode {
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; left: number; moved: boolean } | null>(null);
  const swallowClick = useRef(false);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Колесо мыши вниз-вверх листает ленту вбок, пока ей есть куда; дальше прокручивается страница.
    const onWheel = (event: WheelEvent) => {
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX) || el.scrollWidth <= el.clientWidth) return;
      const atStart = el.scrollLeft <= 0 && event.deltaY < 0;
      const atEnd = el.scrollLeft + el.clientWidth >= el.scrollWidth - 1 && event.deltaY > 0;
      if (atStart || atEnd) return;
      event.preventDefault();
      el.scrollLeft += event.deltaY;
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  return (
    <div
      {...rest}
      ref={ref}
      className={`${className} drag-scroll ${dragging ? "dragging" : ""}`}
      onPointerDown={(event) => {
        if (event.pointerType !== "mouse" || event.button !== 0 || !ref.current) return;
        drag.current = { x: event.clientX, left: ref.current.scrollLeft, moved: false };
      }}
      onPointerMove={(event) => {
        const state = drag.current;
        const el = ref.current;
        if (!state || !el) return;
        const dx = event.clientX - state.x;
        if (!state.moved && Math.abs(dx) < 5) return;
        if (!state.moved) {
          state.moved = true;
          setDragging(true);
          el.setPointerCapture(event.pointerId);
        }
        el.scrollLeft = state.left - dx;
      }}
      onPointerUp={(event) => {
        if (drag.current?.moved) {
          // Щелчок после перетаскивания приходит сразу за отпусканием кнопки; если его не будет - сбросим.
          swallowClick.current = true;
          window.setTimeout(() => (swallowClick.current = false), 0);
          ref.current?.releasePointerCapture(event.pointerId);
        }
        drag.current = null;
        setDragging(false);
      }}
      onPointerCancel={() => {
        drag.current = null;
        setDragging(false);
      }}
      onClickCapture={(event) => {
        if (swallowClick.current) {
          swallowClick.current = false;
          event.preventDefault();
          event.stopPropagation();
        }
      }}
      onDragStart={(event) => event.preventDefault()}
    >
      {children}
    </div>
  );
}

export function Field({ label, error, hint, children }: {
  label: string;
  error?: string | null;
  hint?: string;
  children: ReactNode;
}): ReactNode {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
      {error ? <div className="field-error">{error}</div> : hint ? <div className="field-hint">{hint}</div> : null}
    </div>
  );
}

export function ActionBar({ children, note }: { children: ReactNode; note?: ReactNode }): ReactNode {
  return (
    <div className="action-bar">
      <div className="action-bar-inner">
        {note && <div className="action-note">{note}</div>}
        {children}
      </div>
    </div>
  );
}

export function PageHeader({ title, text }: { title: string; text?: ReactNode }): ReactNode {
  return (
    <div className="page-header">
      <h1>{title}</h1>
      {text && <p>{text}</p>}
    </div>
  );
}

export function ManagerButton({ url, label = "Связаться с менеджером" }: { url: string | null; label?: string }) {
  if (!url) return null;
  return (
    <Button variant="secondary" block onClick={() => openUrl(url)}>
      <Icon.chat />
      {label}
    </Button>
  );
}

export function statusTone(status: string): "ok" | "warn" | "danger" | "accent" | undefined {
  switch (status) {
    case "registration_open":
    case "confirmed":
    case "completed":
      return "ok";
    case "waitlist":
    case "full":
    case "application":
      return "warn";
    case "registration_closed":
    case "cancelled":
    case "cancelled_by_client":
    case "no_show":
      return "danger";
    case "announced":
      return "accent";
    default:
      return undefined;
  }
}

/** «Мест: 3» с правильным словом. */
export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

/** Убрать эмодзи-метки в начале подписей бота («🟢 Идёт набор» → «Идёт набор»): в приложении свои значки. */
export function clean(label: string): string {
  return label.replace(/^[^\p{L}\p{N}«(]+/u, "").trim();
}
