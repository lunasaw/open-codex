import type { NativeEvent } from "./types";

// UI inventory/health reads remain available in raw event history. They do not
// change the conversation and must not feed another full conversation refresh.
const backgroundReads = new Set([
  "mcpServerStatus/list", "config/read", "configRequirements/read", "model/list",
  "thread/list", "thread/read", "thread/loaded/list", "thread/queue/list", "thread/goal/get",
  "thread/backgroundTerminals/list", "threadSection/list", "project/list", "skills/list",
  "app/list", "app/installed", "app/read", "plugin/list", "plugin/read", "plugin/installed",
  "account/read", "account/rateLimits/read", "account/gatewayOAuth/read", "server/diagnostics",
  "permissionProfile/list", "collaborationMode/list", "experimentalFeature/list",
]);
export const isBackgroundRead = (event: NativeEvent) =>
  ["request", "response"].includes(event.kind) && backgroundReads.has(event.method || "");

export function affectsConversation(event: NativeEvent) {
  if (isBackgroundRead(event)) return false;
  // Only inbound approval/input requests affect the chat; outbound requests are
  // diagnostics until their result or an actual content notification arrives.
  if (event.kind === "request") return event.direction === "runtime_to_host";
  if (event.kind === "response") return ["turn/start", "turn/steer", "thread/resume", "thread/rollback"].includes(event.method || "");
  return !["turn/moderationMetadata", "item/reasoning/textDelta"].includes(event.method || "");
}
export const affectsSessions = (event: NativeEvent) =>
  ["session", "session_state", "turn", "message_accepted", "binding", "binding_changed", "thread_restored"].includes(event.kind) ||
  ["turn/started", "turn/completed", "thread/status/changed", "thread/name/updated",
    "thread/settings/updated", "thread/archived", "thread/unarchived", "thread/closed"].includes(event.method || "");
