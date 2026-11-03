// Маршруты - в части адреса после «#»: Telegram открывает приложение по одному адресу, а «Назад» работает
// и кнопкой Telegram, и историей браузера. Разделы админки - под «#/admin».

import { useEffect, useState } from "react";

import type { Kind, Scope } from "./admin/api";

export type Tab = "schedule" | "my" | "profile" | "about";
export type AdminTab = "orders" | "afisha" | "catalog" | "clients";

export type Route =
  | { name: "schedule" }
  | { name: "course"; id: number }
  | { name: "program"; id: number; course?: number }
  | { name: "enroll"; id: number }
  | { name: "my"; focus?: number; order?: number }
  | { name: "profile" }
  | { name: "about" }
  | { name: "start"; next: string }
  | { name: "a-orders" }
  | { name: "a-order-list"; category: string }
  | { name: "a-order"; id: number }
  | { name: "a-clients" }
  | { name: "a-client"; id: number }
  | { name: "a-afisha"; scope: Scope }
  | { name: "a-session"; id: number }
  | { name: "a-people"; id: number }
  | { name: "a-catalog" }
  | { name: "a-courses"; scope: "active" | "archived" }
  | { name: "a-course"; id: number }
  | { name: "a-dict"; kind: Kind }
  | { name: "a-entry"; kind: Kind; id: number }
  | { name: "a-new"; kind: Kind; copy?: number; course?: number }
  | { name: "a-archive" }
  | { name: "a-waitlists" }
  | { name: "a-waitlist"; id: number };

export const TAB_ROUTES: Record<Tab, string> = {
  schedule: "/",
  my: "/my",
  profile: "/profile",
  about: "/about",
};

export const ADMIN_TAB_ROUTES: Record<AdminTab, string> = {
  orders: "/admin",
  afisha: "/admin/afisha",
  catalog: "/admin/catalog",
  clients: "/admin/clients",
};

const KINDS: Kind[] = ["lec", "ven", "spec", "pos", "crs", "ses"];

function scopeOf(value: string | null): Scope {
  return value === "archived" || value === "past" ? value : "active";
}

function parseAdmin(parts: string[], params: URLSearchParams): Route {
  const id = Number(parts[2]);
  const kind = KINDS.find((k) => k === parts[2]);
  switch (parts[1]) {
    case "orders":
      if (parts[2]) return { name: "a-order-list", category: parts[2] };
      break;
    case "order":
      if (id) return { name: "a-order", id };
      break;
    case "clients":
      return { name: "a-clients" };
    case "client":
      if (id) return { name: "a-client", id };
      break;
    case "afisha":
      return { name: "a-afisha", scope: scopeOf(params.get("scope")) };
    case "session":
      if (id && parts[3] === "people") return { name: "a-people", id };
      if (id) return { name: "a-session", id };
      break;
    case "catalog":
      return { name: "a-catalog" };
    case "courses":
      return { name: "a-courses", scope: params.get("scope") === "archived" ? "archived" : "active" };
    case "course":
      if (id) return { name: "a-course", id };
      break;
    case "dict":
      if (kind && Number(parts[3])) return { name: "a-entry", kind, id: Number(parts[3]) };
      if (kind) return { name: "a-dict", kind };
      break;
    case "new":
      if (kind) {
        return {
          name: "a-new",
          kind,
          copy: Number(params.get("copy")) || undefined,
          course: Number(params.get("course")) || undefined,
        };
      }
      break;
    case "archive":
      return { name: "a-archive" };
    case "waitlists":
      return { name: "a-waitlists" };
    case "waitlist":
      if (id) return { name: "a-waitlist", id };
      break;
  }
  return { name: "a-orders" };
}

function parse(hash: string): Route {
  const [path, query = ""] = hash.replace(/^#/, "").split("?");
  const params = new URLSearchParams(query);
  const parts = path.split("/").filter(Boolean);
  const id = Number(parts[1]);
  switch (parts[0]) {
    case "admin":
      return parseAdmin(parts, params);
    case "course":
      if (id) return { name: "course", id };
      break;
    case "program":
      if (id) return { name: "program", id, course: Number(params.get("course")) || undefined };
      break;
    case "enroll":
      if (id) return { name: "enroll", id };
      break;
    case "my":
      return {
        name: "my",
        focus: Number(params.get("focus")) || undefined,
        order: Number(params.get("order")) || undefined,
      };
    case "profile":
      return { name: "profile" };
    case "about":
      return { name: "about" };
    case "start":
      return { name: "start", next: params.get("next") || "/" };
  }
  return { name: "schedule" };
}

export function isAdminRoute(route: Route): boolean {
  return route.name.startsWith("a-");
}

export function tabOf(route: Route): Tab | null {
  switch (route.name) {
    case "schedule":
    case "my":
    case "profile":
    case "about":
      return route.name;
    default:
      return null;
  }
}

export function adminTabOf(route: Route): AdminTab | null {
  switch (route.name) {
    case "a-orders":
      return "orders";
    case "a-afisha":
      return route.scope === "active" ? "afisha" : null;
    case "a-catalog":
      return "catalog";
    case "a-clients":
      return "clients";
    default:
      return null;
  }
}

/** Куда ведёт «Назад», если истории внутри приложения нет (открыли по ссылке, обновили страницу): на экран выше. */
export function parentOf(route: Route): string {
  switch (route.name) {
    case "program":
      return route.course ? `/course/${route.course}` : "/";
    case "enroll":
      return `/course/${route.id}`;
    case "a-order-list":
    case "a-order":
      return "/admin";
    case "a-client":
    case "a-waitlists":
      return "/admin/clients";
    case "a-waitlist":
      return "/admin/waitlists";
    case "a-session":
      return "/admin/afisha";
    case "a-people":
      return `/admin/session/${route.id}`;
    case "a-afisha":
      return route.scope === "active" ? "/admin" : "/admin/archive";
    case "a-courses":
      return route.scope === "archived" ? "/admin/archive" : "/admin/catalog";
    case "a-course":
      return "/admin/courses";
    case "a-dict":
    case "a-archive":
      return "/admin/catalog";
    case "a-entry":
      return `/admin/dict/${route.kind}`;
    case "a-new":
      if (route.copy) return `/admin/session/${route.copy}`;
      if (route.kind === "ses") return route.course ? `/admin/course/${route.course}` : "/admin/afisha";
      return route.kind === "crs" ? "/admin/courses" : `/admin/dict/${route.kind}`;
    default:
      return isAdminRoute(route) ? "/admin" : "/";
  }
}

// Глубина - в самой записи истории (history.state), а не в переменной: так она не сбивается, когда человек
// вернулся системной кнопкой «Назад» или жестом, и «Назад» в приложении не уводит за пределы приложения.
function depth(): number {
  return (window.history.state as { depth?: number } | null)?.depth ?? 0;
}

/** Кнопка бота открыла приложение сразу на экране (адрес …/?go=/my%3Forder%3D5, keyboards.app_page в боте): переходим
 * на него до первой отрисовки. Экран - параметром, а не после «#»: туда Telegram дописывает подпись пользователя, а
 * её SDK уже прочитал (telegram-web-app.js подключён раньше). Принимаем только свои адреса - «/…», не «//сайт». */
export function openStartRoute(): void {
  const url = new URL(window.location.href);
  const go = url.searchParams.get("go");
  if (!go) return;
  url.searchParams.delete("go");
  const internal = /^\/(?!\/)[\w\-/?=&.%]*$/.test(go);
  url.hash = internal ? go : "/";
  window.history.replaceState({ depth: 0 }, "", url.toString());
}

export function navigate(path: string, options: { replace?: boolean } = {}): void {
  const hash = `#${path}`;
  if (window.location.hash === hash) return;
  if (options.replace) window.history.replaceState({ depth: depth() }, "", hash);
  else window.history.pushState({ depth: depth() + 1 }, "", hash);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
  window.scrollTo(0, 0);
}

export function goBack(fallback = "/"): void {
  if (depth() > 0) window.history.back();
  else navigate(fallback, { replace: true });
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  useEffect(() => {
    const update = () => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", update);
    window.addEventListener("popstate", update);
    return () => {
      window.removeEventListener("hashchange", update);
      window.removeEventListener("popstate", update);
    };
  }, []);
  return route;
}

export function currentPath(): string {
  return window.location.hash.replace(/^#/, "") || "/";
}
