import { test } from "node:test";
import assert from "node:assert/strict";
import {
  StreamConnection,
  STREAM_IDLE_TIMEOUT_MS,
  STREAM_REFRESH_INTERVAL_MS,
} from "./stream-connection.ts";

const untilAborted = (signal: AbortSignal) => new Promise<void>((resolve) => {
  if (signal.aborted) resolve();
  else signal.addEventListener("abort", () => resolve(), { once: true });
});

test("a stalled connection times out, including before response headers", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const parent = new AbortController();
  const connection = new StreamConnection(parent.signal);
  let attempt!: AbortSignal;
  const pending = connection.run(async (signal) => {
    attempt = signal;
    await untilAborted(signal);
  });
  t.mock.timers.tick(STREAM_IDLE_TIMEOUT_MS - 1);
  assert.equal(attempt.aborted, false);
  t.mock.timers.tick(1);
  await pending;
  assert.equal(attempt.aborted, true);
  assert.equal(parent.signal.aborted, false);
  assert.equal(connection.refreshRequested, false);
  connection.dispose();
});

test("heartbeat bytes keep a stream alive until its periodic refresh", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const connection = new StreamConnection(new AbortController().signal);
  let activity!: () => void, attempt!: AbortSignal;
  const pending = connection.run(async (signal, received) => {
    activity = received;
    attempt = signal;
    await untilAborted(signal);
  });
  for (let elapsed = 0; elapsed < STREAM_REFRESH_INTERVAL_MS - 30_000; elapsed += 30_000) {
    t.mock.timers.tick(30_000);
    activity();
    assert.equal(attempt.aborted, false);
  }
  t.mock.timers.tick(30_000);
  await pending;
  assert.equal(connection.refreshRequested, true);
  // A deliberate refresh reconnects immediately instead of waiting for backoff.
  await connection.wait(5000);
  connection.dispose();
});

test("returning to the page or network interrupts both attempts and retry waits", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const browser = new EventTarget();
  const document = Object.assign(new EventTarget(), { visibilityState: "hidden" });
  for (const [name, value] of [["window", browser], ["document", document]] as const) {
    const original = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { value, configurable: true });
    t.after(() => {
      if (original) Object.defineProperty(globalThis, name, original);
      else Reflect.deleteProperty(globalThis, name);
    });
  }
  for (const event of ["online", "focus", "pageshow", "visibilitychange"]) {
    const parent = new AbortController();
    const connection = new StreamConnection(parent.signal);
    let attempt!: AbortSignal;
    const pending = connection.run(async (signal) => {
      attempt = signal;
      await untilAborted(signal);
    });
    if (event === "visibilitychange") {
      document.dispatchEvent(new Event(event));
      assert.equal(attempt.aborted, false);
      document.visibilityState = "visible";
      document.dispatchEvent(new Event(event));
    } else browser.dispatchEvent(new Event(event));
    await pending;
    assert.equal(connection.refreshRequested, true);
    connection.refreshRequested = false;
    const waiting = connection.wait(5000);
    browser.dispatchEvent(new Event("online"));
    await waiting;
    parent.abort();
    connection.refreshRequested = false;
    browser.dispatchEvent(new Event("online"));
    assert.equal(connection.refreshRequested, false, "disposed listeners must not fire");
  }
});

test("switching away or disposing stops active attempts and backoff", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const parent = new AbortController();
  const connection = new StreamConnection(parent.signal);
  const pending = connection.run(untilAborted);
  parent.abort();
  await pending;
  await connection.wait(5000);

  const next = new AbortController();
  const retry = new StreamConnection(next.signal);
  const waiting = retry.wait(5000);
  next.abort();
  await waiting;
});
