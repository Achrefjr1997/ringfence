// RingFence verification desk capture worklet.
// float32 @ AudioContext rate  ->  int16 PCM @ 24 kHz  ->  50 ms frames (1200 samples)
// posted to the main thread as transferable ArrayBuffers.
//
// 24 kHz PCM16 is the AssemblyAI Voice Agent API's native input format, so the
// gateway forwards these frames untouched -- no resampling server-side.
// Same naive decimation as worklet.js, for the same reason: the browser's
// capture is already band-limited well below the output Nyquist.

const TARGET_RATE = 24000;
const FRAME = 1200; // 50 ms @ 24 kHz

class DeskCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / TARGET_RATE;
    this.cursor = 0;
    this.buf = new Int16Array(FRAME);
    this.n = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;

    while (this.cursor < ch.length) {
      let s = ch[this.cursor | 0];
      if (s > 1) s = 1;
      else if (s < -1) s = -1;
      this.buf[this.n++] = s < 0 ? (s * 0x8000) | 0 : (s * 0x7fff) | 0;
      if (this.n === FRAME) {
        const frame = this.buf.slice(0);
        this.port.postMessage(frame.buffer, [frame.buffer]);
        this.n = 0;
      }
      this.cursor += this.step;
    }
    this.cursor -= ch.length;
    return true;
  }
}

registerProcessor("desk-capture", DeskCapture);
