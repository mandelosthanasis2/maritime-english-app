"""Azure Speech pronunciation assessment.

Browsers record audio as WebM/Opus, but the Azure Speech SDK expects PCM WAV.
We convert the uploaded audio to 16 kHz mono 16-bit WAV with pydub/ffmpeg before
handing it to Azure.

ffmpeg must be available on the system (see nixpacks.toml for the Railway
deployment).
"""

import io
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


class PronunciationError(Exception):
    """An expected, client-presentable failure with an HTTP status code."""

    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code


def convert_to_wav(audio_bytes):
    """Convert arbitrary uploaded audio to 16 kHz mono 16-bit PCM WAV bytes."""
    try:
        from pydub import AudioSegment
    except ImportError as exc:  # pragma: no cover - dependency missing
        raise PronunciationError(
            "Audio conversion is not available on the server.", 503
        ) from exc

    try:
        segment = AudioSegment.from_file(io.BytesIO(audio_bytes))
    except Exception as exc:
        # Usually a corrupt upload or ffmpeg missing/unable to decode the codec.
        logger.warning("Failed to decode uploaded audio: %s", exc)
        raise PronunciationError(
            "Could not read the uploaded audio. Please record again.", 400
        ) from exc

    segment = segment.set_frame_rate(16000).set_channels(1).set_sample_width(2)
    buf = io.BytesIO()
    try:
        segment.export(buf, format="wav")
    except Exception as exc:  # pragma: no cover - export rarely fails
        logger.warning("Failed to export WAV: %s", exc)
        raise PronunciationError("Could not process the audio.", 500) from exc
    return buf.getvalue()


def assess_pronunciation(audio_bytes, reference_text, language="en-US"):
    """Run Azure pronunciation assessment and return a JSON-serializable dict."""
    if not reference_text or not reference_text.strip():
        raise PronunciationError("reference_text is required.", 400)

    key = os.environ.get("AZURE_SPEECH_KEY")
    region = os.environ.get("AZURE_SPEECH_REGION")
    if not key or not region:
        raise PronunciationError(
            "Speech assessment is not configured on the server.", 503
        )

    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError as exc:  # pragma: no cover - dependency missing
        raise PronunciationError(
            "Speech assessment is not available on the server.", 503
        ) from exc

    wav_bytes = convert_to_wav(audio_bytes)

    # Azure's AudioConfig reads from a file path; write the converted WAV to a
    # short-lived temp file.
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_bytes)
            tmp_path = tmp.name

        speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
        audio_config = speechsdk.audio.AudioConfig(filename=tmp_path)

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
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
