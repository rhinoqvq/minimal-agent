// 仅提供静态页面和公开的 API 地址，不读取 .env 或数据库。
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";

const port = Number(process.env.FRONTEND_PORT || 8001);
const apiBase = process.env.API_BASE_URL || "http://127.0.0.1:8000";
const mimeTypes = {
  js: "text/javascript; charset=utf-8",
  css: "text/css; charset=utf-8",
  svg: "image/svg+xml",
  html: "text/html; charset=utf-8",
};

createServer(async (request, response) => {
  if (!["GET", "HEAD"].includes(request.method)) {
    response.writeHead(405, { Allow: "GET, HEAD" });
    response.end();
    return;
  }
  const path = new URL(request.url, "http://localhost").pathname;
  if (path === "/config.js") {
    response.writeHead(200, {
      "Content-Type": "text/javascript; charset=utf-8",
      "Cache-Control": "no-store",
    });
    response.end(
      request.method === "HEAD"
        ? undefined
        : `window.MINIMAL_AGENT_API_BASE = ${JSON.stringify(apiBase)};\n`,
    );
    return;
  }
  const file =
    path === "/"
      ? "index.html"
      : path === "/favicon.svg"
        ? "favicon.svg"
        : /^\/assets\/[a-zA-Z0-9_.-]+\.(js|css|svg)$/.test(path)
          ? path.slice(1)
          : null;
  if (!file) {
    response.writeHead(404);
    response.end("Not found");
    return;
  }
  try {
    const body = await readFile(
      new URL(`./frontend/dist/${file}`, import.meta.url),
    );
    response.writeHead(200, {
      "Content-Type": mimeTypes[file.split(".").at(-1)],
      "Cache-Control": "no-cache",
      "X-Content-Type-Options": "nosniff",
    });
    response.end(request.method === "HEAD" ? undefined : body);
  } catch {
    response.writeHead(500);
    response.end("Unable to read frontend file");
  }
}).listen(port, "127.0.0.1", () => {
  console.log(`Frontend: http://127.0.0.1:${port} (API: ${apiBase})`);
});
