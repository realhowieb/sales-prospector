import unittest

from shortlists import (
    build_shortlist_item,
    is_saved,
    normalize_collection,
    remove_session_shortlist,
    upsert_session_shortlist,
)


class ShortlistsTests(unittest.TestCase):
    def test_build_shortlist_item_defaults_to_anonymous_saved(self):
        item = build_shortlist_item(target_type="rep", target_id="db-42")

        self.assertEqual(item.owner_type, "anonymous")
        self.assertEqual(item.target_type, "rep")
        self.assertEqual(item.target_id, "db-42")
        self.assertEqual(item.collection, "Saved")

    def test_collection_validation(self):
        self.assertEqual(normalize_collection("Contact Later"), "Contact Later")
        self.assertEqual(normalize_collection("Nope"), "Saved")

    def test_upsert_updates_collection_instead_of_duplicating(self):
        items = []
        saved = build_shortlist_item(target_type="opportunity", target_id="opp-1", collection="Saved")
        items, created = upsert_session_shortlist(items, saved)
        self.assertTrue(created)

        later = build_shortlist_item(target_type="opportunity", target_id="opp-1", collection="Contact Later")
        items, created = upsert_session_shortlist(items, later)
        self.assertFalse(created)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["collection"], "Contact Later")

    def test_remove_and_is_saved(self):
        items = [build_shortlist_item(target_type="company", target_id="co-1").__dict__.copy()]
        self.assertTrue(is_saved(items, "company", "co-1"))
        items = remove_session_shortlist(items, "company", "co-1")
        self.assertFalse(is_saved(items, "company", "co-1"))

    def test_invalid_target_type_raises(self):
        with self.assertRaises(ValueError):
            build_shortlist_item(target_type="lead", target_id="1")


if __name__ == "__main__":
    unittest.main()
