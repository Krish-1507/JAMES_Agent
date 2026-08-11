"""WhatsApp gateway — Twilio WhatsApp API via plain ``requests`` (no extra deps).

Outbound: POST to the Twilio Messages REST endpoint with HTTP basic auth.
Inbound: Twilio posts webhooks to ``POST /api/gateway/whatsapp`` on the JAMES
web server, which the channel exposes as :meth:`handle_webhook`.
"""

from __future__ import annotations

import requests  # nosec B113 - Twilio REST client; creds go over TLS basic auth

from .base import GatewayChannel

_TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


class WhatsAppChannel(GatewayChannel):
    name = "whatsapp"

    def __init__(self, manager, account_sid: str, auth_token: str, from_number: str) -> None:
        super().__init__(manager)
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number

    def _run(self) -> None:
        # Inbound messages arrive over the webhook; this thread only keeps the
        # channel alive until stop() is called.
        while not self._stop.wait(60):
            pass

    def send(self, text: str, chat_id: str = "") -> bool:
        target = chat_id or self.last_chat_id
        if not target:
            return False
        response = requests.post(
            _TWILIO_API.format(sid=self.account_sid),
            data={"From": self.from_number, "To": target, "Body": text},
            auth=(self.account_sid, self.auth_token),
            timeout=35,
        )
        if response.status_code not in (200, 201):
            self.error = f"twilio returned HTTP {response.status_code}"
            return False
        return True

    def handle_webhook(self, form: dict) -> bool:
        """Handle an inbound webhook POST (form-encoded Twilio payload)."""
        body = (form.get("Body") or "").strip()
        from_number = (form.get("From") or "").strip()
        if not body or not from_number:
            return False
        self._dispatch(body, chat_id=from_number, sender=from_number)
        return True
