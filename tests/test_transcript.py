"""Reading the last assistant message out of a Claude Code transcript.

Every failure mode here resolves to None, which the Stop hook reads as "no
claim to check" and therefore as silence. A transcript we cannot read is not
evidence that anything is wrong.
"""

import json

import pytest

from claim_check import transcript
from claim_check.transcript import (
    ReplayAborted, assistant_text, iter_records, record_timestamp,
)


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


def test_assistant_text_joins_only_text_blocks():
    record = {
        "type": "assistant",
        "message": {"content": [
            {"type": "text", "text": "first"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            {"type": "text", "text": "second"},
        ]},
    }
    assert assistant_text(record) == "first\nsecond"


def test_assistant_text_returns_empty_for_malformed_records():
    assert assistant_text({}) == ""
    assert assistant_text({"message": None}) == ""
    assert assistant_text({"message": {"content": "not a list"}}) == ""
    assert assistant_text({"message": {"content": [{"type": "text"}]}}) == ""


def _write_jsonl(path, records):
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    return path


def test_iter_records_yields_every_record_in_file_order(tmp_path):
    p = _write_jsonl(tmp_path / "t.jsonl", [{"i": 0}, {"i": 1}, {"i": 2}])
    assert [r["i"] for r in iter_records(p)] == [0, 1, 2]


def test_iter_records_skips_corrupt_and_blank_lines_without_stopping(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"i": 0}\n\nnot json at all\n{"i": 1}\n', encoding="utf-8")
    assert [r["i"] for r in iter_records(p)] == [0, 1]


def test_iter_records_skips_a_truncated_final_line(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"i": 0}\n{"i": 1', encoding="utf-8")
    assert [r["i"] for r in iter_records(p)] == [0]


def test_iter_records_yields_nothing_for_a_missing_file(tmp_path):
    assert list(iter_records(tmp_path / "nope.jsonl")) == []


def test_iter_records_yields_nothing_for_a_directory(tmp_path):
    # A directory raises PermissionError on Windows, IsADirectoryError on Linux.
    assert list(iter_records(tmp_path)) == []


def test_iter_records_yields_nothing_for_an_unusable_path_type():
    assert list(iter_records(None)) == []


def test_iter_records_raises_when_the_budget_is_exceeded(tmp_path):
    p = _write_jsonl(tmp_path / "t.jsonl", [{"i": n} for n in range(5000)])
    with pytest.raises(ReplayAborted):
        # A zero budget is already spent, so the first check trips it.
        list(iter_records(p, budget_s=0.0))


def test_iter_records_completes_within_a_generous_budget(tmp_path):
    p = _write_jsonl(tmp_path / "t.jsonl", [{"i": n} for n in range(5000)])
    assert len(list(iter_records(p, budget_s=30.0))) == 5000


def test_record_timestamp_parses_iso8601_with_z():
    ts = record_timestamp({"timestamp": "2026-08-11T05:03:41.000Z"})
    assert ts is not None and ts > 0


def test_record_timestamp_returns_none_for_missing_or_malformed():
    assert record_timestamp({}) is None
    assert record_timestamp({"timestamp": 12345}) is None
    assert record_timestamp({"timestamp": "not a date"}) is None
