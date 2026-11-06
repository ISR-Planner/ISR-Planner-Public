import { test, expect } from './shell-fixture.mjs';

for (const filename of ['index.html', 'weather_preview.html']) {
  test(`${filename}: trace display renders neutral records and preserves clear confirmation`, async ({ shell }) => {
    await shell.open(filename);
    const { page } = shell;
    await page.locator('.tab-btn[data-tab="agents-trace"]').click();
    await expect(page.locator('#agents-trace-content .trace-empty')).toHaveText(
      'No agent runs yet. Send a message in the Agents tab.');
    expect(await page.evaluate(() => {
      state.agentTraceRuns = [
        { ts: 1, user_message: 'older neutral record', mission_id: null, trace_events: [] },
        { ts: 2, user_message: '<b>neutral note</b>', mission_id: 'abcdefgh-extra', trace_events: [
          { t: 'warn<tag>', msg: '<img src="neutral">', data: { note: '<safe>' } },
          { type: 'error', message: 'neutral failure' },
        ] },
      ];
      const before = JSON.stringify(state.agentTraceRuns);
      const originalTime = Date.prototype.toLocaleTimeString;
      Date.prototype.toLocaleTimeString = function (...args) {
        if (args.length) throw Error('Unexpected locale arguments');
        return 'neutral trace time';
      };
      try { renderAgentsTraceTab(); }
      finally { Date.prototype.toLocaleTimeString = originalTime; }
      return JSON.stringify(state.agentTraceRuns) === before;
    })).toBe(true);
    await expect(page.locator('.trace-run')).toHaveCount(2);
    await expect(page.locator('.trace-message')).toHaveText(['<b>neutral note</b>', 'older neutral record']);
    await expect(page.locator('.trace-time')).toHaveText(['neutral trace time', 'neutral trace time']);
    await expect(page.locator('.trace-mission-id')).toHaveText('ID: abcdefgh...');
    await expect(page.locator('.trace-warn .trace-msg')).toHaveText('<img src="neutral">');
    await expect(page.locator('.trace-error .trace-msg')).toHaveText('neutral failure');
    await expect(page.locator('.trace-data')).toHaveText('{\n  "note": "<safe>"\n}');
    await expect(page.locator('#agents-trace-content img, #agents-trace-content b')).toHaveCount(0);
    page.once('dialog', dialog => dialog.dismiss());
    await page.locator('#btn-clear-trace').focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('.trace-run')).toHaveCount(2);
    expect(await page.evaluate(() => state.agentTraceRuns.length)).toBe(2);
    page.once('dialog', dialog => dialog.accept());
    await page.keyboard.press('Enter');
    await expect(page.locator('#agents-trace-content .trace-empty')).toBeVisible();
    expect(await page.evaluate(() => state.agentTraceRuns)).toEqual([]);
    await expect(page.locator('#debug-output')).toContainText('Cleared agent trace logs');
  });
}
