// Регистрация, как в боте: согласие → телефон («Поделиться номером» Telegram) → ФИО → направления → должность.
// Прервали - продолжат с того же шага (шаг считает сервер). После - туда, куда шли (next: запись на курс и т. п.).

import { useEffect, useState, type ReactNode } from "react";

import { ApiError, api, type Application, type ConsentForm, type Dictionary, type Step } from "../api";
import { Icon } from "../icons";
import { navigate } from "../router";
import { canRequestContact, openUrl, requestContact } from "../telegram";
import { ActionBar, Button, Check, ErrorState, Field, Loading, Rich, useApp, useLoad } from "../ui";

const ORDER: Step[] = ["consent", "phone", "name", "specialties", "positions", "done"];

export function StartScreen({ next }: { next: string }): ReactNode {
  const { me, refreshMe } = useApp();
  const [linked, setLinked] = useState<Application[] | null>(null);
  const step = me.step;

  const advance = async () => {
    await refreshMe();
  };

  if (linked && linked.length > 0) {
    return (
      <div className="page">
        <div className="state">
          <div className="big-icon ok">
            <Icon.ticket />
          </div>
          <h3>Нашли Вашу запись!</h3>
          <p>Коллега записал(а) Вас на курс:</p>
        </div>
        <div className="stack">
          {linked.map((app) => (
            <div className="card card-body" key={app.id}>
              <div style={{ fontWeight: 600 }}>{app.title}</div>
              <div className="muted small">
                {app.dates}
                {app.partner ? ` · записал(а) ${app.partner}` : ""}
              </div>
            </div>
          ))}
        </div>
        <ActionBar>
          <Button block onClick={() => setLinked(null)}>
            Продолжить регистрацию
          </Button>
        </ActionBar>
      </div>
    );
  }

  return (
    <div className="page">
      {step !== "done" && <Progress step={step} />}
      {step === "consent" && <ConsentStep onDone={advance} />}
      {step === "phone" && <PhoneStep onDone={advance} onLinked={setLinked} />}
      {step === "name" && <NameStep onDone={advance} />}
      {(step === "specialties" || step === "positions") && <SelectionStep key={step} kind={step} onDone={advance} />}
      {step === "done" && <DoneStep next={next} />}
    </div>
  );
}

function Progress({ step }: { step: Step }): ReactNode {
  const index = ORDER.indexOf(step);
  return (
    <div className="steps" aria-label={`Шаг ${index + 1} из 5`}>
      {ORDER.slice(0, 5).map((s, i) => (
        <span key={s} className={i <= index ? "done" : ""} />
      ))}
    </div>
  );
}

function ConsentStep({ onDone }: { onDone: () => Promise<void> }): ReactNode {
  const form = useLoad(api.consent);
  if (form.loading && !form.data) return <Loading rows={1} />;
  if (form.error || !form.data) return <ErrorState error={form.error!} onRetry={form.reload} />;
  return <ConsentView form={form.data} onDone={onDone} reload={form.reload} />;
}

function ConsentView({ form, onDone, reload }: { form: ConsentForm; onDone: () => Promise<void>; reload: () => void }) {
  const { toast, fail } = useApp();
  const [pd, setPd] = useState(false);
  const [ads, setAds] = useState(form.ads_checked);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!pd) {
      toast("Отметьте, пожалуйста, согласие на обработку персональных данных - без него не получится продолжить.", "error");
      return;
    }
    setBusy(true);
    try {
      await api.acceptConsent({
        pd,
        ads,
        pd_document_id: form.pd.document_id,
        ads_document_id: form.ads?.document_id ?? null,
      });
      await onDone();
    } catch (error) {
      if (error instanceof ApiError && error.code === "consent_updated") {
        toast(error.message);
        reload();
      } else {
        fail(error);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="page-header">
        <h1>Добро пожаловать!</h1>
      </div>
      <Rich html={form.intro_html} className="muted" />
      <div className="stack" style={{ marginTop: 16 }}>
        <Check on={pd} onToggle={() => setPd(!pd)}>
          <Rich html={form.pd.html} />
          <div className="muted small" style={{ marginTop: 4 }}>Обязательно</div>
        </Check>
        {form.ads && (
          <Check on={ads} onToggle={() => setAds(!ads)}>
            <Rich html={form.ads.html} />
            <div className="muted small" style={{ marginTop: 4 }}>По желанию</div>
          </Check>
        )}
      </div>
      <ActionBar>
        <Button block busy={busy} onClick={submit}>
          Продолжить
        </Button>
      </ActionBar>
    </>
  );
}

function PhoneStep({ onDone, onLinked }: {
  onDone: () => Promise<void>;
  onLinked: (apps: Application[]) => void;
}): ReactNode {
  const { me, fail } = useApp();
  const [busy, setBusy] = useState(false);
  const [devPhone, setDevPhone] = useState("");
  const [taken, setTaken] = useState<string | null>(null);
  const native = canRequestContact();

  const save = async (body: { contact?: string; phone?: string }) => {
    setBusy(true);
    try {
      const result = await api.savePhone(body);
      if (result.linked.length) onLinked(result.linked);
      await onDone();
    } catch (error) {
      if (error instanceof ApiError && error.code === "phone_taken") setTaken(error.message);
      else fail(error);
    } finally {
      setBusy(false);
    }
  };

  const share = async () => {
    const contact = await requestContact();
    if (contact) await save({ contact });
  };

  return (
    <>
      <div className="big-icon">
        <Icon.phone />
      </div>
      <div className="page-header center">
        <h1>Номер телефона</h1>
        <p>Нам потребуется Ваш номер телефона для связи по важным вопросам</p>
      </div>
      {taken && (
        <div className="notice warn" style={{ marginBottom: 12 }}>
          <span>⚠️</span>
          <span>{taken}</span>
        </div>
      )}
      {taken && me.config.manager_url && (
        <Button block variant="secondary" onClick={() => openUrl(me.config.manager_url!)}>
          <Icon.chat />
          Связаться с менеджером
        </Button>
      )}
      {!native && me.config.dev && (
        <div className="card card-body stack">
          <div className="dev-banner">Режим разработчика: номер можно ввести вручную</div>
          <Field label="Телефон">
            <input
              className="input"
              type="tel"
              value={devPhone}
              placeholder="+7 999 123-45-67"
              onChange={(event) => setDevPhone(event.target.value)}
            />
          </Field>
          <Button busy={busy} disabled={!devPhone.trim()} onClick={() => save({ phone: devPhone.trim() })}>
            Сохранить
          </Button>
        </div>
      )}
      {!native && !me.config.dev && (
        <div className="notice warn">Обновите Telegram, чтобы поделиться номером, или откройте приложение из бота.</div>
      )}
      {native && (
        <ActionBar>
          <Button block busy={busy} onClick={share}>
            <Icon.phone />
            Поделиться номером
          </Button>
        </ActionBar>
      )}
    </>
  );
}

function NameStep({ onDone }: { onDone: () => Promise<void> }): ReactNode {
  const { fail } = useApp();
  const [name, setName] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const cleanName = name.trim().replace(/\s+/g, " ");

  const save = async () => {
    setBusy(true);
    try {
      await api.saveName(cleanName);
      await onDone();
    } catch (e) {
      if (e instanceof ApiError && e.code === "name_invalid") {
        setError(e.message);
        setConfirming(false);
      } else {
        fail(e);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="page-header">
        <h1>ФИО</h1>
        <p>Фамилия, имя и отчество - так, как они должны быть написаны в сертификате о прохождении курса.</p>
      </div>
      {confirming ? (
        <div className="card card-body">
          <div className="muted small">Проверьте, пожалуйста</div>
          <div style={{ fontSize: 20, fontWeight: 650, margin: "6px 0 4px" }}>{cleanName}</div>
          <div className="muted small">Так ФИО попадёт в профиль и в сертификат.</div>
          <div className="actions">
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Исправить
            </Button>
            <Button busy={busy} onClick={save}>
              Всё верно
            </Button>
          </div>
        </div>
      ) : (
        <>
          <Field label="Фамилия Имя Отчество" error={error} hint="Например: Иванова Мария Сергеевна">
            <input
              className={`input ${error ? "invalid" : ""}`}
              value={name}
              autoComplete="name"
              autoFocus
              onChange={(event) => {
                setName(event.target.value);
                setError(null);
              }}
              onKeyDown={(event) => event.key === "Enter" && cleanName.split(" ").length >= 2 && setConfirming(true)}
            />
          </Field>
          <ActionBar>
            <Button block disabled={cleanName.split(" ").length < 2} onClick={() => setConfirming(true)}>
              Далее
            </Button>
          </ActionBar>
        </>
      )}
    </>
  );
}

function SelectionStep({ kind, onDone }: { kind: "specialties" | "positions"; onDone: () => Promise<void> }) {
  const dictionaries = useLoad(api.dictionaries);
  const [selected, setSelected] = useState<number[] | null>(null);
  const { fail } = useApp();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (dictionaries.data && selected === null) {
      setSelected(kind === "specialties" ? dictionaries.data.selected_specialties : dictionaries.data.selected_positions);
    }
  }, [dictionaries.data, kind, selected]);

  if (dictionaries.loading && !dictionaries.data) return <Loading rows={1} />;
  if (dictionaries.error || !dictionaries.data) return <ErrorState error={dictionaries.error!} onRetry={dictionaries.reload} />;
  const options: Dictionary[] = kind === "specialties" ? dictionaries.data.specialties : dictionaries.data.positions;
  const chosen = selected ?? [];

  const save = async () => {
    setBusy(true);
    try {
      if (kind === "specialties") await api.saveSpecialties(chosen);
      else await api.savePositions(chosen);
      await onDone();
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="page-header">
        <h1>{kind === "specialties" ? "Направления" : "Должность"}</h1>
        <p>
          {kind === "specialties"
            ? "Чем Вы занимаетесь? Можно выбрать несколько - так мы будем предлагать подходящие курсы."
            : "Кем Вы работаете? Можно выбрать несколько."}
        </p>
      </div>
      <SelectionChips options={options} selected={chosen} onChange={setSelected} />
      <ActionBar>
        <Button block busy={busy} disabled={chosen.length === 0} onClick={save}>
          Продолжить
        </Button>
      </ActionBar>
    </>
  );
}

export function SelectionChips({ options, selected, onChange }: {
  options: Dictionary[];
  selected: number[];
  onChange: (ids: number[]) => void;
}): ReactNode {
  return (
    <div className="chips">
      {options.map((option) => {
        const on = selected.includes(option.id);
        return (
          <button
            key={option.id}
            className={`chip ${on ? "on" : ""}`}
            aria-pressed={on}
            onClick={() => onChange(on ? selected.filter((id) => id !== option.id) : [...selected, option.id])}
          >
            {on ? "✓ " : ""}
            {option.title}
          </button>
        );
      })}
    </div>
  );
}

function DoneStep({ next }: { next: string }): ReactNode {
  const goingSomewhere = next && next !== "/" && next !== "/profile";
  return (
    <>
      <div className="state">
        <div className="big-icon ok">
          <Icon.check />
        </div>
        <h3>Профиль готов!</h3>
        <p>Город, место работы, стаж и e-mail можно добавить в разделе «Профиль»</p>
      </div>
      <ActionBar>
        <Button block onClick={() => navigate(next || "/", { replace: true })}>
          {goingSomewhere ? "Продолжить запись" : "Готово"}
        </Button>
        {goingSomewhere && (
          <Button block variant="ghost" onClick={() => navigate("/profile", { replace: true })}>
            Дополнить профиль
          </Button>
        )}
      </ActionBar>
    </>
  );
}
