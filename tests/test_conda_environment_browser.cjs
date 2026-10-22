'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    const prefix = '/fixture/env/science';
    const apiSessions = () => page.evaluate(async () => (await fetch('/api/sessions')).json());
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/conda/environments', route => route.fulfill({ json: {
      available: true, environments: [{ name: 'science', prefix }],
    } }));
    const openEnvironment = async () => {
      await page.getByRole('button', { name: '切换任务面板', exact: true }).click();
      await page.getByRole('tab', { name: '运行环境', exact: true }).click();
    };
    await page.goto(process.env.CONSOLE_TEST_URL);
    await page.getByRole('heading', { name: '今天想构建什么？' }).waitFor();
    await openEnvironment();
    await page.getByText('先打开或创建一个会话，再选择运行环境。').waitFor();
    await page.getByRole('button', { name: '关闭任务面板', exact: true }).click();
    await page.locator('#text').fill('parity-fixture');
    const created = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/sessions');
    await page.locator('#send').click();
    const session = await (await created).json();
    await page.getByText('验收完成。', { exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('#interrupt'));
    await openEnvironment();
    await page.getByLabel('Conda 环境', { exact: true }).selectOption(prefix);
    await page.getByRole('button', { name: '应用到当前会话', exact: true }).click();
    await page.locator('.conda-environment strong').getByText('science', { exact: true }).waitFor();
    assert.equal((await apiSessions()).find(row => row.id === session.id).conda_environment.prefix, prefix);
    await page.reload();
    await page.getByText('验收完成。', { exact: true }).waitFor();
    await openEnvironment();
    await page.locator('.conda-environment strong').getByText('science', { exact: true }).waitFor();
    assert.equal(await page.getByLabel('Conda 环境', { exact: true }).inputValue(), prefix);

    await page.getByRole('button', { name: '打开终端', exact: true }).click();
    const terminal = page.getByLabel('底部终端');
    await terminal.getByText('Conda 环境：science', { exact: true }).waitFor();
    await terminal.getByLabel('可执行程序', { exact: true }).fill('python');
    await terminal.getByLabel('参数（每行一个）', { exact: true }).fill('--version');
    const execution = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/api/native/call') && request.postDataJSON().method === 'command/exec');
    await terminal.getByRole('button', { name: '启动终端', exact: true }).click();
    assert.equal((await execution).postDataJSON().session_id, session.id);
    await page.waitForFunction(prefix => document.querySelector('.xterm-rows')?.textContent.includes(prefix), prefix);
    await page.getByRole('button', { name: '关闭终端', exact: true }).click();

    await page.getByLabel('Conda 环境', { exact: true }).selectOption('');
    await page.getByRole('button', { name: '应用到当前会话', exact: true }).click();
    await page.locator('.conda-environment strong').getByText('默认环境', { exact: true }).waitFor();
    const sessions = await apiSessions();
    assert.equal(sessions.find(row => row.id === session.id).conda_environment, null);
    assert.equal(sessions.find(row => row.id === 'history').conda_environment, undefined);
    await page.locator('#text').fill('steer-running-fixture');
    await page.locator('#send').click();
    await page.getByText('当前回复结束后可切换环境。', { exact: true }).waitFor();
    assert.equal(await page.getByLabel('Conda 环境', { exact: true }).isDisabled(), true);
    assert.deepEqual(errors, []);
    console.log('Conda: right panel binding/unbinding, persisted reload, terminal environment, session isolation and running guard passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
