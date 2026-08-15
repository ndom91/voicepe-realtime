"""Compatibility transforms for OpenAI Realtime client-event payloads."""


def transform_live_transcribe_language(payload: dict) -> None:
    """Adapt Pipecat's singular language field for gpt-live-transcribe."""
    if payload.get("type") != "session.update":
        return

    transcription = (
        payload.get("session", {})
        .get("audio", {})
        .get("input", {})
        .get("transcription")
    )
    if not transcription or transcription.get("model") != "gpt-live-transcribe":
        return

    language = transcription.pop("language", None)
    if language:
        transcription["languages"] = [language]
