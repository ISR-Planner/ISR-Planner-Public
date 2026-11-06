import { test, expect } from './shell-fixture.mjs';

for (const filename of ['index.html', 'weather_preview.html']) {
  test.describe(filename, () => {
    test('loads the shell and switches every tab', async ({ shell }) => {
      await shell.open(filename);
      const { page } = shell;
      await expect(page.locator('#app-root')).toBeVisible();
      await expect(page.locator('#env-canvas')).toBeVisible();
      await expect(page.locator('#settings-modal')).toBeHidden();
      await expect(page.locator('#weather-preview-box')).toHaveCount(filename === 'weather_preview.html' ? 1 : 0);
      expect(await page.evaluate(() => ({
        capabilities: window.PageCapabilities.capabilities,
        weatherInitialized: !!window.weatherPreview,
      }))).toEqual({
        capabilities: { weatherPreview: filename === 'weather_preview.html', weatherDiagnostics: false },
        weatherInitialized: filename === 'weather_preview.html',
      });
      const tabs = ['env', 'loiter', 'debug', 'agents', 'agents-trace', 'gnn', 'compare', 'env'];
      for (const id of tabs) {
        await page.locator(`.tab-btn[data-tab="${id}"]`).click();
        await expect(page.locator('.tab-btn.active')).toHaveCount(1);
        await expect(page.locator('.tab-pane.active')).toHaveCount(1);
        await expect(page.locator(`#tab-${id}`)).toBeVisible();
        await expect(page.locator(`#tab-${id}`)).toHaveClass(/active/);
      }
    });

    test('tab controls preserve keyboard activation and support cleanup/remount', async ({ shell }) => {
      await shell.open(filename);
      const { page } = shell;
      const assets = page.locator('.tab-btn[data-tab="loiter"]');
      await assets.focus();
      await page.keyboard.press('Enter');
      await expect(page.locator('#tab-loiter')).toBeVisible();
      const debug = page.locator('.tab-btn[data-tab="debug"]');
      await debug.focus();
      await page.keyboard.press('Space');
      await expect(page.locator('#tab-debug')).toBeVisible();
      await expect(debug).toBeFocused();
      await page.evaluate(() => { tabControls.dispose(); tabControls.dispose(); });
      await assets.click();
      await expect(page.locator('#tab-debug')).toBeVisible();
      await page.evaluate(() => { attachTabSwitching(); attachTabSwitching(); });
      await assets.click();
      await expect(page.locator('#tab-loiter')).toBeVisible();
      await expect(page.locator('.tab-btn.active')).toHaveCount(1);
      await expect(page.locator('.tab-pane.active')).toHaveCount(1);
    });

    test('modal preserves backdrop behavior and supports cleanup/remount', async ({ shell }) => {
      await shell.open(filename);
      const { page } = shell;
      const modal = page.locator('#settings-modal');
      await page.locator('#btn-settings').focus();
      await page.keyboard.press('Enter');
      await expect(modal).toBeVisible();
      await page.locator('#llm-provider').click();
      await page.keyboard.press('Escape');
      await expect(modal).toBeVisible();
      await modal.click({ position: { x: 5, y: 5 } });
      await expect(modal).toBeHidden();
      await page.evaluate(() => {
        LLMSettings.getModalControl().dispose();
        LLMSettings.getModalControl().dispose();
      });
      await page.locator('#btn-settings').click();
      await expect(modal).toBeHidden();
      await page.evaluate(() => {
        LLMSettings.getModalControl().mount();
        LLMSettings.getModalControl().mount();
      });
      await page.locator('#btn-settings').click();
      await expect(modal).toBeVisible();
      await page.locator('#btn-close-settings').focus();
      await page.keyboard.press('Space');
      await expect(modal).toBeHidden();
    });

    test('settings form handlers mount once, dispose, and remount on replacement elements', async ({ shell }) => {
      await shell.open(filename);
      const result = await shell.page.evaluate(() => {
        const counts = { provider: 0, test: 0, save: 0, models: 0 };
        const methods = {
          onProviderChange: 'provider', testConnection: 'test',
          saveSettings: 'save', loadOllamaModels: 'models',
        };
        const originals = {};
        for (const [method, key] of Object.entries(methods)) {
          originals[method] = LLMSettings[method];
          LLMSettings[method] = () => { counts[key]++; };
        }
        const specs = [
          ['llm-provider', 'change'], ['btn-test-connection', 'click'],
          ['btn-save-settings', 'click'], ['btn-refresh-ollama', 'click'],
          ['ollama-host', 'change'],
        ];
        const elements = specs.map(([id]) => document.getElementById(id));
        const dispatch = nodes => nodes.map((node, index) =>
          node.dispatchEvent(new Event(specs[index][1], { cancelable: true })));
        try {
          LLMSettings.setupEventListeners();
          LLMSettings.setupEventListeners();
          const defaults = dispatch(elements);
          const mounted = { ...counts };
          LLMSettings.disposeEventListeners();
          LLMSettings.disposeEventListeners();
          dispatch(elements);
          const disposed = { ...counts };
          const replacements = elements.map(element => {
            const replacement = element.cloneNode(true);
            element.replaceWith(replacement);
            return replacement;
          });
          LLMSettings.setupEventListeners();
          dispatch(elements);
          const detached = { ...counts };
          dispatch(replacements);
          return { defaults, mounted, disposed, detached, remounted: { ...counts } };
        } finally {
          for (const [method, original] of Object.entries(originals)) LLMSettings[method] = original;
        }
      });
      const once = { provider: 1, test: 1, save: 1, models: 2 };
      expect(result.mounted).toEqual(once);
      expect(result.disposed).toEqual(once);
      expect(result.detached).toEqual(once);
      expect(result.remounted).toEqual({ provider: 2, test: 2, save: 2, models: 4 });
      expect(result.defaults).toEqual([true, true, true, false, true]);
      expect(shell.connectionCalls).toEqual([]);
      await shell.page.locator('#btn-settings').click();
      await expect(shell.page.locator('#settings-modal')).toBeVisible();
      await shell.page.locator('#btn-close-settings').click();
      await expect(shell.page.locator('#settings-modal')).toBeHidden();
    });

    test('shared DOM wrappers preserve literal text and resolve replacement elements', async ({ shell }) => {
      await shell.open(filename);
      const result = await shell.page.evaluate(() => {
        const element = document.createElement('div');
        element.id = 'neutral:dom [fixture]';
        document.body.appendChild(element);
        try {
          const found = $(element.id) === element;
          setText(element.id, '<b>literal</b>');
          const literal = element.textContent;
          const childCount = element.children.length;
          const replacement = document.createElement('div');
          replacement.id = element.id;
          element.replaceWith(replacement);
          try {
            setText(replacement.id, 0);
            const zero = replacement.textContent;
            setText(replacement.id, null);
            const cleared = replacement.textContent;
            setText('neutral-missing', 'ignored');
            return { found, literal, childCount, zero, cleared,
              replacementFound: $(replacement.id) === replacement,
              missing: $('neutral-missing') };
          } finally { replacement.remove(); }
        } finally { element.remove(); }
      });
      expect(result).toEqual({ found: true, literal: '<b>literal</b>', childCount: 0,
        zero: '0', cleared: '', replacementFound: true, missing: null });
    });

    test('debug writer preserves locale formatting, literal text, and lazy element lookup', async ({ shell }) => {
      await shell.open(filename);
      const result = await shell.page.evaluate(() => {
        const output = document.getElementById('debug-output');
        const originalTime = Date.prototype.toLocaleTimeString;
        let clockCalls = 0;
        Date.prototype.toLocaleTimeString = function (...args) {
          if (args.length) throw Error('Unexpected locale arguments');
          clockCalls++;
          return 'neutral-local-time';
        };
        let replacement;
        try {
          output.textContent = 'existing';
          const returned = appendDebugLine('<b>literal</b>');
          appendDebugLine('newest');
          const text = output.textContent;
          const children = output.children.length;
          replacement = output.cloneNode(false);
          replacement.textContent = 'replacement';
          output.replaceWith(replacement);
          appendDebugLine(0);
          const replacementText = replacement.textContent;
          replacement.remove();
          appendDebugLine({ toString() { throw Error('Missing element must skip coercion'); } });
          return { text, children, replacementText, clockCalls, returnedUndefined: returned === undefined };
        } finally {
          Date.prototype.toLocaleTimeString = originalTime;
          if (replacement?.isConnected) replacement.replaceWith(output);
          else if (!output.isConnected) document.getElementById('tab-debug').append(output);
        }
      });
      expect(result).toEqual({
        text: '[neutral-local-time] newest\n[neutral-local-time] <b>literal</b>\nexisting',
        children: 0, replacementText: '[neutral-local-time] 0\nreplacement',
        clockCalls: 3, returnedUndefined: true,
      });
    });

    test('opens, closes, and persists settings across reload', async ({ shell }) => {
      await shell.open(filename);
      const { page } = shell;
      await page.locator('#btn-settings').click();
      await expect(page.locator('#settings-modal')).toBeVisible();
      await page.locator('#btn-close-settings').click();
      await expect(page.locator('#settings-modal')).toBeHidden();
      await page.locator('#btn-settings').click();
      await page.locator('#llm-model').selectOption('smoke-b');
      await page.locator('#btn-save-settings').click();
      await expect(page.locator('#settings-modal')).toBeHidden();
      expect(await page.evaluate(() => ({
        provider: localStorage.getItem('isr_llm_provider'),
        model: localStorage.getItem('isr_llm_model_smoke-provider'),
      }))).toEqual({ provider: 'smoke-provider', model: 'smoke-b' });
      await page.reload({ waitUntil: 'load' });
      await page.locator('#btn-settings').click();
      await expect(page.locator('#settings-modal')).toBeVisible();
      await expect(page.locator('#llm-provider')).toHaveValue('smoke-provider');
      await expect(page.locator('#llm-model')).toHaveValue('smoke-b');
      expect(shell.connectionCalls).toEqual([]);
    });

    test('shows a service error and recovers on retry', async ({ shell }) => {
      await shell.open(filename);
      const { page } = shell;
      shell.setConnectionResult({ success: false, message: 'Mock service unavailable' });
      await page.locator('#btn-settings').click();
      await page.locator('#btn-test-connection').click();
      await expect(page.locator('#connection-status')).toHaveClass('connection-status error');
      await expect(page.locator('#connection-status')).toHaveText('Mock service unavailable');
      shell.setConnectionResult({ success: true, message: 'Mock service connected' });
      await page.locator('#btn-test-connection').click();
      await expect(page.locator('#connection-status')).toHaveClass('connection-status success');
      await expect(page.locator('#connection-status')).toHaveText('Mock service connected');
      expect(shell.connectionCalls).toHaveLength(2);
      expect(shell.connectionCalls[0]).toEqual({ provider: 'smoke-provider', model: 'smoke-a', base_url: null });
    });

    test('preserves local-provider host and model preferences', async ({ shell }) => {
      await shell.open(filename);
      const { page } = shell;
      await page.locator('#btn-settings').click();
      await page.locator('#llm-provider').selectOption('ollama');
      await expect(page.locator('#ollama-host')).toHaveValue('http://localhost:11434');
      await expect(page.locator('#llm-model')).toHaveValue('local-a');
      await page.locator('#ollama-host').fill('http://example.test:11434');
      await page.locator('#ollama-host').press('Tab');
      await expect(page.locator('#llm-model')).toHaveValue('local-a');
      await page.locator('#llm-model').selectOption('local-b');
      await page.locator('#btn-save-settings').click();
      await expect(page.locator('#settings-modal')).toBeHidden();
      expect(await page.evaluate(() => ({
        provider: localStorage.getItem('isr_llm_provider'),
        model: localStorage.getItem('isr_llm_model_ollama'),
        host: localStorage.getItem('isr_ollama_host'),
      }))).toEqual({ provider: 'ollama', model: 'local-b', host: 'http://example.test:11434' });
      await page.reload({ waitUntil: 'load' });
      await page.locator('#btn-settings').click();
      await expect(page.locator('#llm-provider')).toHaveValue('ollama');
      await expect(page.locator('#llm-model')).toHaveValue('local-b');
      await expect(page.locator('#ollama-host')).toHaveValue('http://example.test:11434');
      await page.locator('#btn-test-connection').click();
      await expect(page.locator('#connection-status')).toHaveClass('connection-status success');
      expect(shell.connectionCalls).toEqual([
        { provider: 'ollama', model: 'local-b', base_url: 'http://example.test:11434' },
      ]);
    });

    test('ignores stale selections and removes only legacy credential entries', async ({ shell }) => {
      const { page } = shell;
      await page.addInitScript(() => {
        localStorage.setItem('isr_llm_provider', 'removed-provider');
        localStorage.setItem('isr_llm_model_smoke-provider', 'removed-model');
        localStorage.setItem('isr_llm_key_retired', 'synthetic-test-value');
        localStorage.setItem('unrelated-preference', 'keep');
      });
      await shell.open(filename);
      await page.locator('#btn-settings').click();
      await expect(page.locator('#llm-provider')).toHaveValue('smoke-provider');
      await expect(page.locator('#llm-model')).toHaveValue('smoke-a');
      expect(await page.evaluate(() => localStorage.getItem('isr_llm_key_retired'))).toBeNull();
      expect(await page.evaluate(() => localStorage.getItem('unrelated-preference'))).toBe('keep');
    });

    test('reports unavailable storage and keeps unsaved settings open', async ({ shell }) => {
      await shell.open(filename);
      const { page } = shell;
      await page.evaluate(() => {
        Object.defineProperty(window, 'localStorage', {
          configurable: true,
          get() { throw new DOMException('Storage blocked by test', 'SecurityError'); },
        });
      });
      await page.locator('#btn-settings').click();
      await page.locator('#llm-provider').selectOption('smoke-provider');
      await expect(page.locator('#llm-model')).toHaveValue('smoke-a');
      await page.locator('#llm-model').selectOption('smoke-b');
      await page.locator('#btn-save-settings').click();
      await expect(page.locator('#settings-modal')).toBeVisible();
      await expect(page.locator('#connection-status')).toHaveClass('connection-status error');
      await expect(page.locator('#connection-status')).toHaveText(
        'Settings could not be saved. Browser storage is unavailable.');
      await page.locator('#btn-close-settings').click();
      await expect(page.locator('#settings-modal')).toBeHidden();
      expect(shell.connectionCalls).toEqual([]);
    });

    test('shows local model discovery failure and refreshes successfully', async ({ shell }) => {
      await shell.open(filename);
      const { page } = shell;
      let result = { success: false, models: [], message: 'Neutral model discovery unavailable' };
      await page.route('**/api/llm/ollama/models*', route => route.fulfill({ json: result }));
      await page.locator('#btn-settings').click();
      await page.locator('#llm-provider').selectOption('ollama');
      await expect(page.locator('#llm-model')).toHaveText('No models found');
      await expect(page.locator('#connection-status')).toHaveText('Neutral model discovery unavailable');
      result = { success: true, models: [{ id: 'local-recovered', name: 'Recovered local model' }] };
      await page.locator('#btn-refresh-ollama').click();
      await expect(page.locator('#llm-model')).toHaveValue('local-recovered');
      await expect(page.locator('#llm-model')).toHaveText('Recovered local model');
    });

    test('shows a transport failure without breaking the modal', async ({ shell }) => {
      await shell.open(filename);
      const { page } = shell;
      shell.setConnectionResult('abort');
      await page.locator('#btn-settings').click();
      await page.locator('#btn-test-connection').click();
      await expect(page.locator('#connection-status')).toHaveClass('connection-status error');
      await expect(page.locator('#connection-status')).toContainText('Connection failed:');
      await page.locator('#btn-close-settings').click();
      await expect(page.locator('#settings-modal')).toBeHidden();
      expect(shell.connectionCalls).toHaveLength(1);
    });
  });
}
