import assert from "node:assert/strict";
import { test } from "node:test";
import { mcpConfigEdits, mcpDeleteEdit, mcpDraft, validateMcpDraft } from "./mcp-config";

test("MCP drafts map stdio fields and preserve advanced entries", () => {
  const raw = { command: "old", args: ["--old"], cwd: "/work", env: { KEEP: "yes" },
    enabled: true, startup_timeout_ms: 5000 };
  const draft = mcpDraft("local", raw);
  draft.command = "node"; draft.args = ["server.js"]; draft.env = [{ key: "KEEP", value: "yes" }]; draft.cwd = "";
  const edits = mcpConfigEdits("local", raw, draft);
  assert.deepEqual(edits, [
    { keyPath: "mcp_servers.local.command", value: "node", mergeStrategy: "replace" },
    { keyPath: "mcp_servers.local.args", value: ["server.js"], mergeStrategy: "replace" },
    { keyPath: "mcp_servers.local.cwd", value: null, mergeStrategy: "replace" },
  ]);
  assert.equal(edits.some((edit) => edit.keyPath.includes("startup_timeout")), false);
});

test("MCP HTTP auth switches clear the old auth source without exposing existing tokens", () => {
  const raw = { url: "https://example.test/mcp", bearer_token_env_var: "MCP_TOKEN",
    http_headers: { "X-Client": "fixture", Authorization: "[redacted:credential]" },
    env_http_headers: { Authorization: "[redacted:credential]" }, tool_timeout_sec: 10 };
  const draft = mcpDraft("remote.service", raw);
  assert.equal(draft.authMode, "env");
  draft.authMode = "token"; draft.token = "new-fixture-token";
  const edits = mcpConfigEdits("remote.service", raw, draft);
  assert.deepEqual(edits, [
    { keyPath: 'mcp_servers."remote.service".bearer_token_env_var', value: null, mergeStrategy: "replace" },
    { keyPath: 'mcp_servers."remote.service".http_headers.Authorization', value: "Bearer new-fixture-token", mergeStrategy: "replace" },
    { keyPath: 'mcp_servers."remote.service".env_http_headers', value: null, mergeStrategy: "replace" },
  ]);
  assert.equal(JSON.stringify(edits).includes("[redacted"), false);
});

test("MCP auth validation rejects malformed URLs and environment names", () => {
  const draft = mcpDraft("remote", { url: "https://example.test/mcp" });
  assert.throws(() => validateMcpDraft({ ...draft, url: "https://user:pass@example.test/mcp" }), /有效 HTTP/);
  assert.throws(() => validateMcpDraft({ ...mcpDraft("local"), command: "node", env: [{ key: "BAD-NAME", value: "x" }] }), /环境变量名称无效/);
});

test("MCP deletion uses a null named entry and can remove hidden credentials", () => {
  const raw = { url: "https://example.test/mcp", http_headers: { Authorization: "[redacted:credential]" } };
  const draft = mcpDraft("server.with.dot", raw);
  assert.deepEqual(mcpDeleteEdit("server.with.dot"),
    { keyPath: 'mcp_servers."server.with.dot"', value: null, mergeStrategy: "replace" });
  assert.equal(draft.hasExistingToken, true);
});
