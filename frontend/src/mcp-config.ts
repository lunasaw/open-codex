import { configKeyPath, isRedacted, structuredConfigEdits, type ConfigEdit } from "./configuration-data";

export type McpTransport = "stdio" | "http";
export type McpAuthMode = "none" | "env" | "token" | "oauth" | "native";
export type McpPair = { key: string; value: string; originalValue?: unknown };
export type McpDraft = {
  name: string;
  transport: McpTransport;
  command: string;
  args: string[];
  cwd: string;
  env: McpPair[];
  passthrough: string[];
  url: string;
  authMode: McpAuthMode;
  originalAuthMode: McpAuthMode;
  bearerEnv: string;
  token: string;
  hasExistingToken: boolean;
  enabled?: boolean;
  headers: McpPair[];
  envHeaders: McpPair[];
};

const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
const pairs = (value: unknown): McpPair[] => Object.entries(asRecord(value)).map(([key, item]) => ({
  key, value: typeof item === "string" && !isRedacted(item) ? item : "", originalValue: item,
}));

export function mcpDraft(name = "", rawValue: unknown = {}): McpDraft {
  const raw = asRecord(rawValue);
  const envVars = Array.isArray(raw.env_vars) ? raw.env_vars.map((item) => {
    if (typeof item === "string") return item;
    const row = asRecord(item);
    return typeof row.name === "string" ? row.name : "";
  }).filter(Boolean) : [];
  const headers = asRecord(raw.http_headers);
  const authHeader = Object.entries(headers).find(([key]) => key.toLowerCase() === "authorization")?.[1];
  const authMode: McpAuthMode = typeof raw.bearer_token_env_var === "string" && raw.bearer_token_env_var
    ? "env" : raw.auth === "oauth" || raw.oauth ? "oauth" : authHeader ? "token"
      : raw.auth === "chatgpt" || raw.auth === "ema_auth" ? "native" : "none";
  return {
    name,
    transport: typeof raw.url === "string" ? "http" : "stdio",
    command: typeof raw.command === "string" ? raw.command : "",
    args: strings(raw.args),
    cwd: typeof raw.cwd === "string" ? raw.cwd : "",
    env: pairs(raw.env),
    passthrough: envVars,
    url: typeof raw.url === "string" ? raw.url : "",
    authMode,
    originalAuthMode: authMode,
    bearerEnv: typeof raw.bearer_token_env_var === "string" ? raw.bearer_token_env_var : "",
    token: "",
    hasExistingToken: !!authHeader,
    enabled: typeof raw.enabled === "boolean" ? raw.enabled : undefined,
    headers: pairs(raw.http_headers).filter((row) => row.key.toLowerCase() !== "authorization"),
    envHeaders: pairs(raw.env_http_headers).filter((row) => row.key.toLowerCase() !== "authorization"),
  };
}

function pairRecord(rows: McpPair[], label: string, http = false): Record<string, string> | undefined {
  const result: Record<string, string> = {};
  const seen = new Set<string>();
  for (const row of rows) {
    const key = row.key.trim();
    if (!key) continue;
    if (http ? !/^[A-Za-z0-9!#$%&'*+.^_|~-]+$/.test(key) : !/^[A-Za-z_][A-Za-z0-9_]*$/.test(key))
      throw new Error(http ? `HTTP 标头名称无效：${key}` : `环境变量名称无效：${key}`);
    const identity = http ? key.toLowerCase() : key;
    if (seen.has(identity)) throw new Error(`${http ? "HTTP 标头" : "环境变量"}名称重复：${key}`);
    seen.add(identity);
    if (row.value === "" && isRedacted(row.originalValue)) result[key] = String(row.originalValue);
    else result[key] = row.value;
  }
  return Object.keys(result).length ? result : undefined;
}

export function updatePair(rows: McpPair[], index: number, patch: Partial<McpPair>) {
  return rows.map((row, position) => position === index ? { ...row, ...patch } : row);
}

export function mcpDeleteEdit(name: string): ConfigEdit {
  return { keyPath: configKeyPath(["mcp_servers", name]), value: null, mergeStrategy: "replace" };
}

export function validateMcpDraft(draft: McpDraft) {
  const name = draft.name.trim();
  if (!name || /[\u0000-\u001f\u007f]/.test(name)) throw new Error("请输入有效的 MCP 服务名称。");
  if (draft.transport === "stdio") {
    if (!draft.command.trim()) throw new Error("请填写启动命令。");
    pairRecord(draft.env, "环境变量");
    const names = draft.passthrough.map((item) => item.trim()).filter(Boolean);
    if (names.some((item) => !/^[A-Za-z_][A-Za-z0-9_]*$/.test(item))) throw new Error("透传环境变量名称无效。");
    if (new Set(names).size !== names.length) throw new Error("透传环境变量名称不能重复。");
  } else {
    try {
      const url = new URL(draft.url);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error();
    } catch { throw new Error("请填写不含账号密码的有效 HTTP(S) URL。"); }
    pairRecord(draft.headers, "HTTP 标头", true);
    pairRecord(draft.envHeaders, "HTTP 标头环境变量", true);
    if (draft.authMode === "env" && !draft.bearerEnv.trim()) throw new Error("请填写 Token 环境变量名称。");
    if (draft.authMode === "token" && !draft.token.trim() && !draft.hasExistingToken) throw new Error("请填写访问 Token。");
  }
}

// Build from the original entry so advanced native settings and untouched
// redacted credentials survive edits. Only changed leaves are sent to Codex.
export function mcpConfigEdits(
  name: string,
  rawValue: unknown,
  draft: McpDraft,
  enabled?: boolean,
): ConfigEdit[] {
  const raw = structuredClone(asRecord(rawValue));
  const next = structuredClone(raw);
  if (draft.transport === "stdio") {
    next.command = draft.command.trim();
    next.args = [...draft.args];
    if (draft.cwd.trim()) next.cwd = draft.cwd.trim(); else delete next.cwd;
    const env = pairRecord(draft.env, "环境变量");
    if (env) next.env = env; else delete next.env;
    const passthrough = draft.passthrough.map((item) => item.trim()).filter(Boolean);
    const originalPassthrough = Array.isArray(raw.env_vars) ? raw.env_vars : [];
    const preservedPassthrough = passthrough.map((name) => originalPassthrough.find((item) =>
      typeof item === "string" ? item === name : asRecord(item).name === name) || name);
    if (preservedPassthrough.length) next.env_vars = preservedPassthrough; else delete next.env_vars;
    for (const key of ["url", "auth", "bearer_token_env_var", "http_headers", "env_http_headers", "oauth", "oauth_resource", "scopes"])
      delete next[key];
  } else {
    next.url = draft.url.trim();
    for (const key of ["command", "args", "cwd", "env", "env_vars"])
      delete next[key];
    const headers = pairRecord(draft.headers, "HTTP 标头", true) || {};
    const envHeaders = pairRecord(draft.envHeaders, "HTTP 标头环境变量", true) || {};
    const preserveAuthFields = draft.authMode === draft.originalAuthMode;
    const clearOAuth = () => {
      delete next.oauth;
      delete next.oauth_resource;
      delete next.scopes;
    };
    if (draft.authMode === "token") {
      const existing = Object.entries(asRecord(raw.http_headers)).find(([key]) => key.toLowerCase() === "authorization");
      if (draft.token.trim()) headers.Authorization = `Bearer ${draft.token.trim()}`;
      else if (existing) headers[existing[0]] = String(existing[1]);
      else if (!existing) throw new Error("请填写访问 Token。");
      delete next.bearer_token_env_var;
      if (!preserveAuthFields) clearOAuth();
      if (!preserveAuthFields || draft.token.trim()) {
        for (const key of Object.keys(headers)) if (key.toLowerCase() === "authorization" && key !== "Authorization") delete headers[key];
        for (const key of Object.keys(envHeaders)) if (key.toLowerCase() === "authorization") delete envHeaders[key];
      }
    } else if (draft.authMode === "env") {
      next.bearer_token_env_var = draft.bearerEnv.trim();
      if (!preserveAuthFields) {
        delete next.auth;
        clearOAuth();
        for (const key of Object.keys(headers)) if (key.toLowerCase() === "authorization") delete headers[key];
        for (const key of Object.keys(envHeaders)) if (key.toLowerCase() === "authorization") delete envHeaders[key];
      }
    } else if (draft.authMode === "oauth") {
      next.auth = "oauth";
      delete next.bearer_token_env_var;
      if (!preserveAuthFields) {
        for (const key of Object.keys(headers)) if (key.toLowerCase() === "authorization") delete headers[key];
        for (const key of Object.keys(envHeaders)) if (key.toLowerCase() === "authorization") delete envHeaders[key];
      }
    } else if (draft.authMode === "none") {
      delete next.auth;
      delete next.bearer_token_env_var;
      if (!preserveAuthFields) clearOAuth();
      for (const key of Object.keys(headers)) if (key.toLowerCase() === "authorization") delete headers[key];
      for (const key of Object.keys(envHeaders)) if (key.toLowerCase() === "authorization") delete envHeaders[key];
    }
    if (preserveAuthFields && !(draft.authMode === "token" && draft.token.trim())) {
      for (const [key, value] of Object.entries(asRecord(raw.http_headers)))
        if (key.toLowerCase() === "authorization") headers[key] = String(value);
      for (const [key, value] of Object.entries(asRecord(raw.env_http_headers)))
        if (key.toLowerCase() === "authorization") envHeaders[key] = String(value);
    }
    if (draft.authMode === "token" && !preserveAuthFields) delete next.auth;
    if (Object.keys(headers).length) next.http_headers = headers; else delete next.http_headers;
    if (Object.keys(envHeaders).length) next.env_http_headers = envHeaders; else delete next.env_http_headers;
  }
  if (enabled !== undefined) next.enabled = enabled;
  return structuredConfigEdits({ mcp_servers: { [name]: raw } }, { mcp_servers: { [name]: next } });
}
