import { test, expect } from './shell-fixture.mjs';

for (const filename of ['index.html', 'weather_preview.html']) {
  test(`${filename}: composed presentation uses supplied values and an isolated document`, async ({ shell }) => {
    const { page } = shell;
    await shell.open(filename);
    const result = await page.evaluate(() => {
      const hostIds = ['debug-output', 'agents-trace-content', 'agent-chat-history', 'memory-list'];
      const hostBefore = hostIds.map(id => document.getElementById(id).innerHTML);
      const isolated = document.implementation.createHTMLDocument('Neutral presentation');
      isolated.body.innerHTML = '<div id="debug-output"></div><div id="agents-trace-content"></div>' +
        '<div id="agent-chat-history"></div><div id="memory-list"></div>';
      let forbidden = 0, version = 'first', generated = 0, ui;
      const traceTimes = [];
      const unavailable = () => { forbidden++; throw Error('Presentation cannot use engine/transport'); };
      ui = ISRFrontendUI.create({
        document: isolated,
        factories: { dom: ISRDom, debugOutput: ISRDebugOutput, voiceControls: ISRVoiceControls,
          traceView: ISRTraceView, chatView: ISRChatView, fleetDrawers: ISRFleetDrawers, memoryView: ISRMemoryView },
        getById: id => isolated.getElementById(id),
        escapeHtml: value => ui.domHelpers.escapeHtml(value),
        getVoice: unavailable, fetchRequest: unavailable,
        formatDebugTime: () => version,
        formatTraceTime: ts => { traceTimes.push(ts); return `${version}:${ts}`; },
        formatChatTime: () => 'neutral time', createMessageId: () => `neutral-${++generated}`,
      });
      const run = { ts: 1, user_message: 'first <trace>', trace_events: [{ t: 'info', msg: 'first <event>' }] };
      const memory = { id: 1, category: 'general', content: 'first <memory>' };
      const records = [run], memories = [memory];
      const before = JSON.stringify([records, memories]);
      ui.traceView.render(records); ui.memoryView.render(memories);
      const first = {
        trace: isolated.querySelector('.trace-message').textContent,
        memory: isolated.querySelector('.memory-content').textContent,
        traceTime: isolated.querySelector('.trace-time').textContent,
        literal: isolated.querySelectorAll('.trace-message trace, .memory-content memory').length === 0,
        unmodified: JSON.stringify([records, memories]) === before,
      };
      const snapshot = [isolated.getElementById('agents-trace-content').innerHTML,
        isolated.getElementById('memory-list').innerHTML];
      run.ts = 2; run.user_message = 'second <trace>'; run.trace_events[0].msg = 'second <event>';
      memory.id = 2; memory.content = 'second <memory>'; version = 'second';
      const snapshotStable = snapshot[0] === isolated.getElementById('agents-trace-content').innerHTML &&
        snapshot[1] === isolated.getElementById('memory-list').innerHTML;
      const changed = JSON.stringify([records, memories]);
      ui.traceView.render(records); ui.memoryView.render(memories);
      const refreshed = {
        trace: isolated.querySelector('.trace-message').textContent,
        event: isolated.querySelector('.trace-msg').textContent,
        memory: isolated.querySelector('.memory-content').textContent,
        traceTime: isolated.querySelector('.trace-time').textContent,
        action: isolated.querySelector('.btn-delete-memory').getAttribute('onclick'),
        unmodified: JSON.stringify([records, memories]) === changed,
      };
      ui.debugOutput.prepend('<b>neutral debug</b>');
      const debug = { text: isolated.getElementById('debug-output').textContent,
        children: isolated.getElementById('debug-output').children.length };
      const card = ui.chatView.createQABlock('<i>neutral question</i>');
      const question = { detached: card.parentNode === null, text: card.querySelector('.qa-text').textContent,
        children: card.querySelector('.qa-text').children.length, time: card.querySelector('.qa-label').textContent };
      const id = ui.chatView.appendChatMessage('<strong>neutral system</strong>');
      const system = { id, tag: isolated.getElementById(id).firstElementChild.tagName,
        text: isolated.getElementById(id).textContent };
      ui.chatView.removeChatMessage(id);
      return { first, snapshotStable, refreshed, traceTimes, debug, question, system,
        removed: isolated.getElementById(id) === null, generated, forbidden,
        hostUnchanged: hostIds.every((id, i) => document.getElementById(id).innerHTML === hostBefore[i]) };
    });
    expect(result).toEqual({
      first: { trace: 'first <trace>', memory: 'first <memory>', traceTime: 'first:1', literal: true, unmodified: true },
      snapshotStable: true,
      refreshed: { trace: 'second <trace>', event: 'second <event>', memory: 'second <memory>',
        traceTime: 'second:2', action: 'deleteAgentMemory(2)', unmodified: true },
      traceTimes: [1, 2], debug: { text: '[second] <b>neutral debug</b>\n', children: 0 },
      question: { detached: true, text: '<i>neutral question</i>', children: 0, time: 'YOU [neutral time]' },
      system: { id: 'neutral-1', tag: 'STRONG', text: 'neutral system' },
      removed: true, generated: 1, forbidden: 0, hostUnchanged: true,
    });
    expect(shell.requests.some(request => /^(POST|DELETE) /.test(request))).toBe(false);
  });
}
