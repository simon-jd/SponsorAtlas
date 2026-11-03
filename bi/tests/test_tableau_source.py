import copy
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

BI=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BI))
from verify_tableau_source import check_references, check_comparison


class TableauSource(unittest.TestCase):
    def setUp(self):self.root=ET.parse(BI/'tableau/sponsoratlas.twb').getroot()

    def test_all_four_actions_and_filter_cards_resolve(self):
        self.assertEqual(check_references(self.root),4)

    def test_comparison_dashboard_has_shared_parameters_and_no_local_filters(self):
        self.assertEqual(check_comparison(self.root)['shared_parameters'],4)
        dashboard=self.root.find('./dashboards/dashboard[@name="SponsorAtlas - matched wage comparison"]')
        self.assertIsNotNone(dashboard)
        zones=dashboard.findall('./zones/zone')
        names={z.get('name') for z in zones if not z.get('type-v2')}
        expected={'11 Matched LCA wage p50 USD','11 Matched LCA wage p75 USD','11 Matched LCA wage p90 USD','12 Matched official annual median','13 Comparison LCA scope','14 Comparison benchmark status'}
        self.assertEqual(names,expected)
        parameters={z.get('param') for z in zones if z.get('type-v2')=='paramctrl'}
        self.assertEqual(parameters,{'[Parameters].[Comparison SOC]','[Parameters].[Comparison Area]','[Parameters].[Comparison FY]','[Parameters].[Comparison Survey Year]'})
        for name in expected:
            ws=self.root.find(f'./worksheets/worksheet[@name="{name}"]')
            self.assertEqual(ws.findall('./table/view/filter'),[])
            deps=ws.find('./table/view/datasource-dependencies[@datasource="Parameters"]')
            self.assertIsNotNone(deps)
            self.assertEqual({c.get('name') for c in deps.findall('column')},{p.removeprefix('[Parameters].') for p in parameters})
        for fact in ('lca','wages'):
            ds=self.root.find(f'./datasources/datasource[@name="{fact}"]')
            scope=ds.find('./column[@name="[comparison_match]"]/calculation').get('formula')
            for param in ('Comparison SOC','Comparison Area'):self.assertIn('[Parameters].['+param+']',scope)
            self.assertIn('[Parameters].[Comparison FY]' if fact=='lca' else '[Parameters].[Comparison Survey Year]',scope)
            self.assertNotIn('[Parameters].[Comparison FY]' if fact=='wages' else '[Parameters].[Comparison Survey Year]',scope)

    def test_comparison_drift_missing_zone_and_parameter_are_rejected(self):
        for mutation in ('local-filter','missing-sheet','parameter-drift','wrong-metro','unscoped-percentile'):
            with self.subTest(mutation=mutation):
                root=copy.deepcopy(self.root)
                ws=root.find('./worksheets/worksheet[@name="11 Matched LCA wage p75 USD"]')
                if mutation=='local-filter':ET.SubElement(ws.find('./table/view'),'filter')
                elif mutation=='missing-sheet':
                    zones=root.find('./dashboards/dashboard[@name="SponsorAtlas - matched wage comparison"]/zones')
                    zones.remove(zones.find('./zone[@name="11 Matched LCA wage p75 USD"]'))
                elif mutation=='parameter-drift':ws.find('./table/view/datasource-dependencies[@datasource="Parameters"]/column').set('value','"wrong"')
                elif mutation=='wrong-metro':root.find('./datasources/datasource[@name="lca"]/column[@name="[comparison_match]"]/calculation').set('formula','[soc]=[Parameters].[Comparison SOC]')
                else:root.find('./datasources/datasource[@name="lca"]/column[@name="[comparison_wage_p75]"]/calculation').set('formula','PERCENTILE([annual_wage], 0.75)')
                with self.assertRaises(ValueError):check_comparison(root)

    def test_wrong_action_target_and_benchmark_leak_rejected(self):
        for name,value in [('target','Missing dashboard'),('exclude','')]:
            with self.subTest(name=name):
                root=copy.deepcopy(self.root)
                root.find(f'./actions/action/command/param[@name="{name}"]').set('value',value)
                with self.assertRaises(ValueError):check_references(root)

    def test_dangling_filter_card_and_wrong_level_rejected(self):
        for target,attribute in [('./windows/window/cards/edge/strip/card','param'),('./worksheets/worksheet/table/view/filter/groupfilter','level')]:
            with self.subTest(target=target):
                root=copy.deepcopy(self.root);root.find(target).set(attribute,'[missing]')
                with self.assertRaises(ValueError):check_references(root)


if __name__=='__main__':unittest.main()
