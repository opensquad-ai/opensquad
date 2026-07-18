"""Deprecated alias — use ``opensquad.audio.openai_tts`` instead.

Kept so older imports of ``stepfun_tts`` keep working.
"""

from opensquad.audio.openai_tts import synthesize_speech, synthesize_with_card

__all__ = ["synthesize_speech", "synthesize_with_card"]
