// Запросы админки (stubbot/web/admin_api.py). Те же сервисы, что у «⚙️ Админки» в боте.

import { request, type CourseDetails, type Money } from "../api";

export type Kind = "lec" | "ven" | "spec" | "pos" | "crs" | "ses";
export type FieldKind =
  | "full_name"
  | "line"
  | "html"
  | "url"
  | "number"
  | "choice"
  | "multi"
  | "ref"
  | "course"
  | "photo"
  | "document"
  | "days"
  | "datetime"
  | "prices";
export type Scope = "active" | "archived" | "past";

export interface FieldSpec {
  name: string;
  kind: FieldKind;
  label: string;
  required: boolean;
  editable: boolean;
  max_length: number | null;
  min: number | null;
  max: number | null;
  choices: { value: string; label: string }[];
  when: { field: string; values: string[] } | null;
}

export interface KindSpec {
  kind: Kind;
  title: string;
  new_title: string;
  fields: FieldSpec[];
}

export interface Specs {
  kinds: Record<Kind, KindSpec>;
  price_kinds: { value: string; label: string; per_group: boolean }[];
  statuses: { value: string; label: string }[];
  payment_methods: { value: string; label: string }[];
  max_days: number;
  max_price_label: number;
}

export interface Summary {
  applications: boolean;
  afisha: number;
  courses: number;
  archive: { courses: number; sessions: number; past: number };
  dictionaries: Record<"lec" | "ven" | "spec" | "pos", number>;
  orders?: Record<string, number>;
  orders_waiting_client?: number;
  clients?: number;
  waitlists?: number;
}

export interface Option {
  id: number;
  title: string;
}

export interface Photo {
  file_id: string;
  token: string;
  url: string;
}

export interface Day {
  date: string;
  start: string | null;
  end: string | null;
}

export interface PriceValue {
  kind: string;
  label: string;
  amount: number; // копейки
}

export interface CourseRow {
  id: number;
  title: string;
  subtitle: string | null;
  cover_url: string;
  specialties: string[];
  lecturers: string[];
  level_label: string | null;
  format_label: string;
  duration_hours: number | null;
  has_program: boolean;
  sessions_in_afisha: number;
  waiting: number;
  archived_at: string | null;
  sessions_total?: number;
  program_html?: string;
  description_html?: string;
}

export interface SessionRow {
  id: number;
  program_id: number;
  title: string;
  dates: string;
  start_date: string | null;
  month: number;
  month_label: string;
  status: string;
  status_label: string;
  format_label: string;
  venue: string | null;
  cover_url: string;
  capacity: number | null;
  taken: number;
  price_from: Money | null;
  scope: Scope;
  program_archived: boolean;
  archived_at: string | null;
}

export interface DictRow {
  id: number;
  title: string;
  active: boolean;
  subtitle: string | null;
}

export interface Item {
  kind: Kind;
  id: number;
  title: string;
  active: boolean;
  editable: boolean;
  values: Record<string, unknown>;
  html: Record<string, string>;
  photos: Record<string, Photo | null>;
  options: Record<string, Option[]>;
  display: Record<string, string>;
  course?: CourseRow & { sessions_total: number };
  session?: {
    scope: Scope;
    status: string;
    status_label: string;
    program: { id: number; title: string; archived: boolean };
    past: boolean;
    archived_at: string | null;
    participants: number | null;
    card: CourseDetails;
  };
}

export interface Person {
  id: number;
  name: string;
  short_name: string;
  phone: string | null;
  in_bot: boolean;
  username: string | null;
  blocked: boolean;
}

export interface OrderRow {
  id: number;
  number: number;
  payer: string;
  payer_short: string;
  total: Money | null;
  status: string;
  payer_type: "individual" | "organization" | null;
  requisites: boolean;
  invoice_sent: string | null;
  due: string | null;
  overdue: boolean;
  created: string;
}

export interface OrderCard {
  id: number;
  number: number;
  status: string;
  status_label: string;
  session: { id: number; program_id: number; title: string; dates: string };
  payer: Person;
  participants: (Person & { status: string; status_label: string })[];
  price_label: string | null;
  total: Money | null;
  paid: Money | null;
  left: Money | null;
  due: string | null;
  overdue: boolean;
  payer_type: "individual" | "organization" | null;
  requisites: { name: string | null } | null;
  invoice: { sent_at: string; manual: boolean } | null;
  online_pending: { amount: Money; created: string } | null;
  advance: Money | null;
  created: string;
  cancelled: { at: string | null; reason: string | null; choice: string | null } | null;
  money_pending: Money | null;
  money_settled: boolean;
  category: string | null;
  actions: {
    invoice: boolean;
    invoice_manual: boolean;
    invoice_undo: boolean;
    total: boolean;
    pay: boolean;
    apply_advance: Money | null;
    settle: boolean;
    remove: boolean;
    requisites: boolean;
  };
}

export interface ClientRow {
  id: number;
  name: string;
  phone_tail: string | null;
  in_bot: boolean;
  username: string | null;
}

export interface ClientListRow extends ClientRow {
  since: string | null;
  blocked: boolean;
  profile_done: boolean;
  active: number; // действующих записей
}

export interface ClientList {
  items: ClientListRow[];
  total: number;
  page: number;
  pages: number;
  per_page: number;
}

export type ClientFilter = "all" | "profile" | "enrolled" | "blocked";
export type ClientSort = "new" | "old" | "name";

export interface ClientCard {
  id: number;
  full_name: string | null;
  telegram_name: string | null;
  phone: string | null;
  email: string | null;
  city: string | null;
  workplace: string | null;
  experience_years: number | null;
  birth_date: string | null;
  specialties: string[];
  username: string | null;
  in_bot: boolean;
  blocked: boolean;
  marketing: boolean;
  advance: Money;
  since: string | null;
  last_seen: string | null;
  enrollments: {
    id: number;
    session_id: number;
    title: string;
    dates: string;
    status: string;
    status_label: string;
    cancelled: boolean;
    order: { id: number; number: number } | null;
  }[];
  orders: { id: number; number: number; total: Money | null; status: string; status_label: string }[];
  waiting: { program_id: number; title: string; since: string | null }[];
}

export interface ParticipantRow extends ClientRow {
  enrollment_id: number;
  status: string;
  status_label: string;
  seats: number;
  order: { id: number; number: number } | null;
}

export interface Participants {
  session: { id: number; title: string; dates: string; capacity: number | null };
  seats_left: number | null;
  confirmed: ParticipantRow[];
  awaiting: ParticipantRow[];
  waitlist: ParticipantRow[];
  cancelled: ParticipantRow[];
}

export interface Delivered {
  delivery: "delivered" | "blocked" | "failed" | "no_telegram";
  delivered: boolean;
  message: string;
  order?: OrderCard;
}

type Saved = { item: Item; message: string };

function form(text: string, token: string, file: File | null): FormData {
  const data = new FormData();
  data.append("text", text);
  data.append("token", token);
  if (file) data.append("file", file, file.name);
  return data;
}

function upload(file: File): FormData {
  const data = new FormData();
  data.append("file", file, file.name);
  return data;
}

export const admin = {
  summary: () => request<Summary>("GET", "/admin/summary"),
  specs: () => request<Specs>("GET", "/admin/specs"),

  courses: (scope: "active" | "archived") =>
    request<{ items: CourseRow[] }>("GET", `/admin/e/crs?scope=${scope}`),
  sessions: (scope: Scope) => request<{ items: SessionRow[] }>("GET", `/admin/e/ses?scope=${scope}`),
  dictionary: (kind: Kind) => request<{ items: DictRow[] }>("GET", `/admin/e/${kind}`),
  newForm: (kind: Kind) =>
    request<{ kind: Kind; options: Record<string, Option[]>; courses?: CourseRow[] }>("GET", `/admin/e/${kind}/new`),
  item: (kind: Kind, id: number) => request<Item>("GET", `/admin/e/${kind}/${id}`),
  preview: (kind: Kind, values: Record<string, unknown>) =>
    request<{ card?: CourseDetails; course?: CourseRow; values?: Record<string, unknown> }>(
      "POST",
      `/admin/e/${kind}/preview`,
      { values },
    ),
  create: (kind: Kind, values: Record<string, unknown>) => request<Saved>("POST", `/admin/e/${kind}`, { values }),
  update: (kind: Kind, id: number, field: string, value: unknown) =>
    request<Saved>("PATCH", `/admin/e/${kind}/${id}`, { field, value }),
  archive: (kind: Kind, id: number, archived: boolean) =>
    request<Saved>("POST", `/admin/e/${kind}/${id}/archive`, { archived }),
  remove: (kind: Kind, id: number) => request<{ message: string }>("DELETE", `/admin/e/${kind}/${id}`),
  setStatus: (id: number, status: string) => request<Saved>("POST", `/admin/e/ses/${id}/status`, { status }),
  copy: (id: number) =>
    request<{ values: Record<string, unknown>; options: Record<string, Option[]>; program: Option }>(
      "GET",
      `/admin/e/ses/${id}/copy`,
    ),
  participants: (id: number) => request<Participants>("GET", `/admin/e/ses/${id}/participants`),
  uploadPhoto: (file: File) => request<{ photo: Photo }>("POST", "/admin/upload/photo", upload(file)),
  uploadProgram: (file: File) => request<{ html: string }>("POST", "/admin/upload/program", upload(file)),

  waitlists: () => request<{ items: { program_id: number; title: string; count: number }[] }>("GET", "/admin/waitlists"),
  waitlist: (programId: number) =>
    request<{ program: Option; items: (ClientRow & { since: string })[] }>("GET", `/admin/waitlists/${programId}`),
  invited: (programId: number) =>
    request<{ count: number; message: string }>("POST", `/admin/waitlists/${programId}/invited`),

  orders: (category: string) =>
    request<{ category: string; title: string; items: OrderRow[] }>("GET", `/admin/orders?category=${category}`),
  findOrder: (number: string) =>
    request<{ id: number }>("GET", `/admin/orders/find?number=${encodeURIComponent(number)}`),
  order: (id: number) => request<OrderCard>("GET", `/admin/orders/${id}`),
  pay: (id: number, body: { method: string; token: string; amount?: number; full?: boolean }) =>
    request<{ order: OrderCard; message: string }>("POST", `/admin/orders/${id}/payment`, body),
  setTotal: (id: number, amount: number) =>
    request<{ order: OrderCard; message: string }>("POST", `/admin/orders/${id}/total`, { amount }),
  invoiceManual: (id: number, on: boolean) =>
    request<{ order: OrderCard; message: string }>(on ? "POST" : "DELETE", `/admin/orders/${id}/invoice-manual`),
  invoiceCaption: (id: number) => request<{ html: string }>("GET", `/admin/orders/${id}/invoice-caption`),
  sendInvoice: (id: number, text: string, token: string, file: File | null) =>
    request<Delivered>("POST", `/admin/orders/${id}/invoice`, form(text, token, file)),
  messagePayer: (id: number, text: string, token: string, file: File | null) =>
    request<Delivered>("POST", `/admin/orders/${id}/message`, form(text, token, file)),
  removeBooking: (id: number) => request<{ order: OrderCard; message: string }>("POST", `/admin/orders/${id}/remove`),
  credit: (id: number) => request<{ order: OrderCard; message: string }>("POST", `/admin/orders/${id}/credit`),
  refund: (id: number) => request<{ order: OrderCard; message: string }>("POST", `/admin/orders/${id}/refund`),
  applyAdvance: (id: number) =>
    request<{ order: OrderCard; message: string }>("POST", `/admin/orders/${id}/apply-advance`),
  requisites: (id: number) => request<{ message: string }>("POST", `/admin/orders/${id}/requisites`),

  searchClients: (q: string) =>
    request<{ items: ClientRow[]; more: boolean }>("GET", `/admin/clients?q=${encodeURIComponent(q)}`),
  clientList: (params: { q: string; filter: ClientFilter; sort: ClientSort; page: number }) =>
    request<ClientList>(
      "GET",
      `/admin/clients/list?${new URLSearchParams({ q: params.q, filter: params.filter, sort: params.sort, page: String(params.page) })}`,
    ),
  client: (id: number) => request<ClientCard>("GET", `/admin/clients/${id}`),
  advance: (id: number, amount: number, reason: string, token: string) =>
    request<{ client: ClientCard; message: string }>("POST", `/admin/clients/${id}/advance`, { amount, reason, token }),
  messageClient: (id: number, text: string, token: string, file: File | null) =>
    request<Delivered>("POST", `/admin/clients/${id}/message`, form(text, token, file)),
  exportClients: () => request<{ count: number; message: string }>("POST", "/admin/export"),
};

/** Одноразовый ключ действия: повтор того же нажатия сервер не выполнит второй раз. */
export function newToken(): string {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** «45 000 ₽» из копеек - как формат денег бота (неразрывные пробелы). */
export function rub(kopecks: number): string {
  return `${Math.trunc(kopecks / 100).toLocaleString("ru-RU")} ₽`;
}
