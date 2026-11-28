import unittest
from datetime import date

import permits

PERMIT_CSV = """﻿RECORD ID,DATE OPENED,ADDR FULL LINE,RECORD MODULE,RECORD STATUS,RECORD STATUS DATE,DESCRIPTION,JOB VALUE,APN,LICENSE NBR,BUSINESS NAME
26BLD-00001,11/06/2026,"535 COWPER ST, PALO ALTO, CA 94301",Building,In Plan Check,11/06/2026,COM: TI/U&O for 'Finnegan Law Office'. Demo existing partition walls.,"$1,500,000.00",120-01-001,111,ACME BUILDERS INC
26BLD-00002,09/28/2026,"285 HAMILTON AV, PALO ALTO, CA 94301",Building,Permit Issued,09/28/2026,"COM: 'Perplexity AI, Inc.' Standalone U&O",$1.00,120-01-002,,
26BLD-00003,11/12/2026,"908 ARASTRADERO RD, PALO ALTO, CA 94304",Building,Submitted,11/12/2026,GM Bldg G: Use & Occupancy and tenant improvement for a full build out.,"$26,000,000.00",120-01-003,,
26BLD-00004,11/10/2026,"961 ELSINORE DR, PALO ALTO, CA 94303",Building,Permit Issued,11/10/2026,Instant permit for a residential single-family kitchen or bath remodel.,"$200,000.00",120-01-004,,KEYCON INC
26BLD-00005,11/14/2026,"470 RUTHVEN AV, PALO ALTO, CA 94301",Building,Submitted,11/14/2026,Res MEP: Replace gas tank water heater with gas tankless water heater.,"$9,800.00",120-01-005,,
26BLD-00006,09/02/2026,"581 RAMONA ST, PALO ALTO, CA 94301",Building,Permit Issued,09/02/2026,Sweetgreen TI and updated UO.,"$500,000.00",120-01-006,,
26BLD-00007,10/14/2026,"180 EL CAMINO REAL, STE AA1020, PALO ALTO, CA 94304",Building,In Plan Check,10/14/2026,Com: 'Vuori' Space #AA1020. Stanford Shopping Center. TI and U&O of existing 5959sf space.,"$1,225,100.00",120-01-007,,
26BLD-00008,03/01/2026,"1 OLD ST, PALO ALTO, CA 94301",Building,Finaled,04/01/2026,COM: TI for 'Old Co'.,"$90,000.00",120-01-008,,
26BLD-00009,11/01/2026,"2 SIGN ST, PALO ALTO, CA 94301",Building,Permit Issued,11/01/2026,COM: New wall sign for existing store.,"$3,000.00",120-01-009,,
"""

DASHBOARD = """
      revision_id: '296814',
        csvUrl:'/rest/datastreams/296814/data.csv',
        xlsUrl:'/rest/datastreams/296814/data.xls',
      resource_id: '1',
      title: 'Palo Alto Development Center Permits - Dec 2022-Feb 2025',
      revision_id: '301234',
        csvUrl:'/rest/datastreams/301234/data.csv',
        xlsUrl:'/rest/datastreams/301234/data.xls',
      resource_id: '2',
      title: 'Palo Alto Development Center Permits - Mar 2025-Dec 2026',
"""


class PermitTests(unittest.TestCase):
    def rows(self, **kw):
        return permits.parse_palo_alto(PERMIT_CSV, today=date(2026, 11, 27), **kw)

    def test_finds_commercial_moves_and_build_outs(self):
        by_id = {r["Permit #"]: r for r in self.rows()}
        self.assertEqual(by_id["26BLD-00001"]["Business"], "Finnegan Law Office")
        self.assertEqual(by_id["26BLD-00001"]["Signal"], "Move-in (U&O)")
        self.assertEqual(by_id["26BLD-00001"]["Contractor"], "Acme Builders Inc")
        self.assertEqual(by_id["26BLD-00001"]["Address"], "535 Cowper St")
        self.assertEqual(by_id["26BLD-00002"]["Business"], "Perplexity AI, Inc")
        self.assertIsNone(by_id["26BLD-00002"]["Value"])  # $1 placeholder
        self.assertEqual(by_id["26BLD-00003"]["Business"], "GM")
        self.assertEqual(by_id["26BLD-00006"]["Business"], "Sweetgreen")
        self.assertTrue(by_id["26BLD-00006"]["Chain"])

    def test_skips_residential_minor_and_old(self):
        ids = {r["Permit #"] for r in self.rows()}
        self.assertNotIn("26BLD-00004", ids)  # residential remodel
        self.assertNotIn("26BLD-00005", ids)  # residential water heater
        self.assertNotIn("26BLD-00008", ids)  # older than 90 days
        self.assertNotIn("26BLD-00009", ids)  # sign only
        self.assertIn("26BLD-00008", {r["Permit #"] for r in self.rows(days=365)})

    def test_scoring_prefers_big_recent_non_mall_jobs(self):
        rows = self.rows()
        self.assertEqual(rows[0]["Business"], "GM")
        vuori = next(r for r in rows if r["Business"] == "Vuori")
        self.assertIn("Mall tenant", vuori["Note"])
        law = next(r for r in rows if r["Business"] == "Finnegan Law Office")
        self.assertGreater(law["Score"], vuori["Score"])

    def test_latest_slice_is_picked_from_dashboard(self):
        self.assertTrue(permits.latest_csv_url(DASHBOARD).endswith("/301234/data.csv"))
        self.assertTrue(permits.latest_csv_url("").endswith(f"/{permits.PALO_ALTO_FALLBACK_ID}/data.csv"))


if __name__ == "__main__":
    unittest.main()
