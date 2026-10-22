"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { chromium } = require("../frontend/node_modules/playwright");

(async () => {
  const base = process.env.CONSOLE_TEST_URL;
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ["--no-sandbox"] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const running = async () => {
      const response = await context.request.post(base + "/api/sessions", {
        headers: { Origin: base, "Idempotency-Key": crypto.randomUUID() }, data: { options: { cwd: "/fixture" } },
      });
      assert(response.ok(), await response.text());
      const session = await response.json();
      const start = await context.request.post(`${base}/api/sessions/${session.id}/messages`, {
        headers: { Origin: base, "Idempotency-Key": crypto.randomUUID() }, data: { text: "steer-running-fixture" },
      });
      assert(start.ok(), await start.text());
      return session;
    };
    const oldSession = await running(), page = await context.newPage();
    const calls = [], errors = [], config = { model: "fixture-model", model_reasoning_effort: "medium", features: { step_model_switching: false } };
    let version = 1;
    page.setDefaultTimeout(12000);
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/api/native/call", async route => {
      const { method, params } = route.request().postDataJSON();
      calls.push({ method, params });
      if (method === "config/read") return route.fulfill({ json: { config, ...(params.includeLayers ? {
        layers: [{ name: { type: "user", file: "/fixture/config.toml" }, version: "v" + version, config }],
      } : {}) } });
      if (method === "config/batchWrite") {
        assert.equal(params.filePath, "/fixture/config.toml"); assert.equal(params.expectedVersion, "v" + version);
        assert.deepEqual(params.edits.map(edit => edit.keyPath), ["features.step_model_switching"]);
        config.features.step_model_switching = params.edits[0].value;
        version++; return route.fulfill({ json: { status: "ok", version: "v" + version } });
      }
      if (method === "experimentalFeature/list") return route.fulfill({ json: { data: [{ name: "step_model_switching", stage: "underDevelopment", enabled: config.features.step_model_switching }], nextCursor: null } });
      if (method === "turn/settings/update") return params.threadId === oldSession.thread_id
        ? route.fulfill({ status: 422, json: { error: "turn settings updates require the step_model_switching feature" } })
        : route.fulfill({ json: {} });
      return route.continue();
    });
    const button = name => page.getByRole("button", { name, exact: true });
    const control = name => page.getByLabel(name, { exact: true });
    const close = async () => { await button("返回聊天").click(); await page.locator(".settings-page").waitFor({ state: "hidden" }); };
    const settings = async () => {
      const trigger = button("选择推理强度");
      if (await trigger.getAttribute("aria-expanded") === "true") await trigger.click();
      await trigger.click(); await button("选择模型").click();
      await page.getByRole("menuitem", { name: "模型设置…", exact: true }).click();
      await page.getByRole("dialog", { name: "模型与权限", exact: true }).waitFor();
    };
    const category = async name => page.getByRole("navigation", { name: "设置分类" }).getByRole("button", { name, exact: true }).click();
    const screenshot = async name => {
      if (!process.env.CONSOLE_SCREENSHOTS) return;
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/${name}.png` });
    };
    const updates = method => calls.filter(call => call.method === method);
    await page.goto(`${base}/?session=${oldSession.id}`); await page.locator("#interrupt").waitFor();

    // Composer choices always persist the next message, even while a reply is running.
    await button("选择推理强度").click();
    await page.getByText("下次发送消息生效；当前回复可在模型设置中单独调整", { exact: true }).waitFor();
    await page.getByRole("slider", { name: "推理强度" }).press("Home");
    await page.waitForFunction(() => document.querySelector(".composer-model-trigger").textContent.includes("低"));
    assert.equal(updates("thread/settings/update").at(-1).params.effort, "low");
    assert.equal(updates("turn/settings/update").length, 0);
    await button("选择模型").click(); await page.getByRole("menuitem", { name: "Alternate model", exact: true }).click();
    await page.waitForFunction(() => document.querySelector(".composer-model-trigger").textContent.includes("Alternate model"));
    assert.equal(updates("thread/settings/update").at(-1).params.model, "fixture-alternate");
    await page.keyboard.press("Escape");
    await settings();
    assert.equal(await page.getByRole("tab", { name: "用于后续回复" }).getAttribute("aria-selected"), "true");
    assert.equal(await page.getByRole("tab", { name: "只改当前回复" }).isDisabled(), true);
    await control("推理强度").selectOption("medium"); await button("应用到当前会话").click();
    await page.getByText("会话设置已保存，下次发送消息生效", { exact: true }).waitFor();
    const next = updates("thread/settings/update").at(-1).params;
    assert.equal(next.effort, "medium"); assert.equal(next.collaborationMode.settings.reasoning_effort, "medium");
    await page.getByRole("tab", { name: "用于后续回复" }).scrollIntoViewIfNeeded(); await screenshot("reasoning-next-message"); await close();
    await page.reload(); await page.locator("#interrupt").waitFor();
    assert.match(await button("选择推理强度").innerText(), /Alternate model.*中/s);

    // The friendly configuration field and the experimental feature list both use versioned config writes.
    await button("设置").click(); await category("配置");
    await page.locator(".configuration-settings fieldset").first().waitFor();
    await page.waitForFunction(() => !document.querySelector(".configuration-settings fieldset").disabled);
    await page.locator("summary").filter({ hasText: /^模型与运行设置$/ }).click();
    await control("回复中切换模型与推理强度").selectOption("true");
    await button("保存默认配置").click(); await page.getByText("默认配置已保存，并已请求原生重载。已有聊天的设置不会因此全部切换。", { exact: true }).waitFor();
    assert.equal(config.features.step_model_switching, true);
    await category("模型与实验功能"); await page.getByRole("switch", { name: "step_model_switching" }).click();
    await page.getByText("配置已保存，新建聊天后生效。已有运行中的聊天不会立即切换。", { exact: true }).waitFor();
    assert.equal(config.features.step_model_switching, false);
    await page.getByRole("switch", { name: "step_model_switching" }).click();
    await page.waitForFunction(() => document.querySelector('[role="switch"][aria-label="step_model_switching"]').checked && !document.querySelector('[role="switch"][aria-label="step_model_switching"]').disabled);
    assert.equal(config.features.step_model_switching, true);
    assert.equal(updates("experimentalFeature/enablement/set").length, 0);
    assert.equal(updates("config/batchWrite").at(-1).params.reloadUserConfig, true);
    await close();

    // Existing native threads can still reject a newly enabled session-static feature.
    await settings(); await page.getByRole("tab", { name: "只改当前回复" }).click();
    await control("推理强度").selectOption("high"); await button("应用到当前回复").click();
    await page.getByRole("alert").filter({ hasText: "当前聊天尚未启用回复中切换" }).first().waitFor();
    assert.doesNotMatch(await page.locator(".model-settings").innerText(), /422|turn settings updates require/);
    await close();
    assert.match(await button("选择推理强度").innerText(), /中/);

    // A new thread can update just the running reply without replacing the next-message choice.
    const enabledSession = await running();
    await page.goto(`${base}/?session=${enabledSession.id}`); await page.locator("#interrupt").waitFor();
    await settings(); await page.getByRole("tab", { name: "只改当前回复" }).click();
    await control("推理强度").selectOption("low"); await button("应用到当前回复").click();
    await page.getByText("已应用到当前回复，后续回复设置保持不变", { exact: true }).waitFor();
    const live = updates("turn/settings/update").at(-1).params;
    assert.equal(live.threadId, enabledSession.thread_id); assert.ok(live.turnId); assert.equal(live.effort, "low");
    await page.setViewportSize({ width: 390, height: 844 }); await screenshot("reasoning-current-mobile");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await close(); assert.match(await button("选择推理强度").innerText(), /中/);
    await page.setViewportSize({ width: 1440, height: 1000 }); await button("切换深色主题").click();
    await settings(); await screenshot("reasoning-settings-dark");
    assert.deepEqual(errors, []);
    console.log("Reasoning settings: next-message persistence, collaboration values, feature gating/config writes, existing-thread rejection, live-only scope, mobile and dark theme passed.");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
