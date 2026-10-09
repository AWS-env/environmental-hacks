# Synthetic TST-01 fixture: several assertions without messages. Never executed.
import unittest

import numpy as np


class UserTest(unittest.TestCase):
    def test_flags(self):
        user = make_user()
        self.assertTrue(user.active)
        self.assertFalse(user.banned)
        self.assertTrue(user.verified)

    def test_fields(self):
        user = make_user()
        self.assertEqual(user.name, "ada")
        self.assertEqual(user.age, 36)

    def test_mixed(self):
        user = make_user()
        self.assertTrue(user.active, "new users start active")
        self.assertIn("admin", user.roles)
        self.assertIsNone(user.deleted_at)


def test_bare_asserts():
    user = make_user()
    assert user.name == "ada"
    assert user.age == 36


def test_numpy_truthiness():
    scores = load_scores()
    np.testing.assert_(scores.min() >= 0)
    np.testing.assert_(scores.max() <= 1)
