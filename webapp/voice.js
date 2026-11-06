/* =========================================================================
 * voice.js — Speech-to-text (mic) + Text-to-speech (speaker) for the
 * ISR Mission Planner agent chat.
 *
 * STT: browser-native Web Speech API (SpeechRecognition). Zero dependencies,
 *      no API key, works in Chrome/Edge (needs a network connection for the
 *      Google recognizer that those browsers use).
 *
 * TTS: Kokoro neural voices (kokoro-js) loaded lazily from a CDN on first use,
 *      matching the reference project (BengalPirate/Phishing-Detector-AI).
 *      If Kokoro can't load (offline / air-gapped / unsupported), we fall back
 *      to the browser's built-in speechSynthesis so speech still works.
 *
 * Public API (window.ISRVoice):
 *   stt.supported                       -> bool
 *   stt.listening                       -> bool
 *   stt.toggle(onText, onState)         -> start/stop dictation
 *   stt.stop()
 *   tts.speak(text)                     -> Promise (queues + plays)
 *   tts.cancel()                        -> stop all speech immediately
 *   tts.preload()                       -> begin loading Kokoro in background
 *   tts.engineLabel()                   -> "Kokoro" | "Browser" | "loading…"
 * ========================================================================= */
(function () {
  "use strict";

  // --- Config -------------------------------------------------------------
  // jsDelivr ESM build of kokoro-js. `+esm` gives a browser-ready module with
  // its dependencies (transformers.js / onnxruntime-web) bundled.
  const KOKORO_CDN = "https://cdn.jsdelivr.net/npm/kokoro-js@1.2.1/+esm";
  const KOKORO_MODEL = "onnx-community/Kokoro-82M-v1.0-ONNX";
  const KOKORO_DTYPE = "q8"; // quantized -> smaller download, faster
  const KOKORO_VOICE = "af_heart"; // pleasant default English voice
  const STT_LANG = "en-US";

  // ElevenLabs is proxied through our own server (key stays server-side).
  const ELEVEN_ENDPOINT = "/api/tts/elevenlabs";

  // =======================================================================
  // Speech-to-text
  // =======================================================================
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition || null;

  const stt = {
    supported: !!SpeechRecognition,
    listening: false,
    _rec: null,
    _onState: null,

    toggle(onText, onState) {
      if (this.listening) {
        this.stop();
        return;
      }
      if (!this.supported) {
        if (onState) onState("unsupported");
        return;
      }

      const rec = new SpeechRecognition();
      rec.lang = STT_LANG;
      rec.interimResults = true;
      rec.continuous = false;
      rec.maxAlternatives = 1;

      let finalText = "";

      rec.onstart = () => {
        this.listening = true;
        if (onState) onState("listening");
      };
      rec.onresult = (event) => {
        let interim = "";
        finalText = "";
        for (let i = 0; i < event.results.length; i++) {
          const res = event.results[i];
          if (res.isFinal) finalText += res[0].transcript;
          else interim += res[0].transcript;
        }
        // Deliver best-so-far transcript; isFinal flagged when done.
        if (onText) onText((finalText + interim).trim(), false);
      };
      rec.onerror = (event) => {
        if (onState) onState("error", event.error);
      };
      rec.onend = () => {
        this.listening = false;
        this._rec = null;
        if (onText && finalText) onText(finalText.trim(), true);
        if (onState) onState("idle");
      };

      this._rec = rec;
      this._onState = onState;
      try {
        rec.start();
      } catch (e) {
        // start() throws if called while already active — reset defensively.
        this.listening = false;
        if (onState) onState("error", String(e));
      }
    },

    stop() {
      if (this._rec) {
        try {
          this._rec.stop();
        } catch (e) {
          /* ignore */
        }
      }
      this.listening = false;
    },
  };

  // =======================================================================
  // Text-to-speech
  // =======================================================================
  const tts = {
    enabled: false, // set by the UI toggle; speak() ignores this, caller decides
    engine: "kokoro", // "elevenlabs" | "kokoro" | "browser" (set by the UI dropdown)
    _kokoro: null, // loaded KokoroTTS instance
    _kokoroState: "cold", // cold | loading | ready | failed
    _kokoroPromise: null,
    _queue: [],
    _speaking: false,
    _audioEl: null,
    _onProgress: null,

    // Pick the synthesis engine. Switching stops any current narration.
    setEngine(id) {
      if (id === this.engine) return;
      this.cancel();
      this.engine = id === "elevenlabs" || id === "browser" ? id : "kokoro";
    },

    engineLabel() {
      if (this.engine === "elevenlabs") return "ElevenLabs";
      if (this.engine === "browser") return "Browser";
      if (this._kokoroState === "ready") return "Kokoro";
      if (this._kokoroState === "loading") return "loading…";
      if (this._kokoroState === "failed") return "Browser";
      return "Kokoro";
    },

    // Warm up the selected engine so the first reply doesn't stall. Only Kokoro
    // needs a (large) download; ElevenLabs/browser are ready on demand.
    preload() {
      if (this.engine === "kokoro") this._ensureKokoro().catch(() => {});
    },

    async _ensureKokoro() {
      if (this._kokoroState === "ready") return this._kokoro;
      if (this._kokoroState === "failed") return null;
      if (this._kokoroPromise) return this._kokoroPromise;

      this._kokoroState = "loading";
      if (this._onProgress) this._onProgress("loading");

      this._kokoroPromise = (async () => {
        try {
          const mod = await import(/* @vite-ignore */ KOKORO_CDN);
          const KokoroTTS = mod.KokoroTTS || (mod.default && mod.default.KokoroTTS);
          if (!KokoroTTS) throw new Error("KokoroTTS export not found");
          const inst = await KokoroTTS.from_pretrained(KOKORO_MODEL, {
            dtype: KOKORO_DTYPE,
            device: "wasm",
          });
          this._kokoro = inst;
          this._kokoroState = "ready";
          if (this._onProgress) this._onProgress("ready");
          return inst;
        } catch (e) {
          console.warn("[voice] Kokoro TTS unavailable, using browser voice:", e);
          this._kokoroState = "failed";
          this._kokoroPromise = null;
          if (this._onProgress) this._onProgress("failed");
          return null;
        }
      })();
      return this._kokoroPromise;
    },

    onProgress(cb) {
      this._onProgress = cb;
    },

    // Strip characters/symbols a person wouldn't say aloud. Markdown
    // formatting (**bold**, `code`, # headings), decorative glyphs (→, ✓,
    // •, —), emoji, and link syntax all make the engine read out junk like
    // "asterisk", "arrow", or "check mark" and break the conversational flow.
    _sanitizeForSpeech(text) {
      let s = String(text || "");

      // Fenced code blocks: drop entirely (nobody reads code aloud verbatim).
      s = s.replace(/```[\s\S]*?```/g, " ");
      // Inline code: keep the words, drop the backticks.
      s = s.replace(/`([^`]*)`/g, "$1");
      // Images / links: ![alt](url) and [label](url) -> alt / label.
      s = s.replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1");
      // Bare URLs -> drop (unspeakable).
      s = s.replace(/https?:\/\/\S+/g, " ");

      // Arrows read as "to" — in this planner "A -> B" / "A → B" means a route
      // leg from A to B, so that's how a person would say it aloud.
      s = s.replace(/\s*<[-=]+>\s*/g, " to "); // <->, <=> (bidirectional)
      s = s.replace(/\s*[-=]{1,}>\s*/g, " to "); // ->, -->, =>, ==>

      // Line-leading markdown markers.
      s = s.replace(/^\s{0,3}#{1,6}\s*/gm, ""); // # headings
      s = s.replace(/^\s{0,3}>\s?/gm, ""); // > blockquotes
      s = s.replace(/^\s*[-*+]\s+/gm, ""); // - / * / + bullets
      s = s.replace(/^\s*\d+\.\s+/gm, ""); // 1. numbered list markers

      // Decorative symbols -> a pause or nothing (never their spoken name).
      const SYMBOLS = [
        [/[→⇒➜➔➤►▶⟶⟹]/g, " to "], // right arrows -> "to" (route legs)
        [/[←⇐◀⟵]/g, " "],
        [/[↑↓↔↕›»]/g, " "],
        [/[✓✔☑]/g, " "], // checkmarks
        [/[✗✘☒✖×]/g, " "], // crosses
        [/[•·◦▪▫●○■□◆◇★☆]/g, " "], // bullets / markers
        [/[–—―]/g, ", "], // en/em dashes -> comma pause
        [/[|]/g, " "], // table pipes
        [/…/g, "... "],
        [/[“”„«»]/g, '"'],
        [/[‘’‚]/g, "'"],
        [/[*_~^#`]/g, ""], // leftover emphasis/formatting chars
      ];
      for (const [re, rep] of SYMBOLS) s = s.replace(re, rep);

      // Emoji & misc pictographs / dingbats / arrows blocks.
      s = s.replace(
        /[\u{1F000}-\u{1FAFF}\u{2190}-\u{21FF}\u{2300}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE00}-\u{FE0F}\u{200D}]/gu,
        " "
      );

      // Normalize whitespace; turn line breaks into sentence boundaries.
      s = s.replace(/\r\n?/g, "\n");
      s = s.replace(/\n{2,}/g, ". ");
      s = s.replace(/\n/g, ". ");
      s = s.replace(/[ \t]+/g, " ");
      // Collapse punctuation pileups created by the substitutions above.
      s = s.replace(/\s+([.,!?;:])/g, "$1");
      s = s.replace(/([.,])\s*(?:[.,]\s*)+/g, "$1 ");
      return s.trim();
    },

    // Split into sentence-ish chunks so playback can start sooner and long
    // replies stay responsive (mirrors the reference "sentence pipeline").
    // Smaller chunks = shorter per-generation main-thread stalls, which is
    // what keeps the browser from showing the "page unresponsive" dialog.
    _chunk(text) {
      const clean = String(text || "")
        .replace(/\s+/g, " ")
        .trim();
      if (!clean) return [];
      const parts = clean.match(/[^.!?\n]+[.!?]*/g) || [clean];
      // Uniform ~160-char chunks: predictable per-chunk generation time keeps
      // the producer comfortably ahead of the speaker for gap-free playback.
      const TARGET = 160;
      const chunks = [];
      let buf = "";
      for (const p of parts) {
        if ((buf + " " + p).trim().length > TARGET && buf) {
          chunks.push(buf.trim());
          buf = p;
        } else {
          buf = (buf + " " + p).trim();
        }
      }
      if (buf) chunks.push(buf.trim());
      return chunks;
    },

    // True while a reply is queued, generating, or playing.
    isSpeaking() {
      return this._speaking;
    },

    async speak(text) {
      const chunks = this._chunk(this._sanitizeForSpeech(text));
      if (!chunks.length) return;
      this.cancel(); // replace whatever is playing
      this._queue = chunks;
      this._speaking = true;

      if (this.engine === "browser") {
        this._playBrowserQueue();
        return;
      }

      if (this.engine === "elevenlabs") {
        // Cloud synth: fetches happen off the main thread, so no WASM stalls.
        await this._playPipeline((t) => this._genEleven(t), true);
        return;
      }

      // Kokoro (default). If not ready yet, this awaits the (first) load.
      const kokoro = await this._ensureKokoro();
      // A cancel() may have fired while Kokoro was loading.
      if (!this._speaking) return;
      if (kokoro) {
        await this._playPipeline((t) => this._genChunk(kokoro, t), true);
      } else {
        this._playBrowserQueue();
      }
    },

    async _genChunk(kokoro, chunk) {
      const audio = await kokoro.generate(chunk, { voice: KOKORO_VOICE });
      return audio.toBlob();
    },

    // Fetch one chunk of ElevenLabs audio from our server proxy.
    async _genEleven(chunk) {
      const resp = await fetch(ELEVEN_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: chunk }),
      });
      if (!resp.ok) {
        throw new Error("ElevenLabs proxy HTTP " + resp.status);
      }
      return await resp.blob();
    },

    async _playPipeline(genFn, fallbackToBrowser) {
      // Lead-buffered pipeline. We pre-generate LEAD chunks before the first
      // word plays (a slightly longer initial pause, by design), then keep the
      // buffer topped up while audio plays. Generating the next chunk during
      // the current chunk's playback overlaps synth with sound output (the
      // <audio> element plays off the JS thread), so once speech starts it runs
      // seamlessly with no dead air between sentences.
      const LEAD = 2;
      const texts = this._queue.slice();
      this._queue = [];
      const blobs = []; // generated audio, ready to play, indexed by chunk
      let genIndex = 0; // next text to generate
      let playIdx = 0; // next blob to play
      let started = -1; // highest chunk index that has begun playing
      let failed = false;

      const bail = () => {
        // Hand everything not yet played to the browser voice (or just stop).
        this._queue = texts.slice(started + 1);
        failed = true;
      };

      const genNext = async () => {
        if (failed || genIndex >= texts.length) return;
        try {
          blobs.push(await genFn(texts[genIndex]));
          genIndex++;
        } catch (e) {
          console.warn("[voice] TTS generate failed:", e);
          bail();
        }
      };

      const onFail = () => {
        if (fallbackToBrowser) this._playBrowserQueue();
        else this._speaking = false;
      };

      // Prime the lead buffer before speaking.
      for (let i = 0; i < LEAD && this._speaking && !failed; i++) {
        await genNext();
      }
      if (failed) {
        onFail();
        return;
      }

      while (this._speaking && playIdx < blobs.length) {
        started = playIdx;
        const playPromise = this._playBlob(blobs[playIdx]);
        // Refill one slot while this chunk plays (overlaps synth with audio).
        await genNext();
        await playPromise;
        if (failed) {
          onFail();
          return;
        }
        playIdx++;
      }
      this._speaking = false;
    },

    _playBlob(blob) {
      return new Promise((resolve) => {
        const url = URL.createObjectURL(blob);
        const el = new Audio(url);
        this._audioEl = el;
        const done = () => {
          URL.revokeObjectURL(url);
          if (this._audioEl === el) this._audioEl = null;
          resolve();
        };
        el.onended = done;
        el.onerror = done;
        el.play().catch(done);
      });
    },

    _playBrowserQueue() {
      const synth = window.speechSynthesis;
      if (!synth) {
        this._speaking = false;
        return;
      }
      const speakNext = () => {
        if (!this._speaking || !this._queue.length) {
          this._speaking = false;
          return;
        }
        const chunk = this._queue.shift();
        const u = new SpeechSynthesisUtterance(chunk);
        u.rate = 1.0;
        u.pitch = 1.0;
        u.onend = speakNext;
        u.onerror = speakNext;
        synth.speak(u);
      };
      speakNext();
    },

    cancel() {
      this._speaking = false;
      this._queue = [];
      if (this._audioEl) {
        try {
          this._audioEl.pause();
        } catch (e) {
          /* ignore */
        }
        this._audioEl = null;
      }
      if (window.speechSynthesis) {
        try {
          window.speechSynthesis.cancel();
        } catch (e) {
          /* ignore */
        }
      }
    },
  };

  window.ISRVoice = { stt, tts };
})();
