from datetime import UTC, datetime, timedelta

import pytest
from standardwebhooks.webhooks import Webhook, WebhookVerificationError
from ue_api.webhooks import signing

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
BODY = b'{"type":"customer.margin_negative","data":{"customer_id":"c1"}}'


def test_reference_library_accepts_our_signature() -> None:
    secret = signing.generate_secret()
    headers = signing.headers(secret, "msg_1", datetime.now(UTC), BODY)
    assert Webhook(secret).verify(BODY, headers) is not None


def test_we_accept_the_reference_library_signature() -> None:
    secret = signing.generate_secret()
    wh = Webhook(secret)
    sig = wh.sign("msg_2", NOW, BODY.decode())
    headers = {
        "webhook-id": "msg_2",
        "webhook-timestamp": str(int(NOW.timestamp())),
        "webhook-signature": sig,
    }
    signing.verify(secret, headers, BODY, NOW)


def test_tampered_body_fails_with_both_implementations() -> None:
    secret = signing.generate_secret()
    headers = signing.headers(secret, "msg_3", datetime.now(UTC), BODY)
    with pytest.raises(WebhookVerificationError):
        Webhook(secret).verify(BODY + b" ", headers)
    with pytest.raises(signing.InvalidSignatureError):
        signing.verify(secret, headers, BODY + b" ", datetime.now(UTC))


def test_stale_timestamp_is_rejected() -> None:
    secret = signing.generate_secret()
    headers = signing.headers(secret, "msg_4", NOW, BODY)
    with pytest.raises(signing.InvalidSignatureError, match="tolerance"):
        signing.verify(secret, headers, BODY, NOW + timedelta(minutes=6))


def test_any_of_several_signatures_may_match_during_rotation() -> None:
    old, new = signing.generate_secret(), signing.generate_secret()
    headers = signing.headers(new, "msg_5", NOW, BODY)
    headers["webhook-signature"] = (
        signing.sign(old, "msg_5", int(NOW.timestamp()), BODY) + " " + headers["webhook-signature"]
    )
    signing.verify(new, headers, BODY, NOW)
