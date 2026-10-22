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
      assert.equal(response.ok(), true, await response.text());
      return response.json();
    };
    const session = await post('/api/sessions', {});
    await post(`/api/sessions/${session.id}/messages`, { text: 'retention-browser-secret' });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${base}/?session=${session.id}`);
    const user = page.locator('[data-slot="aui_user-message-root"]').filter({ hasText: 'retention-browser-secret' });
    await user.waitFor();
    await page.waitForFunction(() => document.querySelector('#health')?.textContent === '已连接');
    const native = method => post('/api/native/call', { method, params: { threadId: session.thread_id } });
    await native('thread/archive');
    await user.waitFor({ state: 'detached' });
    const history = await context.request.get(`${base}/api/events/history?session_id=${session.id}`);
    assert.equal((await history.json()).events.length, 0);
    await native('thread/unarchive');
    await page.reload();
    await user.waitFor();

    // A reconnect may miss the cleanup control frame. Its first status must
    // invalidate the stale snapshot even when no new event has been committed.
    await page.addInitScript(() => {
      const fetchOriginal = window.fetch;
      window.dropRetention = false;
      window.fetch = async (...args) => {
        const response = await fetchOriginal(...args);
        if (!String(args[0]).includes('/events?')) return response;
        return new Response(response.body.pipeThrough(new TransformStream({
          transform(chunk, controller) { if (!window.dropRetention) controller.enqueue(chunk); },
        })), { headers: response.headers, status: response.status });
      };
    });
    await page.reload();
    await user.waitFor();
    await page.waitForFunction(() => document.querySelector('#health')?.textContent === '已连接');
    await page.evaluate(() => { window.dropRetention = true; });
    await native('thread/archive');
    await page.evaluate(() => { window.dropRetention = false; window.dispatchEvent(new Event('online')); });
    await user.waitFor({ state: 'detached' });
    await native('thread/unarchive');
    await page.reload();
    await user.waitFor();
    await page.evaluate(() => { window.dropRetention = true; });
    await native('thread/delete');
    await page.evaluate(() => { window.dropRetention = false; window.dispatchEvent(new Event('online')); });
    await user.waitFor({ state: 'detached' });
    const remaining = await context.request.get(`${base}/api/events/history?session_id=${session.id}`);
    assert.equal((await remaining.json()).events.length, 0);
    assert.deepEqual(errors, []);
    console.log('Retention: archive clears an open tab, unarchive restores native history, deletion clears after reconnect.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
