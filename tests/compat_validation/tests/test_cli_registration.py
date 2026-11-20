"""Every executable command declares its handler and safety policy."""

from argparse import _SubParsersAction
import unittest

from research_multiplier.cli import build_parser
from research_multiplier.cli._command import Command


def leaves(parser, names=(), inherited=None):
    settings = {**(inherited or {}), **parser._defaults}
    for action in parser._actions:
        if isinstance(action, _SubParsersAction):
            for name, child in action.choices.items():
                yield from leaves(child, (*names, name), settings)
            return
    yield names, settings


class CommandRegistrationTests(unittest.TestCase):
    def test_every_leaf_has_command_and_handler(self):
        commands = list(leaves(build_parser()))
        self.assertTrue(commands)
        for names, settings in commands:
            with self.subTest(command=" ".join(names)):
                self.assertIsInstance(settings.get("_command"), Command)
                self.assertTrue(callable(settings.get("_handler")))
