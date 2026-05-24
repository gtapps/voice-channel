"""
Audio pipeline unit tests — no real audio hardware required.

Tests the trigger-matching logic and the parts of the pipeline that don't
need sounddevice/silero/whisper installed.  Tests that require audio are
skipped when the packages are absent.
"""

import pytest
from voice_dispatcher.audio.pipeline import (
    match_trigger,
    _levenshtein,
    phonetic_spell,
    format_permission_prompt,
    parse_verdict,
)


# ── Levenshtein ───────────────────────────────────────────────────────────────

def test_levenshtein_identical() -> None:
    assert _levenshtein("hey jarvis", "hey jarvis") == 0


def test_levenshtein_one_insert() -> None:
    assert _levenshtein("hey jarvis", "hey jarviss") == 1


def test_levenshtein_one_delete() -> None:
    assert _levenshtein("hey jarvis", "hey jarvi") == 1


def test_levenshtein_one_substitute() -> None:
    assert _levenshtein("hey jarvis", "hey garvis") == 1


def test_levenshtein_empty() -> None:
    assert _levenshtein("", "") == 0
    assert _levenshtein("abc", "") == 3
    assert _levenshtein("", "abc") == 3


# ── match_trigger ─────────────────────────────────────────────────────────────

def test_exact_trigger_match() -> None:
    trigger, command = match_trigger(
        "hey jarvis turn on the lights",
        ["hey jarvis", "agent"],
    )
    assert trigger == "hey jarvis"
    assert command == "turn on the lights"


def test_fuzzy_trigger_match_one_edit() -> None:
    # "ey jarvis" — missing 'h' at start (1 edit)
    trigger, command = match_trigger(
        "ey jarvis what time is it",
        ["hey jarvis"],
    )
    assert trigger == "hey jarvis"
    assert command == "what time is it"


def test_fuzzy_trigger_match_accent() -> None:
    # Portuguese-accented "ó agent" — triggers with "o agent" (1 edit)
    trigger, command = match_trigger(
        "o agent whats up",
        ["ó agent"],
    )
    assert trigger == "ó agent"
    assert command == "whats up"


def test_no_trigger_match() -> None:
    trigger, command = match_trigger(
        "the weather looks nice today",
        ["hey jarvis", "agent", "ó agent"],
    )
    assert trigger is None
    assert command == ""


def test_trigger_only_no_command() -> None:
    trigger, command = match_trigger(
        "hey jarvis",
        ["hey jarvis"],
    )
    assert trigger == "hey jarvis"
    assert command == ""


def test_first_trigger_wins() -> None:
    # "agent" is a prefix of "agent pro" — first matching trigger should win
    trigger, command = match_trigger(
        "agent list files",
        ["agent", "agent pro"],
    )
    assert trigger == "agent"
    assert command == "list files"


def test_punctuation_stripped() -> None:
    # Whisper sometimes returns punctuation in the transcript
    trigger, command = match_trigger(
        "Hey, Jarvis! turn on the lights.",
        ["hey jarvis"],
    )
    assert trigger == "hey jarvis"
    assert command == "turn on the lights"


def test_max_edit_distance_exceeded() -> None:
    # 3+ edits should not match with default max_edit_distance=2
    trigger, command = match_trigger(
        "xyz turn on the lights",
        ["hey jarvis"],
    )
    assert trigger is None


def test_custom_max_edit_distance() -> None:
    trigger, command = match_trigger(
        "xyz turn on the lights",
        ["hey jarvis"],
        max_edit_distance=10,  # very permissive
    )
    assert trigger == "hey jarvis"


# ── Permission relay: phonetic spelling ───────────────────────────────────────

def test_phonetic_spell_basic() -> None:
    assert phonetic_spell("abcde") == "alpha, bravo, charlie, delta, echo"


def test_phonetic_spell_excludes_l() -> None:
    # Claude Code IDs use [a-km-z]; 'l' never appears, but unknown chars pass through
    assert phonetic_spell("kmnop") == "kilo, mike, november, oscar, papa"


def test_format_permission_prompt() -> None:
    prompt = format_permission_prompt("Bash", "abcde")
    assert "Bash" in prompt
    assert "yes or no" in prompt
    assert "alpha, bravo, charlie, delta, echo" in prompt


# ── Permission relay: verdict parsing ─────────────────────────────────────────

def test_parse_verdict_allow_nato() -> None:
    assert parse_verdict("yes alpha bravo charlie delta echo", "abcde") == "allow"


def test_parse_verdict_allow_letters() -> None:
    assert parse_verdict("yes a b c d e", "abcde") == "allow"


def test_parse_verdict_deny_nato() -> None:
    assert parse_verdict("no foxtrot golf hotel india juliet", "fghij") == "deny"


def test_parse_verdict_punctuation_and_caps() -> None:
    assert parse_verdict("Yes, Alpha Bravo Charlie Delta Echo.", "abcde") == "allow"


def test_parse_verdict_wrong_id_rejected() -> None:
    # Correct grammar but wrong id — must not approve
    assert parse_verdict("yes alpha bravo charlie delta echo", "fghij") is None


def test_parse_verdict_bare_yes_rejected() -> None:
    # No id spoken — ambient 'yes' (TV, housemate) must not approve
    assert parse_verdict("yes", "abcde") is None
    assert parse_verdict("yes please", "abcde") is None


def test_parse_verdict_unrecognized_token_rejected() -> None:
    assert parse_verdict("yes banana split", "abcde") is None


def test_parse_verdict_portuguese() -> None:
    assert parse_verdict("sim alpha bravo charlie delta echo", "abcde") == "allow"
    assert parse_verdict("não alpha bravo charlie delta echo", "abcde") == "deny"


def test_parse_verdict_no_verdict_word() -> None:
    assert parse_verdict("alpha bravo charlie delta echo", "abcde") is None


def test_parse_verdict_juliett_alt_spelling() -> None:
    assert parse_verdict("yes juliett kilo mike november oscar", "jkmno") == "allow"


# ── Pipeline instantiation (no audio) ────────────────────────────────────────

def _make_pipeline(enable_relay: bool = True):
    from voice_dispatcher.core.handlers import Dispatcher
    from voice_dispatcher.core.session import AgentConfig, SessionRegistry
    from voice_dispatcher.audio.pipeline import AudioPipeline

    registry = SessionRegistry()
    registry.register(AgentConfig(
        agent_id="jarvis", token="tok", triggers=["hey jarvis"],
        language="en", voice="en_US-lessac-medium.onnx",
        enable_permission_relay=enable_relay,
    ))
    d = Dispatcher(registry=registry)
    config = {
        "agents": {"jarvis": {"triggers": ["hey jarvis"],
                               "voice": "en_US-lessac-medium.onnx",
                               "websocket_token": "tok"}},
        "audio": {"input_device": None, "output_device": None},
        "whisper": {"model": "tiny", "device": "cpu", "compute_type": "int8"},
    }
    return d, AudioPipeline(d, config)


class _FakeSeg:
    def __init__(self, text): self.text = text

class _FakeInfo:
    language = "en"

class _FakeWhisper:
    """Returns a fixed transcript so _process_speech runs without real audio."""
    def __init__(self, transcript): self._transcript = transcript
    def transcribe(self, audio, **kw): return ([_FakeSeg(self._transcript)], _FakeInfo())


def test_pipeline_instantiates_without_audio_deps() -> None:
    """Pipeline should be constructible even when sounddevice/whisper are missing."""
    _, pipeline = _make_pipeline()
    assert pipeline is not None


# ── Permission relay: audio-side integration ──────────────────────────────────

def test_on_permission_requested_sets_pending_and_enqueues_prompt() -> None:
    from voice_dispatcher.core.models import PermissionRequested
    _, pipeline = _make_pipeline()
    pipeline._on_permission_requested(
        PermissionRequested("jarvis", "abcde", "Bash", "run pwd", "{}")
    )
    assert pipeline._pending_permission == ("jarvis", "abcde")
    agent_id, uid, text, voice = pipeline._tts_queue.get_nowait()
    assert agent_id == "jarvis"
    assert "alpha, bravo, charlie, delta, echo" in text


def test_spoken_verdict_submits_to_core() -> None:
    from voice_dispatcher.core.models import PermissionRequested, PermissionVerdict
    d, pipeline = _make_pipeline()
    verdicts: list = []
    d.bus.subscribe(PermissionVerdict, verdicts.append)

    # Plugin requested permission → core emits PermissionRequested → audio prompt
    d.request_permission("jarvis", "abcde", "Bash", "run pwd", "{}")
    pipeline._on_permission_requested(
        PermissionRequested("jarvis", "abcde", "Bash", "run pwd", "{}")
    )

    # Operator speaks the verdict → fake Whisper feeds it into _process_speech
    pipeline._whisper_model = _FakeWhisper("yes alpha bravo charlie delta echo")
    pipeline._process_speech(object())  # audio arg unused by the fake

    assert len(verdicts) == 1
    assert verdicts[0].behavior == "allow"
    assert verdicts[0].request_id == "abcde"
    assert pipeline._pending_permission is None


def test_invalid_verdict_keeps_listening() -> None:
    from voice_dispatcher.core.models import PermissionRequested, PermissionVerdict
    d, pipeline = _make_pipeline()
    verdicts: list = []
    d.bus.subscribe(PermissionVerdict, verdicts.append)

    pipeline._on_permission_requested(
        PermissionRequested("jarvis", "abcde", "Bash", "run pwd", "{}")
    )
    # Wrong id spoken — must not submit, must keep the window open
    pipeline._whisper_model = _FakeWhisper("yes foxtrot golf hotel india juliet")
    pipeline._process_speech(object())

    assert verdicts == []
    assert pipeline._pending_permission == ("jarvis", "abcde")


def test_verdict_window_expiry_reverts_to_trigger_mode() -> None:
    import time
    from voice_dispatcher.core.models import PermissionRequested
    d, pipeline = _make_pipeline()
    pipeline._on_permission_requested(
        PermissionRequested("jarvis", "abcde", "Bash", "run pwd", "{}")
    )
    # Force the window to have already expired
    pipeline._pending_permission_deadline = time.monotonic() - 1
    pipeline._whisper_model = _FakeWhisper("hey jarvis what time is it")
    pipeline._process_speech(object())
    # Expired → pending cleared, falls back to normal listening
    assert pipeline._pending_permission is None
