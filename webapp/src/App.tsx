// Каркас приложения: кто открыл (GET /api/me), нижняя панель, экраны по адресу, кнопка «Назад» Telegram.
// Две части: приложение слушателя и админка (#/admin…) - её видят только админы бота, у них на панели кнопка
// переключения режима.

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { ApiError, api, type Me } from "./api";
import { AdminProvider, useAdmin } from "./admin/common";
import { AfishaScreen, PeopleScreen, SessionScreen } from "./admin/Afisha";
import { ArchiveScreen, CatalogScreen, CourseAdminScreen, CoursesScreen, DictScreen, EntryScreen } from "./admin/Catalog";
import { ClientScreen, ClientsScreen, WaitlistScreen, WaitlistsScreen } from "./admin/Clients";
import { NewScreen } from "./admin/NewEntity";
import { OrderList, OrderScreen, OrdersHome } from "./admin/Orders";
import { Dock, type DockItem } from "./dock";
import { Icon } from "./icons";
import {
  ADMIN_TAB_ROUTES,
  TAB_ROUTES,
  adminTabOf,
  goBack,
  isAdminRoute,
  navigate,
  parentOf,
  tabOf,
  useRoute,
  type AdminTab,
  type Route,
  type Tab,
} from "./router";
import { haptic, insideTelegram, tg } from "./telegram";
import { AppContext, Button, ErrorState, Spinner, useApp, useToast } from "./ui";
import { AboutScreen } from "./screens/About";
import { CourseScreen, ProgramScreen } from "./screens/Course";
import { EnrollScreen } from "./screens/Enroll";
import { MyScreen } from "./screens/My";
import { ProfileScreen } from "./screens/Profile";
import { ScheduleScreen } from "./screens/Schedule";
import { StartScreen } from "./screens/Start";

const TABS: { tab: Tab; label: string; icon: () => ReactNode }[] = [
  { tab: "schedule", label: "Расписание", icon: Icon.calendar },
  { tab: "my", label: "Мои записи", icon: Icon.ticket },
  { tab: "profile", label: "Профиль", icon: Icon.user },
  { tab: "about", label: "О центре", icon: Icon.info },
];

const ADMIN_TABS: { tab: AdminTab; label: string; icon: () => ReactNode; orders?: boolean }[] = [
  { tab: "orders", label: "Заказы", icon: Icon.receipt, orders: true },
  { tab: "afisha", label: "Афиша", icon: Icon.calendar },
  { tab: "catalog", label: "Каталог", icon: Icon.layers },
  { tab: "clients", label: "Клиенты", icon: Icon.people, orders: true },
];

// Экраны с закреплённой кнопкой внизу (над панелью) - им нужен запас снизу.
const WITH_ACTION = ["course", "enroll", "start", "a-new"];

export function App(): ReactNode {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [toastNode, toast] = useToast();

  const refreshMe = useCallback(async () => {
    const fresh = await api.me();
    setMe(fresh);
    return fresh;
  }, []);

  useEffect(() => {
    refreshMe().catch((e) => setError(e instanceof ApiError ? e : new ApiError(0, "unknown", String(e))));
  }, [refreshMe]);

  const fail = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.code === "profile_required") {
        void refreshMe();
        navigate(`/start?next=${encodeURIComponent(window.location.hash.replace(/^#/, "") || "/")}`);
        return;
      }
      toast(e instanceof ApiError ? e.message : "Что-то пошло не так. Попробуйте ещё раз.", "error");
    },
    [refreshMe, toast],
  );

  const context = useMemo(() => (me ? { me, refreshMe, toast, fail } : null), [me, refreshMe, toast, fail]);

  if (error) return <Unavailable error={error} />;
  if (!context) {
    return (
      <div className="state" style={{ paddingTop: "40vh" }}>
        <Spinner />
      </div>
    );
  }
  return (
    <AppContext.Provider value={context}>
      <Shell />
      {toastNode}
    </AppContext.Provider>
  );
}

function Shell(): ReactNode {
  const route = useRoute();
  const { me } = useApp();
  const admin = isAdminRoute(route);
  useTelegramBack(route);

  useEffect(() => {
    if (admin && !me.is_admin) navigate("/", { replace: true });
  }, [admin, me.is_admin]);

  if (admin) {
    if (!me.is_admin) return null;
    return (
      <AdminProvider>
        <AdminShell route={route} />
      </AdminProvider>
    );
  }
  const tab = tabOf(route);
  return (
    <Frame route={route} withTabs={tab !== null}>
      {renderRoute(route)}
      {tab !== null && (
        <Dock
          items={TABS.map((t) => ({ key: t.tab, label: t.label, icon: t.icon }))}
          active={tab}
          onSelect={(key) => navigate(TAB_ROUTES[key as Tab], { replace: true })}
          mode={me.is_admin ? { admin: false, onToggle: () => navigate("/admin", { replace: true }) } : undefined}
        />
      )}
    </Frame>
  );
}

function AdminShell({ route }: { route: Route }): ReactNode {
  const { summary } = useAdmin();
  const tab = adminTabOf(route);
  const orders = summary.orders ?? {};
  const items: DockItem[] = ADMIN_TABS.filter((t) => summary.applications || !t.orders).map((t) => ({
    key: t.tab,
    label: t.label,
    icon: t.icon,
    badge: t.tab === "orders" ? (orders.inv ?? 0) + (orders.late ?? 0) : undefined,
  }));
  // Без записи на курсы (APPLICATIONS_ENABLED=false) заказов нет - главная админки - афиша.
  const redirect = !summary.applications && (route.name === "a-orders" || route.name === "a-clients");
  useEffect(() => {
    if (redirect) navigate("/admin/afisha", { replace: true });
  }, [redirect]);
  if (redirect) return null;
  return (
    <Frame route={route} withTabs={tab !== null}>
      {renderAdminRoute(route)}
      {tab !== null && (
        <Dock
          items={items}
          active={tab}
          onSelect={(key) => navigate(ADMIN_TAB_ROUTES[key as AdminTab], { replace: true })}
          mode={{ admin: true, onToggle: () => navigate("/", { replace: true }) }}
        />
      )}
    </Frame>
  );
}

function Frame({ route, withTabs, children }: { route: Route; withTabs: boolean; children: ReactNode }): ReactNode {
  const withAction = WITH_ACTION.includes(route.name);
  return (
    <div className={`app ${withTabs ? "" : "no-tabs"} ${withAction ? "with-action" : ""} ${isAdminRoute(route) ? "admin-mode" : ""}`}>
      {!insideTelegram && <DevBanner />}
      {!withTabs && <BackBar route={route} />}
      {children}
    </div>
  );
}

/** «Назад» на каждом экране, кроме разделов нижней панели: на предыдущий экран, а без истории - на экран выше.
 * Кнопка Telegram в шапке делает то же самое, но её видно не во всех приложениях Telegram. */
function BackBar({ route }: { route: Route }): ReactNode {
  return (
    <div className="back-bar">
      <button
        className="back-btn"
        onClick={() => {
          haptic("tap");
          goBack(parentOf(route));
        }}
      >
        <Icon.back />
        Назад
      </button>
    </div>
  );
}

function renderRoute(route: Route): ReactNode {
  switch (route.name) {
    case "schedule":
      return <ScheduleScreen />;
    case "course":
      return <CourseScreen key={route.id} id={route.id} />;
    case "program":
      return <ProgramScreen key={route.id} id={route.id} course={route.course} />;
    case "enroll":
      return <EnrollScreen key={route.id} id={route.id} />;
    case "my":
      return <MyScreen focus={route.focus} order={route.order} />;
    case "profile":
      return <ProfileScreen />;
    case "about":
      return <AboutScreen />;
    case "start":
      return <StartScreen next={route.next} />;
    default:
      return null;
  }
}

function renderAdminRoute(route: Route): ReactNode {
  switch (route.name) {
    case "a-orders":
      return <OrdersHome />;
    case "a-order-list":
      return <OrderList key={route.category} category={route.category} />;
    case "a-order":
      return <OrderScreen key={route.id} id={route.id} />;
    case "a-clients":
      return <ClientsScreen />;
    case "a-client":
      return <ClientScreen key={route.id} id={route.id} />;
    case "a-afisha":
      return <AfishaScreen scope={route.scope} />;
    case "a-session":
      return <SessionScreen key={route.id} id={route.id} />;
    case "a-people":
      return <PeopleScreen key={route.id} id={route.id} />;
    case "a-catalog":
      return <CatalogScreen />;
    case "a-courses":
      return <CoursesScreen key={route.scope} scope={route.scope} />;
    case "a-course":
      return <CourseAdminScreen key={route.id} id={route.id} />;
    case "a-dict":
      return <DictScreen key={route.kind} kind={route.kind} />;
    case "a-entry":
      return <EntryScreen key={`${route.kind}${route.id}`} kind={route.kind} id={route.id} />;
    case "a-new":
      return <NewScreen key={`${route.kind}-${route.copy ?? 0}-${route.course ?? 0}`} kind={route.kind} copy={route.copy} course={route.course} />;
    case "a-archive":
      return <ArchiveScreen />;
    case "a-waitlists":
      return <WaitlistsScreen />;
    case "a-waitlist":
      return <WaitlistScreen key={route.id} id={route.id} />;
    default:
      return null;
  }
}

/** Кнопка «Назад» в шапке Telegram - на всех экранах, кроме разделов нижней панели. */
function useTelegramBack(route: Route): void {
  useEffect(() => {
    const button = tg?.BackButton;
    if (!button || !insideTelegram) return;
    if (tabOf(route) !== null || adminTabOf(route) !== null) {
      button.hide();
      return;
    }
    const onBack = () => goBack(parentOf(route));
    button.show();
    button.onClick(onBack);
    return () => button.offClick(onBack);
  }, [route]);
}

function DevBanner(): ReactNode {
  return <div className="dev-banner">Браузер разработчика: вход без Telegram</div>;
}

function Unavailable({ error }: { error: ApiError }): ReactNode {
  const local = ["localhost", "127.0.0.1"].includes(window.location.hostname);
  if (error.status === 401) {
    return (
      <div className="page">
        <div className="state" style={{ paddingTop: "18vh" }}>
          <div className="big-icon">
            <Icon.tooth />
          </div>
          <h3>Откройте приложение из Telegram</h3>
          <p>Расписание курсов, запись и личный кабинет «АкваСтом» работают внутри Telegram - через бота центра.</p>
          {local && (
            <p className="small">
              Разработка в браузере: добавьте к адресу <code>?dev=123456</code> (нужен WEBAPP_DEV_AUTH=true).
            </p>
          )}
        </div>
      </div>
    );
  }
  return (
    <div className="page">
      <ErrorState error={error} />
      <div className="center">
        <Button variant="secondary" onClick={() => window.location.reload()}>
          Обновить
        </Button>
      </div>
    </div>
  );
}
