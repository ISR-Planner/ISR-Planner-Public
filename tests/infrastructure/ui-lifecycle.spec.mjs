import { test, expect } from './shell-fixture.mjs';

for (const filename of ['index.html', 'weather_preview.html']) {
  test(`${filename}: narrow listener disposal preserves foreign handlers through keyboard remount`, async ({ shell }) => {
    const { page } = shell;
    await shell.open(filename);
    await page.evaluate(() => {
      window.neutralLifecycle = { tabs: 0, settings: 0, old: 0, replacement: 0 };
      const button = document.querySelector('.tab-btn[data-tab="debug"]');
      window.foreignTabHandler = () => neutralLifecycle.tabs++;
      button.addEventListener('click', foreignTabHandler);
      tabControls.dispose(); tabControls.dispose();
    });
    const debug = page.locator('.tab-btn[data-tab="debug"]');
    await debug.focus(); await page.keyboard.press('Enter');
    await expect(page.locator('#tab-env')).toBeVisible();
    expect(await page.evaluate(() => neutralLifecycle.tabs)).toBe(1);
    await page.evaluate(() => { attachTabSwitching(); attachTabSwitching(); });
    await debug.focus(); await page.keyboard.press('Space');
    await expect(page.locator('#tab-debug')).toBeVisible();
    expect(await page.evaluate(() => neutralLifecycle.tabs)).toBe(2);

    await page.evaluate(() => {
      window.originalNeutralTestConnection = LLMSettings.testConnection;
      LLMSettings.testConnection = () => neutralLifecycle.settings++;
      window.oldNeutralTestButton = document.getElementById('btn-test-connection');
      window.foreignSettingsHandler = () => neutralLifecycle.old++;
      oldNeutralTestButton.addEventListener('click', foreignSettingsHandler);
      LLMSettings.openModal();
    });
    const testButton = page.locator('#btn-test-connection');
    try {
      await testButton.focus(); await page.keyboard.press('Enter');
      expect(await page.evaluate(() => ({ ...neutralLifecycle }))).toEqual({ tabs: 2, settings: 1, old: 1, replacement: 0 });
      await page.evaluate(() => { LLMSettings.disposeEventListeners(); LLMSettings.disposeEventListeners(); });
      await expect(page.locator('#settings-modal')).toBeVisible();
      await testButton.focus(); await page.keyboard.press('Enter');
      expect(await page.evaluate(() => ({ ...neutralLifecycle }))).toEqual({ tabs: 2, settings: 1, old: 2, replacement: 0 });
      await page.evaluate(() => {
        const replacement = oldNeutralTestButton.cloneNode(true);
        replacement.addEventListener('click', () => neutralLifecycle.replacement++);
        oldNeutralTestButton.replaceWith(replacement);
        LLMSettings.setupEventListeners(); LLMSettings.setupEventListeners();
        oldNeutralTestButton.click();
      });
      expect(await page.evaluate(() => ({ ...neutralLifecycle }))).toEqual({ tabs: 2, settings: 1, old: 3, replacement: 0 });
      await testButton.focus(); await page.keyboard.press('Space');
      expect(await page.evaluate(() => ({ ...neutralLifecycle }))).toEqual({ tabs: 2, settings: 2, old: 3, replacement: 1 });
      await page.locator('#btn-close-settings').focus(); await page.keyboard.press('Enter');
      await expect(page.locator('#settings-modal')).toBeHidden();
      expect(shell.connectionCalls).toEqual([]);
      expect(shell.requests.some(request => /^(POST|DELETE) /.test(request))).toBe(false);
    } finally {
      await page.evaluate(() => {
        LLMSettings.testConnection = originalNeutralTestConnection;
        oldNeutralTestButton.removeEventListener('click', foreignSettingsHandler);
        document.querySelector('.tab-btn[data-tab="debug"]').removeEventListener('click', foreignTabHandler);
      });
    }
  });
}
