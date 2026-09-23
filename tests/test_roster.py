import unittest
from datetime import date
from fetch_tw_universe import parse_roster


class RosterTests(unittest.TestCase):
    def test_current_roster_filters_security_format_not_price_success(self):
        rows = [{'Date': '1150921', 'SecuritiesCompanyCode': sid,
                 'CompanyAbbreviation': 'fixture', 'SecuritiesIndustryCode': '24'}
                for sid in ('1234', '1234A')]
        result = parse_roster(rows, 'tpex', date(2026, 9, 22))
        self.assertEqual(set(result), {'1234'})
        self.assertEqual(result['1234']['roster_date'], '2026-09-21')

    def test_stale_official_roster_rejected(self):
        rows = [{'出表日期': '1150801', '公司代號': '1234', '公司簡稱': 'fixture', '產業別': '24'}]
        with self.assertRaises(ValueError):
            parse_roster(rows, 'twse', date(2026, 9, 22))
