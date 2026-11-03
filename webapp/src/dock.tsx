// Нижняя панель - «капля воды»: плавающая капсула, в которой активный раздел залит водой с волной. Капля
// перетекает между разделами, а при переходе со дна поднимаются пузырьки. У админа рядом - круглая кнопка
// «режима»: клиентская часть ↔ админка.

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

import { Icon } from "./icons";
import { haptic } from "./telegram";

export interface DockItem {
  key: string;
  label: string;
  icon: () => ReactNode;
  badge?: number;
}

const GROW = 2.5; // во сколько раз раздел с водой шире остальных

export function Dock({ items, active, onSelect, mode }: {
  items: DockItem[];
  active: string | null;
  onSelect: (key: string) => void;
  mode?: { admin: boolean; onToggle: () => void };
}): ReactNode {
  const index = Math.max(0, items.findIndex((item) => item.key === active));
  const [splash, setSplash] = useState(0);
  const previous = useRef(index);
  useEffect(() => {
    if (previous.current !== index) setSplash((n) => n + 1);
    previous.current = index;
  }, [index]);

  const style = {
    "--i": index,
    "--total": items.length - 1 + GROW,
    "--grow": GROW,
  } as CSSProperties;
  return (
    <nav className={`dock ${mode?.admin ? "admin" : ""}`} aria-label="Разделы">
      <div className="dock-track" style={style}>
        {active !== null && (
          <span className="dock-drop" aria-hidden="true">
            <span className="dock-wave" />
            <span className="dock-wave back" />
            {splash > 0 && (
              <span className="dock-bubbles" key={splash}>
                <i />
                <i />
                <i />
                <i />
              </span>
            )}
          </span>
        )}
        {items.map((item) => {
          const on = item.key === active;
          return (
            <button
              key={item.key}
              className={`dock-item ${on ? "on" : ""}`}
              onClick={() => {
                if (!on) haptic("select");
                onSelect(item.key);
              }}
              aria-current={on ? "page" : undefined}
              aria-label={item.label}
            >
              <span className="dock-icon">{item.icon()}</span>
              <span className="dock-label">{item.label}</span>
              {item.badge ? <span className="dock-badge">{item.badge > 99 ? "99+" : item.badge}</span> : null}
            </button>
          );
        })}
      </div>
      {mode && (
        <button
          className="dock-mode"
          onClick={() => {
            haptic("tap");
            mode.onToggle();
          }}
          aria-label={mode.admin ? "Вернуться в приложение слушателя" : "Открыть админку"}
          title={mode.admin ? "Приложение слушателя" : "Админка"}
        >
          <span className="dock-mode-icon" key={mode.admin ? "admin" : "client"}>
            {mode.admin ? <Icon.user /> : <Icon.key />}
          </span>
        </button>
      )}
    </nav>
  );
}
