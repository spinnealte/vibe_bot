// Общее для экранов админки: описание полей и сводка (грузятся один раз), строки, разделы, переключатели.

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { ApiError } from "../api";
import { Icon } from "../icons";
import { navigate } from "../router";
import { confirmDialog, haptic } from "../telegram";
import { Spinner, useApp } from "../ui";
import { admin, type Specs, type Summary } from "./api";

interface AdminValue {
  specs: Specs;
  summary: Summary;
  refreshSummary: () => void;
}

const AdminContext = createContext<AdminValue | null>(null);

export function useAdmin(): AdminValue {
  const value = useContext(AdminContext);
  if (!value) throw new Error("AdminContext");
  return value;
}

/** Описание полей и сводка - один раз на вход в админку; сводку обновляют действия (счётчики на панели). */
export function AdminProvider({ children }: { children: ReactNode }): ReactNode {
  const { fail } = useApp();
  const [specs, setSpecs] = useState<Specs | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const refreshSummary = useCallback(() => {
    admin.summary().then(setSummary).catch(fail);
  }, [fail]);

  useEffect(() => {
    Promise.all([admin.specs(), admin.summary()])
      .then(([loadedSpecs, loadedSummary]) => {
        setSpecs(loadedSpecs);
        setSummary(loadedSummary);
      })
      .catch((e: unknown) => setError(e instanceof ApiError ? e : new ApiError(0, "unknown", String(e))));
  }, []);

  if (error) {
    return (
      <div className="page">
        <div className="state">
          <div className="big-icon">
            <Icon.key />
          </div>
          <h3>Админка недоступна</h3>
          <p>{error.message}</p>
        </div>
      </div>
    );
  }
  if (!specs || !summary) {
    return (
      <div className="state" style={{ paddingTop: "35vh" }}>
        <Spinner />
      </div>
    );
  }
  return <AdminContext.Provider value={{ specs, summary, refreshSummary }}>{children}</AdminContext.Provider>;
}

/** Действие с подтверждением и уведомлением: busy - пока идёт, ошибка - во всплывающем уведомлении. */
export function useRun(): [boolean, (job: () => Promise<string | void>, confirm?: string) => Promise<boolean>] {
  const { toast, fail } = useApp();
  const [busy, setBusy] = useState(false);
  const run = useCallback(
    async (job: () => Promise<string | void>, confirm?: string) => {
      if (confirm && !(await confirmDialog(confirm))) return false;
      setBusy(true);
      try {
        const message = await job();
        if (message) toast(message, "ok");
        return true;
      } catch (error) {
        fail(error);
        return false;
      } finally {
        setBusy(false);
      }
    },
    [toast, fail],
  );
  return [busy, run];
}

export function Section({ title, action, children }: {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
}): ReactNode {
  return (
    <section className="a-section">
      {(title || action) && (
        <div className="a-section-head">
          {title && <h2>{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

/** Строка «подпись - значение»; с onClick - нажимается (правка поля, переход). */
export function InfoRow({ label, value, onClick, empty = "—", icon, children }: {
  label: string;
  value?: ReactNode;
  onClick?: () => void;
  empty?: string;
  icon?: ReactNode;
  children?: ReactNode;
}): ReactNode {
  const shown = value === null || value === undefined || value === "" ? null : value;
  const body = (
    <>
      {icon && <span className="a-row-icon">{icon}</span>}
      <span className="grow">
        <span className="label">{label}</span>
        <span className={`value ${shown === null ? "empty" : ""}`}>{shown ?? empty}</span>
        {children}
      </span>
      {onClick && <Icon.pencil />}
    </>
  );
  if (!onClick) return <div className="list-item static a-row">{body}</div>;
  return (
    <button className="list-item a-row editable" onClick={onClick}>
      {body}
    </button>
  );
}

/** Строка-переход: значок, название, число справа. */
export function NavRow({ icon, title, text, count, to, onClick, tone }: {
  icon?: ReactNode;
  title: ReactNode;
  text?: ReactNode;
  count?: number | string | null;
  to?: string;
  onClick?: () => void;
  tone?: "danger" | "warn" | "ok";
}): ReactNode {
  return (
    <button
      className="list-item a-nav"
      onClick={() => {
        haptic("tap");
        if (onClick) onClick();
        else if (to) navigate(to);
      }}
    >
      {icon && <span className={`a-nav-icon ${tone ?? ""}`}>{icon}</span>}
      <span className="grow">
        <span className="a-nav-title">{title}</span>
        {text && <span className="a-nav-text">{text}</span>}
      </span>
      {count !== undefined && count !== null && <span className={`a-count ${tone ?? ""}`}>{count}</span>}
      <Icon.chevron />
    </button>
  );
}

export function Segmented<T extends string>({ value, options, onChange }: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}): ReactNode {
  return (
    <div className="segmented" role="tablist">
      {options.map((option) => (
        <button
          key={option.value}
          role="tab"
          aria-selected={option.value === value}
          className={option.value === value ? "on" : ""}
          onClick={() => {
            if (option.value !== value) haptic("select");
            onChange(option.value);
          }}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function HeaderAction({ icon, label, onClick }: { icon: ReactNode; label: string; onClick: () => void }) {
  return (
    <button
      className="a-header-action"
      onClick={() => {
        haptic("tap");
        onClick();
      }}
    >
      {icon}
      {label}
    </button>
  );
}

/** Заголовок экрана админки: «надзаголовок» (раздел), название, справа - главное действие. */
export function AdminHeader({ over, title, text, action }: {
  over?: string;
  title: ReactNode;
  text?: ReactNode;
  action?: ReactNode;
}): ReactNode {
  return (
    <div className="a-header">
      <div className="grow">
        {over && <div className="a-over">{over}</div>}
        <h1>{title}</h1>
        {text && <p>{text}</p>}
      </div>
      {action}
    </div>
  );
}

export function Thumb({ src }: { src: string }): ReactNode {
  const [failed, setFailed] = useState(false);
  const placeholder = failed || src.endsWith("/default");
  return (
    <span className={`a-thumb ${placeholder ? "placeholder" : ""}`}>
      {placeholder ? <Icon.tooth /> : <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} />}
    </span>
  );
}

/** Текст без разметки - короткий отрывок длинного поля в строке карточки. */
export function excerpt(html: string | undefined, limit = 90): string {
  if (!html) return "";
  const div = document.createElement("div");
  div.innerHTML = html.replace(/<br\s*\/?>/gi, " ");
  const text = (div.textContent ?? "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 1).trimEnd()}…` : text;
}
