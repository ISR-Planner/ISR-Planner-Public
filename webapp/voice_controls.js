// Voice DOM wiring only; speech engines remain in voice.js.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ISRVoiceControls = factory();
})(globalThis, function () {
  'use strict';

  function create({ document, getById, getVoice, fetchRequest }) {
    let voiceListeners = null, selectorListeners = null;
    const replyHandlers = new WeakMap();
    function listen(listeners, element, type, handler) {
      element.addEventListener(type, handler);
      listeners.push([element, type, handler]);
    }
    function dispose() {
      for (const listeners of [voiceListeners, selectorListeners]) {
        listeners?.forEach(([element, type, handler]) => element.removeEventListener(type, handler));
      }
      voiceListeners = null;
      selectorListeners = null;
    }
    function disposeReplyVoice(qaBlock) {
      const button = qaBlock?.querySelector('.qa-speak-btn');
      if (!button) return;
      const handler = replyHandlers.get(button);
      if (handler && button.onclick === handler) button.onclick = null;
      replyHandlers.delete(button);
    }

    function speakRepliesEnabled() {
      const t = document.getElementById("speak-replies-toggle");
      return !!(t && t.checked);
    }

    // Attach the per-message 🔊 replay button and, if the toggle is on, speak now.
    function wireReplyVoice(qaBlock, replyText) {
      if (!getVoice() || !replyText) return;
      const btn = qaBlock.querySelector(".qa-speak-btn");
      if (btn) {
        btn.style.display = "";
        // Toggle: click to play this reply, click again (or while it's talking)
        // to stop. Lets the user interrupt narration from the message itself.
        const handler = () => {
          const tts = getVoice().tts;
          if (tts.isSpeaking()) tts.cancel();
          else tts.speak(replyText);
        };
        btn.onclick = handler;
        replyHandlers.set(btn, handler);
      }
      if (speakRepliesEnabled()) {
        getVoice().tts.speak(replyText);
      }
    }

    function attachVoiceHandlers() {
      if (voiceListeners !== null) return;
      voiceListeners = [];
      const micBtn = getById("btn-agent-mic");
      const inputEl = getById("agent-input");
      const speakToggle = getById("speak-replies-toggle");

      // Hide mic if the browser can't do speech recognition.
      if (micBtn && (!getVoice() || !getVoice().stt.supported)) {
        micBtn.style.display = "none";
        micBtn.title = "Voice input not supported in this browser (try Chrome/Edge)";
      } else if (micBtn && inputEl) {
        // Text present in the box when the current dictation session begins.
        // Everything STT produces this session is appended after it, so pressing
        // the mic again keeps adding to what's already there (until send clears it).
        let sttBase = "";
        listen(voiceListeners, micBtn, "click", () => {
          // Pressing the mic interrupts any in-progress narration so the user
          // can talk over / cut off the assistant.
          if (getVoice().tts) getVoice().tts.cancel();

          // Starting a new session (not stopping one): snapshot existing text so
          // dictation is additive rather than wiping typed/earlier-dictated text.
          if (!getVoice().stt.listening) {
            sttBase = inputEl.value.trim();
          }

          getVoice().stt.toggle(
            (text, isFinal) => {
              // Append this session's transcript to the pre-existing text.
              const sep = sttBase && text ? " " : "";
              inputEl.value = sttBase + sep + text;
              if (isFinal) inputEl.focus();
            },
            (stateName) => {
              micBtn.classList.toggle("listening", stateName === "listening");
              if (stateName === "listening") micBtn.title = "Listening… click to stop";
              else micBtn.title = "Dictate with your voice";
            }
          );
        });
      }

      // When the user first turns on "Speak replies", start loading the selected
      // engine so the first reply doesn't stall (mainly the Kokoro download).
      if (speakToggle && getVoice()) {
        listen(voiceListeners, speakToggle, "change", () => {
          if (speakToggle.checked) {
            getVoice().tts.preload();
          } else {
            getVoice().tts.cancel(); // stop any in-progress narration
          }
        });
      }

      // Voice-engine dropdown: populate from the server (ElevenLabs only appears
      // when a key is configured), pick the server's default, and switch on change.
      attachTtsEngineSelector();
    }

    function attachTtsEngineSelector() {
      const sel = document.getElementById("tts-engine");
      if (!sel || !getVoice()) return;
      if (selectorListeners !== null) return;
      selectorListeners = [];

      fetchRequest("/api/tts/engines")
        .then((r) => r.json())
        .then((data) => {
          const engines = (data && data.engines) || [];
          if (!engines.length) return;
          sel.innerHTML = "";
          for (const e of engines) {
            const opt = document.createElement("option");
            opt.value = e.id;
            opt.textContent = e.label + (e.configured ? "" : " — not configured");
            opt.disabled = !e.configured;
            sel.appendChild(opt);
          }
          const def = (data && data.default) || "kokoro";
          sel.value = def;
          getVoice().tts.setEngine(def);
        })
        .catch(() => {
          // Server unreachable — keep the static Kokoro option as-is.
          getVoice().tts.setEngine("kokoro");
        });

      listen(selectorListeners, sel, "change", () => {
        getVoice().tts.setEngine(sel.value); // setEngine() cancels any narration
        getVoice().tts.preload(); // warm up the newly-selected engine
      });
    }

    return { speakRepliesEnabled, wireReplyVoice, attachVoiceHandlers,
      attachTtsEngineSelector, dispose, disposeReplyVoice };
  }

  return { create };
});
