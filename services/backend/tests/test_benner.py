import unittest

from central_monitor.benner import _is_ok_state


class BennerStateTests(unittest.TestCase):
    def test_success_is_a_healthy_state(self):
        self.assertTrue(_is_ok_state("success"))

    def test_error_is_not_a_healthy_state(self):
        self.assertFalse(_is_ok_state("error"))


if __name__ == "__main__":
    unittest.main()
