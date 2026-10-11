import unittest
from unittest.mock import patch

from central_monitor.secrets import decrypt_secret, encrypt_secret


class NotificationSecretTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch("central_monitor.secrets.get_settings")
        patcher.start().return_value.central_secret_key = "x" * 32
        self.addCleanup(patcher.stop)

    def test_encrypts_and_decrypts_secret(self) -> None:
        encrypted = encrypt_secret("credencial-secreta")
        self.assertIsNotNone(encrypted)
        self.assertNotIn("credencial-secreta", encrypted)
        self.assertEqual(decrypt_secret(encrypted), "credencial-secreta")

    def test_empty_secret_remains_empty(self) -> None:
        self.assertIsNone(encrypt_secret(None))
        self.assertEqual(decrypt_secret(None), "")


if __name__ == "__main__":
    unittest.main()
