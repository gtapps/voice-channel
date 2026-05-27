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

def _tokenize(s: str) -> list[str]:
    """Strip punctuation, lowercase, and split on whitespace."""
    return re.sub(r"[^\w\s]", " ", s.lower()).split()


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
    max_edit_distance: Optional[int] = None,
) -> tuple[Optional[str], str]:
    """
    Returns (matched_trigger, command_text) or (None, "") if no match.

    Tokenizes transcript and trigger on whitespace (after stripping punctuation
    and lowercasing), then compares the first len(trigger_tokens) transcript
    tokens against the trigger using Levenshtein distance.  This gives clean
    word-boundary command extraction — no more mid-word slices like 'y report'
    for trigger 'agent' vs transcript 'agency report'.

    When max_edit_distance is None (default), a length-scaled tolerance is used:
    max(1, len(trigger_without_spaces) // 5).  This rejects very-close
    homophones ('agency' → 'agent', lev=2 > tol=1) while still accepting
    one-off mishearings ('ey jarvis', lev=1 ≤ tol=1).
    Pass an explicit int to override (e.g. max_edit_distance=10 for tests).
    """
    transcript_tokens = _tokenize(transcript)
    for trigger in triggers:
        trigger_tokens = _tokenize(trigger)
        if not trigger_tokens:
            continue
        n = len(trigger_tokens)
        if len(transcript_tokens) < n:
            continue
        head = " ".join(transcript_tokens[:n])
        target = " ".join(trigger_tokens)
        if max_edit_distance is None:
            tol = max(1, len(target.replace(" ", "")) // 5)
        else:
            tol = max_edit_distance
        if _levenshtein(head, target) <= tol:
            command = " ".join(transcript_tokens[n:])
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
    tokens = _tokenize(transcript)
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


# ── No-match tick + trigger detect sound ──────────────────────────────────────

def _play_tone(freq: float, duration: float, output_device: Optional[int] = None) -> None:
    """Play a short synthesized sine tone."""
    try:
        import numpy as np
        import sounddevice as sd

        t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
        tone = (np.sin(2 * math.pi * freq * t) * 0.2).astype(np.float32).reshape(-1, 1)
        sd.play(tone, SAMPLE_RATE, device=output_device, blocking=True)
    except Exception as exc:
        logger.debug("tone: %s", exc)


def _play_tick(output_device: Optional[int] = None) -> None:
    """Play a short 40ms 600 Hz sine — indicates VAD fired but no trigger matched."""
    _play_tone(600, 0.04, output_device)


def _play_detect_sound(output_device: Optional[int] = None) -> None:
    """Play the OS notification sound on trigger detection; fall back to a short tone."""
    import shutil
    import subprocess

    try:
        if sys.platform == "darwin":  # macOS
            if shutil.which("afplay"):
                subprocess.run(
                    ["afplay", "/System/Library/Sounds/Glass.aiff"],
                    check=True,
                    timeout=5,
                )
                return
        else:  # Linux + WSL2
            snd = "/usr/share/sounds/freedesktop/stereo/message.oga"
            if os.path.exists(snd):
                for player in ("pw-play", "paplay"):
                    if shutil.which(player):
                        subprocess.run([player, snd], check=True, timeout=5)
                        return
    except Exception as exc:  # includes subprocess.TimeoutExpired
        logger.debug("detect sound: %s", exc)
    _play_tone(880, 0.04, output_device)  # universal fallback


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
        self._trigger_beep: bool = bool(
            config.get("notifications", {}).get("trigger_beep", True)
        )
        self._whisper_model_size: str = config.get("whisper", {}).get("model", "tiny")
        self._whisper_device: str = config.get("whisper", {}).get("device", "cpu")
        self._whisper_compute: str = config.get("whisper", {}).get("compute_type", "int8")

        # Agents indexed by token for auth; also by agent_id for routing
        self._agents: dict = config.get("agents", {})

        # Half-duplex: set True while TTS is playing
        self._speaking = threading.Event()

        # Permission relay: (agent_id, request_id) while awaiting a spoken verdict, else None.
        # Set/cleared on the bus-callback thread, read on the audio-worker thread; atomic in CPython.
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
        from ..core.models import SpeakCompleted  # type: ignore
        self._dispatcher.bus.subscribe(SpeakRequestEvent, self._on_speak_request)
        # PermissionRequested is only emitted by the core when an agent has
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

    def _maybe_expire_permission(self) -> None:
        """Expire the permission listen window if the deadline has passed.

        Called at the top of every audio-worker loop iteration so the window
        expires even when the operator is silent (not only when speech arrives).
        """
        if self._pending_permission is None:
            return
        if time.monotonic() <= self._pending_permission_deadline:
            return
        agent_id, request_id = self._pending_permission
        logger.info("permission verdict window expired — terminal-only fallback")
        self._pending_permission = None
        self._dispatcher.cancel_pending_permission(agent_id, request_id)

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

        try:
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
                    self._maybe_expire_permission()
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

        except Exception as exc:
            logger.error("audio worker crashed: %s — voice input disabled", exc)

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

        if self._pending_permission is not None:
            self._maybe_expire_permission()
            if self._pending_permission is not None:
                # Still within the window — handle spoken verdict
                agent_id, request_id = self._pending_permission
                behavior = parse_verdict(transcript, request_id)
                if behavior is not None:
                    logger.info("permission verdict: agent=%r id=%r behavior=%r",
                                agent_id, request_id, behavior)
                    self._pending_permission = None
                    self._dispatcher.submit_permission_verdict(agent_id, request_id, behavior)
                else:
                    logger.debug("no valid verdict in %r — still listening", transcript)
                return  # in priority mode, never fall through to trigger matching
            # expired — fall through to trigger matching

        # Match against all registered agents
        for agent_id, agent_cfg in self._agents.items():
            triggers = agent_cfg.get("triggers", [])
            lang_hint = agent_cfg.get("language") or lang
            matched_trigger, command = match_trigger(transcript, triggers)
            if matched_trigger:
                uid = _generate_utterance_id()
                ts = datetime.now(timezone.utc).isoformat()
                logger.info("trigger match: agent=%r trigger=%r command=%r", agent_id, matched_trigger, command)
                self._dispatcher.route_transcript(
                    agent_id=agent_id,
                    utterance_id=uid,
                    text=command,
                    lang=lang_hint,
                    trigger=matched_trigger,
                    ts=ts,
                )
                if self._trigger_beep and not self._speaking.is_set():
                    threading.Thread(
                        target=_play_detect_sound,
                        args=(self._resolved_output,),
                        daemon=True,
                    ).start()
                return  # first match wins

        logger.debug("no trigger matched for: %r", transcript)
        if self._no_match_tick:
            threading.Thread(target=_play_tick, args=(self._resolved_output,), daemon=True).start()

    # ── TTS playback ──────────────────────────────────────────────────────────

    def _on_speak_request(self, event) -> None:
        """Bus callback — enqueue TTS work."""
        agent_cfg = self._agents.get(event.agent_id, {})
        voice = agent_cfg.get("voice", "")
        self._tts_queue.put((event.agent_id, event.utterance_id, event.text, voice))

    def _on_permission_requested(self, event) -> None:
        """Bus callback — speak the phonetic prompt and open the verdict window."""
        # Write the deadline BEFORE _pending_permission: the audio-worker thread
        # keys off `_pending_permission is not None`, so it must never observe a
        # non-None pending with a stale deadline (which would expire it instantly).
        self._pending_permission_deadline = time.monotonic() + PERMISSION_LISTEN_WINDOW
        self._pending_permission = (event.agent_id, event.request_id)
        prompt = format_permission_prompt(event.tool_name, event.request_id)
        voice = self._agents.get(event.agent_id, {}).get("voice", "")
        logger.info("permission prompt: agent=%r id=%r tool=%r",
                    event.agent_id, event.request_id, event.tool_name)
        self._tts_queue.put((event.agent_id, f"perm-{event.request_id}", prompt, voice))

    def _tts_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                agent_id, uid, text, voice_filename = self._tts_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._play_tts(agent_id, uid, text, voice_filename)

    def _play_tts(self, agent_id: str, uid: str, text: str, voice_filename: str) -> None:
        """Synthesise TTS with Piper and play via the system audio stack (half-duplex)."""
        voice = self._load_piper_voice(voice_filename)
        if voice is None:
            logger.error("TTS skipped — voice not available")
            return

        try:
            import wave

            self._speaking.set()
            logger.info("tts: agent=%r uid=%r text=%r", agent_id, uid, text)

            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                # piper-tts >=1.4 renamed the wave-writing method to
                # synthesize_wav(); plain synthesize() now returns an AudioChunk
                # iterable and writes nothing. synthesize_wav sets the WAV header
                # automatically (set_wav_format=True default).
                voice.synthesize_wav(text, wf)

            self._play_wav(buf.getvalue())
            if not uid.startswith("perm-"):
                self._dispatcher.bus.emit(SpeakCompleted(agent_id=agent_id, utterance_id=uid))

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
