import unittest

from deployment_status import (
    CORE_TABLES,
    REQUIRED_SCHEMA_VERSION,
    expected_columns_for,
    latest_schema_version,
    normalize_migration_version,
    schema_status,
    summarize_column_checks,
    summarize_table_checks,
    table_order_for,
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

    def test_table_order_for_schema_migrations(self):
        self.assertEqual(table_order_for("schema_migrations"), "version.desc")
        self.assertEqual(table_order_for("reps"), "created_at.desc")

    def test_summarize_table_checks(self):
        summary = summarize_table_checks([
            {"status": "ok"},
            {"status": "ok"},
            {"status": "missing"},
            {"status": "restricted"},
        ])
        self.assertEqual(summary["ok"], 2)
        self.assertEqual(summary["missing"], 1)
        self.assertEqual(summary["restricted"], 1)
        self.assertEqual(summary["error"], 0)

    def test_expected_columns_cover_core_tables(self):
        missing = [table for table in CORE_TABLES if not expected_columns_for(table)]
        self.assertEqual(missing, [])

    def test_expected_columns_include_recent_reps_fields(self):
        columns = expected_columns_for("reps")
        self.assertIn("profile_slug", columns)
        self.assertIn("availability_status", columns)
        self.assertIn("profile_status", columns)

    def test_summarize_column_checks(self):
        summary = summarize_column_checks([
            {"status": "ok"},
            {"status": "missing"},
            {"status": "error"},
            {"status": ""},
        ])
        self.assertEqual(summary["ok"], 1)
        self.assertEqual(summary["missing"], 1)
        self.assertEqual(summary["error"], 2)


if __name__ == "__main__":
    unittest.main()
