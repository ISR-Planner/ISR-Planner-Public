import { test, expect } from './shell-fixture.mjs';

for (const filename of ['index.html', 'weather_preview.html']) {
  test(`${filename}: shared text escaping preserves native DOM serialization`, async ({ shell }) => {
    const { page } = shell;
    await shell.open(filename);
    const result = await page.evaluate(() => {
      const values = [
        ['markup', '<b>neutral</b> & text'], ['quotes', `"quoted" 'text'`],
        ['unicode', 'é 🌦\nnext line'], ['empty', ''], ['null', null], ['undefined', undefined],
        ['zero', 0], ['false', false], ['custom', { toString() { return '<custom>'; } }],
      ];
      const original = document.createElement;
      const elements = [];
      const before = document.body.childElementCount;
      document.createElement = function (...args) {
        const node = original.apply(this, args); elements.push(node); return node;
      };
      try {
        const output = values.map(([label, value]) => [label, escapeHtml(value), domHelpers.escapeHtml(value)]);
        const error = new Error('neutral conversion failure');
        const bad = { toString() { throw error; } };
        const propagated = [escapeHtml, domHelpers.escapeHtml].map(escape => {
          try { escape(bad); return false; } catch (caught) { return caught === error; }
        });
        return { output, propagated, created: elements.length,
          detached: elements.every(node => node.tagName === 'DIV' && !node.isConnected),
          bodyUnchanged: document.body.childElementCount === before };
      } finally { document.createElement = original; }
    });
    const expected = [
      ['markup', '&lt;b&gt;neutral&lt;/b&gt; &amp; text'], ['quotes', `"quoted" 'text'`],
      ['unicode', 'é 🌦\nnext line'], ['empty', ''], ['null', ''], ['undefined', ''],
      ['zero', '0'], ['false', 'false'], ['custom', '&lt;custom&gt;'],
    ].map(([label, text]) => [label, text, text]);
    expect(result).toEqual({ output: expected, propagated: [true, true], created: 20,
      detached: true, bodyUnchanged: true });
    expect(shell.requests.some(request => request.startsWith('POST '))).toBe(false);
  });
}
