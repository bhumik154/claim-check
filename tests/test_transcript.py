"""Reading the last assistant message out of a Claude Code transcript.

Every failure mode here resolves to None, which the Stop hook reads as "no
claim to check" and therefore as silence. A transcript we cannot read is not
evidence that anything is wrong.
"""

import json

from claim_check import transcript


def _assistant(text, **extra):
    record = {
        "type": "assistant",
        "isSidechain": False,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }
    record.update(extra)
    return json.dumps(record)


def _tool_use(name="Bash"):
    return json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": name, "input": {"command": "ls"}}
        ]},
    })


def _user(text):
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}})


def _write(tmp_path, *lines):
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_reads_the_last_assistant_text_message(tmp_path):
    path = _write(tmp_path, _assistant("first"), _user("hi"), _assistant("second"))
    assert transcript.last_assistant_text(path) == "second"


def test_a_tool_use_only_record_is_skipped(tmp_path):
    # An assistant record whose content holds only a tool call carries no
    # prose, so it cannot contain a claim. The last message with actual text
    # is the one that matters.
    path = _write(tmp_path, _assistant("the real summary"), _tool_use())
    assert transcript.last_assistant_text(path) == "the real summary"


def test_multiple_text_blocks_in_one_message_are_joined(tmp_path):
    record = json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [
            {"type": "text", "text": "part one"},
            {"type": "tool_use", "id": "t", "name": "Bash", "input": {}},
            {"type": "text", "text": "part two"},
        ]},
    })
    path = _write(tmp_path, record)
    assert transcript.last_assistant_text(path) == "part one\npart two"


def test_subagent_records_are_ignored(tmp_path):
    # Subagent output normally lives in separate files, but isSidechain is
    # the documented marker and a sidechain message is not the main agent
    # speaking to the user.
    path = _write(
        tmp_path,
        _assistant("main agent speaking"),
        _assistant("subagent speaking", isSidechain=True),
    )
    assert transcript.last_assistant_text(path) == "main agent speaking"


def test_only_the_tail_of_a_large_transcript_is_read(tmp_path):
    # Real transcripts reach 15 MB. Reading the whole file on every turn end
    # is not acceptable, so only the last window is inspected.
    path = tmp_path / "big.jsonl"
    filler = _user("x" * 4000)
    with path.open("w", encoding="utf-8") as stream:
        stream.write(_assistant("ancient, far outside the window") + "\n")
        for _ in range(200):
            stream.write(filler + "\n")
        stream.write(_assistant("recent") + "\n")
    assert path.stat().st_size > 512 * 1024
    assert transcript.last_assistant_text(path) == "recent"


def test_a_message_older_than_the_window_is_reported_as_absent(tmp_path):
    # Bias: unreadable means "no claim", never "the claim is wrong".
    path = tmp_path / "big.jsonl"
    filler = _user("y" * 4000)
    with path.open("w", encoding="utf-8") as stream:
        stream.write(_assistant("too far back") + "\n")
        for _ in range(200):
            stream.write(filler + "\n")
    assert transcript.last_assistant_text(path) is None


def test_a_partial_first_line_from_the_seek_does_not_break_parsing(tmp_path):
    # Seeking into the middle of the file lands mid-record. That fragment is
    # not valid JSON and must be discarded rather than crashing the read.
    path = tmp_path / "partial.jsonl"
    with path.open("w", encoding="utf-8") as stream:
        for _ in range(300):
            stream.write(_user("z" * 4000) + "\n")
        stream.write(_assistant("after the seek boundary") + "\n")
    assert transcript.last_assistant_text(path, max_bytes=8192) == "after the seek boundary"


def test_invalid_utf8_bytes_are_replaced_rather_than_raising(tmp_path):
    path = tmp_path / "bad.jsonl"
    payload = _assistant("clean text").encode("utf-8")
    path.write_bytes(b"\x81\xff garbage\n" + payload + b"\n")
    assert transcript.last_assistant_text(path) == "clean text"


def test_unparseable_lines_are_skipped_not_fatal(tmp_path):
    path = _write(tmp_path, _assistant("good"), "{not json at all", "[1,2,3]", "null")
    assert transcript.last_assistant_text(path) == "good"


def test_missing_empty_and_directory_paths_all_return_none(tmp_path):
    assert transcript.last_assistant_text(tmp_path / "nope.jsonl") is None
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert transcript.last_assistant_text(empty) is None
    assert transcript.last_assistant_text(tmp_path) is None
    assert transcript.last_assistant_text(None) is None
    assert transcript.last_assistant_text(42) is None


def test_a_transcript_with_no_assistant_text_returns_none(tmp_path):
    path = _write(tmp_path, _user("hello"), _tool_use())
    assert transcript.last_assistant_text(path) is None


def test_an_empty_text_block_is_not_treated_as_a_message(tmp_path):
    path = _write(tmp_path, _assistant("real content"), _assistant("   "))
    assert transcript.last_assistant_text(path) == "real content"
