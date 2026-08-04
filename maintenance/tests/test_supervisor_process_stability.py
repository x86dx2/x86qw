import unittest
from unittest import mock

from x86qw_runtime.supervisor import core


class _Process:
    def __init__(self, states):
        self._states = iter(states)

    def poll(self):
        return next(self._states)


class SupervisorProcessStabilityTests(unittest.TestCase):
    def helper(self):
        helper = getattr(core, "process_remains_alive", None)
        if helper is None:
            self.fail("runtime supervisor does not own process stability checks")
        return helper

    def test_reports_a_process_that_exits_inside_the_stability_window(self):
        process = _Process((23,))
        with mock.patch.object(
            core.time, "monotonic", side_effect=(0.0, 0.1),
        ), mock.patch.object(core.time, "sleep"), mock.patch.object(
            core, "POPEN_TYPE", _Process,
        ):
            self.assertFalse(self.helper()(process, duration=1.0, interval=0.05))

    def test_reports_a_process_that_survives_the_stability_window(self):
        process = _Process((None,))
        with mock.patch.object(
            core.time, "monotonic", side_effect=(0.0, 0.1, 1.0),
        ), mock.patch.object(core.time, "sleep"), mock.patch.object(
            core, "POPEN_TYPE", _Process,
        ):
            self.assertTrue(self.helper()(process, duration=1.0, interval=0.05))

    def test_non_process_test_seams_preserve_the_existing_skip_behavior(self):
        self.assertTrue(self.helper()(object(), duration=1.0, interval=0.05))


if __name__ == "__main__":
    unittest.main()
