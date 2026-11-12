import { api, sessionPath } from "./api.js";

// 仅保存界面状态；上下文、任务状态和工具执行仍由后端管理。
const state = {
  sessions: [],
  activeId: null,
  selection: 0,
  creating: false,
  listLoading: true,
  listError: "",
};
const views = new Map();
const listeners = new Set();
let snapshot;
let listVersion = 0;
let pollTimer;

function view(id) {
  if (!views.has(id))
    views.set(id, {
      entries: [],
      draft: "",
      pending: null,
      remoteRunning: false,
      loaded: false,
      loading: false,
      version: 0,
      statusVersion: 0,
      error: "",
      needsReload: false,
      status: null,
      statusError: false,
      animateSequence: null,
    });
  return views.get(id);
}

function notify() {
  snapshot = {
    ...state,
    views: new Map(
      [...views].map(([id, value]) => [
        id,
        { ...value, entries: [...value.entries] },
      ]),
    ),
  };
  for (const listener of listeners) listener();
}

function remember(id) {
  try {
    if (id) localStorage.setItem("minimal-agent-session", id);
    else localStorage.removeItem("minimal-agent-session");
  } catch {
    /* 浏览器禁用存储时不影响本次聊天。 */
  }
}

function remembered() {
  try {
    return localStorage.getItem("minimal-agent-session");
  } catch {
    return null;
  }
}

function applyStatus(current, data) {
  current.status = {
    phase: data.phase,
    context_usage: data.context_usage,
    context_window: data.context_window,
    compression_ratio: data.compression_ratio,
    compression: data.compression,
  };
  current.remoteRunning = Boolean(data.running);
  current.statusError = false;
}

function schedulePoll() {
  clearTimeout(pollTimer);
  const id = state.activeId;
  if (!id) return;
  const current = view(id);
  if (current.pending || (current.remoteRunning && !current.needsReload)) {
    pollTimer = setTimeout(() => pollStatus(id), 700);
  }
}

async function pollStatus(id) {
  const current = view(id);
  const version = ++current.statusVersion;
  try {
    const data = await api(`${sessionPath(id)}/status`);
    if (version !== current.statusVersion) return;
    if (data.session_id !== id) throw new Error("会话状态不匹配");
    applyStatus(current, data);
    // 刷新页面后恢复的任务结束时读取完整历史；本页 POST 完成前不拉取部分回答。
    if (!current.pending && !data.running) await loadHistory(id);
    else notify();
  } catch {
    if (version === current.statusVersion) {
      current.statusError = true;
      notify();
    }
  } finally {
    schedulePoll();
  }
}

async function loadHistory(id, quiet = false) {
  const current = view(id);
  const version = ++current.version;
  ++current.statusVersion;
  current.loading = !current.loaded;
  if (!quiet) notify();
  try {
    const data = await api(`${sessionPath(id)}/messages`);
    if (version !== current.version) return false;
    if (data.session_id !== id || !Array.isArray(data.messages))
      throw new Error("历史记录格式不正确，请重新读取。");
    current.entries = data.messages;
    applyStatus(current, data);
    current.loaded = true;
    current.needsReload = false;
    return true;
  } catch (error) {
    if (version !== current.version) return false;
    current.error = error.message;
    current.needsReload = true;
    return false;
  } finally {
    if (version === current.version) {
      current.loading = false;
      if (!quiet) notify();
      schedulePoll();
    }
  }
}

async function select(id) {
  if (state.activeId) view(state.activeId).animateSequence = null;
  state.activeId = id;
  state.selection++;
  if (id) view(id).animateSequence = null;
  remember(id);
  clearTimeout(pollTimer);
  notify();
  if (id) await loadHistory(id);
}

async function refresh() {
  const version = ++listVersion;
  try {
    const data = await api("/sessions");
    if (version !== listVersion) return;
    if (!Array.isArray(data.sessions)) throw new Error("会话列表格式不正确");
    state.sessions = data.sessions;
    state.listError = "";
    if (!state.sessions.some((item) => item.session_id === state.activeId)) {
      const next =
        state.sessions.find((item) => item.session_id === remembered()) ||
        state.sessions[0];
      await select(next?.session_id || null);
    }
  } catch (error) {
    if (version === listVersion) state.listError = error.message;
  } finally {
    if (version === listVersion) {
      state.listLoading = false;
      notify();
    }
  }
}

async function create() {
  if (state.creating) return;
  state.creating = true;
  notify();
  try {
    const session = await api("/sessions", "POST", {});
    ++listVersion;
    state.sessions = [session, ...state.sessions];
    state.listError = "";
    await select(session.session_id);
    return session.session_id;
  } catch (error) {
    state.listError = error.message;
  } finally {
    state.creating = false;
    notify();
  }
}

function pendingIsArchived(current) {
  return (
    current.pending &&
    current.entries.some(
      (entry) =>
        Number(entry.sequence) > current.pending.after &&
        entry.message.role === "user" &&
        entry.message.content === current.pending.content,
    )
  );
}

async function send() {
  const id = state.activeId;
  if (!id) return;
  const current = view(id);
  if (
    !current.draft.trim() ||
    !current.loaded ||
    current.loading ||
    current.needsReload ||
    current.pending ||
    current.remoteRunning ||
    state.creating
  )
    return;
  const selection = state.selection;
  const content = current.draft;
  current.pending = {
    content,
    started: new Date().toISOString(),
    after: Math.max(
      0,
      ...current.entries.map((entry) => Number(entry.sequence) || 0),
    ),
  };
  current.version++;
  current.statusVersion++;
  current.animateSequence = null;
  current.draft = "";
  current.error = "";
  current.status = { ...current.status, phase: "running" };
  notify();
  schedulePoll();
  let result = null;
  let failure = null;
  try {
    result = await api(`${sessionPath(id)}/messages`, "POST", { content });
    if (result.session_id !== id || typeof result.content !== "string")
      throw new Error("回答格式不正确，请重新读取历史。");
  } catch (error) {
    failure = error;
  }

  clearTimeout(pollTimer);
  const synced = await loadHistory(id, true);
  if (failure) {
    current.error = `${failure.message}${!synced ? " 历史尚未同步，请点击重新读取。" : ""}`;
    if (!synced || !pendingIsArchived(current)) current.draft = content;
    if (failure.code === "session_busy" && !synced)
      current.remoteRunning = true;
  } else if (!synced && result) {
    if (!pendingIsArchived(current))
      current.entries = [
        ...current.entries,
        { sequence: "local-user", message: { role: "user", content } },
      ];
    current.entries = [
      ...current.entries,
      {
        sequence: "local-answer",
        message: { role: "assistant", content: result.content },
      },
    ];
    current.status = {
      ...current.status,
      phase: "idle",
      context_usage: result.context_usage,
    };
    current.error = "回答已收到，但历史同步失败。请重新读取后继续。";
  }
  current.pending = null;
  ++current.statusVersion;
  const last = current.entries.at(-1);
  if (
    !failure &&
    id === state.activeId &&
    selection === state.selection &&
    last?.message.role === "assistant" &&
    last.message.content === result?.content
  ) {
    current.animateSequence = last.sequence;
  }
  notify();
  schedulePoll();
  await refresh();
}

async function mutate(action, id, title) {
  if (action === "delete") {
    await api(sessionPath(id), "DELETE");
    views.delete(id);
    state.sessions = state.sessions.filter((item) => item.session_id !== id);
    if (state.activeId === id)
      await select(state.sessions[0]?.session_id || null);
  } else {
    const session = await api(sessionPath(id), "PATCH", { title });
    state.sessions = state.sessions.map((item) =>
      item.session_id === id ? session : item,
    );
  }
  ++listVersion;
  notify();
  await refresh();
}

notify();
export const chatStore = {
  subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot: () => snapshot,
  start() {
    refresh();
    return () => clearTimeout(pollTimer);
  },
  select,
  create,
  refresh,
  send,
  mutate,
  setDraft(content) {
    if (state.activeId) {
      view(state.activeId).draft = content;
      notify();
    }
  },
  async reload() {
    if (state.activeId) {
      const current = view(state.activeId);
      current.error = "";
      current.animateSequence = null;
      await loadHistory(state.activeId);
    }
  },
};
export { pendingIsArchived };
