import { test, expect } from './shell-fixture.mjs';

for (const filename of ['index.html', 'weather_preview.html']) {
  test(`${filename}: chat presentation preserves detached cards and system messages`, async ({ shell }) => {
    const { page } = shell;
    await shell.open(filename);
    await page.locator('.tab-btn[data-tab="agents"]').click();
    const initialSystemCount = await page.locator('.system-message').count();
    const card = await page.evaluate(() => {
      const original = Date.prototype.toLocaleTimeString;
      const calls = [];
      Date.prototype.toLocaleTimeString = function (...args) { calls.push(args); return '12:34'; };
      try {
        const block = createQABlock('<b>neutral question</b> & text');
        const detached = !block.isConnected;
        document.getElementById('agent-chat-history').append(block);
        return { detached, calls };
      } finally { Date.prototype.toLocaleTimeString = original; }
    });
    expect(card).toEqual({ detached: true, calls: [[[], { hour: '2-digit', minute: '2-digit' }]] });
    await expect(page.locator('.qa-block .qa-text')).toHaveText('<b>neutral question</b> & text');
    await expect(page.locator('.qa-block .qa-text b')).toHaveCount(0);
    await expect(page.locator('.qa-label')).toHaveText('YOU [12:34]');
    await expect(page.locator('.qa-response-label > span')).toHaveText('AGENT RESPONSE');
    await expect(page.locator('.qa-response')).toHaveClass('qa-response thinking');
    await expect(page.locator('.qa-response')).toHaveText('🤔 Thinking...');
    await expect(page.locator('.qa-speak-btn')).toHaveAttribute('aria-label', 'Read this reply aloud');
    await expect(page.locator('.qa-speak-btn')).toBeHidden();

    const messages = await page.evaluate(() => {
      const history = document.getElementById('agent-chat-history');
      history.style.height = '80px'; history.style.maxHeight = '80px'; history.style.overflow = 'auto';
      const first = appendChatMessage('<strong>neutral system</strong><div style="height:500px"></div>');
      const second = appendChatMessage('<em>next system</em>');
      return { first, second, atBottom: Math.abs(history.scrollHeight - history.clientHeight - history.scrollTop) <= 1 };
    });
    expect(messages.first).toMatch(/^msg-\d+-[a-z0-9]{1,5}$/);
    expect(messages.second).not.toBe(messages.first);
    expect(messages.atBottom).toBe(true);
    await expect(page.locator('.system-message strong')).toHaveText('neutral system');
    await expect(page.locator('.system-message em')).toHaveText('next system');
    expect(await page.evaluate(() => Array.from(document.getElementById('agent-chat-history').children)
      .slice(-3).map(node => node.className))).toEqual([
      'qa-block', 'agent-message system-message', 'agent-message system-message',
    ]);
    await page.evaluate(id => { removeChatMessage(id); removeChatMessage(id); removeChatMessage(''); }, messages.first);
    await expect(page.locator('.system-message')).toHaveCount(initialSystemCount + 1);
    expect(await page.evaluate(() => {
      const old = document.getElementById('agent-chat-history'); old.removeAttribute('id');
      const absent = appendChatMessage('missing history');
      const replacement = document.createElement('div'); replacement.id = 'agent-chat-history';
      old.after(replacement);
      const id = appendChatMessage('replacement history');
      const result = { absent, text: replacement.textContent, idMatches: replacement.firstChild.id === id,
        oldCount: old.querySelectorAll('.system-message').length };
      removeChatMessage(id); replacement.remove(); old.id = 'agent-chat-history';
      return result;
    })).toEqual({ absent: null, text: 'replacement history', idMatches: true, oldCount: initialSystemCount + 1 });
    expect(shell.requests.some(request => request.startsWith('POST /api/agent'))).toBe(false);
  });
}
