import { useEffect, useState } from "react";
import { RefreshCw, Terminal } from "lucide-react";
import { Button } from "./components/ui/button";
import { Feedback, Field } from "./capability-controls";
import { api, busyStatuses, consoleStore as store, type ConsoleState } from "./store";
import "./conda-environment.css";

type Environment = { name: string; prefix: string };
type Inventory = { available: boolean; environments: Environment[]; error?: string };

export function CondaEnvironment({ state }: { state: ConsoleState }) {
  const session = state.sessions.find((s) => s.id === state.active);
  const bound = session?.conda_environment;
  const [inventory, setInventory] = useState<Inventory>();
  const [prefix, setPrefix] = useState(bound?.prefix || "");
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  useEffect(() => { setPrefix(bound?.prefix || ""); }, [session?.id, bound?.prefix]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    void api<Inventory>("/api/conda/environments", undefined, undefined, controller.signal)
      .then(setInventory)
      .catch((e) => { if (!controller.signal.aborted) setError(String(e)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [revision]);
  const busy = state.settingsBusy || state.submitting || state.creating;
  const running = !!session?.active_turn_id || busyStatuses.has(session?.status || "");
  const known = inventory?.environments.some((env) => env.prefix === prefix);
  return <div className="conversation-tool-panel conda-environment">
    <h3><Terminal size={16} /> 运行环境</h3>
    <p className="muted">为当前会话绑定 Conda 环境，后续 Python、pip 和 Shell 命令会使用该环境。</p>
    {!session ? <p>先打开或创建一个会话，再选择运行环境。</p> : <>
      <p>当前环境：<strong>{bound?.name || "默认环境"}</strong></p>
      {bound && <p className="conda-prefix">{bound.prefix}</p>}
      <form onSubmit={(event) => { event.preventDefault(); setStatus(""); void store.updateCondaEnvironment(session.id, prefix || null).then((saved) => {
        if (saved) setStatus(prefix ? "已绑定，后续命令将使用所选环境。" : "已取消绑定，后续命令将使用默认环境。");
      }); }}>
        <Field label="Conda 环境"><select value={prefix} disabled={busy || running || loading}
          onChange={(event) => { setPrefix(event.target.value); setStatus(""); }}>
          <option value="">默认环境（不绑定 Conda）</option>
          {prefix && !known && <option value={prefix}>{bound?.name || prefix}（未发现）</option>}
          {inventory?.environments.map((env) => <option key={env.prefix} value={env.prefix}>{env.name} · {env.prefix}</option>)}
        </select></Field>
        <div className="conda-actions">
          <Button type="submit" size="sm" disabled={busy || running || loading || prefix === (bound?.prefix || "") || (!!prefix && !known)}>
            {busy ? "正在切换…" : "应用到当前会话"}
          </Button>
          <Button type="button" size="sm" variant="outline" disabled={loading || busy} onClick={() => setRevision((n) => n + 1)}>
            <RefreshCw size={14} /> {loading ? "正在读取…" : "刷新环境"}
          </Button>
        </div>
      </form>
      {running && <p className="muted">当前回复结束后可切换环境。</p>}
      <p className="muted">选择会随会话保存；已启动的终端继续使用启动时的环境。安装包时可使用 python -m pip install 包名。</p>
    </>}
    {inventory && !inventory.available && <p role="status">{inventory.error}</p>}
    <Feedback error={error} status={status} />
  </div>;
}
