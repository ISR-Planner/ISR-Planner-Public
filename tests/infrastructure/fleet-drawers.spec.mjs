import { test, expect } from './shell-fixture.mjs';

for (const filename of ['index.html', 'weather_preview.html']) {
  test(`${filename}: drawer disclosure supports keyboard, disabled controls and owned cleanup`, async ({ shell }) => {
    const { page } = shell;
    await shell.open(filename);
    await page.locator('.tab-btn[data-tab="loiter"]').click();
    // Exercise disclosure with neutral DOM controls; configuration policy is outside this test.
    await page.evaluate(() => {
      for (const btn of document.querySelectorAll('[data-drone-toggle]')) btn.disabled = false;
      for (let id = 1; id <= 5; id++) setDroneDrawer(String(id), false);
      attachFleetDrawers(); attachFleetDrawers();
    });
    const first = page.locator('[data-drone-toggle="1"]');
    const second = page.locator('[data-drone-toggle="2"]');
    const collapse = page.locator('#btn-collapse-drones');
    // Keep the page's edit gate intact; exercise native keyboard disclosure only.
    const activate = async locator => { await locator.focus(); await page.keyboard.press('Enter'); };
    await expect(collapse).toBeHidden();
    await first.focus(); await page.keyboard.press('Enter');
    await expect(first).toHaveAttribute('aria-expanded', 'true');
    await expect(first).toHaveText('▾');
    await expect(page.locator('#drawer-d1')).toBeVisible();
    await expect(page.locator('.fleet-row[data-drone-id="1"]')).toHaveClass(/open/);
    await expect(first).toBeFocused();
    await second.focus(); await page.keyboard.press('Space');
    await expect(page.locator('#drawer-d2')).toBeVisible();
    await expect(page.locator('#drawer-d1')).toBeVisible();
    await collapse.focus(); await page.keyboard.press('Enter');
    await expect(page.locator('.fleet-drawer:not([hidden])')).toHaveCount(0);
    await expect(first).toHaveAttribute('aria-expanded', 'false');
    await expect(first).toHaveText('▸');
    await expect(collapse).toBeHidden();
    await first.evaluate(btn => { btn.disabled = true; btn.click(); });
    await expect(page.locator('#drawer-d1')).toBeHidden();
    await first.evaluate(btn => { btn.disabled = false; });
    await activate(first);
    await page.evaluate(() => { fleetDrawers.dispose(); fleetDrawers.dispose(); });
    await activate(first); await activate(collapse);
    await expect(page.locator('#drawer-d1')).toBeVisible();
    await page.evaluate(() => {
      const old = document.querySelector('[data-drone-toggle="1"]');
      window.oldDrawerToggle = old;
      old.replaceWith(old.cloneNode(true));
      const collapse = document.getElementById('btn-collapse-drones');
      window.oldCollapse = collapse;
      collapse.replaceWith(collapse.cloneNode(true));
      attachFleetDrawers(); attachFleetDrawers();
    });
    await activate(first);
    await expect(page.locator('#drawer-d1')).toBeHidden();
    await activate(first);
    await page.evaluate(() => { oldDrawerToggle.click(); oldCollapse.click(); });
    await expect(page.locator('#drawer-d1')).toBeVisible();
    await activate(collapse);
    await expect(page.locator('#drawer-d1')).toBeHidden();
    await page.evaluate(() => {
      fleetDrawers.dispose();
      const old = document.getElementById('drawer-d1');
      old.replaceWith(old.cloneNode(true));
      setDroneDrawer('1', true);
    });
    await expect(page.locator('#drawer-d1')).toBeVisible();
    await expect(first).toHaveAttribute('aria-expanded', 'true');
    expect(shell.requests.some(request => request.startsWith('POST '))).toBe(false);
  });
}
