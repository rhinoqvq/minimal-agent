function sessionPath(id) {
  return `/sessions/${encodeURIComponent(id)}`;
}

async function api(path, method = "GET", body) {
  let response;
  try {
    response = await fetch(new URL(path, window.MINIMAL_AGENT_API_BASE), {
      method,
      cache: "no-store",
      ...(body === undefined
        ? {}
        : {
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }),
    });
  } catch {
    throw new Error(
      "连接中断，后台可能仍在执行。请重新读取历史，确认结果后再发送。",
    );
  }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    const messages = {
      max_steps_exceeded:
        "本轮已达到 5 步执行上限，历史已保存。你可以发送新消息继续。",
      session_busy: "这个会话正在执行，请等待完成后再发送。",
      pending_tool_calls:
        "上次执行有未完成的工具调用，需要在后端补齐结果后继续。",
      model_not_configured:
        "模型尚未配置。请在服务端 .env 中填写 QWEN_API_KEY 后重试。",
      session_not_found: "这个会话已被删除，请选择其他会话或新建会话。",
      agent_run_failed:
        "本轮执行失败，请查看已保存的历史和后端日志，再决定是否继续。",
    };
    const error = new Error(
      messages[detail?.code] ||
        (typeof detail?.message === "string"
          ? detail.message
          : `请求失败（${response.status}），请稍后重试。`),
    );
    error.code = detail?.code;
    throw error;
  }
  if (!data) throw new Error("服务器返回了无效数据，请重新读取。");
  return data;
}

function pretty(value) {
  if (typeof value !== "string") return JSON.stringify(value, null, 2) ?? "";
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

function toolFailed(result) {
  try {
    const error = JSON.parse(result)?.error;
    return Boolean(
      error &&
        typeof error.type === "string" &&
        typeof error.message === "string",
    );
  } catch {
    return false;
  }
}

function displayMessages(entries) {
  const messages = [];
  const pending = new Map();
  for (const entry of entries) {
    const message = entry.message;
    if (message.role === "tool") {
      const tool = pending.get(message.tool_call_id);
      if (tool) {
        tool.result = message.content;
        pending.delete(message.tool_call_id);
      } else
        messages.push({
          ...entry,
          message: { role: "assistant", content: "" },
          tools: [
            {
              call: { function: { name: message.tool_call_id } },
              result: message.content,
            },
          ],
        });
    } else if (["user", "assistant"].includes(message.role)) {
      const tools = (message.tool_calls || []).map((call) => ({ call }));
      for (const tool of tools) pending.set(tool.call.id, tool);
      messages.push({ ...entry, tools });
    }
  }
  return messages;
}

export { api, sessionPath, pretty, toolFailed, displayMessages };
