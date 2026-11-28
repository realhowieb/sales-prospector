import unittest
from datetime import date

import trigger_leads as tl

HEADER = ('"License Type","File Number","Lic or App","Type Status","Type Orig Iss Date","Expir Date","Fee Codes",'
          '"Dup Counts","Master Ind","Term in # of Months","Geo Code",District,"Primary Name","Prem Addr 1",'
          '" Prem Addr 2","Prem City"," Prem State","Prem Zip","DBA Name","Mail Addr 1","Mail Addr 2","Mail City",'
          '"Mail State","Mail Zip","Prem County","Prem Census Tract #"')


def row(lt, file_no, kind, status, issued, owner, addr, city, dba):
    return (f'{lt},{file_no},{kind},{status},"{issued}"," ",P40,001,N,12,4100,25,"{owner}","{addr}"," ",'
            f'{city},CA,94041,"{dba}","1 MAIL ST"," ","SAN JOSE",CA,95128,"SANTA CLARA",5096.00')


CSV = "\n".join([
    '"Updated Monday 9th of November 2026 02:50:23 AM"',
    HEADER,
    row("47", "00670730", "APP", "PEND", " ", "ROYALE KTV LLC", "174-176 CASTRO ST", "MOUNTAIN VIEW", "LITTLE SHEEP"),
    row("41", "00670730", "APP", "PEND", " ", "ROYALE KTV LLC", "174-176 CASTRO ST", "MOUNTAIN VIEW", "LITTLE SHEEP"),
    row("41", "00600001", "LIC", "ACTIVE", "01-OCT-2026", "VARGAS  MIGUEL", "820 WILLOW RD", "MENLO PARK", "TONYS PIZZA"),
    row("41", "00500001", "LIC", "ACTIVE", "24-JUL-1996", "OLD PLACE INC", "1 OLD ST", "PALO ALTO", "OLD PLACE"),
    row("21", "00700001", "APP", "ACTIVE", " ", "SAFEWAY INC", "645 SAN ANTONIO RD", "MOUNTAIN VIEW", "SAFEWAY #2948"),
    row("77", "00800001", "APP", "ACTIVE", " ", "EVENT CO LLC", "2 EVENT ST", "PALO ALTO", "ONE DAY EVENT"),
    row("47", "00900001", "APP", "PEND", " ", "FAR AWAY LLC", "9 FAR ST", "FRESNO", "FAR AWAY"),
])


class ParseAbcTests(unittest.TestCase):
    def setUp(self):
        self.leads = tl.parse_abc(CSV, ["Mountain View", "Menlo Park", "Palo Alto"], today=date(2026, 11, 9))
        self.by_name = {x["Business"]: x for x in self.leads}

    def test_keeps_pending_and_recent_only(self):
        self.assertIn("Little Sheep", self.by_name)
        self.assertIn("Tonys Pizza", self.by_name)
        self.assertNotIn("Old Place", self.by_name)       # licensed in 1996
        self.assertNotIn("One Day Event", self.by_name)   # event permit type skipped
        self.assertNotIn("Far Away", self.by_name)        # outside territory

    def test_one_row_per_premises_with_best_type(self):
        self.assertEqual(sum(1 for x in self.leads if x["ABC file #"] == "00670730"), 1)
        self.assertEqual(self.by_name["Little Sheep"]["Type"], "Full restaurant w/ bar")

    def test_scoring_and_chain_flag(self):
        self.assertEqual(self.by_name["Little Sheep"]["Score"], 100)
        safeway = self.by_name["Safeway #2948"]
        self.assertTrue(safeway["Chain"])
        self.assertLess(safeway["Score"], 60)
        self.assertEqual(self.leads[0]["Business"], "Little Sheep")

    def test_person_and_record_link(self):
        self.assertEqual(self.by_name["Tonys Pizza"]["Person to ask for"], "Miguel Vargas")
        self.assertEqual(self.by_name["Little Sheep"]["Person to ask for"], "")
        self.assertTrue(self.by_name["Little Sheep"]["ABC record"].endswith("LICENSE=670730"))


class ContactTests(unittest.TestCase):
    def test_match_requires_address_and_similar_name(self):
        osm = [
            {"tags": {"name": "Tony's Pizza", "addr:housenumber": "820", "addr:street": "Willow Road",
                      "addr:city": "Menlo Park", "phone": "+1-650-853-0860"}},
            {"tags": {"name": "Shake Shack", "addr:housenumber": "180", "addr:street": "El Camino Real",
                      "addr:city": "Palo Alto", "phone": "+1-650-000-0000"}},
        ]
        by_addr, by_city = tl.index_osm(osm)
        self.assertEqual(tl.match_listing("Tonys Pizza", "820 Willow Rd", "Menlo Park", by_addr, by_city)["phone"],
                         "+1-650-853-0860")
        self.assertIsNone(tl.match_listing("Yucca De Lac", "180 El Camino Real Ste 700", "Palo Alto",
                                           by_addr, by_city))

    def test_extract_contacts_filters_junk_emails(self):
        page = ('<a href="mailto:hello@campermp.com">Email</a> font by team@latofonts.com '
                '<a href="tel:+1-650-321-8980">Call</a>')
        self.assertEqual(tl.extract_contacts("https://campermp.com", [page]),
                         ("hello@campermp.com", "+1-650-321-8980"))
        self.assertEqual(tl.extract_contacts("https://x.com", ["write lemonad@jovanny.ru"])[0], "")

    def test_union_bbox(self):
        self.assertEqual(tl.union_bbox([(1, 2, 3, 4), None, (0, 3, 5, 3)]), (0, 2, 5, 4))
        self.assertIsNone(tl.union_bbox([None]))


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Bartow Ace opens third location in Mountain View - Mountain View Voice</title>
  <link>https://news.example/a</link><pubDate>Wed, 11 Nov 2026 08:00:00 GMT</pubDate>
  <source url="https://mv-voice.com">Mountain View Voice</source></item>
<item><title>Acme signs lease for 40,000 square feet in Mountain View - Bizjournal</title>
  <link>https://news.example/b</link><pubDate>Mon, 23 Nov 2026 08:00:00 GMT</pubDate><source>Bizjournal</source></item>
<item><title>Mountain View affordable homes open to residents - Paper</title>
  <link>https://news.example/c</link><pubDate>Mon, 23 Nov 2026 08:00:00 GMT</pubDate><source>Paper</source></item>
<item><title>Bakery opens downtown - Paper</title>
  <link>https://news.example/d</link><pubDate>Mon, 23 Nov 2026 08:00:00 GMT</pubDate><source>Paper</source></item>
<item><title>Old store opens in Mountain View - Paper</title>
  <link>https://news.example/e</link><pubDate>Mon, 01 Jun 2026 08:00:00 GMT</pubDate><source>Paper</source></item>
</channel></rss>"""


class NewsTests(unittest.TestCase):
    def test_parse_keeps_openings_and_leases_and_drops_noise(self):
        rows = tl.parse_news_rss(RSS, "Mountain View", today=date(2026, 11, 27), days=30)
        by_link = {r["Link"]: r for r in rows}
        self.assertEqual(set(by_link), {"https://news.example/a", "https://news.example/b"})
        self.assertEqual(by_link["https://news.example/a"]["Signal"], "Opening")
        self.assertEqual(by_link["https://news.example/a"]["Headline"], "Bartow Ace opens third location in Mountain View")
        self.assertEqual(by_link["https://news.example/b"]["Signal"], "New lease")
        self.assertGreater(by_link["https://news.example/b"]["Score"], by_link["https://news.example/a"]["Score"])

    def test_bad_xml_is_empty(self):
        self.assertEqual(tl.parse_news_rss("<html>busy</html", "Palo Alto"), [])


class NewOnMapTests(unittest.TestCase):
    def test_keeps_recent_businesses_only(self):
        els = [
            {"type": "node", "id": 1, "version": 1, "timestamp": "2026-10-15T00:00:00Z",
             "tags": {"name": "Tanya's Hair Design", "shop": "hairdresser", "addr:housenumber": "1030",
                      "addr:street": "Castro Street"}},
            {"type": "node", "id": 2, "version": 2, "timestamp": "2026-10-13T00:00:00Z",
             "tags": {"name": "LegalZoom", "office": "company", "addr:housenumber": "954", "addr:street": "Villa St"}},
            {"type": "node", "id": 3, "version": 7, "timestamp": "2026-10-13T00:00:00Z",
             "tags": {"name": "Old Diner", "amenity": "restaurant"}},  # long-mapped, just edited
            {"type": "node", "id": 4, "version": 1, "tags": {"name": "Caltrain BikeHub", "amenity": "bicycle_parking"}},
            {"type": "node", "id": 5, "version": 1, "tags": {"name": "Panda Express", "amenity": "fast_food"}},
            {"type": "node", "id": 6, "version": 1, "tags": {"name": "Global Risk Solutions", "office": "security"}},
        ]
        rows = tl.parse_new_on_map(els)
        names = [r["Business"] for r in rows]
        self.assertEqual(names[0], "LegalZoom")  # offices rank first
        self.assertIn("Tanya's Hair Design", names)
        self.assertNotIn("Old Diner", names)
        self.assertNotIn("Caltrain BikeHub", names)
        self.assertNotIn("Panda Express", names)
        competitor = next(r for r in rows if r["Business"] == "Global Risk Solutions")
        self.assertEqual(competitor["Note"], "Likely security competitor")
        self.assertEqual(rows[-1]["Business"], "Global Risk Solutions")

    def test_query_uses_newer_filter(self):
        q = tl.osm_new_query((37.3, -122.2, 37.5, -122.0), date(2026, 10, 1))
        self.assertIn('(newer:"2026-10-01T00:00:00Z")', q)
        self.assertIn("out center meta", q)


if __name__ == "__main__":
    unittest.main()
