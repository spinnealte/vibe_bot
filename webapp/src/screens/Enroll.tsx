// Запись на курс: тариф → (для двух коллег) коллега → проверка → «Записаться» → «Вы записаны!» и оплата.
// Правила - на сервере (EnrollmentService): здесь только ввод и понятные подсказки.

import { useEffect, useState, type ReactNode } from "react";

import { ApiError, api, type Application, type CourseDetails, type Price } from "../api";
import { Icon } from "../icons";
import { navigate } from "../router";
import { openUrl } from "../telegram";
import {
  ActionBar,
  Button,
  Check,
  Empty,
  ErrorState,
  Field,
  Loading,
  PageHeader,
  clean,
  useApp,
  useLoad,
} from "../ui";
import { PaymentActions } from "./Payment";

interface Created {
  application: Application | null;
  colleague_name?: string | null;
  colleague_in_bot?: boolean;
  invite_link?: string | null;
}

export function EnrollScreen({ id }: { id: number }): ReactNode {
  const { me } = useApp();
  const course = useLoad(() => api.course(id), [id]);
  const [created, setCreated] = useState<Created | null>(null);
  const [waitlist, setWaitlist] = useState<string | null>(null);

  useEffect(() => {
    if (me.step !== "done") navigate(`/start?next=${encodeURIComponent(`/enroll/${id}`)}`, { replace: true });
  }, [me.step, id]);

  if (created) return <Enrolled created={created} onChange={(application) => setCreated({ ...created, application })} />;
  if (waitlist) {
    return (
      <div className="page">
        <Empty icon={<Icon.clock />} title="Вы в листе ожидания" text={waitlist}>
          <Button variant="secondary" onClick={() => navigate("/my", { replace: true })}>
            Мои записи
          </Button>
        </Empty>
      </div>
    );
  }
  if (course.loading && !course.data) {
    return (
      <div className="page">
        <Loading rows={1} />
      </div>
    );
  }
  if (course.error || !course.data) {
    return (
      <div className="page">
        <ErrorState error={course.error!} onRetry={course.reload} />
      </div>
    );
  }
  return <EnrollForm course={course.data} onCreated={setCreated} onWaitlist={setWaitlist} />;
}

function EnrollForm({ course, onCreated, onWaitlist }: {
  course: CourseDetails;
  onCreated: (created: Created) => void;
  onWaitlist: (message: string) => void;
}): ReactNode {
  const { fail, refreshMe } = useApp();
  const prices = course.prices;
  const [priceId, setPriceId] = useState<number | null>(prices.length === 1 ? prices[0].id : null);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [consent, setConsent] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  if (course.mine) {
    return (
      <div className="page">
        <Empty icon={<Icon.ticket />} title="Вы уже записаны" text={`Статус: ${clean(course.mine.label)}`}>
          <Button onClick={() => navigate("/my", { replace: true })}>Открыть мою запись</Button>
        </Empty>
      </div>
    );
  }
  if (!course.accepts_applications) {
    return (
      <div className="page">
        <Empty icon={<Icon.calendar />} title="Запись закрыта" text="Загляните в расписание - там есть другие даты.">
          <Button variant="secondary" onClick={() => navigate("/", { replace: true })}>
            К расписанию
          </Button>
        </Empty>
      </div>
    );
  }

  const price: Price | undefined = prices.find((p) => p.id === priceId);
  const pair = Boolean(price?.pair);
  const seats = pair ? 2 : 1;
  const waitlist = course.goes_to_waitlist || (course.seats_left !== null && course.seats_left < seats);
  const ready = (prices.length === 0 || price) && (!pair || (name.trim() && phone.trim() && consent));

  const submit = async () => {
    setBusy(true);
    setErrors({});
    try {
      const result = await api.enroll(course.id, {
        price_id: price?.id ?? null,
        colleague: pair ? { full_name: name.trim(), phone: phone.trim(), consent } : undefined,
      });
      await refreshMe();
      if (result.outcome === "created") onCreated({ ...result, application: result.application ?? null });
      else onWaitlist(result.message ?? "Если место освободится, менеджер свяжется с Вами.");
    } catch (error) {
      if (error instanceof ApiError && error.code.startsWith("colleague_")) {
        const field = error.code.includes("name") ? "name" : error.code.includes("consent") ? "consent" : "phone";
        setErrors({ [field]: error.message });
      } else {
        fail(error);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <PageHeader title="Запись на курс" text={`${course.title} · ${course.dates}`} />

      {prices.length > 0 && (
        <>
          <div className="section-title">Вариант участия</div>
          <div className="stack" role="radiogroup">
            {prices.map((option) => (
              <Check key={option.id} radio on={option.id === priceId} onToggle={() => setPriceId(option.id)}>
                <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
                  <span>{option.label}</span>
                  <b style={{ whiteSpace: "nowrap" }}>{option.amount_text}</b>
                </div>
                {option.pair && <div className="muted small">Парное участие</div>}
              </Check>
            ))}
          </div>
        </>
      )}

      {pair && (
        <>
          <div className="section-title">Коллега</div>
          <div className="card card-body stack">
            <Field label="ФИО коллеги полностью" error={errors.name}>
              <input
                className={`input ${errors.name ? "invalid" : ""}`}
                value={name}
                autoComplete="off"
                placeholder="Петров Пётр Ильич"
                onChange={(event) => setName(event.target.value)}
              />
            </Field>
            <Field label="Телефон коллеги" error={errors.phone}>
              <input
                className={`input ${errors.phone ? "invalid" : ""}`}
                value={phone}
                type="tel"
                inputMode="tel"
                autoComplete="off"
                placeholder="+7 999 123-45-67"
                onChange={(event) => setPhone(event.target.value)}
              />
            </Field>
            <Check on={consent} onToggle={() => setConsent(!consent)}>
              Коллега согласен на передачу его ФИО и телефона компании «АкваСтом Груп»
            </Check>
            {errors.consent && <div className="field-error">{errors.consent}</div>}
          </div>
        </>
      )}

      <div className="section-title">Проверьте запись</div>
      <div className="card card-body">
        <div className="order-line">
          <span className="muted">Курс</span>
          <b>{course.title}</b>
        </div>
        <div className="order-line">
          <span className="muted">Даты</span>
          <b>{course.dates}</b>
        </div>
        {course.venue && (
          <div className="order-line">
            <span className="muted">Где</span>
            <b>{course.venue.name}</b>
          </div>
        )}
        <div className="order-line">
          <span className="muted">Стоимость</span>
          <b>{price ? price.amount_text : prices.length ? "выберите вариант" : "уточнит менеджер"}</b>
        </div>
        {pair && name.trim() && (
          <div className="order-line">
            <span className="muted">Участники</span>
            <b>Вы и {name.trim()}</b>
          </div>
        )}
      </div>
      <div className={`notice ${waitlist ? "warn" : ""}`} style={{ marginTop: 12 }}>
        <span>{waitlist ? "🕒" : "ℹ️"}</span>
        <span>
          {waitlist
            ? "Свободных мест сейчас нет - запись попадёт в лист ожидания. Если место освободится, менеджер свяжется с Вами."
            : "Место закрепится за Вами сразу. Оплатить нужно в течение 72 часов - по счёту или онлайн, можно частями."}
        </span>
      </div>

      <ActionBar>
        <Button block busy={busy} disabled={!ready} onClick={submit}>
          {waitlist ? "Встать в лист ожидания" : "Записаться"}
        </Button>
      </ActionBar>
    </div>
  );
}

function Enrolled({ created, onChange }: { created: Created; onChange: (app: Application) => void }): ReactNode {
  const app = created.application;
  const order = app?.order;
  return (
    <div className="page">
      <div className="state" style={{ paddingBottom: 16 }}>
        <div className="big-icon ok">
          <Icon.check />
        </div>
        <h3>Вы записаны!</h3>
        <p>
          {app?.title}
          <br />
          {app?.dates}
        </p>
      </div>
      {app && order && (
        <div className="card card-body">
          <div className="order-line">
            <span className="muted">Заказ</span>
            <b>№{order.number}</b>
          </div>
          <div className="order-line">
            <span className="muted">Сумма</span>
            <b>{order.total?.text ?? "уточнит менеджер"}</b>
          </div>
          {order.paid && (
            <div className="order-line">
              <span className="muted">Оплачено</span>
              <b>{order.left && order.left.amount > 0 ? `${order.paid.text}, осталось ${order.left.text}` : `${order.paid.text} - место закреплено`}</b>
            </div>
          )}
          {order.due && (
            <div className="order-line">
              <span className="muted">Оплатить до</span>
              <b>{order.due}</b>
            </div>
          )}
          {created.colleague_name && (
            <div className="order-line">
              <span className="muted">Вместе с Вами</span>
              <b>{created.colleague_name}</b>
            </div>
          )}
          <PaymentActions app={app} onChange={onChange} />
        </div>
      )}
      {created.invite_link && (
        <div className="card card-body" style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 600 }}>Пригласите коллегу в бот, чтобы он мог отслеживать свою запись на обучение</div>
          <p className="muted small" style={{ margin: "4px 0 10px" }}>
            После перехода по ссылке и регистрации запись появится в профиле!
          </p>
          <Button
            variant="secondary"
            size="small"
            onClick={() =>
              openUrl(
                `https://t.me/share/url?url=${encodeURIComponent(created.invite_link!)}&text=${encodeURIComponent(
                  "Записал(а) нас на курс в «АкваСтом» - заходи в бот, там наша запись:",
                )}`,
              )
            }
          >
            <Icon.share />
            Отправить ссылку
          </Button>
        </div>
      )}
      <div className="stack" style={{ marginTop: 16 }}>
        <Button block variant="secondary" onClick={() => navigate("/my", { replace: true })}>
          <Icon.ticket />
          Мои записи
        </Button>
      </div>
    </div>
  );
}
