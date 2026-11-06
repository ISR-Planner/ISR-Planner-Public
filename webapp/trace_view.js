// Trace display only: supplied records remain owned by the application.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ISRTraceView = factory();
})(globalThis, function () {
  'use strict';

  function create({ getContainer, escapeHtml, formatTime }) {
    function render(runs) {
      const container = getContainer();
      if (!container) return;

      if (!runs || runs.length === 0) {
        container.innerHTML = '<div class="trace-empty">No agent runs yet. Send a message in the Agents tab.</div>';
        return;
      }

      let html = '';

      // Show runs in reverse order (newest first)
      for (let i = runs.length - 1; i >= 0; i--) {
        const run = runs[i];
        const time = formatTime(run.ts);

        html += `<div class="trace-run">`;
        html += `<div class="trace-run-header">`;
        html += `<span class="trace-time">${escapeHtml(time)}</span>`;
        html += `<span class="trace-message">${escapeHtml(run.user_message.substring(0, 50))}${run.user_message.length > 50 ? '...' : ''}</span>`;
        if (run.mission_id) {
          html += `<span class="trace-mission-id">ID: ${escapeHtml(run.mission_id.substring(0, 8))}...</span>`;
        }
        html += `</div>`;

        html += `<div class="trace-events">`;
        for (const evt of run.trace_events) {
          const evtType = evt.t || evt.type || '';
          const evtMsg = evt.msg || evt.message || '';
          const levelClass = evtType.includes('error') ? 'trace-error' : evtType.includes('warn') ? 'trace-warn' : 'trace-info';
          html += `<div class="trace-event ${levelClass}">`;
          html += `<span class="trace-type">${escapeHtml(evtType)}</span>`;
          html += `<span class="trace-msg">${escapeHtml(evtMsg)}</span>`;
          if (evt.data) {
            html += `<details class="trace-data-details"><summary>data</summary><pre class="trace-data">${escapeHtml(JSON.stringify(evt.data, null, 2))}</pre></details>`;
          }
          html += `</div>`;
        }
        html += `</div></div>`;
      }

      container.innerHTML = html;
    }

    return { render };
  }

  return { create };
});
