import React, {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { chatStore, pendingIsArchived } from "./chat-store.js";
import { displayMessages, pretty, toolFailed } from "./api.js";

function Icon({ name }) {
  const paths = {
    rename: "m14 5 5 5M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15z",
    delete: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7",
    send: "M12 19V5m-6 6 6-6 6 6",
  };
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d={paths[name]} />
    </svg>
  );
}

function Answer({ content, animate, scrollRef }) {
  const [visible, setVisible] = useState(animate ? "" : content);
  const [typing, setTyping] = useState(false);
  useEffect(() => {
    if (
      !animate ||
      !content ||
      matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      setVisible(content);
      setTyping(false);
      return;
    }
    const letters = Array.from(content);
    const duration = Math.min(4500, Math.max(700, letters.length * 18));
    const started = performance.now();
    let frame;
    setTyping(true);
    const update = (time) => {
      const count = Math.min(
        letters.length,
        Math.ceil(((time - started) / duration) * letters.length),
      );
      setVisible(letters.slice(0, count).join(""));
      const area = scrollRef.current;
      if (area && area.scrollHeight - area.scrollTop - area.clientHeight < 100)
        area.scrollTop = area.scrollHeight;
      if (count < letters.length) frame = requestAnimationFrame(update);
      else setTyping(false);
    };
    frame = requestAnimationFrame(update);
    return () => cancelAnimationFrame(frame);
  }, [content, animate, scrollRef]);
  return (
    <div className={`message-content${typing ? " typing" : ""}`}>
      {visible ||
        (typing || (animate && visible !== content) ? "" : "（空回复）")}
    </div>
  );
}

function ToolCard({ tool, busy }) {
  const done = tool.result !== undefined;
  const failed = done && toolFailed(tool.result);
  return (
    <details className="tool-card">
      <summary>
        <span className="tool-name">
          {tool.call.function?.name || "工具结果"}
        </span>
        <span
          className={`tool-status ${failed ? "failure" : !done ? "pending" : ""}`}
        >
          {done ? (failed ? "失败" : "成功") : busy ? "等待结果" : "未完成"}
        </span>
      </summary>
      <div className="tool-detail">
        <h4>参数</h4>
        <pre>{pretty(tool.call.function?.arguments ?? "")}</pre>
        <h4>结果</h4>
        <pre>{done ? pretty(tool.result) : "尚未收到工具结果"}</pre>
      </div>
    </details>
  );
}

function ContextMeter({ current }) {
  const status = current?.status;
  const usage = status?.context_usage;
  const stale = Boolean(
    usage && (usage.needs_refresh || current.pending || current.remoteRunning),
  );
  const percent = usage ? usage.ratio * 100 : null;
  const compressing = status?.phase === "compressing";
  const compression = status?.compression?.status;
  const number = (value) => new Intl.NumberFormat("zh-CN").format(value);
  const remaining = usage
    ? `${stale ? "上次统计：" : ""}剩余 ${number(usage.remaining_tokens)} / ${number(usage.context_window)} tokens`
    : status?.context_window
      ? `窗口 ${number(status.context_window)} tokens · 尚未统计`
      : "上下文窗口 · 等待用量统计";
  let message = "";
  if (current?.statusError) message = "状态读取失败，正在重试";
  else if (compressing) message = "正在压缩上下文…";
  else if (current?.pending || current?.remoteRunning)
    message = "正在处理，完成后更新用量";
  else if (compression === "rejected") message = "上次压缩失败，已保留原上下文";
  else if (compression === "interrupted")
    message = "上次压缩中断，已保留原上下文";
  else if (usage?.needs_refresh && compression === "applied")
    message = "已压缩 · 新用量待下一轮更新";
  else if (usage?.needs_refresh) message = "上下文已变化，用量待下一轮更新";
  return (
    <section
      className={`context-meter${compressing ? " compressing" : ""}${stale ? " stale" : ""}`}
      id="context-meter"
      aria-label="上下文窗口"
    >
      <div className="context-labels">
        <span id="context-remaining">{remaining}</span>
        {usage && (
          <span id="context-percent">
            已用 {percent.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}
            %
          </span>
        )}
      </div>
      <div
        className="context-track"
        role="progressbar"
        aria-label="上下文窗口占用"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={
          percent === null ? undefined : Math.min(100, Math.max(0, percent))
        }
        aria-valuetext={remaining}
      >
        <div
          className={`context-fill${percent >= 80 ? " near-limit" : ""}`}
          style={{ width: `${Math.min(100, Math.max(0, percent || 0))}%` }}
        />
        <span
          className="context-threshold"
          style={{ left: `${(status?.compression_ratio || 0.8) * 100}%` }}
          title="达到 80% 后自动压缩"
        />
      </div>
      {message && (
        <div className="context-state" id="context-state" role="status">
          {compressing && <span className="spinner" aria-hidden="true" />}
          {message}
        </div>
      )}
    </section>
  );
}

function SessionDialog({ target, dismiss }) {
  const ref = useRef(null);
  const [name, setName] = useState(target.title);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const deleting = target.action === "delete";
  useEffect(() => {
    ref.current.showModal();
  }, []);
  const submit = async (event) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await chatStore.mutate(target.action, target.id, name);
      dismiss();
    } catch (error) {
      setError(error.message);
      setBusy(false);
    }
  };
  return (
    <dialog
      id="session-dialog"
      ref={ref}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) dismiss();
      }}
    >
      <form id="dialog-form" onSubmit={submit}>
        <h2 id="dialog-title">{deleting ? "删除这个会话？" : "重命名会话"}</h2>
        <p id="dialog-description">
          {deleting
            ? `“${target.title}”的聊天记录和调用日志将一并删除，无法恢复。`
            : "给这段对话一个容易找到的名字。"}
        </p>
        {!deleting && (
          <>
            <label id="title-label" htmlFor="session-name">
              会话名称
            </label>
            <input
              id="session-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={200}
              required
              autoComplete="off"
              autoFocus
            />
          </>
        )}
        {error && (
          <p className="dialog-error" id="dialog-error" role="alert">
            {error}
          </p>
        )}
        <div className="dialog-actions">
          <button
            type="button"
            id="dialog-cancel"
            className="secondary-button"
            disabled={busy}
            onClick={dismiss}
          >
            取消
          </button>
          <button
            type="submit"
            id="dialog-confirm"
            className={`primary-button${deleting ? " danger" : ""}`}
            disabled={busy}
          >
            {deleting ? "删除会话" : "保存"}
          </button>
        </div>
      </form>
    </dialog>
  );
}

const suggestions = [
  ["↗", "梳理一个想法", "帮我梳理一个想法，我会告诉你背景。"],
  ["≡", "拆解一项任务", "帮我把一个任务拆成可执行的步骤。"],
  ["✓", "确认下一步", "帮我整理当前任务，确认接下来要做什么。"],
];

export default function App() {
  const state = useSyncExternalStore(
    chatStore.subscribe,
    chatStore.getSnapshot,
  );
  const [menu, setMenu] = useState(false);
  const [dialog, setDialog] = useState(null);
  const input = useRef(null);
  const scroll = useRef(null);
  const current = state.views.get(state.activeId);
  const session = state.sessions.find(
    (item) => item.session_id === state.activeId,
  );
  const busy = Boolean(current?.pending || current?.remoteRunning);
  const compressing = current?.status?.phase === "compressing";
  const entries = current ? [...current.entries] : [];
  if (current?.pending && !pendingIsArchived(current))
    entries.push({
      sequence: "pending",
      message: { role: "user", content: current.pending.content },
    });
  const messages = displayMessages(entries);
  useEffect(() => chatStore.start(), []);
  useLayoutEffect(() => {
    input.current.style.height = "auto";
    input.current.style.height = `${Math.min(180, Math.max(50, input.current.scrollHeight))}px`;
  }, [current?.draft, state.activeId]);
  useLayoutEffect(() => {
    if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [state.activeId, entries.length]);
  useEffect(() => {
    const escape = (event) => {
      if (event.key === "Escape") setMenu(false);
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, []);
  const create = async () => {
    setMenu(false);
    await chatStore.create();
    input.current?.focus();
  };
  const select = (id) => {
    setMenu(false);
    chatStore.select(id);
  };
  const submit = (event) => {
    event.preventDefault();
    chatStore.send();
  };
  const showDialog = (action) =>
    setDialog({ action, id: state.activeId, title: session.title });
  return (
    <div className={`app${menu ? " menu-open" : ""}`}>
      <aside className="sidebar" id="sidebar" aria-label="会话管理">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            m<span>·</span>
          </span>
          <div>Minimal Agent</div>
        </div>
        <button
          className="new-session"
          id="new-session"
          disabled={state.creating}
          onClick={create}
        >
          <span aria-hidden="true">＋</span> 新建会话
        </button>
        <div className="list-heading">
          <button
            className="icon-button"
            id="refresh-list"
            aria-label="刷新会话列表"
            title="刷新会话列表"
            onClick={async () => {
              await chatStore.refresh();
              await chatStore.reload();
            }}
          >
            ↻
          </button>
        </div>
        {state.listError && (
          <p className="sidebar-error" id="sidebar-error" role="alert">
            {state.listError}
          </p>
        )}
        <nav id="session-list" aria-label="会话列表">
          {!state.sessions.length && (
            <p className="list-empty">
              {state.listLoading
                ? "正在读取会话…"
                : "还没有会话。点击上方，开始第一段对话。"}
            </p>
          )}
          {state.sessions.map((item) => (
            <button
              key={item.session_id}
              className={`session-item${item.session_id === state.activeId ? " active" : ""}`}
              data-session-id={item.session_id}
              aria-current={
                item.session_id === state.activeId ? "true" : "false"
              }
              onClick={() => select(item.session_id)}
            >
              <span className="session-icon">▤</span>
              <span className="session-text">
                <span className="session-title">{item.title}</span>
              </span>
              {(state.views.get(item.session_id)?.pending ||
                state.views.get(item.session_id)?.remoteRunning) && (
                <span className="session-busy" title="正在执行" />
              )}
            </button>
          ))}
        </nav>
      </aside>
      <button
        className="sidebar-overlay"
        id="sidebar-overlay"
        aria-label="关闭会话列表"
        hidden={!menu}
        onClick={() => setMenu(false)}
      />
      <main className="chat">
        <header className="chat-header">
          <button
            className="icon-button mobile-menu"
            id="menu-button"
            aria-label="打开会话列表"
            aria-expanded={menu}
            onClick={() => setMenu(true)}
          >
            ☰
          </button>
          <div className="chat-heading">
            <h1 id="chat-title">{session?.title || "开始一段对话"}</h1>
          </div>
          <div className="header-actions">
            <button
              className="session-action"
              id="rename-session"
              title="重命名会话"
              aria-label="重命名会话"
              disabled={!session || busy}
              onClick={() => showDialog("rename")}
            >
              <Icon name="rename" />
              <span>重命名会话</span>
            </button>
            <button
              className="session-action danger"
              id="delete-session"
              title="删除会话"
              aria-label="删除会话"
              disabled={!session || busy}
              onClick={() => showDialog("delete")}
            >
              <Icon name="delete" />
              <span>删除会话</span>
            </button>
          </div>
        </header>
        <div
          className="conversation"
          id="conversation"
          ref={scroll}
          tabIndex={0}
          aria-label="聊天记录"
        >
          {!messages.length && !current?.loading && (
            <section className="welcome" id="welcome">
              <div className="welcome-mark" aria-hidden="true">
                m<span>·</span>
              </div>
              <h2>今天，一起做点什么？</h2>
              <div className="suggestions">
                {suggestions.map(([icon, label, prompt]) => (
                  <button
                    key={label}
                    onClick={async () => {
                      if (!state.activeId) await chatStore.create();
                      chatStore.setDraft(prompt);
                      input.current?.focus();
                    }}
                  >
                    <span>{icon}</span> {label}
                  </button>
                ))}
              </div>
            </section>
          )}
          <div
            className="message-list"
            id="messages"
            key={`${state.activeId}:${state.selection}`}
          >
            {messages.map((entry) => (
              <article
                className={`message ${entry.message.role}`}
                data-role={entry.message.role}
                data-sequence={entry.sequence}
                key={entry.sequence}
              >
                <span className="avatar">
                  {entry.message.role === "user" ? "你" : "m"}
                </span>
                <div className="message-body">
                  {(entry.message.content || !entry.tools.length) && (
                    <Answer
                      content={entry.message.content || ""}
                      animate={
                        entry.message.role === "assistant" &&
                        entry.sequence === current.animateSequence
                      }
                      scrollRef={scroll}
                    />
                  )}
                  {entry.tools.map((tool, index) => (
                    <ToolCard
                      key={`${tool.call.id}:${index}`}
                      tool={tool}
                      busy={busy}
                    />
                  ))}
                </div>
              </article>
            ))}
          </div>
          <div
            className="waiting"
            id="waiting"
            role="status"
            hidden={!busy && !current?.loading}
          >
            <span className="spinner" aria-hidden="true" />
            <span id="waiting-text">
              {current?.loading
                ? "正在读取历史…"
                : compressing
                  ? "正在压缩上下文…"
                  : "Agent 正在处理，请稍候…"}
            </span>
          </div>
        </div>
        <div className="composer-area">
          <div
            className="notice"
            id="notice"
            role="alert"
            hidden={!current?.error}
          >
            <span id="notice-text">{current?.error}</span>
            <button
              id="retry-history"
              hidden={!current?.needsReload}
              onClick={chatStore.reload}
            >
              重新读取
            </button>
          </div>
          <form
            className="composer"
            id="composer"
            onSubmit={submit}
            aria-busy={busy}
          >
            <label className="sr-only" htmlFor="message-input">
              消息
            </label>
            <textarea
              id="message-input"
              ref={input}
              placeholder="发送消息，开始一起思考…"
              rows={2}
              value={current?.draft || ""}
              disabled={!current || busy || current.loading || state.creating}
              onChange={(event) => chatStore.setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing &&
                  event.keyCode !== 229
                ) {
                  event.preventDefault();
                  chatStore.send();
                }
              }}
            />
            <div className="composer-bottom">
              <button
                id="send-message"
                className="send-button"
                type="submit"
                disabled={
                  !current ||
                  !current.loaded ||
                  current.loading ||
                  busy ||
                  state.creating ||
                  current.needsReload ||
                  !current.draft.trim()
                }
              >
                <span id="send-label">
                  {compressing ? "压缩中" : busy ? "处理中" : "发送"}
                </span>
                <Icon name="send" />
              </button>
            </div>
          </form>
          <ContextMeter current={current} />
        </div>
      </main>
      {dialog && (
        <SessionDialog target={dialog} dismiss={() => setDialog(null)} />
      )}
    </div>
  );
}
