// Обёртка над Telegram Mini Apps SDK (telegram-web-app.js из index.html).
// В обычном браузере (разработка) SDK тоже есть, но без initData: тогда вход разработчика «dev <id>».

type HapticStyle = "light" | "medium" | "heavy" | "rigid" | "soft";

interface BackButton {
  show(): void;
  hide(): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
}

interface ContactResult {
  status: "sent" | "cancelled";
  response?: string;
}

interface WebApp {
  initData: string;
  initDataUnsafe: { user?: { id: number; first_name?: string }; start_param?: string };
  version: string;
  platform: string;
  colorScheme: "light" | "dark";
  ready(): void;
  expand(): void;
  isVersionAtLeast(version: string): boolean;
  setHeaderColor?(color: string): void;
  setBackgroundColor?(color: string): void;
  openLink(url: string, options?: { try_instant_view?: boolean }): void;
  openTelegramLink(url: string): void;
  requestContact?(cb: (shared: boolean, result?: ContactResult) => void): void;
  showConfirm?(message: string, cb: (ok: boolean) => void): void;
  BackButton: BackButton;
  HapticFeedback?: {
    impactOccurred(style: HapticStyle): void;
    notificationOccurred(type: "error" | "success" | "warning"): void;
    selectionChanged(): void;
  };
  disableVerticalSwipes?(): void;
  onEvent?(event: "themeChanged", cb: () => void): void;
}

declare global {
  interface Window {
    Telegram?: { WebApp: WebApp };
  }
}

export const tg: WebApp | undefined = window.Telegram?.WebApp;

/** Открыто внутри Telegram (есть подпись пользователя), а не в обычном браузере. */
export const insideTelegram = Boolean(tg?.initData);

const DEV_KEY = "akvastom-dev-user";

/** id разработчика для входа без Telegram: ?dev=123 в адресе запоминается в браузере. */
export function devUserId(): string | null {
  try {
    const fromUrl = new URLSearchParams(window.location.search).get("dev");
    if (fromUrl && /^\d{1,18}$/.test(fromUrl)) {
      localStorage.setItem(DEV_KEY, fromUrl);
      return fromUrl;
    }
    return localStorage.getItem(DEV_KEY);
  } catch {
    return null;
  }
}

export function authorization(): string | null {
  if (tg?.initData) return `tma ${tg.initData}`;
  const dev = devUserId();
  return dev ? `dev ${dev}` : null;
}

export function initTelegram(): void {
  if (!tg) return;
  tg.ready();
  tg.expand();
  if (tg.initData) {
    // Цвета - из темы Telegram; светлая она или тёмная - для фирменного акцента (styles.css).
    const root = document.documentElement;
    root.dataset.telegram = "1";
    const scheme = () => (root.dataset.scheme = tg.colorScheme);
    scheme();
    tg.onEvent?.("themeChanged", scheme);
  }
  try {
    tg.disableVerticalSwipes?.(); // листание страниц не должно сворачивать приложение
  } catch {
    /* старый клиент Telegram */
  }
}

export function haptic(kind: "tap" | "success" | "error" | "select" = "tap"): void {
  const feedback = tg?.HapticFeedback;
  if (!feedback || !insideTelegram) return;
  if (kind === "tap") feedback.impactOccurred("light");
  else if (kind === "select") feedback.selectionChanged();
  else feedback.notificationOccurred(kind);
}

/** Ссылка t.me - внутри Telegram (чат с менеджером), остальные - во внешнем браузере (карта, страница оплаты). */
export function openUrl(url: string): void {
  if (insideTelegram && tg) {
    if (/^https:\/\/t\.me\//.test(url)) tg.openTelegramLink(url);
    else tg.openLink(url);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

/** «Поделиться номером»: подписанный ответ Telegram (его проверяет сервер). null - человек отказался. */
export function requestContact(): Promise<string | null> {
  return new Promise((resolve) => {
    if (!tg?.requestContact || !insideTelegram) {
      resolve(null);
      return;
    }
    tg.requestContact((shared, result) => {
      resolve(shared && result?.status === "sent" && result.response ? result.response : null);
    });
  });
}

export function confirmDialog(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    if (insideTelegram && tg?.showConfirm && tg.isVersionAtLeast("6.2")) {
      tg.showConfirm(message, resolve);
      return;
    }
    resolve(window.confirm(message));
  });
}

export function canRequestContact(): boolean {
  return Boolean(insideTelegram && tg?.requestContact && tg.isVersionAtLeast("6.9"));
}
