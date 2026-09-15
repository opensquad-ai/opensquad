"""Native-thought XML should drop real blocks, not punch holes in mentions."""

from opensquad.xml_parser import (
    StreamingTagParser,
    StreamingThoughtBlockDropper,
    strip_prompted_thought_blocks,
    strip_silent_protocol_blocks,
)

SESSION_LIKE = (
    "If a model outputs <thought> tags in the content stream, "
    "the code only removes the tags themselves:\n"
    "```python\n"
    'content = content.replace("<thought>", "").replace("</thought>", "")\n'
    "```\n"
    "That is the bug."
)

REAL_BLOCK = "Hello\n<thought>\nduplicate native-style dump\n</thought>\nDone."


def _feed_chunks(text: str, size: int) -> str:
    dropper = StreamingThoughtBlockDropper()
    out = []
    for i in range(0, len(text), size):
        out.append(dropper.feed(text[i : i + size]))
    out.append(dropper.flush())
    return "".join(out)


def test_mention_and_code_sample_are_kept():
    for size in (1, 3, 7, 64, 4000):
        got = _feed_chunks(SESSION_LIKE, size)
        assert "<thought> tags in the content stream" in got, size
        assert 'replace("<thought>", "")' in got, size
        assert 'replace("</thought>", "")' in got, size
        assert got.replace("\r\n", "\n") == SESSION_LIKE


def test_line_start_thought_block_is_dropped():
    got = strip_prompted_thought_blocks(REAL_BLOCK)
    assert "duplicate native-style dump" not in got
    assert "Hello" in got
    assert "Done." in got
    assert "<thought>" not in got.lower()


def test_think_block_dropped_mention_kept():
    text = "see <think> tags please\n<think>\nsecret\n</think>\nOK"
    got = strip_prompted_thought_blocks(text)
    assert "see <think> tags please" in got
    assert "secret" not in got
    assert "OK" in got


def test_space_after_open_tag_is_mention_even_at_line_start():
    text = "<thought> tags should stay\nNext line"
    got = strip_prompted_thought_blocks(text)
    assert "<thought> tags should stay" in got
    assert "Next line" in got


def test_strip_silent_does_not_hole_punch_thought_mentions():
    got = strip_silent_protocol_blocks(SESSION_LIKE)
    assert "<thought> tags in the content stream" in got
    assert 'replace("<thought>", "")' in got
    assert strip_silent_protocol_blocks("<timeout>60</timeout>") == ""
    assert "60" not in strip_silent_protocol_blocks("hi <timeout>60</timeout>")


def test_parser_passthrough_keeps_mention_out_of_thought_handler():
    leaked: list[str] = []
    thoughts: list[str] = []
    parser = StreamingTagParser(
        handlers={"thought": thoughts.append, "think": thoughts.append},
        default_handler=leaked.append,
    )
    parser.set_passthrough_tags(("thought", "think"))
    dropper = StreamingThoughtBlockDropper()
    blob = SESSION_LIKE + "\n<thought>\nhidden dump\n</thought>\nVisible."
    for i in range(0, len(blob), 5):
        piece = dropper.feed(blob[i : i + 5])
        if piece:
            parser.feed(piece)
    tail = dropper.flush()
    if tail:
        parser.feed(tail)
    parser.finish()
    out = "".join(leaked)
    assert "<thought> tags in the content stream" in out
    assert 'replace("</thought>", "")' in out
    assert "hidden dump" not in out
    assert "Visible." in out
    assert "hidden dump" not in "".join(thoughts)
