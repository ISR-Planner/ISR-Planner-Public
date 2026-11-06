(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ISRDebugOutput = factory();
})(globalThis, function () {
  'use strict';

  function create({ getOutput, formatTime }) {
    return {
      prepend(message) {
        const output = getOutput();
        if (!output) return;
        const now = formatTime();
        output.textContent = `[${now}] ${message}\n` + output.textContent;
      },
    };
  }

  return { create };
});
