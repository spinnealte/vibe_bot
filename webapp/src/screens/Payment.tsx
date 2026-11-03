// Оплата записи: «Как будете оплачивать?» (физлицо / юрлицо), реквизиты организации файлом, онлайн-оплата.
// То же, что в боте под «Вы записаны!» и в «Моих записях».

import { useRef, useState, type ReactNode } from "react";

import { ApiError, api, type Application, type Payment, type PaymentResult } from "../api";
import { Icon } from "../icons";
import { openUrl } from "../telegram";
import { ActionButton, Button, Field, Rich, Sheet, useApp } from "../ui";

const REQUISITES_ACCEPT = ".pdf,.doc,.docx,.xls,.xlsx,.odt,.ods,.rtf,.txt,.jpg,.jpeg,.png,.heic,.webp";

export function PaymentActions({ app, onChange }: { app: Application; onChange: (app: Application) => void }) {
  const { toast } = useApp();
  const order = app.order;
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [paying, setPaying] = useState(false);
  if (!order || !app.can_choose_payer) return null;

  const choose = async (type: "individual" | "organization") => {
    const result = await api.choosePayer(order.id, type);
    if (result.application) onChange(result.application);
  };

  const upload = async (file: File) => {
    setUploading(true);
    try {
      const result = await api.uploadRequisites(order.id, file);
      toast(result.message, "ok");
      if (result.application) onChange(result.application);
    } catch (error) {
      toast(error instanceof ApiError ? error.message : "Файл не отправился - попробуйте ещё раз.", "error");
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  if (order.payer_type === null) {
    return (
      <div style={{ marginTop: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Как будете оплачивать?</div>
        <div className="actions" style={{ marginTop: 0 }}>
          <ActionButton variant="secondary" onClick={() => choose("individual")}>
            <Icon.user />
            Физ. лицо
          </ActionButton>
          <ActionButton variant="secondary" onClick={() => choose("organization")}>
            <Icon.file />
            Юр. лицо
          </ActionButton>
        </div>
      </div>
    );
  }

  if (order.payer_type === "individual") {
    return (
      <div style={{ marginTop: 12 }}>
        <div className="notice">
          <span>👤</span>
          <span>
            Оплата как физлицо. Счёт придёт в чат с ботом или на почту из профиля
            {order.due && !app.paid ? ` - оплатить до ${order.due}` : ""}. Можно частями.
          </span>
        </div>
        <div className="actions">
          {app.can_pay_online && (
            <Button onClick={() => setPaying(true)}>
              <Icon.card />
              Оплатить онлайн
            </Button>
          )}
          <ActionButton variant="ghost" size="small" onClick={() => choose("organization")}>
            Платит организация
          </ActionButton>
        </div>
        {paying && <OnlinePaymentSheet app={app} onClose={() => setPaying(false)} onPaid={onChange} />}
      </div>
    );
  }

  return (
    <div style={{ marginTop: 12 }}>
      {order.requisites_received ? (
        <div className="notice ok">
          <span>✅</span>
          <span>
            Реквизиты получены{order.requisites_file_name ? ` (${order.requisites_file_name})` : ""}. Менеджер
            подготовит счёт и пришлёт его в чат с ботом.
          </span>
        </div>
      ) : (
        <div className="notice">
          <span>🏢</span>
          <span>Пришлите реквизиты организации файлом - PDF, Word, Excel или фото карточки предприятия (до 20 МБ).</span>
        </div>
      )}
      <input
        ref={fileInput}
        type="file"
        accept={REQUISITES_ACCEPT}
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void upload(file);
        }}
      />
      <div className="actions">
        <Button busy={uploading} variant={order.requisites_received ? "secondary" : "primary"} onClick={() => fileInput.current?.click()}>
          <Icon.file />
          {order.requisites_received ? "Заменить файл" : "Прислать реквизиты"}
        </Button>
        <ActionButton variant="ghost" size="small" onClick={() => choose("individual")}>
          Плачу сам
        </ActionButton>
      </div>
    </div>
  );
}

/** Онлайн-оплата: ссылка на страницу оплаты (или тестовые кнопки) и «Проверить оплату». */
function OnlinePaymentSheet({ app, onClose, onPaid }: {
  app: Application;
  onClose: () => void;
  onPaid: (app: Application) => void;
}): ReactNode {
  const { fail } = useApp();
  const [payment, setPayment] = useState<Payment | null>(null);
  const [result, setResult] = useState<PaymentResult | null>(null);
  const [needEmail, setNeedEmail] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const order = app.order!;

  const start = async (withEmail?: string) => {
    setBusy(true);
    try {
      const { payment } = await api.pay(order.id, withEmail);
      setPayment(payment);
      setNeedEmail(null);
      if (payment.url) openUrl(payment.url);
    } catch (error) {
      if (error instanceof ApiError && (error.code === "need_email" || error.code === "email_invalid")) {
        setNeedEmail(error.message);
      } else {
        fail(error);
      }
    } finally {
      setBusy(false);
    }
  };

  const [fresh, setFresh] = useState<Application | null>(null);

  const finish = async (call: () => Promise<PaymentResult>) => {
    const outcome = await call();
    setResult(outcome);
    if (outcome.state !== "pending") {
      setFresh((await api.applications()).items.find((item) => item.id === app.id) ?? null);
    }
  };

  // Записи обновляем, когда окно закрыли: иначе карточка перерисуется и окно с итогом пропадёт раньше времени.
  const close = () => {
    if (fresh) onPaid(fresh);
    onClose();
  };

  return (
    <Sheet open onClose={close}>
      <h2>Оплата онлайн</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Заказ №{order.number} · {app.title}
      </p>
      {result ? (
        <div className="stack">
          <div className={`notice ${result.state === "succeeded" ? "ok" : result.state === "canceled" ? "danger" : ""}`}>
            <Rich html={result.message_html} />
          </div>
          {result.state === "pending" && payment && <CheckButtons payment={payment} onResult={finish} />}
          {result.state === "canceled" && (
            <Button block onClick={() => { setResult(null); void start(); }}>
              Попробовать ещё раз
            </Button>
          )}
          <Button block variant="secondary" onClick={close}>
            Готово
          </Button>
        </div>
      ) : needEmail ? (
        <div className="stack">
          <div className="notice">{needEmail}</div>
          <Field label="E-mail для чека">
            <input
              className="input"
              type="email"
              inputMode="email"
              autoComplete="email"
              value={email}
              placeholder="ivanova@mail.ru"
              onChange={(event) => setEmail(event.target.value)}
            />
          </Field>
          <Button block busy={busy} disabled={!email.trim()} onClick={() => start(email.trim())}>
            Продолжить
          </Button>
        </div>
      ) : payment ? (
        <div className="stack">
          <div className="order-box" style={{ marginTop: 0 }}>
            <div className="order-line">
              <span className="muted">К оплате</span>
              <b>{payment.amount.text}</b>
            </div>
            {payment.receipt_email && (
              <div className="order-line">
                <span className="muted">Чек придёт на</span>
                <b>{payment.receipt_email}</b>
              </div>
            )}
          </div>
          {payment.fake ? (
            <>
              <div className="notice warn">🧪 Тестовая оплата: настоящие деньги не списываются.</div>
              <ActionButton block onClick={() => finish(() => api.fakePayment(payment.id, true))}>
                🧪 Оплата прошла
              </ActionButton>
              <ActionButton block variant="danger" onClick={() => finish(() => api.fakePayment(payment.id, false))}>
                Оплата не прошла
              </ActionButton>
            </>
          ) : (
            <>
              <div className="muted small">
                Страница оплаты открылась в браузере. Оплатите и вернитесь сюда - нажмите «Проверить оплату».
              </div>
              {payment.url && (
                <Button block variant="secondary" onClick={() => openUrl(payment.url!)}>
                  Открыть страницу оплаты
                </Button>
              )}
              <CheckButtons payment={payment} onResult={finish} />
            </>
          )}
        </div>
      ) : (
        <div className="stack">
          <div className="order-box" style={{ marginTop: 0 }}>
            <div className="order-line">
              <span className="muted">Осталось оплатить</span>
              <b>{order.left?.text ?? order.total?.text}</b>
            </div>
          </div>
          <p className="muted small" style={{ margin: 0 }}>
            Оплата картой или через СБП на странице ЮKassa
          </p>
          <Button block busy={busy} onClick={() => start()}>
            <Icon.card />
            Перейти к оплате
          </Button>
        </div>
      )}
    </Sheet>
  );
}

function CheckButtons({ payment, onResult }: {
  payment: Payment;
  onResult: (call: () => Promise<PaymentResult>) => Promise<void>;
}): ReactNode {
  return (
    <ActionButton block onClick={() => onResult(() => api.checkPayment(payment.id))}>
      🔄 Проверить оплату
    </ActionButton>
  );
}
