import unittest

from central_monitor.security import validate_outbound_url


class OutboundSecurityTests(unittest.TestCase):
    def test_allows_public_and_private_monitoring_targets(self):
        self.assertEqual(validate_outbound_url("https://monitor.example.com/health"),"https://monitor.example.com/health")
        self.assertEqual(validate_outbound_url("http://172.19.19.248:8080/health"),"http://172.19.19.248:8080/health")

    def test_blocks_metadata_loopback_and_embedded_credentials(self):
        for url in ("http://169.254.169.254/latest/meta-data","http://127.0.0.1/admin","http://user:pass@example.com"):
            with self.subTest(url=url),self.assertRaises(ValueError):validate_outbound_url(url)

    def test_blocks_non_http_protocols(self):
        with self.assertRaises(ValueError):validate_outbound_url("file:///etc/passwd")


if __name__=="__main__":unittest.main()
