import unittest

from ai_karaoke.interfaces.console import dependency_output_tail


class CliOutputTests(unittest.TestCase):
    def test_dependency_tail_normalizes_countdown_output(self):
        output = "loading\rremaining 3\rremaining 2\rremaining 1\ndone\n"
        self.assertEqual(dependency_output_tail(output, line_count=2), "remaining 1\ndone")
