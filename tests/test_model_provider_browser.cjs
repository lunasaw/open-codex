"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { chromium } = require("../frontend/node_modules/playwright");

(async () => {
  const base = process.env.CONSOLE_TEST_URL;
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ["--no-sandbox"] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const response = await context.request.post(base + "/api/sessions", {
      headers: { Origin: base, "Idempotency-Key": crypto.randomUUID() },
      data: { options: { cwd: "/fixture", model: "fixture-model", modelProvider: "fixture" } },
    });
    assert(response.ok(), await response.text());
    const session = await response.json();
    const page = await context.newPage(), calls = [], errors = [];
    page.setDefaultTimeout(12000);
    page.on("pageerror", error => errors.push(error.message));
    page.on("request", request => {
      if (request.url().endsWith("/api/native/call")) calls.push(request.postDataJSON());
    });
    const button = name => page.getByRole("button", { name, exact: true });
    const settings = async () => {
      if (await button("选择推理强度").getAttribute("aria-expanded") !== "true") await button("选择推理强度").click();
      await button("选择模型").click();
      await page.getByRole("menuitem", { name: "模型设置…", exact: true }).click();
      await page.getByRole("dialog", { name: "模型与权限", exact: true }).waitFor();
    };
    const screenshot = async name => {
      if (!process.env.CONSOLE_SCREENSHOTS) return;
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/${name}.png` });
    };
    await page.goto(`${base}/?session=${session.id}`);
    await button("选择推理强度").click();
    await button("选择模型服务商").click();
    await page.getByRole("menuitem", { name: "Alternate", exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('[aria-label="选择推理强度"]').disabled);
    if (await button("选择推理强度").getAttribute("aria-expanded") !== "true") await button("选择推理强度").click();
    await page.waitForFunction(() => document.querySelector('[aria-label="选择模型服务商"]')?.textContent.includes("Alternate"));
    assert.equal(calls.find(call => call.method === "thread/resume").params.modelProvider, "alternate");
    assert.equal(calls.find(call => call.method === "thread/resume").params.threadId, session.thread_id);
    await screenshot("provider-picker-desktop");
    await page.reload();
    await settings();
    assert.equal(await page.getByLabel("模型服务商", { exact: true }).inputValue(), "alternate");
    await page.getByLabel("模型服务商", { exact: true }).selectOption("fixture");
    await page.getByLabel("模型", { exact: true }).fill("custom-provider-model");
    await button("应用到当前会话").click();
    await page.getByText("会话设置已保存，下次发送消息生效", { exact: true }).waitFor();
    const resume = calls.filter(call => call.method === "thread/resume").at(-1).params;
    assert.equal(resume.modelProvider, "fixture");
    assert.equal(resume.model, "custom-provider-model");
    await page.getByLabel("模型服务商", { exact: true }).scrollIntoViewIfNeeded();
    await screenshot("provider-settings-desktop");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByLabel("模型服务商", { exact: true }).scrollIntoViewIfNeeded();
    await screenshot("provider-settings-mobile");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await button("返回聊天").click();
    await page.reload();
    await button("选择推理强度").click();
    assert.match(await button("选择推理强度").innerText(), /custom-provider-model/);
    assert.match(await button("选择模型服务商").innerText(), /Fixture/);
    await screenshot("provider-picker-mobile");
    await page.keyboard.press("Escape");
    await page.setViewportSize({ width: 1440, height: 1000 });

    // A failed reload must not replace the accepted provider in the picker.
    await page.route("**/api/native/call", async route => route.request().postDataJSON().method === "thread/resume"
      ? route.fulfill({ status: 422, json: { error: "provider unavailable" } }) : route.continue());
    await button("选择推理强度").click();
    await button("选择模型服务商").click();
    await page.getByRole("menuitem", { name: "Alternate", exact: true }).click();
    await page.getByRole("alert").filter({ hasText: "provider unavailable" }).first().waitFor();
    if (await button("选择推理强度").getAttribute("aria-expanded") !== "true") await button("选择推理强度").click();
    assert.match(await button("选择模型服务商").innerText(), /Fixture/);
    await page.unroute("**/api/native/call");
    await page.keyboard.press("Escape");

    const started = await context.request.post(`${base}/api/sessions/${session.id}/messages`, {
      headers: { Origin: base, "Idempotency-Key": crypto.randomUUID() }, data: { text: "steer-running-fixture" },
    });
    assert(started.ok(), await started.text());
    await page.reload();
    await page.locator("#interrupt").waitFor();
    await button("选择推理强度").click();
    assert.equal(await button("选择模型服务商").isDisabled(), true);
    await settings();
    assert.equal(await page.getByLabel("模型服务商", { exact: true }).isDisabled(), true);
    assert.deepEqual(errors, []);
    console.log("Provider switching: original chat, persisted selection, custom model, rejection, active-turn guard, desktop and mobile passed.");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
