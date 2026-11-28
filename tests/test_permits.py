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


SJ_CSV = '''"Status","gx_location","ASSESSORS_PARCEL_NUMBER","APPLICANT","OWNERNAME","CONTRACTOR","FOLDERNUMBER","FOLDERDESC","FOLDERNAME","SUBTYPEDESCRIPTION","WORKDESCRIPTION","PERMITAPPROVALS","ISSUEDATE","FINALDATE","DWELLINGUNITS","PERMITVALUATION","SQUAREFOOTAGE","FOLDERRSN"
"Active","3000  ORCHARD PY  , SAN JOSE CA 95134-0000","1","","","ACME BUILDERS INC","2026-1-TI","Commercial/Industrial","ROSENDIN ELECTRIC (BEPM100%) TENANT IMPROVEMENT","Office","Tenant Improvement","","11/3/2026 12:00:00 AM","","0","10255535","0","1"
"Active","1 MAIN ST  , SAN JOSE CA 95110-0000","2","","","SIGNCO","2026-2-SG","Commercial/Industrial","FIFTH THIRD BANK (BE 100%) ILLUMINATED SIGNS","Bank","Tenant Improvement","","11/9/2026 12:00:00 AM","","0","24000","0","2"
"Active","2 MAIN ST  , SAN JOSE CA 95110-0000","3","","","","2026-3-CS","Commercial/Industrial","T-MOBILE (BE100%) ANTENNA CELL SITE","Antenna/Cell Site","Tenant Improvement","","11/13/2026 12:00:00 AM","","0","66000","0","3"
"Active","3 MAIN ST  , SAN JOSE CA 95110-0000","4","","","","2026-4-RS","1 & 2 Family Detached Dwellings","SMITH KITCHEN","Single-Family","Additions/Alterations","","11/13/2026 12:00:00 AM","","0","66000","0","4"
"Active","4 MAIN ST  , SAN JOSE CA 95110-0000","5","","","","2026-5-ST","Commercial/Industrial","(STAR) SRP (BEMP100%) TERIYAKI CITY GRILL #3","Restaurant","Tenant Improvement","","11/23/2026 12:00:00 AM","","0","85000","0","5"
'''

SF_ROWS = [
    {"permit_number": "1", "permit_creation_date": "2026-11-18T00:00:00.000", "status": "filed",
     "description": "tenant improvement coffee cafe. (e) restroom to remain", "estimated_cost": "175000.0",
     "existing_use": "retail sales", "proposed_use": "food/beverage hndlng", "street_number": "2108",
     "street_name": "Sutter", "street_suffix": "St"},
    {"permit_number": "2", "permit_creation_date": "2026-11-20T00:00:00.000", "status": "issued",
     "description": "17/f: tenant improvement minimal demolition of non-structural walls", "estimated_cost": "618000.0",
     "existing_use": "office", "proposed_use": "office", "street_number": "101", "street_name": "02nd",
     "street_suffix": "St"},
    {"permit_number": "3", "permit_creation_date": "2026-11-20T00:00:00.000", "status": "issued",
     "description": "kitchen remodel", "estimated_cost": "40000.0", "existing_use": "2 family dwelling",
     "proposed_use": "2 family dwelling", "street_number": "9", "street_name": "Elm", "street_suffix": "St"},
]


class CityPermitTests(unittest.TestCase):
    def test_san_jose_keeps_build_outs_drops_signs_towers_homes(self):
        rows = permits.parse_san_jose(SJ_CSV, today=date(2026, 11, 27))
        by = {r["Permit #"]: r for r in rows}
        self.assertEqual(set(by), {"2026-1-TI", "2026-5-ST"})
        self.assertEqual(by["2026-1-TI"]["Business"], "Rosendin Electric")
        self.assertEqual(by["2026-1-TI"]["Address"], "3000 Orchard Py")
        self.assertEqual(by["2026-1-TI"]["Contractor"], "Acme Builders Inc")
        self.assertEqual(by["2026-5-ST"]["Business"], "Teriyaki City Grill #3")
        self.assertIn("2026-2-SG", {r["Permit #"] for r in permits.parse_san_jose(SJ_CSV, today=date(2026, 11, 27),
                                                                                 include_minor=True)})

    def test_san_francisco_change_of_use_is_a_move_in(self):
        rows = permits.parse_san_francisco(SF_ROWS, today=date(2026, 11, 27))
        by = {r["Permit #"]: r for r in rows}
        self.assertEqual(set(by), {"1", "2"})  # the 2-family home is skipped
        self.assertEqual(by["1"]["Signal"], "Move-in (change of use)")
        self.assertIn("retail sales → food/beverage hndlng", by["1"]["Description"])
        self.assertEqual(by["2"]["Signal"], "Build-out (TI)")
        self.assertEqual(by["2"]["Address"], "101 2nd St")
