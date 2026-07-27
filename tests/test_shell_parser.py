from claim_check.shell_parser import extract_commit_message


def test_simple_double_quoted_message():
    assert extract_commit_message('git commit -m "fix: thing"') == "fix: thing"


def test_simple_single_quoted_message():
    assert extract_commit_message("git commit -m 'fix: thing'") == "fix: thing"


def test_unquoted_single_word_message():
    assert extract_commit_message("git commit -m fix") == "fix"


def test_long_message_flag():
    assert extract_commit_message('git commit --message "fix: thing"') == "fix: thing"


def test_long_message_flag_with_equals():
    assert extract_commit_message('git commit --message="fix: thing"') == "fix: thing"


def test_bundled_short_flags_am():
    assert extract_commit_message('git commit -am "fix: thing"') == "fix: thing"


def test_amend_does_not_change_extraction():
    assert extract_commit_message('git commit --amend -m "fix: thing"') == "fix: thing"


def test_the_exact_heredoc_pattern_this_environment_uses():
    cmd = (
        "git commit -m \"$(cat <<'EOF'\n"
        "Fix the scale-invariance bug\n"
        "\n"
        "22 tests passing\n"
        "EOF\n"
        ")\""
    )
    assert extract_commit_message(cmd) == "Fix the scale-invariance bug\n\n22 tests passing"


def test_multiple_m_flags_joined_with_blank_line_matching_gits_own_behavior():
    assert extract_commit_message('git commit -m "first paragraph" -m "second paragraph"') == (
        "first paragraph\n\nsecond paragraph"
    )


def test_no_m_flag_at_all_is_a_clean_none_not_an_error():
    # No -m means an editor would open; there is nothing to statically check.
    assert extract_commit_message("git commit") is None
    assert extract_commit_message("git commit --amend") is None


def test_git_commit_inside_a_chained_command_is_still_found():
    assert extract_commit_message('cd repo && git commit -m "fix: thing"') == "fix: thing"


def test_git_commit_substring_inside_an_unrelated_echo_is_not_misidentified():
    # "git commit" appearing inside a quoted string argument to a totally
    # different command must not be mistaken for an actual invocation.
    assert extract_commit_message('echo "please run git commit -m later"') is None


def test_unbalanced_quotes_fails_open_returns_none_without_raising():
    extract_commit_message('git commit -m "unterminated')  # must not raise
    assert extract_commit_message('git commit -m "unterminated') is None


def test_heredoc_with_unresolved_unquoted_variable_expansion_returns_none():
    # An unquoted heredoc delimiter (<<EOF, not <<'EOF') means the shell
    # would expand $VAR before git ever sees it - we can't perform that
    # expansion, so extracting the literal "$VAR" text would be wrong.
    cmd = (
        "git commit -m \"$(cat <<EOF\n"
        "message referencing $SOME_VAR here\n"
        "EOF\n"
        ")\""
    )
    assert extract_commit_message(cmd) is None


def test_heredoc_with_quoted_delimiter_and_literal_dollar_sign_is_extracted_as_is():
    # A *quoted* delimiter (<<'EOF') disables shell expansion entirely, so
    # a literal "$" in the body is exactly what git would receive - safe
    # to extract verbatim, not a case for "unresolved expansion".
    cmd = (
        "git commit -m \"$(cat <<'EOF'\n"
        "price is $5, not a variable\n"
        "EOF\n"
        ")\""
    )
    assert extract_commit_message(cmd) == "price is $5, not a variable"


def test_first_of_multiple_git_commit_invocations_is_used():
    cmd = 'git commit -m "first" && git commit -m "second"'
    assert extract_commit_message(cmd) == "first"


def test_non_ascii_message_extracted_correctly():
    assert extract_commit_message('git commit -m "fix: café unicode test"') == "fix: café unicode test"
