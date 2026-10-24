'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const base = process.env.CONSOLE_TEST_URL;
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    const post = async (path, data) => {
      const response = await context.request.post(base + path, {
        headers: { 'Idempotency-Key': crypto.randomUUID(), Origin: base }, data,
      });
      assert(response.ok(), await response.text()); return response.json();
    };
    const first = await post('/api/sessions', {});
    const second = await post('/api/sessions', {});
    await post(`/api/sessions/${first.id}/messages`, { text: 'performance-first' });
    await post(`/api/sessions/${second.id}/messages`, { text: 'performance-second' });
    const page = await context.newPage();
    const errors = [], calls = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => calls.push(new URL(r.url()).pathname));
    await page.addInitScript(() => {
      const original = window.fetch;
      window.injectPolling = false;
      window.pollingFrames = 0;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (!String(args[0]).includes('/events?') || !response.ok) return response;
        // Simulate an older Host sending a large polling stream, so the client
        // must remain responsive even before a backend deployment is possible.
        let cursor, timer, injected = 0n;
        const reader = response.body.getReader();
        return new Response(new ReadableStream({
          start(controller) {
            const encoder = new TextEncoder();
            timer = setInterval(() => {
              if (!window.injectPolling || !cursor) return;
              const event = { event_id: cursor.split(':')[0] + ':' + (BigInt(cursor.split(':')[1]) + ++injected),
                kind: 'response', direction: 'runtime_to_host', method: 'mcpServerStatus/list', redactions: [],
                payload: { result: { data: 'x'.repeat(100000) } } };
              window.pollingFrames++;
              controller.enqueue(encoder.encode('event: observation\ndata: ' + JSON.stringify(event) + '\n\n'));
            }, 20);
            void (async () => { try {
              for (;;) {
                const { done, value } = await reader.read(); if (done) break;
                const text = new TextDecoder().decode(value);
                const found = text.match(/"cursor":"([^"]+)"/); if (found) cursor = found[1];
                controller.enqueue(value);
              }
              controller.close();
            } catch (e) { controller.error(e); } finally { clearInterval(timer); } })();
          },
          cancel() { clearInterval(timer); return reader.cancel(); },
        }), { headers: response.headers, status: response.status });
      };
    });
    await page.goto(`${base}/?session=${first.id}`);
    await page.locator('[data-slot="aui_user-message-root"]').filter({ hasText: 'performance-first' }).waitFor();
    await page.waitForFunction(() => document.querySelector('#health')?.textContent === '已连接');
    await page.locator('#text').fill('保留性能测试草稿');
    await page.waitForTimeout(500);
    const countBefore = calls.filter(x => x.endsWith('/snapshot')).length;
    await page.evaluate(() => { window.injectPolling = true; });
    const started = Date.now();
    await page.locator('#text').pressSequentially(' typing', { delay: 15 });
    const inputMs = Date.now() - started;
    await page.waitForTimeout(1500);
    await page.evaluate(() => { window.injectPolling = false; });
    const frames = await page.evaluate(() => window.pollingFrames);
    assert(frames >= 30, `only ${frames} background frames were exercised`);
    assert.equal(calls.filter(x => x.endsWith('/snapshot')).length, countBefore, 'polling must never trigger snapshots');
    assert(inputMs < 2000, `input stalled for ${inputMs} ms`);
    assert.equal(await page.locator('#text').inputValue(), '保留性能测试草稿 typing');
    // Synthetic event identities are deliberately ahead of the real fixture.
    // Reopen at its real cursor before testing recovery from a transport fault.
    await page.reload();
    await page.locator('[data-slot="aui_user-message-root"]').filter({ hasText: 'performance-first' }).waitFor();
    await page.locator('#text').fill('保留性能测试草稿 typing');

    let failures = 0;
    await page.route(`**/api/sessions/${first.id}/snapshot`, async route => {
      if (++failures === 1) return route.abort('failed');
      return route.continue();
    });
    await post(`/api/sessions/${first.id}/messages`, { text: 'read-recovered' });
    await page.locator('[data-slot="aui_user-message-root"]').filter({ hasText: 'read-recovered' }).waitFor();
    assert(failures >= 2, 'a failed snapshot must recover automatically');
    assert.equal(await page.locator('#text').inputValue(), '保留性能测试草稿 typing');
    assert.equal(await page.getByText('TypeError: Failed to fetch', { exact: true }).count(), 0);
    await page.locator(`[data-slot="aui_thread-list-item-trigger"][title="${second.id}"]`).click();
    await page.locator('[data-slot="aui_user-message-root"]').filter({ hasText: 'performance-second' }).waitFor();
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, pollingFrames: frames, inputMs, pollingSnapshots: 0,
      checks: ['large background event stream', 'responsive input', 'read retry', 'draft retention', 'chat switch'] }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
