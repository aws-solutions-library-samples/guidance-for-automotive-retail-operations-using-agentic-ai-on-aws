// Amazon Transcribe Streaming engine for useVoiceAssistant (AR-7). Pattern from
// reference/voice/aws-transcribe-streaming.ts: mic → 16 kHz PCM → Transcribe
// streaming over Cognito Identity Pool credentials. Loaded lazily, and only
// when NEXT_PUBLIC_TRANSCRIBE_IDENTITY_POOL is configured.

export interface TranscribeConfig { region: string; identityPoolId: string; languageCode: string }

export function transcribeConfig(language: string): TranscribeConfig | null {
  const identityPoolId = process.env.NEXT_PUBLIC_TRANSCRIBE_IDENTITY_POOL || "";
  if (!identityPoolId) return null;
  return {
    region: process.env.NEXT_PUBLIC_COGNITO_REGION || "us-east-1",
    identityPoolId,
    languageCode: language,
  };
}

/** Start streaming; returns a stop function. `onText(text, isFinal)` per result. */
export async function startTranscribe(cfg: TranscribeConfig, onText: (text: string, isFinal: boolean) => void,
                                      onError: (e: Error) => void): Promise<() => void> {
  const [{ TranscribeStreamingClient, StartStreamTranscriptionCommand }, { fromCognitoIdentityPool }] = await Promise.all([
    import("@aws-sdk/client-transcribe-streaming"),
    import("@aws-sdk/credential-provider-cognito-identity"),
  ]);
  const client = new TranscribeStreamingClient({
    region: cfg.region,
    credentials: fromCognitoIdentityPool({ clientConfig: { region: cfg.region }, identityPoolId: cfg.identityPoolId }),
  });
  const media = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  const ctx = new AudioContext({ sampleRate: 16000 });
  const source = ctx.createMediaStreamSource(media);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  const queue: Uint8Array[] = [];
  let running = true;
  proc.onaudioprocess = (ev) => {
    const input = ev.inputBuffer.getChannelData(0);
    const pcm = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i]));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    queue.push(new Uint8Array(pcm.buffer));
  };
  source.connect(proc);
  proc.connect(ctx.destination);

  async function* audio() {
    while (running) {
      const chunk = queue.shift();
      if (chunk) yield { AudioEvent: { AudioChunk: chunk } };
      else await new Promise((r) => setTimeout(r, 50));
    }
  }

  const stop = () => {
    running = false;
    try { proc.disconnect(); source.disconnect(); } catch { /* already stopped */ }
    media.getTracks().forEach((t) => t.stop());
    ctx.close().catch(() => {});
  };

  (async () => {
    try {
      const resp = await client.send(new StartStreamTranscriptionCommand({
        LanguageCode: cfg.languageCode as any, MediaEncoding: "pcm", MediaSampleRateHertz: 16000,
        AudioStream: audio(),
      }));
      for await (const ev of resp.TranscriptResultStream || []) {
        for (const r of ev.TranscriptEvent?.Transcript?.Results || []) {
          const text = r.Alternatives?.[0]?.Transcript;
          if (text) onText(text, !r.IsPartial);
        }
      }
    } catch (e: any) {
      if (running) onError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      stop();
    }
  })();
  return stop;
}
