import unittest

from app.realtime_payload import transform_live_transcribe_language


class TestLiveTranscribeLanguage(unittest.TestCase):
    def test_replaces_singular_language_on_pipecat_session_payload(self):
        payload = {
            "type": "session.update",
            "session": {
                "audio": {
                    "input": {
                        "transcription": {
                            "model": "gpt-live-transcribe",
                            "language": "en",
                        }
                    }
                }
            },
        }

        transform_live_transcribe_language(payload)

        self.assertEqual(
            payload["session"]["audio"]["input"]["transcription"],
            {"model": "gpt-live-transcribe", "languages": ["en"]},
        )

    def test_leaves_other_transcription_models_unchanged(self):
        payload = {
            "type": "session.update",
            "session": {
                "audio": {
                    "input": {
                        "transcription": {
                            "model": "gpt-4o-transcribe",
                            "language": "en",
                        }
                    }
                }
            },
        }

        transform_live_transcribe_language(payload)

        self.assertEqual(
            payload["session"]["audio"]["input"]["transcription"],
            {"model": "gpt-4o-transcribe", "language": "en"},
        )
