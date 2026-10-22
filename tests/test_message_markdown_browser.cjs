'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('../frontend/node_modules/playwright');

(async () => {
  const base = process.env.CONSOLE_TEST_URL;
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await context.newPage();
    const errors = [], submissions = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (/\/api\/sessions\/[^/]+\/messages$/.test(request.url())) submissions.push(request.postDataJSON());
    });
    await page.goto(base);
    const input = page.locator('#text');
    const paste = async (plain, html) => {
      await page.evaluate(async ({ plain, html }) => {
        const data = { 'text/plain': new Blob([plain], { type: 'text/plain' }) };
        if (html) data['text/html'] = new Blob([html], { type: 'text/html' });
        await navigator.clipboard.write([new ClipboardItem(data)]);
      }, { plain, html });
      await input.focus();
      await input.press('Control+v');
    };
    const source = '# 粘贴格式验收\n\n**重点**与 `code`\n\n- 第一项\n  - 子项\n\n```python\nprint("你好")\n  # 缩进保留\n```\n\n| 字段 | 内容 |\n| --- | --- |\n| 结果 | 保留 |\n\n最后一行';
    await input.fill('');
    await paste(source);
    assert.equal(await input.inputValue(), source, 'literal Markdown, indentation and final line survive a real paste');
    await page.getByRole('button', { name: '预览 Markdown', exact: true }).click();
    const preview = page.getByRole('region', { name: 'Markdown 预览', exact: true });
    await preview.getByRole('heading', { name: '粘贴格式验收', exact: true }).waitFor();
    assert.equal(await preview.locator('strong').innerText(), '重点');
    assert.equal(await preview.locator('ul ul li').innerText(), '子项');
    assert.equal(await preview.locator('pre code').textContent(), 'print("你好")\n  # 缩进保留\n');
    assert.equal(await preview.locator('table').count(), 1);
    await input.press('End');
    await input.pressSequentially(' continued');
    await preview.getByText('最后一行 continued', { exact: true }).waitFor();
    assert.equal(await input.inputValue(), source + ' continued');
    await page.getByRole('button', { name: '收起 Markdown 预览', exact: true }).click();

    // Rendered web content carries plain text and HTML; recover its Markdown.
    const html = '<h2>网页粘贴</h2><p><strong>加粗</strong>、<em>斜体</em>和 <a href="https://example.test/docs">文档</a></p>' +
      '<ul><li>父项<ul><li>子项</li></ul></li><li><input type="checkbox" checked>已完成</li></ul>' +
      '<blockquote><p>引用内容</p></blockquote><pre><code class="language-js">const answer = 42;\n  console.log(answer);</code></pre>' +
      '<table>\n<tbody>\n<tr>\n<td>字段</td>\n<td>值</td>\n</tr>\n<tr><td>结果</td><td>a|b</td></tr>\n</tbody></table><p><del>删除线</del></p>' +
      '<script>window.pastedScriptRan = true</script>';
    await input.fill('前缀 替换 后缀');
    await input.evaluate(element => element.setSelectionRange(3, 5));
    await paste('网页粘贴\n加粗、斜体和文档\n父项\n子项\n已完成\n引用内容\nconst answer = 42;\n  console.log(answer);\n字段 值\n结果 a|b\n删除线', html);
    const converted = await input.inputValue();
    assert(converted.startsWith('前缀 ## 网页粘贴'), converted);
    assert(converted.endsWith(' 后缀'), converted);
    for (const part of ['**加粗**', '_斜体_', '[文档](https://example.test/docs)', '[x] 已完成', '> 引用内容', '```js\nconst answer = 42;\n  console.log(answer);\n```', '| 字段 | 值 |', '| --- | --- |', 'a\\|b', '~~删除线~~']) assert(converted.includes(part), part + ': ' + converted);
    assert.match(converted, /-\s+父项\n +-[ \t]+子项/);
    assert(!converted.includes('pastedScriptRan'));
    assert.equal(await page.evaluate(() => window.pastedScriptRan), undefined);
    await input.press('Control+z');
    assert.equal(await input.inputValue(), '前缀 替换 后缀', 'one undo reverses the entire converted paste');
    await input.press('Control+Shift+z');
    assert.equal(await input.inputValue(), converted);

    // Editor HTML must not double-escape the literal Markdown source.
    await input.fill('');
    await paste('## 编辑器原文\n\n**保留源码**', '<pre><span>## 编辑器原文\n\n**保留源码**</span></pre>');
    assert.equal(await input.inputValue(), '## 编辑器原文\n\n**保留源码**');
    await input.fill('');
    await paste('_斜体_ 和 `code_name`', '<p><span>_斜体_ 和 `code_name`</span></p>');
    assert.equal(await input.inputValue(), '_斜体_ 和 `code_name`');

    // The fallback inserts at the selection even without native insertText.
    await input.fill('前 后');
    await input.evaluate(element => {
      element.focus(); element.setSelectionRange(2, 2);
      const original = document.execCommand.bind(document);
      document.execCommand = (command, ...args) => command === 'insertText' ? false : original(command, ...args);
      const data = new DataTransfer();
      data.setData('text/markdown', '**中间**');
      element.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
      document.execCommand = original;
    });
    assert.equal(await input.inputValue(), '前 **中间**后');
    assert.equal(await input.evaluate(element => element.selectionStart), 8);

    // A pasted text file still uses the existing attachment adapter.
    await input.fill('文件草稿');
    await input.evaluate(element => {
      const data = new DataTransfer();
      data.items.add(new File(['附件内容'], 'paste.txt', { type: 'text/plain' }));
      element.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
    });
    const attachments = page.locator('.aui-composer-attachments');
    await attachments.getByRole('button', { name: 'Remove paste.txt' }).waitFor();
    assert.equal(await input.inputValue(), '文件草稿');
    await attachments.getByRole('button', { name: 'Remove paste.txt' }).click();

    // Hold the response so copying is checked before send reconciliation too.
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    await page.route('**/api/sessions/*/messages', async route => { await pending; await route.continue(); });
    await input.fill(source);
    await page.locator('#send').click();
    const message = page.locator('[data-slot="aui_user-message-root"]').filter({ has: page.getByRole('heading', { name: '粘贴格式验收', exact: true }) }).last();
    await message.waitFor();
    const copy = message.getByRole('button', { name: '复制消息', exact: true });
    await page.mouse.move(0, 0);
    assert.equal(await copy.evaluate(element => getComputedStyle(element.closest('.aui-user-action-bar-root')).opacity), '0');
    await message.hover();
    assert.equal(await copy.evaluate(element => getComputedStyle(element.closest('.aui-user-action-bar-root')).opacity), '1');
    await input.fill('复制保留草稿');
    await message.hover();
    await copy.click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), source, 'pending message copies its original Markdown, not the current draft');
    assert.equal(await input.inputValue(), '复制保留草稿');
    release();
    await page.waitForFunction(() => !document.querySelector('#interrupt'));
    await page.getByText('你好，完整回复。', { exact: true }).waitFor();
    assert.equal(submissions.at(-1).text, source, 'rendering does not change the model input');
    assert.equal(await message.locator('table').count(), 1);
    assert.equal(await message.locator('[data-slot="aui_user-branch-picker"]').count(), 0, 'no branch counter when the adapter cannot switch branches');
    // Move through the gap and around the button instead of letting click()
    // automatically hover/retry an action bar that disappears on the way.
    await input.focus();
    await page.evaluate(() => navigator.clipboard.writeText('等待点击复制'));
    await page.mouse.move(0, 0);
    const buttonBox = await copy.boundingBox();
    const bubbleBox = await message.locator('.aui-user-message-content').boundingBox();
    const x = buttonBox.x + buttonBox.width / 2;
    const y = buttonBox.y + buttonBox.height / 2;
    for (let current = bubbleBox.y + bubbleBox.height - 4; current <= y; current += 2) {
      await page.mouse.move(x, current);
      assert.equal(await copy.evaluate(element => getComputedStyle(element.closest('.aui-user-action-bar-root')).opacity), '1', 'copy stays visible between bubble and button');
    }
    await page.mouse.move(0, 0);
    await page.mouse.move(x, buttonBox.y + buttonBox.height + 6);
    assert.equal(await copy.evaluate(element => getComputedStyle(element.closest('.aui-user-action-bar-root')).opacity), '1', 'approaching the button from nearby also reveals it');
    await page.mouse.move(x, y, { steps: 8 });
    await page.mouse.down();
    await page.mouse.up();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), source);

    // Keyboard access also reveals the toolbar without pointer hovering.
    await page.mouse.move(0, 0);
    await copy.focus();
    assert.equal(await copy.evaluate(element => getComputedStyle(element.closest('.aui-user-action-bar-root')).opacity), '1');
    await copy.press('Enter');
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), source);
    await input.focus();
    if (process.env.CONSOLE_SCREENSHOTS) {
      fs.mkdirSync(process.env.CONSOLE_SCREENSHOTS, { recursive: true });
      await message.hover();
      await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/message-markdown-desktop.png` });
    }
    await page.reload();
    await message.waitFor();
    await message.hover();
    await copy.click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), source, 'history copies the same Markdown after reload');
    await page.evaluate(() => document.documentElement.classList.add('dark'));
    await page.setViewportSize({ width: 390, height: 844 });
    await input.fill(source);
    await page.getByRole('button', { name: '预览 Markdown', exact: true }).click();
    await preview.locator('table').waitFor();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    if (process.env.CONSOLE_SCREENSHOTS) await page.screenshot({ path: `${process.env.CONSOLE_SCREENSHOTS}/message-markdown-mobile.png` });

    const mobile = await browser.newPage({ isMobile: true, hasTouch: true, viewport: { width: 390, height: 844 } });
    await mobile.goto(page.url());
    const mobileCopy = mobile.getByRole('button', { name: '复制消息', exact: true }).last();
    await mobileCopy.waitFor();
    assert.equal(await mobileCopy.evaluate(element => getComputedStyle(element.closest('.aui-user-action-bar-root')).opacity), '1', 'touch devices expose copying without hover');
    await mobile.close();
    assert.deepEqual(errors, []);
    console.log('Message Markdown: real plain/rich paste, code/tables, selection, undo/redo, preview, attachments, pending/history copying, hover, keyboard, unsupported branches and mobile passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
