"""
Audio pipeline unit tests — no real audio hardware required.

Tests the trigger-matching logic and the parts of the pipeline that don't
need sounddevice/silero/whisper installed.  Tests that require audio are
skipped when the packages are absent.
"""

import pytest
from voice_dispatcher.audio.pipeline import match_trigger, _levenshtein


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
        ["hey jarvis", "hermit"],
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
    # Portuguese-accented "ó hermit" — triggers with "o hermit" (1 edit)
    trigger, command = match_trigger(
        "o hermit whats up",
        ["ó hermit"],
    )
    assert trigger == "ó hermit"
    assert command == "whats up"


def test_no_trigger_match() -> None:
    trigger, command = match_trigger(
        "the weather looks nice today",
        ["hey jarvis", "hermit", "ó hermit"],
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
    # "hermit" is a prefix of "hermit pro" — first matching trigger should win
    trigger, command = match_trigger(
        "hermit list files",
        ["hermit", "hermit pro"],
    )
    assert trigger == "hermit"
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


# ── Pipeline instantiation (no audio) ────────────────────────────────────────

def test_pipeline_instantiates_without_audio_deps() -> None:
    """Pipeline should be constructible even when sounddevice/whisper are missing."""
    from voice_dispatcher.core.handlers import Dispatcher
    from voice_dispatcher.audio.pipeline import AudioPipeline

    d = Dispatcher()
    config = {
        "hermits": {
            "jarvis": {
                "triggers": ["hey jarvis"],
                "voice": "en_US-lessac-medium.onnx",
                "websocket_token": "tok",
            }
        },
        "audio": {"input_device": None, "output_device": None},
        "whisper": {"model": "tiny", "device": "cpu", "compute_type": "int8"},
    }
    pipeline = AudioPipeline(d, config)
    # Should not raise; no models loaded yet (lazy)
    assert pipeline is not None
