# Synthetic TST-01 fixture: documented or single assertions. Never executed.
import unittest
from unittest import mock

import numpy as np
import pytest


class UserTest(unittest.TestCase):
    def test_single_assertion(self):
        self.assertTrue(make_user().active)

    def test_all_documented(self):
        user = make_user()
        self.assertTrue(user.active, "new users start active")
        self.assertEqual(user.age, 36, msg="age comes from the fixture")
        self.assertAlmostEqual(user.score, 0.5, 2, "score is rounded")

    def test_one_undocumented(self):
        user = make_user()
        self.assertTrue(user.active, "new users start active")
        self.assertEqual(user.age, 36)

    def test_assertions_without_message_slot(self):
        sender = mock.Mock()
        notify(sender)
        sender.send.assert_called_once_with("hi")
        with self.assertRaises(ValueError):
            notify(None)
        self.assertJSONEqual(sender.payload, "{}")
        self.assertJSONEqual(sender.headers, "{}")


def test_documented_bare_asserts():
    user = make_user()
    assert user.name == "ada", "name comes from the fixture"
    assert user.age == 36, "age comes from the fixture"


def test_numpy_err_msg():
    scores = load_scores()
    np.testing.assert_allclose(scores.mean(), 0.5, err_msg="mean score")
    np.testing.assert_equal(scores.size, 10, err_msg="score count")


def test_pytest_raises_and_one_assert():
    with pytest.raises(KeyError):
        lookup("missing")
    assert lookup("present") == 1
