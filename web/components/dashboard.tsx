"use client";

import { ChangeEvent, useCallback, useEffect, useMemo, useState } from "react";

type Status = "review" | "ready" | "exported" | "ignored";
type Filter = "review" | "ready" | "all";

type Transaction = {
  id: number;
  bank: string;
  source_name?: string | null;
  operation_date?: string | null;
  amount: number;
  direction?: string | null;
  currency: string;
  counterparty_name?: string | null;
  counterparty_bin?: string | null;
  payment_purpose?: string | null;
  status: Status;
  ai_summary?: string | null;
  ai_category?: string | null;
};

type Counts = Record<Status, number>;
type Health = { ok: boolean; ai_enabled: boolean; ai_model: string; database: string; max_pdf_mb: number };
type Notice = { text: string; error?: boolean } | null;

const emptyCounts: Counts = { review: 0, ready: 0, exported: 0, ignored: 0 };

async function request<T>(url: string, apiKey = "", init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (apiKey) headers.set("X-API-Key", apiKey);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(url, { ...init, headers, cache: "no-store" });
  const body = await response.json().catch(() => ({ detail: "Сервер вернул некорректный ответ" }));
  if (!response.ok) throw new Error(body.detail ?? "Ошибка запроса");
  return body as T;
}

function Icon({ name }: { name: "upload" | "check" | "spark" | "skip" | "save" | "trash" }) {
  const paths = {
    upload: <><path d="M12 3v12"/><path d="m7 8 5-5 5 5"/><path d="M5 21h14"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    spark: <><path d="m12 3 1.4 4.1L17.5 9l-4.1 1.4L12 14.5l-1.4-4.1L6.5 9l4.1-1.9L12 3Z"/><path d="m19 15 .7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7L19 15Z"/></>,
    skip: <><path d="m6 6 12 12"/><path d="M18 6 6 18"/></>,
    save: <><path d="M5 3h12l2 2v16H5Z"/><path d="M8 3v6h8V3"/><path d="M8 21v-7h8v7"/></>,
    trash: <><path d="M4 7h16"/><path d="m9 7 1-3h4l1 3"/><path d="m6 7 1 14h10l1-14"/><path d="M10 11v6"/><path d="M14 11v6"/></>,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}

export function Dashboard() {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [counts, setCounts] = useState<Counts>(emptyCounts);
  const [health, setHealth] = useState<Health>({ ok: false, ai_enabled: false, ai_model: "gpt-4o", database: "—", max_pdf_mb: 15 });
  const [apiKey, setApiKey] = useState("");
  const [filter, setFilter] = useState<Filter>("review");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [notice, setNotice] = useState<Notice>(null);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [bulkAction, setBulkAction] = useState<"approve" | "delete" | null>(null);

  const loadData = useCallback(async () => {
    try {
      const [txData, healthData] = await Promise.all([
        request<{ items: Transaction[]; counts: Counts }>("/api/v1/transactions?limit=5000&compact=true"),
        request<Health>("/backend-health"),
      ]);
      setTransactions(txData.items);
      setCounts(txData.counts);
      setHealth(healthData);
    } catch (error) {
      setNotice({ text: error instanceof Error ? error.message : "Не удалось загрузить данные", error: true });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Initial hydration from browser storage and API is intentionally mount-driven.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setApiKey(sessionStorage.getItem("bankhub-key") ?? "");
    void loadData();
  }, [loadData]);

  const visible = useMemo(
    () => transactions.filter((item) => filter === "all" || item.status === filter),
    [filter, transactions],
  );

  function rememberKey() {
    sessionStorage.setItem("bankhub-key", apiKey);
    setNotice({ text: "Ключ сохранён до закрытия вкладки" });
  }

  async function uploadStatement() {
    if (!file) return;
    if (file.size > health.max_pdf_mb * 1024 * 1024) {
      setNotice({ text: `PDF должен быть не больше ${health.max_pdf_mb} МБ`, error: true });
      return;
    }
    setUploading(true);
    setNotice({ text: "Распознаём выписку…" });
    const form = new FormData();
    form.append("file", file);
    try {
      const result = await request<{ detected_operations: number; created: number; duplicates: number; warning?: string }>(
        "/api/v1/pdf", apiKey, { method: "POST", body: form },
      );
      setNotice({ text: `Найдено: ${result.detected_operations}. Добавлено: ${result.created}. Дубли: ${result.duplicates}.${result.warning ? ` ${result.warning}` : ""}` });
      setFile(null);
      await loadData();
    } catch (error) {
      setNotice({ text: error instanceof Error ? error.message : "Ошибка загрузки", error: true });
    } finally {
      setUploading(false);
    }
  }

  async function approveSelected() {
    const ids = [...selected].filter((id) => transactions.find((item) => item.id === id)?.status === "review");
    if (!ids.length) return setNotice({ text: "Выберите операции на проверке", error: true });
    setBulkAction("approve");
    try {
      const result = await request<{ approved: number }>("/api/v1/transactions/approve", apiKey, {
        method: "POST", body: JSON.stringify({ ids }),
      });
      setNotice({ text: `Подтверждено: ${result.approved}` });
      setSelected(new Set());
      if (result.approved === ids.length) {
        const idSet = new Set(ids);
        setTransactions((current) => current.map((item) => idSet.has(item.id) ? { ...item, status: "ready" } : item));
        setCounts((current) => ({
          ...current,
          review: Math.max(0, current.review - result.approved),
          ready: current.ready + result.approved,
        }));
      } else {
        await loadData();
      }
    } catch (error) {
      setNotice({ text: error instanceof Error ? error.message : "Ошибка подтверждения", error: true });
    } finally {
      setBulkAction(null);
    }
  }

  async function deleteSelected() {
    const ids = [...selected].filter((id) => transactions.some((item) => item.id === id));
    if (!ids.length) return setNotice({ text: "Выберите операции для удаления", error: true });
    const confirmed = window.confirm(`Удалить выбранные операции (${ids.length}) без возможности восстановления?`);
    if (!confirmed) return;
    const idSet = new Set(ids);
    const deletingItems = transactions.filter((item) => idSet.has(item.id));
    setBulkAction("delete");
    setNotice({ text: `Удаляем операций: ${ids.length}…` });
    try {
      const result = await request<{ deleted: number }>("/api/v1/transactions/delete", apiKey, {
        method: "POST", body: JSON.stringify({ ids }),
      });
      setNotice({ text: `Удалено операций: ${result.deleted}` });
      setSelected(new Set());
      if (result.deleted === deletingItems.length) {
        const removedByStatus = deletingItems.reduce<Counts>((totals, item) => {
          totals[item.status] += 1;
          return totals;
        }, { ...emptyCounts });
        setTransactions((current) => current.filter((item) => !idSet.has(item.id)));
        setCounts((current) => ({
          review: Math.max(0, current.review - removedByStatus.review),
          ready: Math.max(0, current.ready - removedByStatus.ready),
          exported: Math.max(0, current.exported - removedByStatus.exported),
          ignored: Math.max(0, current.ignored - removedByStatus.ignored),
        }));
      } else {
        await loadData();
      }
    } catch (error) {
      setNotice({ text: error instanceof Error ? error.message : "Ошибка удаления", error: true });
    } finally {
      setBulkAction(null);
    }
  }

  function toggleSelected(id: number, checked: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(id); else next.delete(id);
      return next;
    });
  }

  function toggleVisible(checked: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      for (const item of visible) {
        if (checked) next.add(item.id); else next.delete(item.id);
      }
      return next;
    });
  }

  const selectedReviewCount = [...selected].filter((id) => transactions.find((item) => item.id === id)?.status === "review").length;
  const allVisibleSelected = visible.length > 0 && visible.every((item) => selected.has(item.id));

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="logo">1С</div>
          <div><h1>Выписки → 1С</h1><p>Проверка банковских операций</p></div>
        </div>
        <div className="keybox">
          <input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="X-API-Key" aria-label="API ключ" />
          <button className="button ghost" onClick={rememberKey}>Запомнить</button>
        </div>
      </header>

      <section className="hero">
        <div className="upload-card">
          <span className="eyebrow">Новая выписка</span>
          <h2>От PDF до готовых документов в 1С</h2>
          <p>Загрузите выписку, проверьте найденные операции и подтвердите их. Повторные платежи будут пропущены автоматически.</p>
          <div className="upload-controls">
            <label className="file-button">
              <Icon name="upload" />
              <span>{file ? "Выбрать другой файл" : "Выбрать PDF"}</span>
              <input type="file" accept="application/pdf,.pdf" onChange={(event: ChangeEvent<HTMLInputElement>) => setFile(event.target.files?.[0] ?? null)} />
            </label>
            <span className="file-name">{file?.name ?? "Файл не выбран"}</span>
            <button className="button accent" disabled={!file || uploading} onClick={() => void uploadStatement()}>{uploading ? "Распознаём…" : "Загрузить"}</button>
          </div>
          {notice && <div className={`notice ${notice.error ? "error" : ""}`}>{notice.text}</div>}
          <div className="privacy">PDF до {health.max_pdf_mb} МБ обрабатывается в памяти и не сохраняется на сервере.</div>
        </div>

        <aside className="flow-card">
          <div className="flow-title"><span>Как это работает</span><span className={`service-dot ${health.ok ? "online" : ""}`}>{health.ok ? "API доступен" : "Нет связи"}</span></div>
          {[["01", "Загрузите PDF", "Выписка из интернет-банка"], ["02", "Проверьте операции", "Текст и реквизиты можно исправить"], ["03", "Подтвердите для 1С", "Только проверенные документы"]].map(([number, title, text]) => (
            <div className="flow-step" key={number}><span>{number}</span><div><b>{title}</b><p>{text}</p></div></div>
          ))}
          <div className="ai-state"><Icon name="spark" /><span>GPT‑4o: {health.ai_enabled ? `подключён · ${health.ai_model}` : "добавьте OPENAI_API_KEY"}</span></div>
        </aside>
      </section>

      <section className="stats" aria-label="Сводка">
        <Stat value={counts.review} label="На проверке" tone="amber" />
        <Stat value={counts.ready} label="Готово для 1С" tone="blue" />
        <Stat value={counts.exported} label="Выгружено" tone="green" />
        <Stat value={counts.ignored} label="Пропущено" tone="gray" />
      </section>

      <section className="operations">
        <div className="operations-head">
          <div><span className="eyebrow dark">Операции</span><h2>Проверка выписки</h2></div>
          <div className="tabs" role="tablist">
            {([['review', 'На проверке'], ['ready', 'Для 1С'], ['all', 'Все']] as [Filter, string][]).map(([value, label]) => (
              <button key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{label}</button>
            ))}
          </div>
        </div>
        <div className="bulkbar">
          <label><input type="checkbox" disabled={bulkAction !== null} checked={allVisibleSelected} onChange={(event) => toggleVisible(event.target.checked)} /> Выбрать видимые {selected.size > 0 && `· выбрано ${selected.size}`}</label>
          <div className="bulk-actions">
            <button className="button danger" disabled={selected.size === 0 || bulkAction !== null} onClick={() => void deleteSelected()}><Icon name="trash" />{bulkAction === "delete" ? "Удаляем…" : "Удалить выбранные"}</button>
            <button className="button primary" disabled={selectedReviewCount === 0 || bulkAction !== null} onClick={() => void approveSelected()}><Icon name="check" />{bulkAction === "approve" ? "Подтверждаем…" : "Подтвердить выбранные"}</button>
          </div>
        </div>

        <div className="table-wrap">
          {loading ? <div className="empty">Загружаем операции…</div> : visible.length === 0 ? <div className="empty">В этом разделе пока нет операций.</div> : (
            <table>
              <thead><tr><th></th><th>Статус</th><th>Дата</th><th>Банк</th><th>Операция</th><th>Сумма</th><th>Контрагент / БИН</th><th>Назначение</th><th>Действия</th></tr></thead>
              <tbody>{visible.map((item) => <TransactionRow key={item.id} item={item} apiKey={apiKey} aiEnabled={health.ai_enabled} selected={selected.has(item.id)} onSelect={toggleSelected} onRefresh={loadData} onNotice={setNotice} />)}</tbody>
            </table>
          )}
        </div>
      </section>
    </main>
  );
}

function Stat({ value, label, tone }: { value: number; label: string; tone: string }) {
  return <div className="stat"><span className={`stat-mark ${tone}`} /><div><strong>{value}</strong><p>{label}</p></div></div>;
}

function TransactionRow({ item, apiKey, aiEnabled, selected, onSelect, onRefresh, onNotice }: {
  item: Transaction; apiKey: string; aiEnabled: boolean; selected: boolean;
  onSelect: (id: number, checked: boolean) => void; onRefresh: () => Promise<void>; onNotice: (notice: Notice) => void;
}) {
  const editable = item.status === "review";
  const [draft, setDraft] = useState({
    operation_date: item.operation_date ?? "", direction: item.direction ?? "", amount: String(item.amount),
    counterparty_name: item.counterparty_name ?? "", counterparty_bin: item.counterparty_bin ?? "", payment_purpose: item.payment_purpose ?? "",
  });
  const [busy, setBusy] = useState(false);

  async function save(quiet = false) {
    try {
      await request(`/api/v1/transactions/${item.id}`, apiKey, { method: "PATCH", body: JSON.stringify({ ...draft, amount: Number(draft.amount) }) });
      if (!quiet) onNotice({ text: `Операция №${item.id} сохранена` });
      return true;
    } catch (error) {
      onNotice({ text: error instanceof Error ? error.message : "Ошибка сохранения", error: true });
      return false;
    }
  }

  async function action(kind: "approve" | "ignore") {
    if (kind === "approve" && !(await save(true))) return;
    setBusy(true);
    try {
      await request(`/api/v1/transactions/${item.id}/${kind}`, apiKey, { method: "POST" });
      await onRefresh();
    } catch (error) {
      onNotice({ text: error instanceof Error ? error.message : "Ошибка операции", error: true });
    } finally { setBusy(false); }
  }

  async function askAI() {
    setBusy(true);
    try {
      const result = await request<{ summary: string; category: string; normalized_purpose: string }>(`/api/v1/transactions/${item.id}/ai-text`, apiKey, { method: "POST" });
      const apply = window.confirm(`${result.summary}\n\nКатегория: ${result.category}\n\nПредлагаемый текст:\n${result.normalized_purpose}\n\nЗаменить назначение платежа?`);
      if (apply) setDraft((current) => ({ ...current, payment_purpose: result.normalized_purpose }));
      onNotice({ text: `GPT‑4o: ${result.summary} · ${result.category}` });
      await onRefresh();
    } catch (error) {
      onNotice({ text: error instanceof Error ? error.message : "GPT‑4o недоступен", error: true });
    } finally { setBusy(false); }
  }

  const update = (field: keyof typeof draft, value: string) => setDraft((current) => ({ ...current, [field]: value }));

  return (
    <tr className={busy ? "busy" : ""}>
      <td><input type="checkbox" checked={selected} onChange={(event) => onSelect(item.id, event.target.checked)} /></td>
      <td><StatusPill status={item.status} /></td>
      <td><input className="cell-input date" disabled={!editable} value={draft.operation_date} onChange={(event) => update("operation_date", event.target.value)} /></td>
      <td><b>{item.bank}</b><small className="source">{item.source_name}</small></td>
      <td><select className="cell-input" disabled={!editable} value={draft.direction} onChange={(event) => update("direction", event.target.value)}><option value="">—</option><option value="incoming">Поступление</option><option value="outgoing">Списание</option></select></td>
      <td><div className="money"><input className="cell-input" type="number" min="0.01" step="0.01" disabled={!editable} value={draft.amount} onChange={(event) => update("amount", event.target.value)} /><span>{item.currency}</span></div></td>
      <td><div className="party"><input className="cell-input" disabled={!editable} placeholder="Название" value={draft.counterparty_name} onChange={(event) => update("counterparty_name", event.target.value)} /><input className="cell-input" disabled={!editable} placeholder="БИН/ИИН" value={draft.counterparty_bin} onChange={(event) => update("counterparty_bin", event.target.value)} /></div></td>
      <td className="purpose"><textarea className="cell-input" disabled={!editable} value={draft.payment_purpose} onChange={(event) => update("payment_purpose", event.target.value)} />{item.ai_summary && <p className="ai-note"><Icon name="spark" />{item.ai_summary} · {item.ai_category}</p>}</td>
      <td>{editable ? <div className="row-actions"><button title="Сохранить" onClick={() => void save()}><Icon name="save" /></button><button className="approve" title="Подтвердить" onClick={() => void action("approve")}><Icon name="check" /></button><button className="ai" disabled={!aiEnabled} title="Обработать текст с GPT‑4o" onClick={() => void askAI()}><Icon name="spark" /></button><button className="skip" title="Пропустить" onClick={() => void action("ignore")}><Icon name="skip" /></button></div> : <span className="done-text">Редактирование завершено</span>}</td>
    </tr>
  );
}

function StatusPill({ status }: { status: Status }) {
  const labels: Record<Status, string> = { review: "На проверке", ready: "Для 1С", exported: "Выгружено", ignored: "Пропущено" };
  return <span className={`pill ${status}`}>{labels[status]}</span>;
}
