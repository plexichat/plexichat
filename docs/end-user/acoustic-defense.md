# Acoustic Eavesdropping Defense

Plexichat includes built-in defenses against AI-based acoustic eavesdropping attacks that attempt to decipher what you type by analyzing the sound of your keyboard clicks during voice calls.

## What is Acoustic Eavesdropping?

Recent research (Self-Supervised Acoustic Eavesdropping Attacks on Keyboards, July 2026) demonstrated that a general Transformer model can:

- Map an unfamiliar keyboard using only 100-150 keystrokes
- Achieve over 99% transcription accuracy
- Bypass previous hardware-based limitations

The attack works by analyzing the unique acoustic signature of each key press — the sharp "attack" and "decay" transients, the high-frequency components, and the precise timing patterns. Modern AI models can cluster these acoustic features to identify which keys were pressed.

## How Plexichat Defends You

Plexichat implements four server-side audio processing defenses that work together to destroy the acoustic fingerprint of keystrokes:

### 1. Jitter Injection

**What it does:** Injects randomized 5-20ms delays into audio packet delivery.

**Why it works:** Transformer models rely heavily on precise timing, duration, and local continuity of keystroke signals in the time-frequency domain. Randomized jitter breaks this temporal continuity, preventing the AI from cleanly segmenting the start and end of a keystroke.

**Impact on audio:** Completely imperceptible to humans during normal conversation.

### 2. Spectral Masking

**What it does:** During detected silence (no speech), injects low-amplitude comfort noise specifically in the 1kHz-8kHz frequency band.

**Why it works:** Keyboard clicks produce sharp, high-frequency acoustic anomalies. Flooding these specific bands with unnoticeable noise masks the subtle acoustic differences between different key types (e.g., spacebar vs. letter key), rendering the AI's clustering algorithms useless.

**Impact on audio:** Listeners may notice a very faint "shhh" during pauses — similar to standard VoIP comfort noise (DTX). It is barely perceptible.

### 3. VAD Gating (Voice Activity Detection)

**What it does:** The server analyzes the spectral envelope of incoming audio. Packets that contain isolated impulse noises (like keyboard clicks) without corresponding low-to-mid frequency human vocal harmonics are instantly dropped or hard-muted.

**Why it works:** Human speech has distinct harmonic structures, whereas keyboard clicks are short, broad-band impulses. By filtering out non-speech packets at the server, the bad actor never receives the raw audio needed for their attack.

**Impact on audio:** If someone is typing heavily during a call, you may notice very slight gaps in their audio. This is normal and expected.

### 4. Transient Shaving

**What it does:** Applies a digital limiter with a fast attack time (0.5ms) and fast release (5ms) to flatten sharp volume spikes.

**Why it works:** The distinct sound profile of a keyboard comes from the initial peak power when the plastic switch bottoms out. By compressing these dynamic peaks down into the floor of ambient background noise, the Transformer loses its cleanest data points.

**Impact on audio:** Slightly reduces the "sharpness" of audio, similar to mild compression on a podcast. Speech remains clear and intelligible.

## Enabling Acoustic Defense

1. Go to **Settings** → **Voice**
2. Under **Audio Processing**, toggle **Acoustic Eavesdropping Defense**
3. The setting is saved automatically

You can also enable it per-server in `config.yaml`:

```yaml
voice:
  acoustic_defense:
    enabled: true
    jitter_ms_min: 5
    jitter_ms_max: 20
    spectral_masking: true
    vad_gating: true
    transient_shaving: true
```

## Technical Details

The defenses are implemented server-side in the SFU (Selective Forwarding Unit) adapter. All audio processing happens in-process before packets are forwarded to other participants. The defenses are:

- **Server-side only** — no client-side processing required
- **Adaptive** — automatically detects silence vs. speech
- **Configurable** — each defense can be tuned independently
- **Low-latency** — designed for real-time voice communication

## Research Background

This feature is based on the paper:

> Self-Supervised Acoustic Eavesdropping Attacks on Keyboards (July 2026)

The paper proved that general Transformer models can bypass previous hardware limitations and achieve over 99% keystroke transcription accuracy from audio alone. Plexichat's defenses are designed specifically to break the assumptions that these AI models rely on.

## Limitations

- Acoustic defense is most effective when enabled server-side (SFU adapter)
- The aiortc backend (in-process SFU) provides the strongest protection
- External SFU backends (mediasoup, Janus) have limited audio processing capabilities
- Defense effectiveness may vary depending on microphone quality and background noise levels
