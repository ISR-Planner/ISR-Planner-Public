import { test, expect } from './shell-fixture.mjs';

for (const filename of ['index.html', 'weather_preview.html']) {
  test(`${filename}: memory display preserves neutral records and existing delete dispatch`, async ({ shell }) => {
    const { page } = shell;
    await shell.open(filename);
    await page.locator('.tab-btn[data-tab="agents"]').click();
    const toggle = page.locator('#btn-toggle-memory');
    await expect(page.locator('#memory-panel')).toBeHidden();
    await toggle.focus(); await page.keyboard.press('Enter');
    await expect(toggle).toHaveText('Hide');
    await expect(page.locator('#memory-list')).toHaveText('No memories yet. Add instructions the agent should remember.');
    await expect(page.locator('#memory-count')).toHaveText('0');
    expect(await page.evaluate(() => {
      const records = Object.freeze([
        Object.freeze({ id: 7, category: 'general', content: '<b>neutral</b> & note' }),
        Object.freeze({ id: 9, category: 'preference', content: 'second neutral note' }),
      ]);
      const before = JSON.stringify(records);
      renderMemoryList(records);
      return JSON.stringify(records) === before;
    })).toBe(true);
    await expect(page.locator('.memory-item')).toHaveCount(2);
    await expect(page.locator('.memory-item').nth(0)).toHaveAttribute('data-id', '7');
    await expect(page.locator('.memory-item').nth(1)).toHaveAttribute('data-id', '9');
    await expect(page.locator('.memory-category')).toHaveText(['general', 'preference']);
    await expect(page.locator('.memory-content')).toHaveText(['<b>neutral</b> & note', 'second neutral note']);
    await expect(page.locator('.memory-content b')).toHaveCount(0);
    await expect(page.locator('.btn-delete-memory')).toHaveText(['×', '×']);
    await page.evaluate(() => {
      window.savedMemoryDelete = deleteAgentMemory;
      window.neutralMemoryDeletes = [];
      deleteAgentMemory = id => neutralMemoryDeletes.push(id);
    });
    try {
      await page.locator('.btn-delete-memory').nth(1).focus();
      await page.keyboard.press('Enter');
      expect(await page.evaluate(() => neutralMemoryDeletes)).toEqual([9]);
    } finally {
      await page.evaluate(() => { deleteAgentMemory = savedMemoryDelete; delete window.savedMemoryDelete; });
    }
    await expect(page.locator('.memory-item')).toHaveCount(2);
    // Rendering does not own the API count or memory mutations.
    await expect(page.locator('#memory-count')).toHaveText('0');
    expect(await page.evaluate(() => {
      const old = document.getElementById('memory-list'); old.removeAttribute('id');
      renderMemoryList([{ id: 11, category: 'general', content: 'absent list' }]);
      const untouched = old.querySelectorAll('.memory-item').length === 2;
      const replacement = document.createElement('div'); replacement.id = 'memory-list';
      old.after(replacement);
      renderMemoryList([{ id: 12, category: 'general', content: 'replacement note' }]);
      const replacementText = replacement.querySelector('.memory-content').textContent;
      renderMemoryList(null);
      const empty = replacement.textContent;
      replacement.remove(); old.id = 'memory-list';
      return { untouched, replacementText, empty };
    })).toEqual({ untouched: true, replacementText: 'replacement note',
      empty: 'No memories yet. Add instructions the agent should remember.' });
    await toggle.focus(); await page.keyboard.press('Enter');
    await expect(page.locator('#memory-panel')).toBeHidden();
    await expect(toggle).toHaveText('Show');
    expect(shell.requests.some(request => /^(POST|DELETE) /.test(request))).toBe(false);
  });
}
