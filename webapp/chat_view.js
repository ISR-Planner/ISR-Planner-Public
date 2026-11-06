// Chat presentation only; callers own insertion of question cards and responses.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ISRChatView = factory();
})(globalThis, function () {
  'use strict';

  function create({ document, getHistory, escapeHtml, formatTime, createMessageId }) {
    function createQABlock(question) {
      const timestamp = formatTime();

      const block = document.createElement("div");
      block.className = "qa-block";
      block.innerHTML = `
    <div class="qa-question">
      <span class="qa-label">YOU [${timestamp}]</span>
      <span class="qa-text">${escapeHtml(question)}</span>
    </div>
    <div class="qa-response-wrapper">
      <div class="qa-response-label">
        <span>AGENT RESPONSE</span>
        <button class="qa-speak-btn" type="button" title="Read this reply aloud" aria-label="Read this reply aloud" style="display:none;">🔊</button>
      </div>
      <div class="qa-response thinking">
        🤔 Thinking...
      </div>
    </div>
  `;
      return block;
    }

    function appendChatMessage(content) {
      // Legacy function - kept for system messages
      const chatHistory = getHistory();
      if (!chatHistory) return null;

      const msgId = createMessageId();
      const msgDiv = document.createElement("div");
      msgDiv.id = msgId;
      msgDiv.className = "agent-message system-message";
      msgDiv.innerHTML = content;

      chatHistory.appendChild(msgDiv);
      chatHistory.scrollTop = chatHistory.scrollHeight;
      return msgId;
    }

    function removeChatMessage(msgId) {
      if (!msgId) return;
      const el = document.getElementById(msgId);
      if (el) el.remove();
    }

    return { createQABlock, appendChatMessage, removeChatMessage };
  }

  return { create };
});
