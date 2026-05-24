"""
Audio pipeline — mic → VAD → Whisper → trigger-match → core.route_transcript()
             + TTS playback: core SpeakRequest events → Piper → speaker

Designed for macOS (critical-path) and Linux.  All audio imports are optional
so the core unit tests run without sounddevice/silero/whisper installed.

Half-duplex invariant: the mic input stream is **paused** while TTS is playing.
This prevents the system from transcribing its own voice back into the channel.
"""

from __future__ import annotations
import io
import logging
import math
import os
import queue
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..core.handlers import Dispatcher
    from ..core.models import SpeakRequest as SpeakRequestEvent

logger = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────────────────

SAMPLE_RATE = 16_000          # Hz fed to Whisper / Silero VAD
CHUNK_SAMPLES = 512           # Silero VAD chunk size (512 @ 16 kHz = 32 ms)
SILENCE_CHUNKS_END = 25       # ~800 ms of silence ends utterance
MAX_UTTERANCE_CHUNKS = 1_875  # ~60 s safety limit

# On Linux the raw ALSA device often only supports 44100/48000 Hz.
# "sysdefault" (or "default" on many setups) routes through PipeWire/dmix
# and supports any rate including 16 kHz.  Set via config audio.input_device.
LINUX_DEFAULT_DEVICE = "sysdefault"


# ── Trigger matching ──────────────────────────────────────────────────────────

def _levenshtein(a: str, b: str) -> int:
    """Simple Levenshtein distance — O(m*n), suitable for short trigger phrases."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


def match_trigger(
    transcript: str,
    triggers: list[str],
    max_edit_distance: int = 2,
) -> tuple[Optional[str], str]:
    """
    Returns (matched_trigger, command_text) or (None, "") if no match.

    Strips punctuation, lowercases, then checks startswith with up to
    `max_edit_distance` edit tolerance on the trigger portion.
    """
    clean = re.sub(r"[^\w\s]", "", transcript.lower()).strip()
    for trigger in triggers:
        t = re.sub(r"[^\w\s]", "", trigger.lower()).strip()
        prefix = clean[: len(t)]
        dist = _levenshtein(prefix, t)
        if dist <= max_edit_distance:
            command = clean[len(t):].strip()
            return trigger, command
    return None, ""


# ── Permission relay (opt-in): phonetic prompt + spoken-verdict grammar ────────

PERMISSION_LISTEN_WINDOW = 30.0   # seconds to listen for a spoken verdict

# NATO phonetic alphabet for spelling the 5-letter request_id aloud.
# Claude Code's ID alphabet is [a-km-z] (excludes 'l'), so 'lima' never appears.
_NATO = {
    "a": "alpha", "b": "bravo", "c": "charlie", "d": "delta", "e": "echo",
    "f": "foxtrot", "g": "golf", "h": "hotel", "i": "india", "j": "juliet",
    "k": "kilo", "m": "mike", "n": "november", "o": "oscar", "p": "papa",
    "q": "quebec", "r": "romeo", "s": "sierra", "t": "tango", "u": "uniform",
    "v": "victor", "w": "whiskey", "x": "xray", "y": "yankee", "z": "zulu",
}
_NATO_REVERSE = {word: letter for letter, word in _NATO.items()}
_NATO_REVERSE["juliett"] = "j"   # common alternate spelling Whisper may emit

_YES_WORDS = {"yes", "y", "yeah", "yep", "s", "sim"}    # English + Portuguese
_NO_WORDS = {"no", "n", "nope", "nao", "não"}


def phonetic_spell(request_id: str) -> str:
    """'abcde' → 'alpha, bravo, charlie, delta, echo'."""
    return ", ".join(_NATO.get(c, c) for c in request_id)


def format_permission_prompt(tool_name: str, request_id: str) -> str:
    """Build the spoken prompt for an inbound permission request."""
    return (
        f"{tool_name} needs permission. "
        f"Say yes or no, followed by {phonetic_spell(request_id)}."
    )


def parse_verdict(transcript: str, expected_id: str) -> Optional[str]:
    """
    Parse a spoken verdict against the expected request_id.

    Returns 'allow', 'deny', or None (no confident match). The operator must
    speak the 5-letter id (phonetically — 'alpha bravo…' — or as letters) so
    that ambient speech ('yes' from a TV) cannot approve a tool call.
    """
    tokens = re.sub(r"[^\w\s]", " ", transcript.lower()).split()
    if len(tokens) < 2:
        return None

    head, rest = tokens[0], tokens[1:]
    if head in _YES_WORDS:
        behavior = "allow"
    elif head in _NO_WORDS:
        behavior = "deny"
    else:
        return None

    letters = []
    for tok in rest:
        if tok in _NATO_REVERSE:
            letters.append(_NATO_REVERSE[tok])
        elif len(tok) == 1 and tok.isalpha():
            letters.append(tok)
        else:
            return None   # unrecognised token — reject rather than guess
    return behavior if "".join(letters) == expected_id else None


# ── No-match tick ─────────────────────────────────────────────────────────────

def _play_tick(output_device: Optional[int] = None) -> None:
    """Play a short 40ms 600 Hz sine — indicates VAD fired but no trigger matched."""
    try:
        import numpy as np
        import sounddevice as sd

        t = np.linspace(0, 0.04, int(SAMPLE_RATE * 0.04), endpoint=False)
        tone = (np.sin(2 * math.pi * 600 * t) * 0.2).astype(np.float32).reshape(-1, 1)
        sd.play(tone, SAMPLE_RATE, device=output_device, blocking=True)
    except Exception as exc:
        logger.debug("tick: %s", exc)


# ── Audio pipeline ────────────────────────────────────────────────────────────

class AudioPipeline:
    """
    Drives the mic→VAD→Whisper→trigger→core pipeline.
    Subscribes to the bus for SpeakRequest events and handles TTS playback.

    Call start() to begin; stop() to tear down cleanly.
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        config: dict,
    ) -> None:
        self._dispatcher = dispatcher
        self._cfg = config

        # Audio device indices/names (None = system default; resolved in start())
        self._input_device: Optional[int] = config.get("audio", {}).get("input_device")
        self._output_device: Optional[int] = config.get("audio", {}).get("output_device")
        self._resolved_input: object = self._input_device
        self._resolved_output: object = self._output_device

        self._vad_threshold: float = float(
            config.get("audio", {}).get("vad_threshold", 0.5)
        )
        self._no_match_tick: bool = bool(
            config.get("audio", {}).get("no_match_tick", False)
        )
        self._whisper_model_size: str = config.get("whisper", {}).get("model", "tiny")
        self._whisper_device: str = config.get("whisper", {}).get("device", "cpu")
        self._whisper_compute: str = config.get("whisper", {}).get("compute_type", "int8")

        # Hermits indexed by token for auth; also by hermit_id for routing
        self._hermits: dict = config.get("hermits", {})

        # Half-duplex: set True while TTS is playing
        self._speaking = threading.Event()

        # Permission relay: (hermit_id, request_id) while awaiting a spoken
        # verdict, else None.  Set/cleared from the bus-callback thread and read
        # from the audio-worker thread; both ops are atomic assignments.
        self._pending_permission: Optional[tuple[str, str]] = None
        self._pending_permission_deadline: float = 0.0

        # TTS work queue
        self._tts_queue: queue.Queue = queue.Queue()

        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

        # Lazy-initialised heavy objects
        self._vad_model = None
        self._whisper_model = None
        self._piper_voices: dict = {}

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Load models, subscribe to bus, start threads."""
        self._resolve_devices()
        self._load_vad()
        self._load_whisper()

        # Subscribe to SpeakRequest events from the core
        from ..core.models import SpeakRequest as SpeakRequestEvent  # type: ignore
        from ..core.models import PermissionRequested  # type: ignore
        self._dispatcher.bus.subscribe(SpeakRequestEvent, self._on_speak_request)
        # PermissionRequested is only emitted by the core when a hermit has
        # enable_permission_relay=True, so subscribing unconditionally is safe.
        self._dispatcher.bus.subscribe(PermissionRequested, self._on_permission_requested)

        # TTS thread
        t_tts = threading.Thread(target=self._tts_worker, name="tts-worker", daemon=True)
        t_tts.start()
        self._threads.append(t_tts)

        # Mic→VAD→Whisper thread
        t_audio = threading.Thread(target=self._audio_worker, name="audio-worker", daemon=True)
        t_audio.start()
        self._threads.append(t_audio)

        logger.info("audio pipeline started (Whisper %s, VAD threshold %.2f)",
                    self._whisper_model_size, self._vad_threshold)

    def stop(self) -> None:
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=5)
        logger.info("audio pipeline stopped")

    # ── Device resolution ─────────────────────────────────────────────────────

    def _resolve_devices(self) -> None:
        """
        On Linux, fall back to 'sysdefault' when no device is configured.
        sysdefault routes through PipeWire/dmix and supports arbitrary sample
        rates (including 16 kHz) via software resampling — unlike raw hw:x,y.
        """
        try:
            import sounddevice as sd
        except ImportError:
            return

        if sys.platform.startswith('linux'):
            if self._input_device is None:
                try:
                    sd.check_input_settings(device=LINUX_DEFAULT_DEVICE,
                                            samplerate=SAMPLE_RATE, channels=1)
                    self._resolved_input = LINUX_DEFAULT_DEVICE
                    logger.debug("input device resolved to %r", self._resolved_input)
                except Exception:
                    pass
            if self._output_device is None:
                try:
                    sd.check_output_settings(device=LINUX_DEFAULT_DEVICE,
                                             samplerate=SAMPLE_RATE, channels=1)
                    self._resolved_output = LINUX_DEFAULT_DEVICE
                    logger.debug("output device resolved to %r", self._resolved_output)
                except Exception:
                    pass

    # ── Model loading ─────────────────────────────────────────────────────────

    def _load_vad(self) -> None:
        try:
            from silero_vad import load_silero_vad  # type: ignore
            self._vad_model = load_silero_vad(onnx=True)
            logger.info("Silero VAD loaded (ONNX)")
        except ImportError:
            logger.warning("silero-vad not installed — VAD disabled; every audio chunk treated as speech")

    def _load_whisper(self) -> None:
        try:
            from faster_whisper import WhisperModel  # type: ignore
            self._whisper_model = WhisperModel(
                self._whisper_model_size,
                device=self._whisper_device,
                compute_type=self._whisper_compute,
            )
            logger.info("Whisper model loaded (%s/%s/%s)",
                        self._whisper_model_size, self._whisper_device, self._whisper_compute)
        except ImportError:
            logger.warning("faster-whisper not installed — transcription will fail")

    def _load_piper_voice(self, voice_filename: str):
        if voice_filename in self._piper_voices:
            return self._piper_voices[voice_filename]
        try:
            from piper import PiperVoice  # type: ignore
            voices_dir = os.path.expanduser(
                self._cfg.get("piper", {}).get("voices_dir", "~/.local/share/voice-dispatcher/voices")
            )
            voice_path = os.path.join(voices_dir, voice_filename)
            voice = PiperVoice.load(voice_path)
            self._piper_voices[voice_filename] = voice
            logger.info("Piper voice loaded: %s", voice_filename)
            return voice
        except Exception as exc:
            logger.error("Failed to load Piper voice %r: %s", voice_filename, exc)
            return None

    # ── Audio capture loop ────────────────────────────────────────────────────

    def _audio_worker(self) -> None:
        try:
            import sounddevice as sd  # type: ignore
            import numpy as np
        except ImportError:
            logger.error("sounddevice/numpy not installed — audio capture disabled")
            return

        device = self._resolved_input
        logger.info("audio worker starting (input device: %s)", device)

        audio_q: queue.Queue = queue.Queue()

        def _callback(indata, frames, time_info, status):
            if status:
                logger.debug("sounddevice status: %s", status)
            if not self._speaking.is_set():
                audio_q.put(indata[:, 0].copy())  # mono

        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=CHUNK_SAMPLES,
            device=device,
            callback=_callback,
        )

        speech_buf: list[np.ndarray] = []
        in_speech = False
        silence_count = 0

        with stream:
            while not self._stop_event.is_set():
                # Drain while speaking (half-duplex)
                if self._speaking.is_set():
                    try:
                        audio_q.get(timeout=0.05)
                    except queue.Empty:
                        pass
                    continue

                try:
                    chunk = audio_q.get(timeout=0.1)
                except queue.Empty:
                    continue

                # VAD scoring
                speech_prob = self._vad_score(chunk)

                if speech_prob >= self._vad_threshold:
                    in_speech = True
                    silence_count = 0
                    speech_buf.append(chunk)
                    if len(speech_buf) >= MAX_UTTERANCE_CHUNKS:
                        # Force-end very long utterances
                        self._process_speech(np.concatenate(speech_buf))
                        speech_buf = []
                        in_speech = False
                elif in_speech:
                    speech_buf.append(chunk)
                    silence_count += 1
                    if silence_count >= SILENCE_CHUNKS_END:
                        self._process_speech(np.concatenate(speech_buf))
                        speech_buf = []
                        in_speech = False
                        silence_count = 0

    def _vad_score(self, chunk) -> float:
        """Return speech probability for this chunk (0.0–1.0)."""
        if self._vad_model is None:
            return 1.0  # no VAD — treat everything as speech
        try:
            import torch  # type: ignore
            t = torch.from_numpy(chunk)
            prob = self._vad_model(t, SAMPLE_RATE).item()
            return float(prob)
        except Exception as exc:
            logger.debug("VAD error: %s", exc)
            return 1.0

    # ── Transcription + routing ───────────────────────────────────────────────

    def _process_speech(self, audio) -> None:
        """Transcribe audio, match triggers, route to core."""
        if self._whisper_model is None:
            logger.warning("whisper not available — dropping speech buffer")
            return

        try:
            import numpy as np
            ts_start = time.monotonic()

            # vad_filter=True strips any residual silence Silero missed
            segments, info = self._whisper_model.transcribe(
                audio,
                vad_filter=True,
                language=None,  # auto-detect per segment
            )
            transcript = " ".join(seg.text.strip() for seg in segments).strip()
            lang = info.language if info else "en"
            elapsed = time.monotonic() - ts_start
            logger.debug("whisper: %r (%.2fs, lang=%s)", transcript, elapsed, lang)

        except Exception as exc:
            logger.error("whisper transcription failed: %s", exc)
            return

        if not transcript:
            return

        # Permission-relay priority mode: while awaiting a spoken verdict, parse
        # this utterance as a verdict instead of matching triggers.  The local
        # terminal dialog remains the always-available fallback.
        if self._pending_permission is not None:
            if time.monotonic() > self._pending_permission_deadline:
                logger.info("permission verdict window expired — terminal-only fallback")
                self._pending_permission = None
            else:
                hermit_id, request_id = self._pending_permission
                behavior = parse_verdict(transcript, request_id)
                if behavior is not None:
                    logger.info("permission verdict: hermit=%r id=%r behavior=%r",
                                hermit_id, request_id, behavior)
                    self._pending_permission = None
                    self._dispatcher.submit_permission_verdict(hermit_id, request_id, behavior)
                else:
                    logger.debug("no valid verdict in %r — still listening", transcript)
                return  # in priority mode, never fall through to trigger matching

        # Match against all registered hermits
        for hermit_id, hermit_cfg in self._hermits.items():
            triggers = hermit_cfg.get("triggers", [])
            lang_hint = hermit_cfg.get("language") or lang
            matched_trigger, command = match_trigger(transcript, triggers)
            if matched_trigger:
                uid = _generate_utterance_id()
                ts = datetime.now(timezone.utc).isoformat()
                logger.info("trigger match: hermit=%r trigger=%r command=%r", hermit_id, matched_trigger, command)
                self._dispatcher.route_transcript(
                    hermit_id=hermit_id,
                    utterance_id=uid,
                    text=command,
                    lang=lang_hint,
                    trigger=matched_trigger,
                    ts=ts,
                )
                return  # first match wins

        logger.debug("no trigger matched for: %r", transcript)
        if self._no_match_tick:
            threading.Thread(target=_play_tick, args=(self._resolved_output,), daemon=True).start()

    # ── TTS playback ──────────────────────────────────────────────────────────

    def _on_speak_request(self, event) -> None:
        """Bus callback — enqueue TTS work."""
        hermit_cfg = self._hermits.get(event.hermit_id, {})
        voice = hermit_cfg.get("voice", "")
        self._tts_queue.put((event.hermit_id, event.utterance_id, event.text, voice))

    def _on_permission_requested(self, event) -> None:
        """Bus callback — speak the phonetic prompt and open the verdict window."""
        self._pending_permission = (event.hermit_id, event.request_id)
        self._pending_permission_deadline = time.monotonic() + PERMISSION_LISTEN_WINDOW
        prompt = format_permission_prompt(event.tool_name, event.request_id)
        voice = self._hermits.get(event.hermit_id, {}).get("voice", "")
        logger.info("permission prompt: hermit=%r id=%r tool=%r",
                    event.hermit_id, event.request_id, event.tool_name)
        self._tts_queue.put((event.hermit_id, f"perm-{event.request_id}", prompt, voice))

    def _tts_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                hermit_id, uid, text, voice_filename = self._tts_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._play_tts(hermit_id, uid, text, voice_filename)

    def _play_tts(self, hermit_id: str, uid: str, text: str, voice_filename: str) -> None:
        """Synthesise TTS with Piper and play via the system audio stack (half-duplex)."""
        voice = self._load_piper_voice(voice_filename)
        if voice is None:
            logger.error("TTS skipped — voice not available")
            return

        try:
            import wave

            self._speaking.set()
            logger.info("tts: hermit=%r uid=%r text=%r", hermit_id, uid, text)

            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                # piper-tts >=1.3 renamed the wave-writing method to
                # synthesize_wav(); plain synthesize() now returns an AudioChunk
                # iterable and writes nothing. synthesize_wav sets the WAV header
                # automatically (set_wav_format=True default).
                voice.synthesize_wav(text, wf)

            self._play_wav(buf.getvalue())

        except Exception as exc:
            logger.error("TTS playback failed: %s", exc)
        finally:
            self._speaking.clear()

    def _play_wav(self, wav_bytes: bytes) -> None:
        """Play WAV bytes through the system audio output.

        On Linux, use pw-play (PipeWire) so audio reaches whatever sink PipeWire
        has selected as default (e.g. AirPods in A2DP mode) rather than going to
        the raw ALSA hw:x,y device that sounddevice would open by default.
        pw-play needs a real file — it can't parse a WAV header from a pipe —
        so we write to a temp file. Falls back to sounddevice if pw-play fails.
        """
        if sys.platform.startswith('linux'):
            import shutil
            import subprocess
            import tempfile
            pw_play = shutil.which('pw-play')
            if pw_play:
                tmp_path = None
                try:
                    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                        tmp.write(wav_bytes)
                        tmp_path = tmp.name
                    subprocess.run([pw_play, tmp_path], check=True)
                    return
                except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                    logger.debug("pw-play failed (%s) — falling back to sounddevice", exc)
                finally:
                    if tmp_path is not None:
                        os.unlink(tmp_path)

        import sounddevice as sd  # type: ignore
        import numpy as np
        import wave
        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, "rb") as wf:
            rate = wf.getframerate()
            n_ch = wf.getnchannels()
            frames = wf.readframes(wf.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            if n_ch > 1:
                audio = audio.reshape(-1, n_ch)
        sd.play(audio, rate, device=self._resolved_output, blocking=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _generate_utterance_id() -> str:
    short = str(uuid.uuid4()).replace("-", "")[:8]
    ts = int(datetime.now(timezone.utc).timestamp())
    return f"u-{ts}-{short}"
