import unittest

from deployment_status import (
    REQUIRED_SCHEMA_VERSION,
    latest_schema_version,
    normalize_migration_version,
    schema_status,
)


class DeploymentStatusTests(unittest.TestCase):
    def test_normalize_migration_version(self):
        self.assertEqual(normalize_migration_version("20"), "020")
        self.assertEqual(normalize_migration_version("migration_007"), "007")
        self.assertEqual(normalize_migration_version(""), "")

    def test_latest_schema_version(self):
        rows = [{"version": "001"}, {"version": "019"}, {"version": "020"}]
        self.assertEqual(latest_schema_version(rows), "020")

    def test_schema_status_current(self):
        status = schema_status([{"version": REQUIRED_SCHEMA_VERSION}])
        self.assertTrue(status["ok"])
        self.assertEqual(status["label"], "Schema current")

    def test_schema_status_missing_tracking(self):
        status = schema_status([])
        self.assertFalse(status["ok"])
        self.assertEqual(status["label"], "Schema tracking not installed")

    def test_schema_status_behind(self):
        status = schema_status([{"version": "019"}], "020")
        self.assertFalse(status["ok"])
        self.assertEqual(status["current"], "019")
        self.assertIn("Schema behind", status["label"])


if __name__ == "__main__":
    unittest.main()

