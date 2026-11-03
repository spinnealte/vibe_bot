// Сообщение клиенту от имени бота: текст с форматированием и/или файл. Счёт - всегда файл, бот сам добавит
// подпись с номером заказа, суммой и сроком. Перед отправкой - как это увидит клиент (как предпросмотр в боте).

import { useMemo, useRef, useState, type ReactNode } from "react";

import { Icon } from "../icons";
import { ActionButton, Button, Rich, Sheet, useApp } from "../ui";
import { newToken, type Delivered } from "./api";
import { RichEditor } from "./editors";

export function ComposeSheet({ title, to, invoice, captionHtml, onSend, onClose }: {
  title: string;
  to: string;
  invoice?: boolean;
  captionHtml?: string | null;
  onSend: (text: string, token: string, file: File | null) => Promise<Delivered>;
  onClose: (result?: Delivered) => void;
}): ReactNode {
  const { toast } = useApp();
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState(false);
  const token = useMemo(() => newToken(), []);
  const input = useRef<HTMLInputElement>(null);
  const empty = !file && !text.replace(/<[^>]+>/g, "").trim();

  return (
    <Sheet open onClose={() => onClose()}>
      <h2>{title}</h2>
      <p className="muted small">Кому: {to}</p>
      {!preview ? (
        <div className="stack">
          {invoice && captionHtml && (
            <div className="notice">
              <div className="grow">
                <div className="small muted">Бот сам добавит подпись:</div>
                <Rich html={captionHtml} />
              </div>
            </div>
          )}
          <RichEditor value={text} onChange={setText} placeholder={invoice ? "Подпись к счёту (необязательно)…" : "Текст сообщения…"} compact />
          <input
            ref={input}
            type="file"
            hidden
            accept=".pdf,.doc,.docx,.xls,.xlsx,.odt,.ods,.rtf,.txt,.jpg,.jpeg,.png,.heic,.webp"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              e.target.value = "";
            }}
          />
          {file ? (
            <div className="file-chip">
              <Icon.file />
              <span className="grow">{file.name}</span>
              <button className="a-icon-btn" onClick={() => setFile(null)} aria-label="Убрать файл">
                <Icon.close />
              </button>
            </div>
          ) : (
            <Button variant="secondary" size="small" onClick={() => input.current?.click()}>
              <Icon.file />
              {invoice ? "Выбрать файл счёта" : "Приложить файл"}
            </Button>
          )}
          <Button block disabled={invoice ? !file : empty} onClick={() => setPreview(true)}>
            Посмотреть, как увидит клиент
          </Button>
        </div>
      ) : (
        <div className="stack">
          <div className="chat-preview">
            {file && (
              <div className="chat-file">
                <Icon.file />
                {file.name}
              </div>
            )}
            {invoice && captionHtml && <Rich html={captionHtml} />}
            {text && <Rich html={text} />}
          </div>
          <ActionButton
            block
            onClick={async () => {
              const result = await onSend(text, token, file);
              toast(result.message, result.delivered ? "ok" : "error");
              onClose(result);
            }}
          >
            <Icon.send />
            Отправить
          </ActionButton>
          <Button variant="ghost" block onClick={() => setPreview(false)}>
            Изменить
          </Button>
        </div>
      )}
    </Sheet>
  );
}
