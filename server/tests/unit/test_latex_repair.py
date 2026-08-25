"""Unit tests for the LaTeX repair chain in notes_graph.

These are pure string functions with no I/O, and they are where most of this
project's rendering bugs have actually lived — a model emitting `\\u0024`
instead of `$`, a JSON decoder eating the backslash off `\\hat`, an over-eager
collapse rule destroying matrix row separators. Each of those shipped because
the only way to notice was to read rendered notes by eye.
"""

import pytest

from app.services.notes_graph import _repair_latex_escapes, _repair_mangled_dollars


class TestRepairMangledDollars:
    """Models emit the unicode escape for "$" instead of the character, often
    *inside* a real pair of delimiters."""

    # Assembled from pieces rather than written as a literal: an editor or a
    # tool that interprets escape sequences would silently turn a literal
    # "$" into "$" and leave a test that proves nothing.
    ESC = chr(92) + "u0024"  # the six characters: \ u 0 0 2 4
    ESC_LONG = chr(92) + "U00000024"

    def test_escape_becomes_a_dollar(self) -> None:
        assert _repair_mangled_dollars(f"{self.ESC}x{self.ESC}") == "$x$"

    def test_long_form_escape_becomes_a_dollar(self) -> None:
        assert _repair_mangled_dollars(f"{self.ESC_LONG}x{self.ESC_LONG}") == "$x$"

    def test_five_digit_slip_becomes_a_dollar(self) -> None:
        # 4 decodes to STX + "4" rather than "$".
        assert _repair_mangled_dollars("\x024x\x024") == "$x$"

    def test_escape_inside_real_delimiters_does_not_double_up(self) -> None:
        # A real $ pair wrapping escapes for the same character. Replacing the
        # escapes naively yields $$x$$, promoting inline math to a display
        # block — the sentinel dance in the code exists to prevent that.
        assert _repair_mangled_dollars(f"${self.ESC}x{self.ESC}$") == "$x$"

    def test_real_display_delimiters_are_preserved(self) -> None:
        # The counterpart: $$ that was always $$ must stay $$.
        assert _repair_mangled_dollars("$$x$$") == "$$x$$"

    def test_plain_text_is_untouched(self) -> None:
        text = "A vector has magnitude and direction."
        assert _repair_mangled_dollars(text) == text


class TestRepairLatexEscapes:
    def test_clean_input_is_unchanged(self) -> None:
        assert _repair_latex_escapes(r"$\hat{i}$") == r"$\hat{i}$"

    def test_is_idempotent(self) -> None:
        # The repair runs on every note write, and notes are rewritten in full
        # each turn — so it is applied to its own output repeatedly.
        text = r"$\hat{i}$ and $\vec{v}$"
        once = _repair_latex_escapes(text)
        assert _repair_latex_escapes(once) == once

    def test_restores_a_dropped_backslash(self) -> None:
        # Structured output sometimes loses it: $hat{i}$ instead of $\hat{i}$.
        assert _repair_latex_escapes("$hat{i}$") == r"$\hat{i}$"

    def test_collapses_a_doubled_backslash_on_a_command(self) -> None:
        assert _repair_latex_escapes(r"$\\hat{i}$") == r"$\hat{i}$"

    def test_control_character_becomes_a_backslash(self) -> None:
        # A C0 control before a letter is a mangled escape, not content.
        assert _repair_latex_escapes("$\x05hat{i}$") == r"$\hat{i}$"

    def test_json_escape_artifacts_are_repaired(self) -> None:
        # \b, \f, \t, \r are real JSON escapes, so a decoder turns "\bhat"
        # into BACKSPACE + "hat". The letter has to be put back.
        assert _repair_latex_escapes("$\x08hat{i}$") == r"$\bhat{i}$"

    def test_only_touches_math_spans(self) -> None:
        # "hat" in prose must not sprout a backslash.
        text = "The hat {i} notation is common in prose."
        assert _repair_latex_escapes(text) == text

    def test_never_invents_a_command_letter(self) -> None:
        # Guards the \chat{i} incident: no input without a "c" should ever
        # produce one. The repair inserts backslashes, never letters.
        for source in (r"$\hat{i}$", "$hat{i}$", "$\x05hat{i}$", r"$\\hat{i}$"):
            assert r"\chat" not in _repair_latex_escapes(source)


class TestMatrixRowSeparators:
    r"""`\\` inside a matrix is a row separator, not an over-escaped command.

    Regression cover. The collapse rule used to match `\\` before *any*
    letter, so a row starting with one — `a&b\\c&d` — lost its separator and
    became the undefined command `\c`. Rows starting with a digit were
    unaffected, which is why numeric examples rendered and symbolic ones did
    not. The rule now requires a known command name.
    """

    def test_numeric_rows_survive(self) -> None:
        source = r"$$\begin{bmatrix}2&1\\0&3\end{bmatrix}$$"
        assert _repair_latex_escapes(source) == source

    def test_letter_rows_survive(self) -> None:
        source = r"$$\begin{bmatrix}a&b\\c&d\end{bmatrix}$$"
        assert _repair_latex_escapes(source) == source

    def test_aligned_environment_survives(self) -> None:
        source = r"$$\begin{aligned}x &= 1\\y &= 2\end{aligned}$$"
        assert _repair_latex_escapes(source) == source

    def test_the_exact_reply_that_was_broken(self) -> None:
        # Copied from the chat message that surfaced this.
        source = r"$\begin{bmatrix}a&b\\c&d\end{bmatrix}\begin{bmatrix}x\\y\end{bmatrix}$"
        assert _repair_latex_escapes(source) == source

    def test_over_escaped_commands_are_still_collapsed(self) -> None:
        # The fix must not stop the rule doing its original job.
        assert _repair_latex_escapes(r"$\\frac{a}{b}$") == r"$\frac{a}{b}$"

    def test_over_escaped_environment_is_collapsed(self) -> None:
        assert _repair_latex_escapes(r"$\\begin{bmatrix}1\end{bmatrix}$") == (
            r"$\begin{bmatrix}1\end{bmatrix}$"
        )

    def test_three_backslashes_before_a_command_still_collapse(self) -> None:
        # Documented as seen in production, and the reason the collapse runs
        # in a loop rather than a single pass.
        assert _repair_latex_escapes(r"$\\\hat{i}$") == r"$\hat{i}$"
