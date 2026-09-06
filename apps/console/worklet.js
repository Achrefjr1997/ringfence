// RingFence capture worklet.
// float32 @ AudioContext rate  ->  int16 PCM @ 16 kHz  ->  40 ms frames (640 samples)
// posted to the main thread as transferable ArrayBuffers.
//
// Naive decimation is deliberate: the browser's own AudioContext output is
// already band-limited, and 16 kHz is far above telephone bandwidth, so no
// extra anti-alias filter is needed here.

const TARGET_RATE = 16000;
const FRAME = 640; // 40 ms @ 16 kHz

class RingfenceCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / TARGET_RATE; // input samples consumed per output sample
    this.cursor = 0; // fractional read position, carried across process() blocks
    this.buf = new Int16Array(FRAME);
    this.n = 0;
    this.sumSq = 0; // running energy for the level meter
    this.count = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;

    while (this.cursor < ch.length) {
      let s = ch[this.cursor | 0];
      if (s > 1) s = 1;
      else if (s < -1) s = -1;
      this.buf[this.n++] = s < 0 ? (s * 0x8000) | 0 : (s * 0x7fff) | 0;
      this.sumSq += s * s;
      this.count++;

      if (this.n === FRAME) {
        const frame = this.buf.slice(0);
        this.port.postMessage({ type: "frame", buffer: frame.buffer }, [frame.buffer]);
        this.n = 0;
      }
      this.cursor += this.step;
    }
    this.cursor -= ch.length; // keep the sub-sample remainder for the next block

    if (this.count >= TARGET_RATE / 20) {
      // ~50 ms
      this.port.postMessage({ type: "level", rms: Math.sqrt(this.sumSq / this.count) });
      this.sumSq = 0;
      this.count = 0;
    }
    return true;
  }
}

registerProcessor("ringfence-capture", RingfenceCapture);
