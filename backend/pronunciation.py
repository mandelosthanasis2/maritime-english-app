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
