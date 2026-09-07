/**
 * Capturing the caller's voice.
 *
 * The browser records at whatever rate the hardware runs at, in floating point.
 * The transcription service wants sixteen thousand samples a second as signed
 * sixteen-bit integers, so both conversions happen here before anything is
 * sent.
 *
 * The audio goes to our own server rather than straight to the transcription
 * service, because going direct would mean putting the credentials in a page
 * anyone can read.
 */

const TARGET_RATE_HZ = 16000;

/** About a fifth of a second per message: small enough that transcription keeps
 *  up with speech, large enough not to flood the socket. */
const SAMPLES_PER_MESSAGE = 3200;

// Runs on the audio thread and hands raw samples back to the page. Written as a
// string because a worklet has to be loaded from its own file, and this avoids
// shipping one.
const WORKLET = `
class Capture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel && channel.length) this.port.postMessage(channel.slice());
    return true;
  }
}
registerProcessor("capture", Capture);
`;

export class Microphone {
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private analyser: AnalyserNode | null = null;
  private pending: Float32Array[] = [];
  private pendingLength = 0;

  get open(): boolean {
    return this.stream !== null;
  }

  /**
   * Ask for the microphone and start sending audio.
   *
   * Throws if permission is refused, which the caller has to surface: a page
   * that silently fails to hear anything is indistinguishable from a broken one.
   */
  async start(send: (base64Pcm16: string) => void): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        // The browser's own cleanup. Without it the agent hears itself through
        // the speakers and transcribes its own words back.
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    this.context = new AudioContext();
    const blob = new Blob([WORKLET], { type: "application/javascript" });
    const url = URL.createObjectURL(blob);
    await this.context.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);

    const source = this.context.createMediaStreamSource(this.stream);
    // Tapped so the page can show the caller's own voice moving. Not knowing
    // whether the microphone is picking anything up was the single most
    // confusing thing about using this.
    this.analyser = this.context.createAnalyser();
    this.analyser.fftSize = 256;
    source.connect(this.analyser);
    this.node = new AudioWorkletNode(this.context, "capture");
    this.node.port.onmessage = (event) => {
      this.collect(event.data as Float32Array, this.context!.sampleRate, send);
    };
    source.connect(this.node);
    // Connected to the output so the graph runs, but silenced, or the caller
    // hears themselves back.
    const silence = this.context.createGain();
    silence.gain.value = 0;
    this.node.connect(silence).connect(this.context.destination);
  }

  private collect(
    samples: Float32Array,
    fromRateHz: number,
    send: (base64Pcm16: string) => void,
  ): void {
    const resampled = resample(samples, fromRateHz, TARGET_RATE_HZ);
    this.pending.push(resampled);
    this.pendingLength += resampled.length;

    while (this.pendingLength >= SAMPLES_PER_MESSAGE) {
      const chunk = new Float32Array(SAMPLES_PER_MESSAGE);
      let filled = 0;
      while (filled < SAMPLES_PER_MESSAGE) {
        const head = this.pending[0];
        const take = Math.min(head.length, SAMPLES_PER_MESSAGE - filled);
        chunk.set(head.subarray(0, take), filled);
        filled += take;
        if (take === head.length) this.pending.shift();
        else this.pending[0] = head.subarray(take);
      }
      this.pendingLength -= SAMPLES_PER_MESSAGE;
      send(toBase64Pcm16(chunk));
    }
  }

  /** How loud the caller is right now, from nothing to one. */
  get level(): number {
    if (!this.analyser) return 0;
    const samples = new Uint8Array(this.analyser.frequencyBinCount);
    this.analyser.getByteTimeDomainData(samples);
    let peak = 0;
    for (const sample of samples) peak = Math.max(peak, Math.abs(sample - 128));
    return Math.min(1, peak / 45);
  }

  async stop(): Promise<void> {
    this.node?.disconnect();
    this.node = null;
    this.analyser = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    await this.context?.close();
    this.context = null;
    this.pending = [];
    this.pendingLength = 0;
  }
}

/** Drop the rate down by taking a sample every so often, interpolating between
 *  neighbours so the result is not jagged. */
function resample(input: Float32Array, fromHz: number, toHz: number): Float32Array {
  if (fromHz === toHz) return input;
  const ratio = fromHz / toHz;
  const output = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < output.length; i++) {
    const at = i * ratio;
    const low = Math.floor(at);
    const high = Math.min(low + 1, input.length - 1);
    output[i] = input[low] + (input[high] - input[low]) * (at - low);
  }
  return output;
}

/** Floats between -1 and 1 become signed sixteen-bit, then base64 for the socket. */
function toBase64Pcm16(samples: Float32Array): string {
  const pcm = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    pcm[i] = clamped < 0 ? clamped * 32768 : clamped * 32767;
  }
  const bytes = new Uint8Array(pcm.buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}
