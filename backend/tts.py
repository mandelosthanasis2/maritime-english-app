"""Azure Speech text-to-speech synthesis.

Synthesizes English text to MP3 audio using an Azure neural voice, suitable for
the maritime/radio context. Reuses the existing AZURE_SPEECH_KEY / REGION config.
"""

import logging
import os

from pronunciation import PronunciationError
from usage import log_usage

logger = logging.getLogger(__name__)

# Clear, natural English neural voice. Overridable via env.
DEFAULT_VOICE = os.environ.get("AZURE_TTS_VOICE", "en-US-GuyNeural")
MAX_TEXT_LENGTH = 1000


def synthesize(text, voice=None, user_id=None):
    """Synthesize text to MP3 bytes. Raises PronunciationError on failure."""
    text = (text or "").strip()
    if not text:
        raise PronunciationError("text is required.", 400)
    if len(text) > MAX_TEXT_LENGTH:
        raise PronunciationError("Text is too long to synthesize.", 400)

    key = os.environ.get("AZURE_SPEECH_KEY")
    region = os.environ.get("AZURE_SPEECH_REGION")
    if not key or not region:
        logger.error(
            "Azure Speech is not configured: AZURE_SPEECH_KEY set=%s, "
            "AZURE_SPEECH_REGION set=%s.",
            bool(key),
            bool(region),
        )
        raise PronunciationError("Text-to-speech is not configured on the server.", 503)

    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError as exc:  # pragma: no cover - dependency missing
        logger.exception("azure-cognitiveservices-speech failed to import.")
        raise PronunciationError("Text-to-speech is not available on the server.", 503) from exc

    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    speech_config.speech_synthesis_voice_name = voice or DEFAULT_VOICE
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3
    )

    # audio_config=None keeps the audio in memory instead of playing to a device.
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
    result = synthesizer.speak_text_async(text).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        # Azure bills neural TTS per character of input text.
        log_usage(provider="azure_tts", endpoint="tts", units=len(text), user_id=user_id)
        return bytes(result.audio_data)

    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        logger.warning(
            "Azure TTS canceled: %s | %s",
            details.reason,
            details.error_details,
        )
        raise PronunciationError("Speech synthesis failed. Please try again.", 502)

    raise PronunciationError("Unexpected synthesis result.", 502)
