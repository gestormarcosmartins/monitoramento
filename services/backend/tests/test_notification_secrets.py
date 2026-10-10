import unittest

from central_monitor.secrets import decrypt_secret, encrypt_secret


class NotificationSecretTests(unittest.TestCase):
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
