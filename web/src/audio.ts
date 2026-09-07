/**
 * Playing the agent's voice.
 *
 * Audio arrives as a stream of small pieces while the reply is still being
 * written, so each has to be queued behind the last rather than played the
 * moment it lands. Playing them as they arrive would overlap them into noise.
 *
 * A browser will not make sound until the person has interacted with the page,
 * so the audio context is created when they press Call and not before.
 */

/** Signed 16-bit, one channel, matching what the speech service is asked for. */
const SAMPLE_RATE_HZ = 16000;

export class Playback {
  private context: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  /** When the last queued piece finishes, so the next can start there. */
  private playingUntil = 0;
  private sources = new Set<AudioBufferSourceNode>();

  /** Called from the click that starts the call, which is what unlocks sound. */
  start(): void {
    if (!this.context) {
      this.context = new AudioContext({ sampleRate: SAMPLE_RATE_HZ });
      // Everything is routed through here so the page can show the agent's
      // voice moving. On a call there is nothing else to look at.
      this.analyser = this.context.createAnalyser();
      this.analyser.fftSize = 256;
      this.analyser.connect(this.context.destination);
    }
    void this.context.resume();
    this.playingUntil = this.context.currentTime;
  }

  play(base64Pcm16: string): void {
    if (!this.context) return;

    const bytes = Uint8Array.from(atob(base64Pcm16), (c) => c.charCodeAt(0));
    // Signed 16-bit little-endian, which the browser needs as floats between
    // -1 and 1. 32768 is the magnitude of the most negative value.
    const samples = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2);
    const buffer = this.context.createBuffer(1, samples.length, SAMPLE_RATE_HZ);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768;

    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.analyser ?? this.context.destination);

    // Start where the last piece ends, or now if nothing is playing. Without
    // this every piece would begin immediately and they would overlap.
    const startAt = Math.max(this.context.currentTime, this.playingUntil);
    source.start(startAt);
    this.playingUntil = startAt + buffer.duration;

    this.sources.add(source);
    source.onended = () => this.sources.delete(source);
  }

  /** Cut the agent off mid-word, for when the caller starts talking. */
  stop(): void {
    for (const source of this.sources) {
      try {
        source.stop();
      } catch {
        // Already finished on its own.
      }
    }
    this.sources.clear();
    if (this.context) this.playingUntil = this.context.currentTime;
  }

  async close(): Promise<void> {
    this.stop();
    await this.context?.close();
    this.context = null;
  }

  get speaking(): boolean {
    return this.sources.size > 0;
  }

  /** How loud the agent is right now, from nothing to one. */
  get level(): number {
    if (!this.analyser || this.sources.size === 0) return 0;
    const samples = new Uint8Array(this.analyser.frequencyBinCount);
    this.analyser.getByteTimeDomainData(samples);
    let peak = 0;
    for (const sample of samples) peak = Math.max(peak, Math.abs(sample - 128));
    return Math.min(1, peak / 90);
  }
}
