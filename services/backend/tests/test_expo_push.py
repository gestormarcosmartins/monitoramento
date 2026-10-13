import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

try:
    from cryptography.fernet import Fernet
    from central_monitor import expo_push
except ImportError:  # backend dependencies are not installed
    expo_push = None

TOKEN = "ExponentPushToken[abcdEFGH1234_-xyz]"


@unittest.skipIf(expo_push is None, "backend dependencies not installed")
class ExpoPushPureTests(unittest.TestCase):
    def test_token_format(self):
        self.assertTrue(expo_push.TOKEN_RE.fullmatch(TOKEN))
        self.assertTrue(expo_push.TOKEN_RE.fullmatch("ExpoPushToken[abcdefgh1234]"))
        for bad in ("abc", "ExponentPushToken[]", "ExponentPushToken[a b c d e f g h]", "fcm:APA91bE", "https://x.test/"):
            self.assertFalse(expo_push.TOKEN_RE.fullmatch(bad), bad)

    def test_hash_hint_and_encryption_roundtrip(self):
        settings = mock.Mock(push_token_key=Fernet.generate_key().decode())
        with mock.patch.object(expo_push, "get_settings", return_value=settings):
            cipher = expo_push.encrypt_token(TOKEN)
            self.assertNotIn("abcdEFGH", cipher)  # never stored in clear
            self.assertEqual(expo_push.decrypt_token(cipher), TOKEN)
        self.assertEqual(len(expo_push.token_hash(TOKEN)), 64)
        self.assertEqual(expo_push.token_hint(TOKEN), "…-xyz")

    def test_missing_key_fails_closed(self):
        with mock.patch.object(expo_push, "get_settings", return_value=mock.Mock(push_token_key="")):
            with self.assertRaises(RuntimeError):
                expo_push.encrypt_token(TOKEN)

    def test_project_allowlist(self):
        with mock.patch.object(expo_push, "get_settings", return_value=mock.Mock(expo_project_ids=" a-1 , b-2,, ")):
            self.assertEqual(expo_push.allowed_project_ids(), {"a-1", "b-2"})
        with mock.patch.object(expo_push, "get_settings", return_value=mock.Mock(expo_project_ids="")):
            self.assertEqual(expo_push.allowed_project_ids(), set())  # nothing accepted until configured

    def test_ticket_classification(self):
        self.assertEqual(expo_push.classify_ticket({"status": "ok", "id": "T1"}), ("SENT_TO_EXPO", "T1", None))
        self.assertEqual(expo_push.classify_ticket({"status": "error", "details": {"error": "DeviceNotRegistered"}})[0], "DEVICE_INVALID")
        self.assertEqual(expo_push.classify_ticket({"status": "error", "details": {"error": "MessageRateExceeded"}})[0], "RETRY_WAIT")
        self.assertEqual(expo_push.classify_ticket({"status": "error", "details": {"error": "MessageTooBig"}})[0], "FAILED_FINAL")
        self.assertEqual(expo_push.classify_ticket({"status": "ok"})[0], "FAILED_FINAL")  # ok without ticket id is not a hand-off

    def test_receipt_classification(self):
        self.assertEqual(expo_push.classify_receipt({"status": "ok"}), ("PROVIDER_ACCEPTED", None))
        self.assertEqual(expo_push.classify_receipt({"status": "error", "details": {"error": "DeviceNotRegistered"}}),
                         ("DEVICE_INVALID", "DeviceNotRegistered"))
        self.assertEqual(expo_push.classify_receipt({"status": "error", "details": {"error": "InvalidCredentials"}})[0], "FAILED_FINAL")

    def test_backoff_grows_is_capped_and_jittered(self):
        delays = [expo_push.retry_delay_seconds(n) for n in range(1, 12)]
        self.assertTrue(all(delays[i] < delays[i + 1] * 1.3 for i in range(5)))
        self.assertLessEqual(max(delays), 900 * 1.2 + 1)
        self.assertGreater(len({expo_push.retry_delay_seconds(3) for _ in range(20)}), 1)

    def test_message_carries_ids_only_and_urgent_channel(self):
        now = datetime.now(UTC)
        row = {"severity": "critical", "title": "Nexora · Novo incidente", "body_safe": "Servidor X fora", "action_type": "incident",
               "notification_id": "n1", "domain": "INFRA", "action_ref": "i1", "expires_at": now + timedelta(hours=1)}
        message = expo_push.build_message(TOKEN, row, "tenant-1", now)
        self.assertEqual(message["channelId"], "alerts")
        self.assertEqual(message["priority"], "high")
        self.assertEqual(set(message["data"]), {"type", "notificationId", "tenantId", "domain", "actionRef"})
        low = expo_push.build_message(TOKEN, {**row, "severity": "medium"}, "tenant-1", now)
        self.assertEqual((low["channelId"], low["priority"]), ("default", "default"))

    def test_registration_payload_validation(self):
        base = {"expoPushToken": TOKEN, "platform": "android", "installationId": "install-0001",
                "expoProjectId": "11111111-2222-3333-4444-555555555555", "appVersion": "1.0.12"}
        self.assertEqual(expo_push.DeviceIn(**base).platform, "android")
        for override in ({"platform": "web"}, {"expoPushToken": "nope"}, {"installationId": "x"}):
            with self.assertRaises(ValueError):
                expo_push.DeviceIn(**{**base, **override})


if __name__ == "__main__":
    unittest.main()
