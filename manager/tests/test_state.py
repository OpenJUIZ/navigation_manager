import math
import unittest

from navigation_manager.state import (
    CANCELED,
    FAILED,
    FOLLOWING,
    IDLE,
    PLAN,
    PLANNING,
    STOP,
    SUCCEEDED,
    Navigator,
    Params,
    Pose,
)


class NavigatorTest(unittest.TestCase):
    def test_goal_plan_follow_arrive(self):
        n = Navigator()
        self.assertEqual(n.state, IDLE)
        self.assertEqual(n.set_goal(Pose(1.0, 2.0), 0.0), [PLAN])
        self.assertEqual(n.state, PLANNING)
        self.assertEqual(n.on_plan_result(True, "", 0.5), [])
        self.assertEqual(n.state, FOLLOWING)
        self.assertEqual(n.on_pose(Pose(0.0, 0.0), 1.0), [])
        self.assertEqual(n.on_pose(Pose(1.1, 2.1, 0.1), 2.0), [STOP])
        self.assertEqual(n.state, SUCCEEDED)

    def test_arrival_needs_heading(self):
        n = Navigator()
        n.set_goal(Pose(1.0, 0.0, 0.0), 0.0)
        n.on_plan_result(True, "", 0.0)
        n.on_pose(Pose(1.0, 0.0, 1.0), 1.0)
        self.assertEqual(n.state, FOLLOWING)
        # -π と π は同じ向き
        n.set_goal(Pose(1.0, 0.0, math.pi), 2.0)
        n.on_plan_result(True, "", 2.0)
        n.on_pose(Pose(1.0, 0.0, -math.pi + 0.01), 3.0)
        self.assertEqual(n.state, SUCCEEDED)

    def test_stuck_replans_then_fails(self):
        n = Navigator(Params(max_planning_retries=2))
        n.set_goal(Pose(3.0, 0.0), 0.0)
        n.on_plan_result(True, "", 0.0)
        for i in (1, 2):
            self.assertEqual(n.on_local_status("stuck", float(i)), [PLAN])
            self.assertEqual(n.state, PLANNING)
            n.on_plan_result(True, "", float(i))
        self.assertEqual(n.on_local_status("stuck", 9.0), [STOP])
        self.assertEqual(n.state, FAILED)

    def test_failed_plan_retries(self):
        n = Navigator()
        n.set_goal(Pose(1.0, 0.0), 0.0)
        self.assertEqual(n.on_plan_result(False, "no route", 0.0), [PLAN])
        self.assertEqual(n.status()["retries"], 1)
        self.assertIn("no route", n.status()["message"])

    def test_cancel_and_new_goal(self):
        n = Navigator()
        n.set_goal(Pose(1.0, 0.0), 0.0)
        n.on_plan_result(False, "", 0.0)
        self.assertEqual(n.cancel(1.0), [STOP])
        self.assertEqual(n.state, CANCELED)
        # 結果が後から来ても状態は変わらない
        self.assertEqual(n.on_plan_result(True, "", 1.5), [])
        self.assertEqual(n.state, CANCELED)
        n.set_goal(Pose(2.0, 0.0), 2.0)
        self.assertEqual(n.status()["retries"], 0)
        self.assertEqual(n.status()["goal_seq"], 2)

    def test_status_distance(self):
        n = Navigator()
        self.assertIsNone(n.status()["goal_distance"])
        n.set_goal(Pose(3.0, 4.0), 0.0)
        n.on_pose(Pose(0.0, 0.0), 0.0)
        self.assertAlmostEqual(n.status()["goal_distance"], 5.0)


if __name__ == "__main__":
    unittest.main()
