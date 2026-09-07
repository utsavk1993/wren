"""Telling the caller something in writing.

A case number said out loud, once, to someone standing over a broken sensor is
a number they will not have in an hour. It goes out as a text and an email as
well, so there is something to come back to.

Neither is required. With no credentials the message is written to the log and
reported as unsent, which keeps the project runnable by anyone who clones it
and keeps the tests off the network. What must not happen is a claim that a
text was sent when it was not: the caller is told about the text only if one
actually went, so `sent` is what the agent is allowed to say.

Nothing here is allowed to sink a call. The case already exists in the customer
system by the time any of this runs, and an outage at a messaging provider is
not a reason for the caller to hear an error.

Addresses are redacted at the call site rather than left to the formatter,
because only the JSON formatter redacts and the text one is the default while
developing. These lines are the only place a number is logged next to a
delivery failure, which is exactly the line someone will paste into a ticket.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

from observability import redact

log = logging.getLogger(__name__)

TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"

# Short, because this is happening while the caller is still on the phone. A
# message that has not gone by now is not worth holding the call open for; it
# is reported unsent and the agent simply does not mention it.
REQUEST_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class Delivery:
    """What happened to one message."""

    channel: str
    to: str
    sent: bool
    detail: str = ""


class Notifier:
    """Sends the caller a text and an email. Never raises."""

    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        twilio_sid: str | None = None,
        twilio_token: str | None = None,
        twilio_from: str | None = None,
        sendgrid_key: str | None = None,
        sendgrid_from: str | None = None,
    ) -> None:
        self.twilio_sid = twilio_sid or os.environ.get("TWILIO_ACCOUNT_SID", "")
        self.twilio_token = twilio_token or os.environ.get("TWILIO_AUTH_TOKEN", "")
        self.twilio_from = twilio_from or os.environ.get("TWILIO_FROM_NUMBER", "")
        self.sendgrid_key = sendgrid_key or os.environ.get("SENDGRID_API_KEY", "")
        self.sendgrid_from = sendgrid_from or os.environ.get("SENDGRID_FROM_EMAIL", "")
        self._http = http or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        self._owns_http = http is None

    @property
    def sms_configured(self) -> bool:
        return bool(self.twilio_sid and self.twilio_token and self.twilio_from)

    @property
    def email_configured(self) -> bool:
        return bool(self.sendgrid_key and self.sendgrid_from)

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def send_sms(self, to: str, body: str) -> Delivery:
        if not to:
            return Delivery("sms", to, False, "no mobile number on the account")
        if not self.sms_configured:
            log.info("sms not configured; would have sent to %s: %s", redact(to), body)
            return Delivery("sms", to, False, "sms is not configured")
        try:
            response = await self._http.post(
                TWILIO_URL.format(sid=self.twilio_sid),
                auth=(self.twilio_sid, self.twilio_token),
                data={"To": to, "From": self.twilio_from, "Body": body},
            )
        except Exception as exc:  # noqa: BLE001 - a failed text cannot end a call
            log.warning("could not send a text to %s: %s", redact(to), exc)
            return Delivery("sms", to, False, "the message could not be sent")
        if response.status_code >= 300:
            log.warning(
                "text to %s refused: HTTP %s %s",
                redact(to), response.status_code, response.text[:200],
            )
            return Delivery("sms", to, False, f"HTTP {response.status_code}")
        log.info("texted %s", redact(to))
        return Delivery("sms", to, True)

    async def send_email(self, to: str, subject: str, body: str) -> Delivery:
        if not to:
            return Delivery("email", to, False, "no email address on the account")
        if not self.email_configured:
            log.info("email not configured; would have sent to %s: %s", redact(to), subject)
            return Delivery("email", to, False, "email is not configured")
        try:
            response = await self._http.post(
                SENDGRID_URL,
                headers={"Authorization": f"Bearer {self.sendgrid_key}"},
                json={
                    "personalizations": [{"to": [{"email": to}]}],
                    "from": {"email": self.sendgrid_from},
                    "subject": subject,
                    "content": [{"type": "text/plain", "value": body}],
                },
            )
        except Exception as exc:  # noqa: BLE001 - a failed email cannot end a call
            log.warning("could not email %s: %s", redact(to), exc)
            return Delivery("email", to, False, "the message could not be sent")
        if response.status_code >= 300:
            log.warning(
                "email to %s refused: HTTP %s %s",
                redact(to), response.status_code, response.text[:200],
            )
            return Delivery("email", to, False, f"HTTP {response.status_code}")
        log.info("emailed %s", redact(to))
        return Delivery("email", to, True)
