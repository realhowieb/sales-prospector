import unittest
from unittest import mock

try:
    from fastapi.testclient import TestClient

    import api.main as main
    from api import cache
    from api.auth import User, current_user, get_settings
    from api.settings import Settings
except ImportError:  # API deps are optional for the Streamlit app's CI
    main = None

from tests.test_trigger_leads import CSV

SETTINGS = Settings("https://sb.example", "anon", "service", ("http://localhost:3000",))


@unittest.skipIf(main is None, "fastapi not installed")
class ApiTests(unittest.TestCase):
    def setUp(self):
        cache.clear()
        main.app.dependency_overrides[current_user] = lambda: User("u-1", "rep@example.com")
        main.app.dependency_overrides[get_settings] = lambda: SETTINGS
        self.client = TestClient(main.app)

    def tearDown(self):
        main.app.dependency_overrides.clear()

    def test_health_is_public(self):
        main.app.dependency_overrides.clear()
        self.assertEqual(self.client.get("/healthz").json()["ok"], True)

    def test_v1_requires_sign_in(self):
        main.app.dependency_overrides.pop(current_user)
        r = self.client.get("/v1/me")
        self.assertEqual(r.status_code, 401)

    def test_licenses_from_abc_file(self):
        with mock.patch.object(main, "abc_csv", return_value=CSV):
            r = self.client.get("/v1/leads/licenses", params={"cities": "Mountain View, Menlo Park", "days": 180})
        self.assertEqual(r.status_code, 200)
        names = [x["Business"] for x in r.json()["leads"]]
        self.assertEqual(names[0], "Little Sheep")
        self.assertIn("Tonys Pizza", names)
        self.assertNotIn("Safeway #2948", names)  # chains hidden by default

    def test_preset_territory_and_city_limits(self):
        with mock.patch.object(main, "abc_csv", return_value=CSV):
            r = self.client.get("/v1/leads/licenses", params={"cities": "Peninsula (Belmont → Mountain View)"})
        self.assertIn("Belmont", r.json()["cities"])
        r = self.client.get("/v1/leads/licenses", params={"cities": ",".join(f"City{i}" for i in range(30))})
        self.assertEqual(r.status_code, 422)

    def test_news_is_cached(self):
        fake = mock.Mock(return_value=[{"Headline": "Shop opens in Palo Alto", "Score": 90}])
        with mock.patch.object(main, "news", fake):
            self.client.get("/v1/leads/news", params={"cities": "Palo Alto"})
            r = self.client.get("/v1/leads/news", params={"cities": "Palo Alto"})
        self.assertEqual(r.json()["count"], 1)
        fake.assert_called_once()

    def test_new_on_map_hides_chains(self):
        els = [
            {"type": "node", "id": 1, "version": 1, "tags": {"name": "Tanya's Hair Design", "shop": "hairdresser"}},
            {"type": "node", "id": 2, "version": 1, "tags": {"name": "Panda Express", "amenity": "fast_food"}},
        ]
        with mock.patch.object(main, "territory_bbox", return_value=(37.3, -122.2, 37.5, -122.0)), \
                mock.patch.object(main, "overpass", return_value=els):
            r = self.client.get("/v1/leads/new-on-map", params={"cities": "Palo Alto"})
        self.assertEqual([x["Business"] for x in r.json()["leads"]], ["Tanya's Hair Design"])

    def test_discover_scores_prospects(self):
        els = [{"type": "node", "id": 9, "lat": 37.4, "lon": -122.1,
                "tags": {"name": "Joe's Cafe", "amenity": "cafe", "phone": "650-555-0100",
                         "addr:housenumber": "1", "addr:street": "Main St"}}]
        with mock.patch.object(main, "discover_fetch", return_value=els):
            r = self.client.get("/v1/discover", params={"metro": "Peninsula, CA", "categories": "Restaurant & Café"})
        body = r.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["prospects"][0]["id"], "node/9")
        self.assertGreater(body["prospects"][0]["score"], 0)
        bad = self.client.get("/v1/discover", params={"metro": "Peninsula, CA", "categories": "Nope"})
        self.assertEqual(bad.status_code, 422)

    def test_pipeline_is_scoped_to_user(self):
        calls = {}

        def fake_post(url, headers=None, params=None, json=None, timeout=None):
            calls["row"] = json[0]
            return mock.Mock(status_code=201, json=lambda: [dict(json[0], updated_at="2026-11-28T00:00:00Z")])

        with mock.patch("api.pipeline.requests.post", fake_post):
            r = self.client.put("/v1/pipeline/abc-00670730",
                                json={"name": "Little Sheep", "stage": "Bogus", "note": "Pending license"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(calls["row"]["owner_user_id"], "u-1")
        self.assertEqual(calls["row"]["owner_key_hash"], "user:u-1")
        self.assertEqual(calls["row"]["stage"], "New lead")  # unknown stage normalized

        with mock.patch("api.pipeline.requests.get") as get:
            get.return_value = mock.Mock(status_code=200, json=lambda: [])
            self.client.get("/v1/pipeline")
        self.assertEqual(get.call_args.kwargs["params"]["owner_user_id"], "eq.u-1")


if __name__ == "__main__":
    unittest.main()
