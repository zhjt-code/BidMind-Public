"""Keep routine Dependabot groups small enough for independent review."""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class DependabotPolicyTest(unittest.TestCase):
    def test_npm_and_pip_group_only_minor_and_patch_by_dependency_type(self) -> None:
        config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"))
        updates = config["updates"]

        for ecosystem, directory in (("npm", "/frontend"), ("pip", "/backend")):
            with self.subTest(ecosystem=ecosystem):
                matches = [
                    update for update in updates
                    if update["package-ecosystem"] == ecosystem
                    and update["directory"] == directory
                ]
                self.assertEqual(len(matches), 1)
                groups = matches[0]["groups"]
                self.assertEqual(len(groups), 2)
                self.assertEqual(
                    {group["dependency-type"] for group in groups.values()},
                    {"production", "development"},
                )
                for group in groups.values():
                    self.assertEqual(group["patterns"], ["*"])
                    self.assertEqual(set(group["update-types"]), {"minor", "patch"})


if __name__ == "__main__":
    unittest.main()
