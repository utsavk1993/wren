"""Checks on confirming a case in writing.

The rule that matters here is that a failed message never reaches the caller as
an error, and never gets reported as sent. The case exists either way; what
changes is only what the agent is allowed to say next.
"""

from __future__ import annotations

import httpx
import pytest

from systems.notify import Notifier

pytestmark = pytest.mark.asyncio


def configured(handler) -> Notifier:
    """A notifier with both channels set up, talking to a fake transport."""
    return Notifier(
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        twilio_sid="AC-test",
        twilio_token="token",
        twilio_from="+15550000000",
        sendgrid_key="SG.test",
        sendgrid_from="wren@example.com",
    )


async def test_a_text_reports_where_it_went():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"sid": "SM1"})

    delivery = await configured(handler).send_sms("+15551234567", "Your case is 00123456.")
    assert delivery.sent
    assert delivery.channel == "sms"
    assert "Messages.json" in str(seen[0].url)
    assert b"00123456" in seen[0].content


async def test_an_email_reports_where_it_went():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202)

    delivery = await configured(handler).send_email(
        "someone@example.com", "Your case 00123456", "Your case number is 00123456."
    )
    assert delivery.sent
    assert delivery.channel == "email"


async def test_nothing_is_sent_without_credentials():
    """The project has to run for anyone who clones it.

    With no keys the message is logged rather than sent, and reported unsent so
    the agent does not tell the caller about a text that does not exist.
    """
    quiet = Notifier(
        http=httpx.AsyncClient(transport=httpx.MockTransport(_never_called)),
        twilio_sid="", twilio_token="", twilio_from="",
        sendgrid_key="", sendgrid_from="",
    )
    assert not quiet.sms_configured
    assert not quiet.email_configured
    assert not (await quiet.send_sms("+15551234567", "hello")).sent
    assert not (await quiet.send_email("a@example.com", "hi", "hello")).sent


async def test_a_provider_outage_does_not_raise():
    """A messaging provider being down cannot end a call.

    The case is already open in the customer system by the time this runs, so
    the caller must not hear an error about it.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    delivery = await configured(handler).send_sms("+15551234567", "hello")
    assert not delivery.sent
    assert delivery.detail


async def test_a_refused_message_is_not_reported_as_sent():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="not a valid number")

    delivery = await configured(handler).send_sms("+1555", "hello")
    assert not delivery.sent
    assert "400" in delivery.detail


async def test_a_missing_address_is_not_an_error():
    """Not every record has both. A blank one is simply not written to."""
    notifier = configured(_never_called)
    assert not (await notifier.send_sms("", "hello")).sent
    assert not (await notifier.send_email("", "hi", "hello")).sent


def _never_called(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"nothing should have been sent, but {request.url} was")
