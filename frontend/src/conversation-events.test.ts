import { test } from "node:test";
import assert from "node:assert/strict";
import { affectsConversation, affectsSessions } from "./conversation-events";
import type { NativeEvent } from "./types";
const row = (method: string, kind = "notification", direction = "runtime_to_host"): NativeEvent =>
  ({ event_id: "e:1", seq: "1", redactions: [], method, kind, direction });
test("background reads cannot trigger full conversation or sidebar refreshes", () => {
  for (const method of ["mcpServerStatus/list", "config/read", "thread/queue/list", "thread/read"])
    for (const kind of ["request", "response"]) {
      assert.equal(affectsConversation(row(method, kind)), false);
      assert.equal(affectsSessions(row(method, kind)), false);
    }
  assert.equal(affectsConversation(row("item/tool/requestUserInput", "request")), true);
  for (const method of ["item/agentMessage/delta", "item/completed", "warning", "thread/rollback"])
    assert.equal(affectsConversation(row(method)), true);
  assert.equal(affectsSessions(row("item/agentMessage/delta")), false);
  assert.equal(affectsSessions(row("turn/completed")), true);
  for (const kind of ["session", "turn", "message_accepted", "binding_changed"])
    assert.equal(affectsSessions(row("", kind, "")), true);
});
