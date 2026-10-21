'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const base = process.env.CONSOLE_TEST_URL || 'http://127.0.0.1:18881';
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
    const first = await post('/api/sessions', { options: { cwd: '/fixture' } });
    const second = await post('/api/sessions', { options: { cwd: '/fixture' } });
    await post(`/api/sessions/${first.id}/messages`, {
      text: 'connection-first\n' + Array.from({ length: 60 }, (_, i) => `history-line-${i}`).join('\n'),
    });
    await post(`/api/sessions/${second.id}/messages`, { text: 'connection-second' });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.clock.install();
    await page.addInitScript(id => {
      localStorage.setItem('codex-agent.active', id);
      const original = window.fetch;
      window.connectionStreams = [];
      window.connectionActivity = 0;
      window.fetch = async (...args) => {
        const path = String(args[0]);
        const stream = /\/events\?after=|\/events\/stream\?after=/.test(path);
        if (stream) window.connectionStreams.push(path);
        const response = await original(...args);
        if (!stream || !response.ok) return response;
        // Keep the TCP stream open while withholding all bytes from the app.
        return new Response(response.body.pipeThrough(new TransformStream({
          transform(chunk, controller) {
            if (!window.dropConnectionBytes) {
              window.connectionActivity++;
              controller.enqueue(chunk);
            }
          },
        })), { status: response.status, headers: response.headers });
      };
    }, first.id);
    await page.goto(base);
    await page.locator('[data-slot="aui_user-message-root"]').filter({ hasText: 'connection-first' }).waitFor();
    await page.waitForFunction(() => document.querySelector('#health')?.textContent === '已连接');
    await page.locator('#text').fill('重连时保留的草稿');
    const draft = async () => assert.equal(await page.locator('#text').inputValue(), '重连时保留的草稿');
    const count = () => page.evaluate(() => window.connectionStreams.length);
    const before = await count();
    const initialCursor = await page.evaluate(() => window.connectionStreams.at(-1));
    await page.evaluate(() => { window.dropConnectionBytes = true; });
    await post(`/api/sessions/${first.id}/messages`, { text: 'missed-while-stalled' });
    await page.clock.fastForward(45_001);
    await page.getByText('连接暂时中断，正在重新连接…', { exact: true }).waitFor();
    await page.evaluate(() => { window.dropConnectionBytes = false; });
    await page.clock.runFor(600);
    await page.waitForFunction(previous => window.connectionStreams.length > previous, before);
    await page.locator('[data-slot="aui_user-message-root"]').filter({ hasText: 'missed-while-stalled' }).waitFor();
    await draft();
    assert.equal(await page.evaluate(() => window.connectionStreams.at(-1)), initialCursor,
      'a stalled stream must resume from the last applied cursor');
    assert.equal(await page.locator('[data-slot="aui_user-message-root"]').filter({ hasText: 'missed-while-stalled' }).count(), 1);
    await page.waitForFunction(() =>
      document.querySelectorAll('[data-slot="aui_assistant-message-footer"]').length === 2 &&
      !document.querySelector('#interrupt'));

    // Heartbeats keep the idle deadline fresh while five minutes elapse.
    const healthy = await count();
    const viewport = page.locator('[data-slot="aui_thread-viewport"]');
    await page.clock.runFor(500);
    await viewport.evaluate(node => { node.scrollTop = node.scrollHeight; });
    const bottom = await viewport.evaluate(node => node.scrollTop);
    const bounds = await viewport.boundingBox();
    await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + 120);
    await page.mouse.wheel(0, -500);
    await page.waitForFunction(previous =>
      document.querySelector('[data-slot="aui_thread-viewport"]').scrollTop <= previous - 400, bottom);
    const scrollTop = await viewport.evaluate(node => node.scrollTop);
    assert(scrollTop > 0, 'the fixture must have scrollable conversation history');
    const historyNode = await page.locator('[data-slot="aui_user-message-root"]').first().elementHandle();
    for (let step = 0; step < 10; step++) {
      const received = await page.evaluate(() => window.connectionActivity);
      await page.clock.fastForward(30_000);
      await page.waitForFunction(previous => window.connectionActivity > previous, received);
    }
    assert.equal(await count(), healthy + 1, 'a healthy connection refreshes once after five minutes');
    assert.equal(await historyNode.evaluate(node => node.isConnected), true);
    const afterRefresh = await viewport.evaluate(node => node.scrollTop);
    assert(Math.abs(afterRefresh - scrollTop) <= 1, `reading position changed from ${scrollTop} to ${afterRefresh}`);
    await draft();

    // Page and network wakeups reconnect without replacing the conversation.
    for (const type of ['focus', 'online', 'visibilitychange']) {
      const previous = await count();
      const message = page.locator('[data-slot="aui_user-message-root"]').first();
      const originalNode = await message.elementHandle();
      await page.evaluate(type => {
        (type === 'visibilitychange' ? document : window).dispatchEvent(new Event(type));
      }, type);
      await page.waitForFunction(previous => window.connectionStreams.length > previous, previous);
      assert.equal(await originalNode.evaluate(node => node.isConnected), true);
      await draft();
    }

    // Offline failures retry automatically once the network returns.
    await context.setOffline(true);
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.getByText('连接暂时中断，正在重新连接…', { exact: true }).waitFor();
    await context.setOffline(false);
    await page.evaluate(() => window.dispatchEvent(new Event('online')));
    await page.waitForFunction(() => document.querySelector('#health')?.textContent === '已连接');
    await draft();
    await page.locator('#text').fill('send-after-reconnect');
    await page.locator('#text').press('Enter');
    await page.locator('[data-slot="aui_user-message-root"]').filter({ hasText: 'send-after-reconnect' }).waitFor();
    await page.waitForFunction(() => document.querySelector('#text')?.value === '');

    // Switching conversations cancels timers and listeners belonging to the old stream.
    await page.locator(`[data-slot="aui_thread-list-item-trigger"][title="${second.id}"]`).click();
    await page.locator('[data-slot="aui_user-message-root"]').filter({ hasText: 'connection-second' }).waitFor();
    const switched = await count();
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.waitForFunction(previous => window.connectionStreams.length > previous, switched);
    const streams = await page.evaluate(previous => window.connectionStreams.slice(previous), switched);
    assert(streams.every(path => path.includes(second.id)), 'only the selected conversation may reconnect');

    await page.locator('#text').fill('页面恢复后保留的草稿');
    await page.evaluate(() => {
      window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));
      window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
    });
    await page.waitForFunction(() => document.querySelector('#health')?.textContent === '已连接');
    await page.locator('[data-slot="aui_user-message-root"]').filter({ hasText: 'connection-second' }).waitFor();
    assert.equal(await page.locator('#text').inputValue(), '页面恢复后保留的草稿');
    assert.deepEqual(errors, []);
    console.log('Connection recovery passed: silent timeout, cursor replay, periodic refresh, scroll/draft retention, page/network wakeup, offline retry, sending, switching and page cache restoration.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
