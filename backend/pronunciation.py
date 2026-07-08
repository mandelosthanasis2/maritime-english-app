"""Azure Speech pronunciation assessment.

Browsers record audio as WebM/Opus, but the Azure Speech SDK expects PCM WAV.
We convert the uploaded audio to 16 kHz mono 16-bit WAV by invoking the ffmpeg
binary directly (via subprocess). This deliberately avoids pydub, which imports
the standard-library ``audioop`` module that was removed in Python 3.13.

ffmpeg must be available on the system PATH (the Dockerfile installs it; see
also FFMPEG_BIN to override the binary location).
"""

import logging
import os
import shutil
import subprocess
import tempfile
import threading

from usage import log_usage, wav_seconds

logger = logging.getLogger(__name__)

FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFMPEG_TIMEOUT_SECONDS = 30


class PronunciationError(Exception):
    """An expected, client-presentable failure with an HTTP status code."""

    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code


def convert_to_wav_file(audio_bytes):
    """Convert uploaded audio to a 16 kHz mono PCM WAV temp file.

    Returns the path to the WAV file (caller is responsible for removing it).
    """
    ffmpeg_path = shutil.which(FFMPEG_BIN)
    if ffmpeg_path is None:
        # Detailed diagnostics server-side; friendly message to the user.
        logger.error(
            "ffmpeg binary not found. Looked for %r on PATH=%r. Audio conversion "
            "cannot run — ensure ffmpeg is installed in the deployment image "
            "(see backend/Dockerfile).",
            FFMPEG_BIN,
            os.environ.get("PATH", ""),
        )
        raise PronunciationError(
            "Audio processing is temporarily unavailable. Please try again later.",
            503,
        )

    out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    out_path = out.name
    out.close()

    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", "pipe:0",      # read uploaded bytes from stdin
        "-ac", "1",          # mono
        "-ar", "16000",      # 16 kHz
        "-acodec", "pcm_s16le",  # 16-bit PCM
        out_path,
    ]

    try:
        proc = subprocess.run(
            cmd,
            input=audio_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        _safe_remove(out_path)
        logger.error("ffmpeg timed out after %ss converting audio.", FFMPEG_TIMEOUT_SECONDS)
        raise PronunciationError("Could not process the audio. Please try again.", 500)
    except Exception as exc:  # pragma: no cover - unexpected spawn failure
        _safe_remove(out_path)
        logger.exception("Failed to invoke ffmpeg: %s", exc)
        raise PronunciationError("Could not process the audio.", 500) from exc

    if proc.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        _safe_remove(out_path)
        logger.error(
            "ffmpeg conversion failed (returncode=%s): %s",
            proc.returncode,
            stderr or "<no stderr output>",
        )
        raise PronunciationError(
            "Could not read the uploaded audio. Please record again.", 400
        )

    return out_path


def _safe_remove(path):
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


def assess_pronunciation(audio_bytes, reference_text, language="en-US", user_id=None):
    """Run Azure pronunciation assessment and return a JSON-serializable dict."""
    if not reference_text or not reference_text.strip():
        raise PronunciationError("reference_text is required.", 400)

    key = os.environ.get("AZURE_SPEECH_KEY")
    region = os.environ.get("AZURE_SPEECH_REGION")
    if not key or not region:
        logger.error(
            "Azure Speech is not configured: AZURE_SPEECH_KEY set=%s, "
            "AZURE_SPEECH_REGION set=%s.",
            bool(key),
            bool(region),
        )
        raise PronunciationError(
            "Speech assessment is not configured on the server.", 503
        )

    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError as exc:  # pragma: no cover - dependency missing
        logger.exception("azure-cognitiveservices-speech failed to import.")
        raise PronunciationError(
            "Speech assessment is not available on the server.", 503
        ) from exc

    wav_path = convert_to_wav_file(audio_bytes)
    # Azure bills pronunciation assessment per second of audio; the converted
    # WAV gives an accurate duration even when the upload doesn't carry one.
    audio_seconds = wav_seconds(wav_path)

    try:
        speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
        audio_config = speechsdk.audio.AudioConfig(filename=wav_path)

        pa_config = speechsdk.PronunciationAssessmentConfig(
            reference_text=reference_text,
            grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
            granularity=speechsdk.PronunciationAssessmentGranularity.Word,
            enable_miscue=True,
        )

        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            language=language,
            audio_config=audio_config,
        )
        pa_config.apply_to(recognizer)

        result = recognizer.recognize_once()

        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            log_usage(
                provider="azure_pronunciation",
                endpoint="pronunciation",
                units=audio_seconds,
                user_id=user_id,
            )
            pa_result = speechsdk.PronunciationAssessmentResult(result)
            words = [
                {
                    "word": w.word,
                    "accuracy_score": w.accuracy_score,
                    # "None" when the word is fine; otherwise Mispronunciation /
                    # Omission / Insertion.
                    "error_type": w.error_type,
                }
                for w in pa_result.words
            ]
            return {
                "recognized_text": result.text,
                "accuracy_score": pa_result.accuracy_score,
                "fluency_score": pa_result.fluency_score,
                "completeness_score": pa_result.completeness_score,
                "pronunciation_score": pa_result.pronunciation_score,
                "words": words,
            }

        if result.reason == speechsdk.ResultReason.NoMatch:
            # The audio was still processed (and billed) even with no speech.
            log_usage(
                provider="azure_pronunciation",
                endpoint="pronunciation",
                units=audio_seconds,
                user_id=user_id,
            )
            raise PronunciationError(
                "No speech was recognized. Please speak clearly and try again.", 422
            )

        if result.reason == speechsdk.ResultReason.Canceled:
            details = result.cancellation_details
            logger.warning(
                "Azure assessment canceled: %s | %s",
                details.reason,
                details.error_details,
            )
            raise PronunciationError(
                "Speech assessment failed. Please try again.", 502
            )

        raise PronunciationError("Unexpected assessment result.", 502)
    finally:
        _safe_remove(wav_path)


def assess_pronunciation_unscripted(
    audio_bytes,
    language="en-US",
    user_id=None,
    usage_endpoint="pronunciation_unscripted",
    max_seconds=None,
):
    """Azure pronunciation assessment in UNSCRIPTED mode (no reference text).

    Unlike the scripted lesson flow above, Azure both transcribes the speech
    AND scores it, so this works for free-form answers (e.g. the interview
    prep chat). Runs CONTINUOUS recognition — free answers run 60-90 seconds,
    far past what recognize_once() captures — and merges the per-utterance
    results into one summary.

    `max_seconds` rejects over-long audio with 413 AFTER the (local, free)
    ffmpeg conversion but BEFORE anything is sent to Azure, so an oversized
    recording never becomes a paid call.

    Returns a JSON-serializable dict:
      {transcript, accuracy_score, fluency_score, prosody_score|None,
       pronunciation_score, words: [{word, accuracy_score, error_type,
       phonemes: [{phoneme, accuracy_score}]}]}
    """
    key = os.environ.get("AZURE_SPEECH_KEY")
    region = os.environ.get("AZURE_SPEECH_REGION")
    if not key or not region:
        logger.error(
            "Azure Speech is not configured: AZURE_SPEECH_KEY set=%s, "
            "AZURE_SPEECH_REGION set=%s.",
            bool(key),
            bool(region),
        )
        raise PronunciationError(
            "Speech assessment is not configured on the server.", 503
        )

    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError as exc:  # pragma: no cover - dependency missing
        logger.exception("azure-cognitiveservices-speech failed to import.")
        raise PronunciationError(
            "Speech assessment is not available on the server.", 503
        ) from exc

    wav_path = convert_to_wav_file(audio_bytes)
    audio_seconds = wav_seconds(wav_path)

    if max_seconds is not None and audio_seconds > max_seconds:
        _safe_remove(wav_path)
        raise PronunciationError(
            f"Η ηχογράφηση είναι πολύ μεγάλη (όριο {int(max_seconds // 60)} λεπτά). "
            "Δώσε μια πιο σύντομη απάντηση.",
            413,
        )

    try:
        speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
        audio_config = speechsdk.audio.AudioConfig(filename=wav_path)

        # Empty reference text = unscripted mode: Azure scores whatever was
        # actually said. Miscue detection is meaningless without a script.
        pa_config = speechsdk.PronunciationAssessmentConfig(
            reference_text="",
            grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
            granularity=speechsdk.PronunciationAssessmentGranularity.Phoneme,
            enable_miscue=False,
        )
        # Prosody + readable phoneme names are newer SDK features — best effort.
        if hasattr(pa_config, "enable_prosody_assessment"):
            pa_config.enable_prosody_assessment()
        try:
            pa_config.phoneme_alphabet = "IPA"
        except Exception:  # pragma: no cover - older SDK
            pass

        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            language=language,
            audio_config=audio_config,
        )
        pa_config.apply_to(recognizer)

        segments = []
        cancel_error = []
        done = threading.Event()

        def on_recognized(evt):
            if (
                evt.result.reason == speechsdk.ResultReason.RecognizedSpeech
                and evt.result.text
            ):
                segments.append(evt.result)

        def on_canceled(evt):
            details = getattr(evt, "cancellation_details", None) or evt
            reason = getattr(details, "reason", None)
            # EndOfStream is the normal end of a file input, not an error.
            if reason == speechsdk.CancellationReason.Error:
                cancel_error.append(getattr(details, "error_details", str(reason)))
            done.set()

        recognizer.recognized.connect(on_recognized)
        recognizer.canceled.connect(on_canceled)
        recognizer.session_stopped.connect(lambda evt: done.set())

        recognizer.start_continuous_recognition()
        # Files are processed faster than real time; the margin covers service
        # latency. A hung session must not hold the request forever.
        finished = done.wait(timeout=max(60.0, audio_seconds + 60.0))
        recognizer.stop_continuous_recognition()

        if cancel_error:
            logger.warning("Azure unscripted assessment canceled: %s", cancel_error[0])
            raise PronunciationError("Speech assessment failed. Please try again.", 502)
        if not finished and not segments:
            logger.error("Azure unscripted assessment timed out with no results.")
            raise PronunciationError("Speech assessment timed out. Please try again.", 502)

        # The audio was processed (and billed) even if it held no speech.
        log_usage(
            provider="azure_pronunciation",
            endpoint=usage_endpoint,
            units=audio_seconds,
            user_id=user_id,
        )

        if not segments:
            raise PronunciationError(
                "No speech was recognized. Please speak clearly and try again.", 422
            )

        return _merge_unscripted_segments(speechsdk, segments)
    finally:
        _safe_remove(wav_path)


def _merge_unscripted_segments(speechsdk, segments):
    """Merge continuous-recognition utterances into one assessment summary.

    Overall scores are weighted by each utterance's word count so a short
    "yes" can't drag down (or prop up) a long answer; prosody is averaged
    over the utterances that report it (None when none do).
    """
    transcript_parts = []
    words = []
    weighted = {"accuracy": 0.0, "fluency": 0.0, "pronunciation": 0.0}
    prosody_total = 0.0
    prosody_weight = 0
    total_weight = 0

    for result in segments:
        pa = speechsdk.PronunciationAssessmentResult(result)
        transcript_parts.append(result.text)
        seg_words = pa.words or []
        weight = max(1, len(seg_words))
        total_weight += weight
        weighted["accuracy"] += (pa.accuracy_score or 0) * weight
        weighted["fluency"] += (pa.fluency_score or 0) * weight
        weighted["pronunciation"] += (pa.pronunciation_score or 0) * weight
        prosody = getattr(pa, "prosody_score", None)
        if prosody is not None:
            prosody_total += prosody * weight
            prosody_weight += weight

        for w in seg_words:
            phonemes = [
                {"phoneme": p.phoneme, "accuracy_score": p.accuracy_score}
                for p in (getattr(w, "phonemes", None) or [])
                if getattr(p, "phoneme", None)
            ]
            words.append(
                {
                    "word": w.word,
                    "accuracy_score": w.accuracy_score,
                    "error_type": w.error_type,
                    "phonemes": phonemes,
                }
            )

    return {
        "transcript": " ".join(t.strip() for t in transcript_parts if t.strip()),
        "accuracy_score": round(weighted["accuracy"] / total_weight, 1),
        "fluency_score": round(weighted["fluency"] / total_weight, 1),
        "pronunciation_score": round(weighted["pronunciation"] / total_weight, 1),
        "prosody_score": (
            round(prosody_total / prosody_weight, 1) if prosody_weight else None
        ),
        "words": words,
    }
