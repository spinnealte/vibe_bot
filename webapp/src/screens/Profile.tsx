// «Профиль» (личный кабинет бота): данные, направления и должность, рассылки, аванс, приглашение коллег.

import { useState, type ReactNode } from "react";

import { ApiError, api, type Dictionary, type Profile } from "../api";
import { Icon } from "../icons";
import { navigate } from "../router";
import { canRequestContact, openUrl, requestContact } from "../telegram";
import {
  ActionButton,
  Button,
  Empty,
  ErrorState,
  Field,
  Loading,
  Rich,
  Sheet,
  plural,
  useApp,
  useLoad,
} from "../ui";
import { initials } from "./Course";
import { SelectionChips } from "./Start";

type Editable = "full_name" | "phone" | "city" | "workplace_raw" | "practice_since_year" | "email" | "birth_date";

const FIELDS: { key: Editable; label: string; placeholder: string; hint?: string; type?: string }[] = [
  { key: "city", label: "Город", placeholder: "Санкт-Петербург" },
  { key: "workplace_raw", label: "Место работы", placeholder: "Клиника «Улыбка»" },
  { key: "practice_since_year", label: "Стаж, лет", placeholder: "7", type: "number" },
  { key: "email", label: "E-mail", placeholder: "ivanova@mail.ru", hint: "Сюда будут приходить счета и чеки об оплате", type: "email" },
  { key: "birth_date", label: "Дата рождения", placeholder: "15.04.1995", hint: "В формате дд.мм.гггг" },
];

function display(profile: Profile, key: Editable): string | null {
  switch (key) {
    case "full_name":
      return profile.full_name;
    case "phone":
      return profile.phone;
    case "city":
      return profile.city;
    case "workplace_raw":
      return profile.workplace;
    case "practice_since_year":
      return profile.experience_years === null
        ? null
        : `${profile.experience_years} ${plural(profile.experience_years, "год", "года", "лет")}`;
    case "email":
      return profile.email;
    case "birth_date":
      return profile.birth_date;
  }
}

export function ProfileScreen(): ReactNode {
  const { me } = useApp();
  if (me.step !== "done") {
    return (
      <div className="page">
        <Empty
          icon={<Icon.user />}
          title="Личный кабинет"
          text="Заполните короткий профиль - и записывайтесь на курсы в пару нажатий."
        >
          <Button onClick={() => navigate(`/start?next=${encodeURIComponent("/profile")}`)}>
            {me.step === "consent" ? "Завести кабинет" : "Продолжить регистрацию"}
          </Button>
        </Empty>
      </div>
    );
  }
  return <ProfileLoaded />;
}

function ProfileLoaded(): ReactNode {
  const profile = useLoad(api.profile);
  const [editing, setEditing] = useState<Editable | "specialties" | "positions" | "marketing" | null>(null);
  if (profile.loading && !profile.data) {
    return (
      <div className="page">
        <Loading rows={2} />
      </div>
    );
  }
  if (profile.error || !profile.data) {
    return (
      <div className="page">
        <ErrorState error={profile.error!} onRetry={profile.reload} />
      </div>
    );
  }
  const data = profile.data;
  const close = () => setEditing(null);
  const saved = (fresh: Profile) => {
    profile.setData(fresh);
    close();
  };

  return (
    <div className="page">
      <div className="row" style={{ gap: 14, padding: "10px 2px 18px" }}>
        <div className="avatar" style={{ width: 60, height: 60, fontSize: 20 }}>
          {initials(data.full_name)}
        </div>
        <div className="grow">
          <div style={{ fontSize: 20, fontWeight: 700, lineHeight: 1.2 }}>{data.full_name}</div>
          <div className="muted">{data.phone}</div>
        </div>
      </div>

      {data.advance && (
        <div className="notice ok" style={{ marginBottom: 12 }}>
          <span>💰</span>
          <span>
            Аванс: <b>{data.advance.text}</b> - менеджер зачтёт его в оплату курса.
          </span>
        </div>
      )}

      <div className="list">
        <Row label="ФИО" value={data.full_name} onClick={() => setEditing("full_name")} />
        <Row label="Телефон" value={data.phone} onClick={() => setEditing("phone")} />
        {FIELDS.map((field) => (
          <Row key={field.key} label={field.label} value={display(data, field.key)} onClick={() => setEditing(field.key)} />
        ))}
      </div>

      <div className="section-title">Работа</div>
      <div className="list">
        <Row label="Направления" value={data.specialties.map((s) => s.title).join(", ")} onClick={() => setEditing("specialties")} />
        <Row label="Должность" value={data.positions.map((p) => p.title).join(", ")} onClick={() => setEditing("positions")} />
      </div>

      <div className="section-title">Рассылки</div>
      <div className="list">
        <button className="list-item" onClick={() => setEditing("marketing")}>
          <Icon.bell />
          <div className="grow">
            <div className="value">Анонсы новых курсов</div>
            <div className="label">{data.marketing ? "Включены" : "Выключены"}</div>
          </div>
          <span className={`badge ${data.marketing ? "ok" : ""}`}>{data.marketing ? "Вкл" : "Выкл"}</span>
        </button>
      </div>

      {data.referral_link && (
        <>
          <div className="section-title">Пригласить коллег</div>
          <div className="card card-body">
            <div className="row" style={{ alignItems: "flex-start" }}>
              <Icon.gift />
              <div className="grow">
                <div style={{ fontWeight: 600 }}>Ваша ссылка-приглашение</div>
                <div className="muted small" style={{ wordBreak: "break-all" }}>{data.referral_link}</div>
                <div className="muted small" style={{ marginTop: 4 }}>
                  По ней пришло: {data.referrals} {plural(data.referrals, "человек", "человека", "человек")}
                </div>
              </div>
            </div>
            <div className="actions">
              <Button
                size="small"
                variant="secondary"
                onClick={() =>
                  openUrl(
                    `https://t.me/share/url?url=${encodeURIComponent(data.referral_link!)}&text=${encodeURIComponent(
                      "Курсы для стоматологов в «АкваСтом» - расписание и запись в боте:",
                    )}`,
                  )
                }
              >
                <Icon.share />
                Поделиться
              </Button>
              <CopyButton text={data.referral_link} />
            </div>
          </div>
        </>
      )}

      {editing === "full_name" && <NameSheet profile={data} onClose={close} onSaved={saved} />}
      {editing === "phone" && <PhoneSheet onClose={close} onSaved={() => { close(); profile.reload(); }} />}
      {FIELDS.map(
        (field) =>
          editing === field.key && (
            <FieldSheet key={field.key} field={field} profile={data} onClose={close} onSaved={saved} />
          ),
      )}
      {(editing === "specialties" || editing === "positions") && (
        <SelectionSheet kind={editing} profile={data} onClose={close} onSaved={() => { close(); profile.reload(); }} />
      )}
      {editing === "marketing" && (
        <MarketingSheet on={data.marketing} onClose={close} onSaved={(on) => { profile.setData({ ...data, marketing: on }); close(); }} />
      )}
    </div>
  );
}

function Row({ label, value, onClick }: { label: string; value: string | null; onClick: () => void }): ReactNode {
  return (
    <button className="list-item" onClick={onClick}>
      <div className="grow">
        <div className="label">{label}</div>
        <div className={`value ${value ? "" : "empty"}`}>{value || "не указано"}</div>
      </div>
      <Icon.chevron />
    </button>
  );
}

function CopyButton({ text }: { text: string }): ReactNode {
  const { toast } = useApp();
  return (
    <Button
      size="small"
      variant="ghost"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          toast("Ссылка скопирована", "ok");
        } catch {
          toast("Не получилось скопировать - выделите ссылку вручную", "error");
        }
      }}
    >
      Скопировать
    </Button>
  );
}

function FieldSheet({ field, profile, onClose, onSaved }: {
  field: (typeof FIELDS)[number];
  profile: Profile;
  onClose: () => void;
  onSaved: (profile: Profile) => void;
}): ReactNode {
  const current = field.key === "practice_since_year" ? profile.experience_years?.toString() ?? "" : display(profile, field.key) ?? "";
  const [value, setValue] = useState(current);
  const [error, setError] = useState<string | null>(null);
  const { fail } = useApp();

  const save = async (raw: string | null) => {
    try {
      onSaved(await api.editProfile(field.key, raw));
    } catch (e) {
      if (e instanceof ApiError && e.code === "invalid_value") setError(e.message);
      else fail(e);
    }
  };

  return (
    <Sheet open onClose={onClose}>
      <h2>{field.label}</h2>
      <div className="stack">
        <Field label={field.label} error={error} hint={field.hint}>
          <input
            className={`input ${error ? "invalid" : ""}`}
            value={value}
            type={field.type === "number" ? "text" : field.type ?? "text"}
            inputMode={field.type === "number" ? "numeric" : field.type === "email" ? "email" : undefined}
            placeholder={field.placeholder}
            autoFocus
            onChange={(event) => {
              setValue(event.target.value);
              setError(null);
            }}
          />
        </Field>
        <ActionButton block disabled={!value.trim()} onClick={() => save(value.trim())}>
          Сохранить
        </ActionButton>
        {current && (
          <ActionButton block variant="ghost" onClick={() => save(null)}>
            Очистить
          </ActionButton>
        )}
      </div>
    </Sheet>
  );
}

function NameSheet({ profile, onClose, onSaved }: {
  profile: Profile;
  onClose: () => void;
  onSaved: (profile: Profile) => void;
}): ReactNode {
  const [value, setValue] = useState(profile.full_name ?? "");
  const [error, setError] = useState<string | null>(null);
  const { fail } = useApp();
  return (
    <Sheet open onClose={onClose}>
      <h2>ФИО</h2>
      <div className="stack">
        <Field label="Фамилия Имя Отчество" error={error} hint="Так ФИО попадёт в сертификат - проверьте, что без ошибок">
          <input
            className={`input ${error ? "invalid" : ""}`}
            value={value}
            autoFocus
            onChange={(event) => {
              setValue(event.target.value);
              setError(null);
            }}
          />
        </Field>
        <ActionButton
          block
          disabled={value.trim().split(/\s+/).length < 2}
          onClick={async () => {
            try {
              const result = await api.saveName(value);
              onSaved({ ...profile, full_name: result.full_name });
            } catch (e) {
              if (e instanceof ApiError && e.code === "name_invalid") setError(e.message);
              else fail(e);
            }
          }}
        >
          Сохранить
        </ActionButton>
      </div>
    </Sheet>
  );
}

function PhoneSheet({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }): ReactNode {
  const { me, fail, toast } = useApp();
  const [devPhone, setDevPhone] = useState("");
  const native = canRequestContact();
  const save = async (body: { contact?: string; phone?: string }) => {
    try {
      const result = await api.savePhone(body);
      toast(result.linked.length ? "Номер сохранён - нашли Вашу запись от коллеги" : "Номер сохранён", "ok");
      onSaved();
    } catch (error) {
      fail(error);
    }
  };
  return (
    <Sheet open onClose={onClose}>
      <h2>Телефон</h2>
      <p className="muted" style={{ marginTop: 0 }}>Telegram передаст номер Вашего аккаунта.</p>
      <div className="stack">
        {native && (
          <ActionButton
            block
            onClick={async () => {
              const contact = await requestContact();
              if (contact) await save({ contact });
            }}
          >
            <Icon.phone />
            Поделиться номером
          </ActionButton>
        )}
        {!native && me.config.dev && (
          <>
            <Field label="Телефон (режим разработчика)">
              <input className="input" type="tel" value={devPhone} onChange={(event) => setDevPhone(event.target.value)} />
            </Field>
            <ActionButton block disabled={!devPhone.trim()} onClick={() => save({ phone: devPhone.trim() })}>
              Сохранить
            </ActionButton>
          </>
        )}
        {!native && !me.config.dev && <div className="notice warn">Откройте приложение из Telegram, чтобы сменить номер.</div>}
      </div>
    </Sheet>
  );
}

function SelectionSheet({ kind, profile, onClose, onSaved }: {
  kind: "specialties" | "positions";
  profile: Profile;
  onClose: () => void;
  onSaved: () => void;
}): ReactNode {
  const dictionaries = useLoad(api.dictionaries);
  const initial = (kind === "specialties" ? profile.specialties : profile.positions).map((item: Dictionary) => item.id);
  const [selected, setSelected] = useState<number[]>(initial);
  return (
    <Sheet open onClose={onClose}>
      <h2>{kind === "specialties" ? "Направления" : "Должность"}</h2>
      {dictionaries.data ? (
        <div className="stack">
          <SelectionChips
            options={kind === "specialties" ? dictionaries.data.specialties : dictionaries.data.positions}
            selected={selected}
            onChange={setSelected}
          />
          <ActionButton
            block
            disabled={selected.length === 0}
            onClick={async () => {
              if (kind === "specialties") await api.saveSpecialties(selected);
              else await api.savePositions(selected);
              onSaved();
            }}
          >
            Сохранить
          </ActionButton>
        </div>
      ) : dictionaries.error ? (
        <ErrorState error={dictionaries.error} onRetry={dictionaries.reload} />
      ) : (
        <Loading rows={1} />
      )}
    </Sheet>
  );
}

function MarketingSheet({ on, onClose, onSaved }: {
  on: boolean;
  onClose: () => void;
  onSaved: (on: boolean) => void;
}): ReactNode {
  const form = useLoad(api.marketing);
  return (
    <Sheet open onClose={onClose}>
      <h2>Анонсы новых курсов</h2>
      {form.data ? (
        <div className="stack">
          {on ? (
            <p className="muted" style={{ margin: 0 }}>
              Рассылки включены. Выключить можно в любой момент - уведомления о Ваших записях и оплате всё равно будут
              приходить.
            </p>
          ) : (
            <div className="card card-body">
              <Rich html={form.data.html} />
            </div>
          )}
          {on ? (
            <ActionButton block variant="danger" onClick={async () => onSaved((await api.setMarketing(false)).marketing)}>
              Выключить рассылки
            </ActionButton>
          ) : (
            <ActionButton
              block
              onClick={async () => onSaved((await api.setMarketing(true, form.data!.document_id)).marketing)}
            >
              <Icon.bell />
              Да, присылать
            </ActionButton>
          )}
        </div>
      ) : form.error ? (
        <ErrorState error={form.error} onRetry={form.reload} />
      ) : (
        <Loading rows={1} />
      )}
    </Sheet>
  );
}
