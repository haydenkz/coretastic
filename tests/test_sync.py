import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "firmware"))
from sync_upstream import compare_environments, new_releases, updated_versions


def release(tag, prerelease=False, draft=False):
    return dict(tag_name=tag, prerelease=prerelease, draft=draft)


class SyncTests(unittest.TestCase):
    def test_only_newer_stable_releases_with_the_prefix_are_adopted(self):
        releases = [
            release("companion-v1.18.0"),
            release("companion-v1.19.0", prerelease=True),
            release("companion-v1.20.0", draft=True),
            release("repeater-v1.18.0"),
            release("companion-v1.17.0"),  # Already locked.
            release("companion-v1.16.5"),  # Older than every locked version.
            release("companion-latest"),
            release("companion-v1.18.1"),
        ]
        locked = ["companion-v1.17.1", "companion-v1.17.0"]
        self.assertEqual(
            new_releases(releases, "companion-v", locked),
            ["companion-v1.18.1", "companion-v1.18.0"],
        )
        self.assertEqual(new_releases(releases[:1], "v", ["v2.7.26.54e0d8d"]), [])

    def test_locked_versions_stay_newest_first_and_bounded(self):
        locked = [dict(version=f"v2.7.{n}.x") for n in (26, 20, 15)]
        kept, dropped = updated_versions(locked, [dict(version="v2.8.0.y")], 3)
        self.assertEqual([v["version"] for v in kept], ["v2.8.0.y", "v2.7.26.x", "v2.7.20.x"])
        self.assertEqual(dropped, ["v2.7.15.x"])

    def test_environment_changes_are_reported(self):
        previous = {"env:v4": {"lib_deps": ["a@1", "b@1"], "platform": "p@6"}}
        same = {"env:v4": {"lib_deps": "a@1\nb@1\n", "platform": "p@6"}}
        self.assertEqual(compare_environments(previous, same, {"heltec-v4-oled": "v4"}), [])
        changed = {"env:v4": {"lib_deps": ["a@1", "c@2"], "platform": "p@7"}}
        findings = compare_environments(previous, changed, {"heltec-v4-oled": "v4"})
        self.assertEqual(len(findings), 2)
        self.assertIn("removed ['b@1'], added ['c@2']", findings[0])
        self.assertIn("`platform` changed", findings[1])
        missing = compare_environments(previous, {}, {"heltec-v3": "v3"})
        self.assertEqual(missing, ["`heltec-v3`: upstream environment `v3` no longer exists"])


if __name__ == "__main__":
    unittest.main()
