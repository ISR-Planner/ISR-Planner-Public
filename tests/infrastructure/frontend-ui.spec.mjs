import { readFile } from 'node:fs/promises';
import { test, expect } from './shell-fixture.mjs';

for (const filename of ['index.html', 'weather_preview.html']) {
  test(`${filename}: general UI composition constructs once and supplies existing delegates`, async ({ shell }) => {
    const { page } = shell;
    await page.route('**/frontend_ui.js*', async route => route.fulfill({
      contentType: 'application/javascript',
      body: await readFile(new URL('../../webapp/frontend_ui.js', import.meta.url), 'utf8') + `
        window.neutralUIConstruction = {calls: 0, counts: {}};
        const observedCreate = ISRFrontendUI.create;
        ISRFrontendUI.create = function(options) {
          neutralUIConstruction.calls++;
          const factories = Object.fromEntries(Object.entries(options.factories).map(([name, factory]) => [name, {
            create(...args) {
              neutralUIConstruction.counts[name] = (neutralUIConstruction.counts[name] || 0) + 1;
              return factory.create(...args);
            }
          }]));
          const result = observedCreate({...options, factories});
          window.neutralUIInstances = result;
          return result;
        };
      `,
    }));
    await shell.open(filename);
    expect(await page.evaluate(() => ({ ...neutralUIConstruction,
      keys: Object.keys(neutralUIInstances),
      same: [domHelpers, debugOutput, voiceControls, traceView, chatView, fleetDrawers, memoryView]
        .every((value, i) => value === Object.values(neutralUIInstances)[i]),
      lexical: Object.keys(neutralUIInstances).every(key => !Object.hasOwn(window, key)),
      startupAPI: ['init', 'mount', 'dispose'].some(key => key in neutralUIInstances),
    }))).toEqual({ calls: 1, counts: { dom: 1, debugOutput: 1, voiceControls: 1,
      traceView: 1, chatView: 1, fleetDrawers: 1, memoryView: 1 },
      keys: ['domHelpers', 'debugOutput', 'voiceControls', 'traceView', 'chatView', 'fleetDrawers', 'memoryView'],
      same: true, lexical: true, startupAPI: false });
    expect(await page.evaluate(() => {
      const calls = [], restore = [];
      const stub = (object, method, tag) => {
        const original = object[method]; restore.push(() => { object[method] = original; });
        object[method] = (...args) => { calls.push([tag, args]); return tag; };
      };
      const neutralRecords = [{ id: 1, category: 'general', content: 'neutral' }];
      const neutralBlock = {};
      const element = document.createElement('div'); element.id = 'neutral-composition-dom';
      document.body.append(element);
      try {
        const found = $(element.id) === element; setText(element.id, '<neutral>');
        stub(debugOutput, 'prepend', 'debug');
        stub(traceView, 'render', 'trace'); stub(memoryView, 'render', 'memory');
        stub(chatView, 'createQABlock', 'card'); stub(chatView, 'appendChatMessage', 'append');
        stub(chatView, 'removeChatMessage', 'remove');
        stub(fleetDrawers, 'attachFleetDrawers', 'drawers-mount'); stub(fleetDrawers, 'setDroneDrawer', 'drawer');
        stub(voiceControls, 'speakRepliesEnabled', 'speak'); stub(voiceControls, 'wireReplyVoice', 'reply');
        stub(voiceControls, 'attachVoiceHandlers', 'voice-mount'); stub(voiceControls, 'attachTtsEngineSelector', 'tts-mount');
        const results = [appendDebugLine('neutral'), renderAgentsTraceTab(), renderMemoryList(neutralRecords),
          createQABlock('question'), appendChatMessage('system'), removeChatMessage('neutral-id'),
          attachFleetDrawers(), setDroneDrawer('1', false), speakRepliesEnabled(),
          wireReplyVoice(neutralBlock, 'reply'), attachVoiceHandlers(), attachTtsEngineSelector()];
        return { found, literal: element.textContent, childCount: element.children.length, results,
          tags: calls.map(call => call[0]), traceIdentity: calls[1][1][0] === state.agentTraceRuns,
          recordIdentity: calls[2][1][0] === neutralRecords,
          replyIdentity: calls[9][1][0] === neutralBlock,
          arguments: [calls[0][1], calls[3][1], calls[4][1], calls[5][1], calls[7][1], calls[9][1][1]],
          constructions: neutralUIConstruction.calls };
      } finally { restore.reverse().forEach(fn => fn()); element.remove(); }
    })).toEqual({ found: true, literal: '<neutral>', childCount: 0,
      results: ['debug', 'trace', 'memory', 'card', 'append', 'remove', 'drawers-mount', 'drawer', 'speak', 'reply', 'voice-mount', 'tts-mount'],
      tags: ['debug', 'trace', 'memory', 'card', 'append', 'remove', 'drawers-mount', 'drawer', 'speak', 'reply', 'voice-mount', 'tts-mount'],
      traceIdentity: true, recordIdentity: true, replyIdentity: true,
      arguments: [['neutral'], ['question'], ['system'], ['neutral-id'], ['1', false], 'reply'], constructions: 1 });
    expect(shell.requests.some(request => /^(POST|DELETE) /.test(request))).toBe(false);
  });
}
