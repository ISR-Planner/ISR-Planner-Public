// Inert construction only; the application retains startup and compatibility bindings.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ISRFrontendUI = factory();
})(globalThis, function () {
  'use strict';

  function create({ document, factories, getById, escapeHtml, getVoice, fetchRequest,
    formatDebugTime, formatTraceTime, formatChatTime, createMessageId }) {
    const domHelpers = factories.dom.create(document);
    const debugOutput = factories.debugOutput.create({
      getOutput: () => getById('debug-output'),
      formatTime: formatDebugTime,
    });
    const voiceControls = factories.voiceControls.create({ document, getById, getVoice, fetchRequest });
    const traceView = factories.traceView.create({
      getContainer: () => getById('agents-trace-content'),
      escapeHtml,
      formatTime: formatTraceTime,
    });
    const chatView = factories.chatView.create({
      document,
      getHistory: () => getById('agent-chat-history'),
      escapeHtml,
      formatTime: formatChatTime,
      createMessageId,
    });
    const fleetDrawers = factories.fleetDrawers.create({ document, getById });
    const memoryView = factories.memoryView.create({
      getList: () => getById('memory-list'),
      escapeHtml,
    });
    return { domHelpers, debugOutput, voiceControls, traceView, chatView, fleetDrawers, memoryView };
  }

  return { create };
});
