import unittest

from scripts.production_smoke import PYTHON_FILES, ROOT


class ProductionSmokeManifestTests(unittest.TestCase):
    def test_smoke_manifest_files_exist(self):
        missing = [path for path in PYTHON_FILES if not (ROOT / path).exists()]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
