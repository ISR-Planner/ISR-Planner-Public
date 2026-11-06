import { test, expect } from './shell-fixture.mjs';

function installFakeVoice() {
  window.voiceCalls = [];
  let speaking = false;
  window.ISRVoice = {
    stt: { supported: true, listening: false, toggle(onText, onState) {
      voiceCalls.push(['toggle']);
      this.listening = !this.listening;
      window.emitNeutralDictation = onText;
      onState(this.listening ? 'listening' : 'idle');
    } },
    tts: {
      isSpeaking: () => speaking,
      cancel() { voiceCalls.push(['cancel']); speaking = false; },
      speak(text) { voiceCalls.push(['speak', text]); speaking = true; },
      preload: () => voiceCalls.push(['preload']),
      setEngine: id => voiceCalls.push(['engine', id]),
    },
  };
}

for (const filename of ['index.html', 'weather_preview.html']) {
  test(`${filename}: voice wiring preserves controls, reply behavior and cleanup with fake engines`, async ({ shell }) => {
    const { page } = shell;
    await page.route('**/voice.js*', route => route.fulfill({
      contentType: 'application/javascript', body: `(${installFakeVoice.toString()})();`,
    }));
    await page.route('**/api/tts/engines', route => route.fulfill({ json: {
      engines: [{ id: 'browser', label: 'Browser', configured: true },
        { id: 'kokoro', label: 'Local', configured: true },
        { id: 'cloud', label: 'Cloud', configured: false }], default: 'browser',
    } }));
    await shell.open(filename);
    await page.locator('.tab-btn[data-tab="agents"]').click();
    await expect(page.locator('#tts-engine')).toHaveValue('browser');
    await expect(page.locator('#tts-engine option[value="cloud"]')).toBeDisabled();
    await page.evaluate(() => { voiceCalls.length = 0; attachVoiceHandlers(); attachTtsEngineSelector(); });
    await page.locator('#agent-input').fill('existing');
    await page.locator('#btn-agent-mic').focus();
    await page.keyboard.press('Enter');
    expect(await page.evaluate(() => voiceCalls)).toEqual([['cancel'], ['toggle']]);
    await expect(page.locator('#btn-agent-mic')).toHaveClass(/listening/);
    await page.evaluate(() => emitNeutralDictation('neutral dictation', true));
    await expect(page.locator('#agent-input')).toHaveValue('existing neutral dictation');
    await expect(page.locator('#agent-input')).toBeFocused();
    await page.locator('#speak-replies-toggle').check();
    expect(await page.evaluate(() => voiceCalls.at(-1))).toEqual(['preload']);
    await page.locator('#tts-engine').selectOption('kokoro');
    expect(await page.evaluate(() => voiceCalls.slice(-2))).toEqual([['engine', 'kokoro'], ['preload']]);

    await page.evaluate(() => {
      const block = document.createElement('div');
      const button = document.createElement('button');
      button.id = 'neutral-voice-replay'; button.className = 'qa-speak-btn';
      button.textContent = 'Replay neutral text'; block.append(button);
      document.getElementById('tab-agents').append(block);
      window.neutralReplyBlock = block;
      wireReplyVoice(block, 'neutral reply');
    });
    expect(await page.evaluate(() => voiceCalls.at(-1))).toEqual(['speak', 'neutral reply']);
    await page.locator('#neutral-voice-replay').click();
    expect(await page.evaluate(() => voiceCalls.at(-1))).toEqual(['cancel']);
    await page.locator('#neutral-voice-replay').click();
    expect(await page.evaluate(() => voiceCalls.at(-1))).toEqual(['speak', 'neutral reply']);
    const beforeCleanup = await page.evaluate(() => {
      voiceControls.disposeReplyVoice(neutralReplyBlock);
      voiceControls.dispose(); voiceControls.dispose();
      return voiceCalls.length;
    });
    await page.locator('#neutral-voice-replay').click();
    await page.locator('#btn-agent-mic').click();
    await page.locator('#speak-replies-toggle').uncheck();
    expect(await page.evaluate(() => voiceCalls.length)).toBe(beforeCleanup);
    await page.evaluate(() => { attachVoiceHandlers(); attachVoiceHandlers(); });
    await expect(page.locator('#tts-engine')).toHaveValue('browser');
    const beforeToggle = await page.evaluate(() => voiceCalls.filter(call => call[0] === 'toggle').length);
    await page.locator('#btn-agent-mic').click();
    expect(await page.evaluate(() => voiceCalls.filter(call => call[0] === 'toggle').length)).toBe(beforeToggle + 1);
  });
}
