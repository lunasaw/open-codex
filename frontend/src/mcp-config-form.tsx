import { useCallback, useEffect, useMemo, useState } from "react";
import { Button } from "./components/ui/button";
import { Feedback, useAction, useResource } from "./capability-controls";
import { configPath, configWriteParams, type ConfigSnapshot } from "./configuration-data";
import { native, type Obj } from "./native";
import { sharedMutation } from "./native-mutation";
import { consoleStore as store, type ConsoleState } from "./store";
import {
  mcpConfigEdits, mcpDeleteEdit, mcpDraft, updatePair, validateMcpDraft,
  type McpAuthMode, type McpDraft, type McpPair, type McpTransport,
} from "./mcp-config";

type Props = {
  state: ConsoleState;
  initialName: string;
  onClose: () => void;
  onSaved: () => void;
};

const text = (value: unknown) => typeof value === "string" ? value : "";
const objectValue = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

function PairRows({
  label, rows, onChange, http = false,
}: { label: string; rows: McpPair[]; onChange: (rows: McpPair[]) => void; http?: boolean }) {
  return <div className="mcp-pair-list">
    <div className="mcp-subheading"><strong>{label}</strong><Button type="button" size="sm" variant="ghost" onClick={() => onChange([...rows, { key: "", value: "" }])}>＋ 添加</Button></div>
    {rows.map((row, index) => <div className="mcp-pair-row" key={`${index}:${row.key}`}>
      <input aria-label={`${label}名称 ${index + 1}`} placeholder={http ? "Header-Name" : "KEY"} value={row.key}
        onChange={(event) => onChange(updatePair(rows, index, { key: event.target.value }))} />
      <input aria-label={`${label}值 ${index + 1}`} type={http && /authorization|token|secret|password|api.?key/i.test(row.key) ? "password" : "text"}
        placeholder={http ? "值" : "value"} value={row.value}
        onChange={(event) => onChange(updatePair(rows, index, { value: event.target.value }))} />
      <Button type="button" size="icon-xs" variant="ghost" aria-label={`删除${label} ${index + 1}`} onClick={() => onChange(rows.filter((_, position) => position !== index))}>×</Button>
    </div>)}
    {!rows.length && <p className="mcp-empty-line">未设置</p>}
  </div>;
}

function ArgsRows({ args, onChange }: { args: string[]; onChange: (args: string[]) => void }) {
  return <div className="mcp-pair-list">
    <div className="mcp-subheading"><strong>Arguments</strong><Button type="button" size="sm" variant="ghost" onClick={() => onChange([...args, ""])}>＋ 添加参数</Button></div>
    {args.map((value, index) => <div className="mcp-argument-row" key={index}>
      <span>{index + 1}</span><input aria-label={`参数 ${index + 1}`} value={value} onChange={(event) => onChange(args.map((item, position) => position === index ? event.target.value : item))} />
      <Button type="button" size="icon-xs" variant="ghost" aria-label={`删除参数 ${index + 1}`} onClick={() => onChange(args.filter((_, position) => position !== index))}>×</Button>
    </div>)}
    {!args.length && <p className="mcp-empty-line">没有参数</p>}
  </div>;
}

function TransportTabs({ value, onChange, disabled }: { value: McpTransport; onChange: (value: McpTransport) => void; disabled: boolean }) {
  return <div className="mcp-tabs" role="tablist" aria-label="MCP 传输类型">
    {([["stdio", "STDIO"], ["http", "Streamable HTTP"]] as const).map(([transport, label]) =>
      <button type="button" role="tab" aria-selected={value === transport} disabled={disabled} key={transport}
        onClick={() => onChange(transport)}>{label}</button>)}
  </div>;
}

function AuthFields({ draft, set }: { draft: McpDraft; set: (patch: Partial<McpDraft>) => void }) {
  const options: Array<[McpAuthMode, string]> = [
    ["none", "不提供凭证"], ["env", "Token 环境变量"], ["token", "直接 Token"], ["oauth", "OAuth（原生登录）"],
  ];
  if (draft.authMode === "native") options.push(["native", "原生会话认证（保持现有配置）"]);
  return <>
    <label className="mcp-field"><span>认证方式</span><select aria-label="认证方式" value={draft.authMode} onChange={(event) => set({ authMode: event.target.value as McpAuthMode })}>
      {options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select></label>
    {draft.authMode === "none" && <p className="mcp-help">不在此服务配置中设置显式 Token；服务端若要求认证，可改用环境变量或 OAuth。</p>}
    {draft.authMode === "env" && <label className="mcp-field"><span>Token 环境变量</span><input aria-label="Token 环境变量" value={draft.bearerEnv} placeholder="MCP_TOKEN" onChange={(event) => set({ bearerEnv: event.target.value })} /><small>原生 MCP 读取该环境变量，不需要把 Token 写进配置。</small></label>}
    {draft.authMode === "token" && <label className="mcp-field"><span>访问 Token</span><input aria-label="访问 Token" type="password" autoComplete="new-password" value={draft.token} placeholder={draft.hasExistingToken ? "已保存；留空保留原 Token" : "输入 Token"} onChange={(event) => set({ token: event.target.value })} /><small>直接 Token 会写入原生用户配置，读取和日志会遮罩；优先使用环境变量。</small></label>}
    {draft.authMode === "oauth" && <p className="mcp-help">保存后可在服务卡片点击 OAuth 登录。OAuth 凭据由原生 Codex 管理。</p>}
    {draft.authMode === "native" && <p className="mcp-help">当前服务使用原生会话认证，表单会保留该字段。</p>}
  </>;
}

export function McpConfigForm({ state, initialName, onClose, onSaved }: Props) {
  const cwd = store.cwd;
  const resource = useResource(useCallback(() => native<ConfigSnapshot>("config/read", { includeLayers: true, ...(cwd ? { cwd } : {}) }), [cwd]));
  const action = useAction();
  const [draft, setDraft] = useState<McpDraft>(() => mcpDraft(initialName));
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [notice, setNotice] = useState("");
  const newServer = !initialName;
  const layers = resource.data?.layers || [];
  const layer = layers.find((item) => item.name.type === "user");
  const rawServers = objectValue(layer?.config?.mcp_servers);
  const effectiveServers = objectValue(resource.data?.config?.mcp_servers);
  const raw = initialName ? rawServers[initialName] ?? effectiveServers[initialName] ?? {} : {};
  const userOwned = !!layer && (!initialName || Object.hasOwn(rawServers, initialName));
  const editable = !!state.nativeProtocol?.configuration_editable && !!layer && layer.name.type === "user" && !layer.disabledReason;
  const key = `${initialName}:${layer?.version || "loading"}`;
  useEffect(() => {
    setDraft(mcpDraft(initialName, raw));
    setConfirmDelete(false);
    setNotice("");
  // Reinitialize only after the observed layer changes; input edits must not reset.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(mcpDraft(initialName, raw)), [draft, initialName, raw]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const set = (patch: Partial<McpDraft>) => { setDraft((value) => ({ ...value, ...patch })); setNotice(""); };
  const save = async (enabled?: boolean) => {
    validateMcpDraft(draft);
    if (!layer) throw new Error("没有可写入的用户配置层。");
    if (newServer && Object.hasOwn(effectiveServers, draft.name.trim())) throw new Error("此名称已在原生配置中使用，请编辑现有服务。");
    const edits = mcpConfigEdits(draft.name.trim(), raw, draft, enabled);
    if (!edits.length) { setNotice("没有检测到配置变化。"); return; }
    const result = await sharedMutation(`config:${configPath(layer)}`).run("config/batchWrite", configWriteParams(layer, edits, true));
    await native("config/mcpServer/reload", null);
    setNotice(result.status === "okOverridden" || result.overriddenMetadata ? "已保存，但部分值被更高优先级配置覆盖。" : "已保存并请求重新加载 MCP。");
    await resource.refresh();
    onSaved();
  };
  const remove = async () => {
    if (!initialName || !layer) throw new Error("请选择已有的 MCP 服务。");
    // Delete the complete named entry with a single native null edit. This
    // also removes redacted children without ever sending their contents.
    const edits = [mcpDeleteEdit(initialName)];
    await sharedMutation(`config:${configPath(layer)}`).run("config/batchWrite", configWriteParams(layer, edits, true));
    await native("config/mcpServer/reload", null);
    setNotice("已删除并请求重新加载 MCP。");
    onSaved();
    onClose();
  };
  const disabled = !editable || !userOwned || resource.loading || !!resource.error || action.busy;
  return <section className="mcp-editor" aria-label={newServer ? "添加 MCP 服务" : `编辑 MCP 服务 ${initialName}`}>
    <div className="mcp-editor-heading"><div><h4>{newServer ? "添加自定义 MCP" : `编辑 ${initialName}`}</h4><p className="mcp-help">保存到 Codex 用户配置；MCP 重载后读取真实连接状态。</p></div><Button type="button" variant="ghost" onClick={onClose}>关闭</Button></div>
    <Feedback error={action.error || resource.error} status={notice || action.status || (resource.loading ? "读取原生配置中…" : "")} />
    {!state.nativeProtocol?.configuration_editable && <p className="mcp-readonly">当前 Host 的发布配置为只读，不能写入 MCP。</p>}
    {!layer && !resource.loading && <p className="mcp-readonly">没有读取到可写的用户配置层。</p>}
    {!userOwned && initialName && <p className="mcp-readonly">此服务来自其他原生配置层；Codex 原生写入接口只允许修改用户配置层。</p>}
    <form onSubmit={(event) => { event.preventDefault(); void action.run(() => save(), ""); }}>
      <fieldset disabled={disabled}>
        <label className="mcp-field"><span>名称</span><input aria-label="MCP 服务名称" value={draft.name} disabled={!newServer} placeholder="MCP server name" onChange={(event) => set({ name: event.target.value })} /></label>
        <div className="mcp-section"><div className="mcp-section-heading"><span>传输类型</span><TransportTabs value={draft.transport} onChange={(transport) => set({ transport })} disabled={false} /></div>
          {draft.transport === "stdio" ? <>
            <label className="mcp-field"><span>启动命令</span><input aria-label="启动命令" value={draft.command} placeholder="npx" onChange={(event) => set({ command: event.target.value })} /></label>
            <ArgsRows args={draft.args} onChange={(args) => set({ args })} />
            <PairRows label="环境变量" rows={draft.env} onChange={(env) => set({ env })} />
            <div className="mcp-field"><span>环境变量透传</span><div><div className="mcp-passthrough-list">{draft.passthrough.map((value, index) => <div className="mcp-argument-row" key={index}><input aria-label={`透传环境变量 ${index + 1}`} value={value} placeholder="HOME" onChange={(event) => set({ passthrough: draft.passthrough.map((item, position) => position === index ? event.target.value : item) })} /><Button type="button" size="icon-xs" variant="ghost" aria-label={`删除透传变量 ${index + 1}`} onClick={() => set({ passthrough: draft.passthrough.filter((_, position) => position !== index) })}>×</Button></div>)}</div><Button type="button" size="sm" variant="ghost" onClick={() => set({ passthrough: [...draft.passthrough, ""] })}>＋ 添加变量</Button><small>把 Host 环境中的指定变量传给 MCP 子进程。</small></div></div>
            <label className="mcp-field"><span>工作目录</span><input aria-label="MCP 工作目录" value={draft.cwd} placeholder={cwd || "~/code"} onChange={(event) => set({ cwd: event.target.value })} /></label>
          </> : <>
            <label className="mcp-field"><span>服务 URL</span><input aria-label="MCP 服务 URL" type="url" value={draft.url} placeholder="https://example.com/mcp" onChange={(event) => set({ url: event.target.value })} /></label>
            <AuthFields draft={draft} set={set} />
            <PairRows label="HTTP 标头" rows={draft.headers} onChange={(headers) => set({ headers })} http />
            <PairRows label="HTTP 标头环境变量" rows={draft.envHeaders} onChange={(envHeaders) => set({ envHeaders })} http />
          </>}
        </div>
        {!newServer && <div className="mcp-editor-actions"><Button type="submit" variant="primary" disabled={disabled || !dirty}>保存并重新加载</Button><Button type="button" variant="outline" disabled={disabled} onClick={() => void action.run(() => save(draft.enabled === false ? true : false), "")}>{draft.enabled === false ? "启用服务" : "停用服务"}</Button>{userOwned && (confirmDelete ? <><span className="mcp-delete-confirm">确定删除？</span><Button type="button" variant="destructive" disabled={disabled} onClick={() => void action.run(remove, "")}>确认删除</Button><Button type="button" variant="ghost" onClick={() => setConfirmDelete(false)}>取消</Button></> : <Button type="button" variant="ghost" disabled={disabled} onClick={() => setConfirmDelete(true)}>删除</Button>)}</div>}
        {newServer && <div className="mcp-editor-actions"><Button type="submit" variant="primary" disabled={disabled || !dirty}>保存并重新加载</Button><Button type="button" variant="outline" disabled={action.busy} onClick={onClose}>取消</Button></div>}
      </fieldset>
    </form>
    {userOwned && <Button type="button" variant="ghost" disabled={resource.loading || action.busy} onClick={() => { setNotice(""); void resource.refresh(); }}>{dirty ? "放弃修改并重读配置" : "重新读取配置"}</Button>}
    <p className="mcp-help">原生字段（超时、工具白名单、OAuth 客户端等）不会因表单保存而丢失；需要修改时可在“配置”里的原生设置中编辑。未修改的凭证不会回显。</p>
  </section>;
}
