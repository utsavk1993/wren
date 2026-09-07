"""Checks on who may start a call, and how many.

A public link is a link anyone can hold open, and every call spends tokens,
transcription minutes, and requests against a customer system allowed fifteen
thousand a day.
"""

from __future__ import annotations

import pytest

from access import Gate


def test_nothing_is_guarded_unless_a_passphrase_is_set():
    """Running it locally must be unchanged by any of this."""
    gate = Gate(passphrase="")
    assert not gate.guarded
    assert gate.check_passphrase("")
    assert gate.check_passphrase("anything at all")


@pytest.mark.parametrize("given", ["", "wrong", "TRY-WREN", "try-wren "])
def test_a_wrong_passphrase_is_refused(given):
    gate = Gate(passphrase="try-wren")
    if given.strip() == "try-wren":
        pytest.skip("that one is right after trimming")
    assert not gate.check_passphrase(given)


def test_the_right_passphrase_is_accepted_with_stray_spaces():
    # People paste these, and a trailing space should not be a locked door.
    gate = Gate(passphrase="try-wren")
    assert gate.check_passphrase("try-wren")
    assert gate.check_passphrase("  try-wren  ")


def test_only_so_many_calls_at_once():
    gate = Gate(passphrase="", max_concurrent=2, max_per_day=100)
    assert gate.may_start()[0]
    gate.started()
    gate.started()
    allowed, why = gate.may_start()
    assert not allowed
    assert "already" in why, "and it should say why in words a caller understands"


def test_a_finished_call_frees_its_place():
    gate = Gate(passphrase="", max_concurrent=1, max_per_day=100)
    gate.started()
    assert not gate.may_start()[0]
    gate.ended()
    assert gate.may_start()[0]


def test_a_place_cannot_be_freed_twice():
    """A call that fails before it starts would otherwise leave a permanent gap.

    Ending more often than starting must not drive the count negative, or the
    limit quietly stops applying.
    """
    gate = Gate(passphrase="", max_concurrent=1, max_per_day=100)
    gate.ended()
    gate.ended()
    gate.started()
    assert not gate.may_start()[0], "one call is still one call"


def test_there_is_a_ceiling_for_the_day():
    gate = Gate(passphrase="", max_concurrent=10, max_per_day=3)
    for _ in range(3):
        assert gate.may_start()[0]
        gate.started()
        gate.ended()
    allowed, why = gate.may_start()
    assert not allowed
    assert "today" in why


def test_the_daily_ceiling_is_below_what_the_customer_system_allows():
    """The point of the cap is that it runs out before the CRM does.

    That org answers fifteen thousand requests a day and a call makes several,
    so a ceiling anywhere near that would lock the account out for everyone
    rather than just turning one caller away.
    """
    gate = Gate()
    requests_per_call = 4
    assert gate.max_per_day * requests_per_call < 15_000
