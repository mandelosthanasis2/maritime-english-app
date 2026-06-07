"""Azure Speech speech-to-text transcription.

Reuses the WebM/Opus -> 16 kHz mono PCM WAV conversion from pronunciation.py, then
runs Azure Speech recognition (en-US) and returns the transcript.
"""

import logging
import os

from pronunciation import PronunciationError, _safe_remove, convert_to_wav_file

logger = logging.getLogger(__name__)


def transcribe(audio_bytes, language="en-US"):
    """Transcribe uploaded audio and return {"text": str} (empty if no speech)."""
    key = os.environ.get("AZURE_SPEECH_KEY")
    region = os.environ.get("AZURE_SPEECH_REGION")
    if not key or not region:
        logger.error(
            "Azure Speech is not configured: AZURE_SPEECH_KEY set=%s, "
            "AZURE_SPEECH_REGION set=%s.",
            bool(key),
            bool(region),
        )
        raise PronunciationError("Speech-to-text is not configured on the server.", 503)

    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError as exc:  # pragma: no cover - dependency missing
        logger.exception("azure-cognitiveservices-speech failed to import.")
        raise PronunciationError("Speech-to-text is not available on the server.", 503) from exc

    wav_path = convert_to_wav_file(audio_bytes)

    try:
        speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
        speech_config.speech_recognition_language = language
        audio_config = speechsdk.audio.AudioConfig(filename=wav_path)
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config, audio_config=audio_config
        )

        result = recognizer.recognize_once()

        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            return {"text": result.text}

        if result.reason == speechsdk.ResultReason.NoMatch:
            # No speech recognized — return empty text rather than an error.
            return {"text": ""}

        if result.reason == speechsdk.ResultReason.Canceled:
            details = result.cancellation_details
            logger.warning(
                "Azure transcription canceled: %s | %s",
                details.reason,
                details.error_details,
            )
            raise PronunciationError("Transcription failed. Please try again.", 502)

        raise PronunciationError("Unexpected transcription result.", 502)
    finally:
        _safe_remove(wav_path)
