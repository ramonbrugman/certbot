"""Test :mod:`certbot.display.util`."""
import io
import socket
import tempfile
import unittest

from certbot import errors
import certbot.tests.util as test_util

try:
    import mock
except ImportError:  # pragma: no cover
    from unittest import mock


class NotifyTest(unittest.TestCase):
    """Tests for certbot.display.util.notify"""

    @test_util.patch_display_util()
    def test_notify(self, mock_util):
        from certbot.display.util import notify
        notify("Hello World")
        mock_util().notification.assert_called_with(
            "Hello World", pause=False, decorate=False, wrap=False
        )


class NotificationTest(unittest.TestCase):
    """Tests for certbot.display.util.notification"""

    @test_util.patch_display_util()
    def test_notification(self, mock_util):
        from certbot.display.util import notification
        notification("Hello World")
        mock_util().notification.assert_called_with(
            "Hello World", pause=True, decorate=True, wrap=True, force_interactive=False
        )


class MenuTest(unittest.TestCase):
    """Tests for certbot.display.util.menu"""

    @test_util.patch_display_util()
    def test_menu(self, mock_util):
        from certbot.display.util import menu
        menu("Hello World", ["one", "two"], default=0)
        mock_util().menu.assert_called_with(
            "Hello World", ["one", "two"], default=0, cli_flag=None, force_interactive=False
        )


class InputTextTest(unittest.TestCase):
    """Tests for certbot.display.util.input_text"""

    @test_util.patch_display_util()
    def test_input_text(self, mock_util):
        from certbot.display.util import input_text
        input_text("Hello World", default="something")
        mock_util().input.assert_called_with(
            "Hello World", default='something', cli_flag=None, force_interactive=False
        )


class YesNoTest(unittest.TestCase):
    """Tests for certbot.display.util.yesno"""

    @test_util.patch_display_util()
    def test_yesno(self, mock_util):
        from certbot.display.util import yesno
        yesno("Hello World", default=True)
        mock_util().yesno.assert_called_with(
            "Hello World", yes_label='Yes', no_label='No', default=True, cli_flag=None,
            force_interactive=False
        )


class ChecklistTest(unittest.TestCase):
    """Tests for certbot.display.util.checklist"""

    @test_util.patch_display_util()
    def test_checklist(self, mock_util):
        from certbot.display.util import checklist
        checklist("Hello World", ["one", "two"], default="one")
        mock_util().checklist.assert_called_with(
            "Hello World", ['one', 'two'], default='one', cli_flag=None, force_interactive=False
        )


class DirectorySelectTest(unittest.TestCase):
    """Tests for certbot.display.util.directory_select"""

    @test_util.patch_display_util()
    def test_directory_select(self, mock_util):
        from certbot.display.util import directory_select
        directory_select("Hello World", default="something")
        mock_util().directory_select.assert_called_with(
            "Hello World", default='something', cli_flag=None, force_interactive=False
        )


class InputWithTimeoutTest(unittest.TestCase):
    """Tests for certbot.display.util.input_with_timeout."""
    @classmethod
    def _call(cls, *args, **kwargs):
        from certbot.display.util import input_with_timeout
        return input_with_timeout(*args, **kwargs)

    def test_eof(self):
        with tempfile.TemporaryFile("r+") as f:
            with mock.patch("certbot.display.util.sys.stdin", new=f):
                self.assertRaises(EOFError, self._call)

    def test_input(self, prompt=None):
        expected = "foo bar"
        stdin = io.StringIO(expected + "\n")
        with mock.patch("certbot.compat.misc.select.select") as mock_select:
            mock_select.return_value = ([stdin], [], [],)
            self.assertEqual(self._call(prompt), expected)

    @mock.patch("certbot.display.util.sys.stdout")
    def test_input_with_prompt(self, mock_stdout):
        prompt = "test prompt: "
        self.test_input(prompt)
        mock_stdout.write.assert_called_once_with(prompt)
        mock_stdout.flush.assert_called_once_with()

    def test_timeout(self):
        stdin = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        stdin.bind(('', 0))
        stdin.listen(1)
        with mock.patch("certbot.display.util.sys.stdin", stdin):
            self.assertRaises(errors.Error, self._call, timeout=0.001)
        stdin.close()


class SeparateListInputTest(unittest.TestCase):
    """Test Module functions."""
    def setUp(self):
        self.exp = ["a", "b", "c", "test"]

    @classmethod
    def _call(cls, input_):
        from certbot.display.util import separate_list_input
        return separate_list_input(input_)

    def test_commas(self):
        self.assertEqual(self._call("a,b,c,test"), self.exp)

    def test_spaces(self):
        self.assertEqual(self._call("a b c test"), self.exp)

    def test_both(self):
        self.assertEqual(self._call("a, b, c, test"), self.exp)

    def test_mess(self):
        actual = [
            self._call("  a , b    c \t test"),
            self._call(",a, ,, , b c  test  "),
            self._call(",,,,, , a b,,, , c,test"),
        ]

        for act in actual:
            self.assertEqual(act, self.exp)


class SummarizeDomainListTest(unittest.TestCase):
    @classmethod
    def _call(cls, domains):
        from certbot.display.util import summarize_domain_list
        return summarize_domain_list(domains)

    def test_single_domain(self):
        self.assertEqual("example.com", self._call(["example.com"]))

    def test_two_domains(self):
        self.assertEqual("example.com and example.org",
                         self._call(["example.com", "example.org"]))

    def test_many_domains(self):
        self.assertEqual("example.com and 2 more domains",
                         self._call(["example.com", "example.org", "a.example.com"]))

    def test_empty_domains(self):
        self.assertEqual("", self._call([]))


if __name__ == "__main__":
    unittest.main()  # pragma: no cover
