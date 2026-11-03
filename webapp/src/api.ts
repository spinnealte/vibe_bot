// Запросы к серверу приложения (stubbot/web/api.py). Каждый - с подписью Telegram в заголовке Authorization.

import { authorization } from "./telegram";

export type Step = "consent" | "phone" | "name" | "specialties" | "positions" | "done";

export interface Money {
  amount: number; // копейки
  text: string; // «45 000 ₽»
}

export interface Venue {
  id: number;
  name: string;
  address: string;
  map_url: string | null;
  directions_html: string;
}

export interface Price {
  id: number;
  label: string;
  amount: number;
  amount_text: string;
  pair: boolean;
  per_group: boolean;
}

export interface Mine {
  status: string;
  label: string;
}

export interface CourseCard {
  id: number;
  program_id: number;
  title: string;
  subtitle: string | null;
  cover_url: string;
  dates: string;
  start_date: string | null;
  end_date: string | null;
  venue: Venue | null;
  format: string;
  format_label: string;
  level_label: string | null;
  duration_hours: number | null;
  specialties: string[];
  lecturers: string[];
  status: string;
  status_label: string;
  seats_left: number | null;
  accepts_applications: boolean;
  goes_to_waitlist: boolean;
  announced: boolean;
  deadline: string | null;
  price_from: Money | null;
  prices_count: number;
  has_program: boolean;
  excerpt: string | null;
  mine: Mine | null;
}

export interface Lecturer {
  id: number;
  name: string;
  short_name: string;
  regalia: string | null;
  bio_html: string;
  photo_url: string | null;
}

export interface Day {
  date: string;
  label: string;
  time: string | null;
  topic: string | null;
  venue: Venue | null;
}

export interface CourseDetails extends CourseCard {
  days: Day[];
  lecturers_full: Lecturer[];
  prices: Price[];
  description_html: string;
  online: boolean;
  manager_url: string | null;
}

export interface ProgramPage {
  id: number;
  title: string;
  subtitle: string | null;
  cover_url: string;
  html: string;
  manager_url: string | null;
}

export interface Order {
  id: number;
  number: number;
  status: string;
  total: Money | null;
  paid: Money | null;
  left: Money | null;
  payable: boolean;
  payer_type: "individual" | "organization" | null;
  requisites_received: boolean;
  requisites_file_name: string | null;
  due: string | null;
  overdue: boolean;
  invoice_sent: boolean;
}

export interface Application {
  id: number;
  session_id: number;
  program_id: number;
  title: string;
  cover_url: string;
  dates: string;
  start_date: string | null;
  venue: Venue | null;
  price: Price | null;
  status: string;
  status_label: string;
  active: boolean;
  waitlist: boolean;
  is_payer: boolean;
  partner: string | null;
  payment_label: string; // статус оплаты - для коллеги, которого записали вдвоём
  order: Order | null;
  can_choose_payer: boolean;
  can_send_requisites: boolean;
  can_pay_online: boolean;
  can_cancel: boolean;
  cancel_until: string | null;
  paid: number;
  manager_cancel: boolean;
  manager_url: string | null;
}

export interface Me {
  client_id: number;
  first_name: string | null;
  step: Step;
  is_admin: boolean;
  config: {
    applications: boolean;
    online_payments: boolean;
    fake_payments: boolean;
    manager_url: string | null;
    bot_url: string | null;
    dev: boolean;
  };
}

export interface Dictionary {
  id: number;
  title: string;
}

export interface Profile {
  id: number;
  full_name: string | null;
  phone: string | null;
  email: string | null;
  birth_date: string | null;
  city: string | null;
  workplace: string | null;
  experience_years: number | null;
  specialties: Dictionary[];
  positions: Dictionary[];
  marketing: boolean;
  advance: Money | null;
  referral_link: string | null;
  referrals: number;
  step: Step;
}

export interface ConsentForm {
  intro_html: string;
  pd: { document_id: number; html: string };
  ads: { document_id: number; html: string } | null;
  ads_checked: boolean;
}

export interface Payment {
  id: number;
  url: string | null;
  order_number: number;
  title: string;
  amount: Money;
  fake: boolean;
  receipt_email: string | null;
}

export interface PaymentResult {
  state: "pending" | "succeeded" | "canceled";
  order_id: number;
  order_number: number;
  url: string | null;
  message_html: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public extra: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const auth = authorization();
  if (auth) headers.Authorization = auth;
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { method, headers, body: payload });
  } catch {
    throw new ApiError(0, "network", "Нет связи с сервером. Проверьте интернет и попробуйте ещё раз.");
  }
  let data: unknown = null;
  try {
    data = await response.json();
  } catch {
    /* не JSON - ниже общая ошибка */
  }
  if (!response.ok) {
    const error = (data as { error?: { code: string; message: string } & Record<string, unknown> })?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "server_error",
      error?.message || "Что-то пошло не так. Попробуйте ещё раз чуть позже.",
      error ?? {},
    );
  }
  return data as T;
}

export const api = {
  me: () => request<Me>("GET", "/me"),
  consent: () => request<ConsentForm>("GET", "/consent"),
  acceptConsent: (body: { pd: boolean; ads: boolean; pd_document_id: number; ads_document_id: number | null }) =>
    request<{ step: Step }>("POST", "/consent", body),
  savePhone: (body: { contact?: string; phone?: string }) =>
    request<{ result: string; linked_name: string | null; linked: Application[]; step: Step }>(
      "POST",
      "/profile/phone",
      body,
    ),
  saveName: (full_name: string) => request<{ full_name: string; step: Step }>("PUT", "/profile/name", { full_name }),
  dictionaries: () =>
    request<{
      specialties: Dictionary[];
      positions: Dictionary[];
      selected_specialties: number[];
      selected_positions: number[];
    }>("GET", "/dictionaries"),
  saveSpecialties: (ids: number[]) =>
    request<{ step: Step; just_completed: boolean }>("PUT", "/profile/specialties", { ids }),
  savePositions: (ids: number[]) =>
    request<{ step: Step; just_completed: boolean }>("PUT", "/profile/positions", { ids }),
  profile: () => request<Profile>("GET", "/profile"),
  editProfile: (field: string, value: string | null) => request<Profile>("PATCH", "/profile", { field, value }),
  marketing: () => request<{ document_id: number; html: string; on: boolean }>("GET", "/marketing"),
  setMarketing: (on: boolean, document_id?: number) =>
    request<{ marketing: boolean }>("POST", "/marketing", { on, document_id }),
  schedule: () => request<{ items: CourseCard[]; manager_url: string | null }>("GET", "/schedule"),
  course: (id: number) => request<CourseDetails>("GET", `/schedule/${id}`),
  program: (id: number) => request<ProgramPage>("GET", `/programs/${id}`),
  interest: (programId: number) =>
    request<{ created: boolean; message: string }>("POST", `/programs/${programId}/interest`),
  about: () => request<{ html: string; venues: Venue[]; manager_url: string | null }>("GET", "/about"),
  enroll: (
    sessionId: number,
    body: { price_id: number | null; colleague?: { full_name: string; phone: string; consent: boolean } },
  ) =>
    request<{
      outcome: "created" | "waitlist";
      application?: Application | null;
      colleague_name?: string | null;
      colleague_in_bot?: boolean;
      invite_link?: string | null;
      message?: string;
      pair?: boolean;
    }>("POST", `/sessions/${sessionId}/enroll`, body),
  applications: () => request<{ items: Application[]; advance: Money | null }>("GET", "/applications"),
  choosePayer: (orderId: number, type: "individual" | "organization") =>
    request<{ application: Application | null }>("POST", `/orders/${orderId}/payer`, { type }),
  uploadRequisites: (orderId: number, file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return request<{ message: string; application: Application | null }>("POST", `/orders/${orderId}/requisites`, form);
  },
  cancel: (enrollmentId: number, choice: string | null) =>
    request<{ outcome: string; choice: string | null; message_html: string; manager_url: string | null }>(
      "POST",
      `/applications/${enrollmentId}/cancel`,
      { choice },
    ),
  pay: (orderId: number, email?: string) =>
    request<{ payment: Payment }>("POST", `/orders/${orderId}/pay`, email ? { email } : {}),
  checkPayment: (paymentId: number) => request<PaymentResult>("POST", `/payments/${paymentId}/check`),
  fakePayment: (paymentId: number, success: boolean) =>
    request<PaymentResult>("POST", `/payments/${paymentId}/fake`, { success }),
};
