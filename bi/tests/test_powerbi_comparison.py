"""Static PBIR/TMDL mechanism checks; these do not execute DAX."""
import json
import sys
import unittest
from pathlib import Path
BI=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BI))
from build_powerbi import MEASURES

class PowerBIComparison(unittest.TestCase):
    def test_all_comparison_cards_use_area_aware_measures(self):
        fields=[]
        for path in (BI/'powerbi/SponsorAtlas.Report/definition/pages/WageComparison/visuals').glob('*/visual.json'):
            value=json.loads(path.read_text())['visual']
            for role in value['query']['queryState'].values():
                for item in role['projections']:
                    if 'Measure' in item['field']:fields.append(item['field']['Measure']['Property'])
        for measure in ['Comparison Wage P50','Comparison Wage P75','Comparison Wage P90','Comparison OEWS Median','Comparison Scope']:
            self.assertIn(measure,fields)
        self.assertNotIn('Wage P75',fields)
        self.assertNotIn('OEWS Median',fields)

    def test_area_maps_to_lca_without_reverse_relationship(self):
        for p in (50,75,90):
            formula=MEASURES['lca'][f'Comparison Wage P{p}'][0]
            for text in ['SELECTEDVALUE(PublishedArea[area])','Area = "1:99"','LEFT(Area, 2) = "4:"','REMOVEFILTERS(Metro)','TREATAS({MID(Area, 3, 5)}, lca[metro])']:
                self.assertIn(text,formula)
            self.assertIn('HASONEVALUE(SOC[soc])',formula)
        median=MEASURES['wages']['Comparison OEWS Median'][0]
        self.assertIn('REMOVEFILTERS(Metro)',median)
        relationships=(BI/'powerbi/SponsorAtlas.SemanticModel/definition/relationships.tmdl').read_text()
        self.assertNotIn('bothDirections',relationships)
        self.assertEqual(relationships.count('relationship '),relationships.count('crossFilteringBehavior: oneDirection'))

if __name__=='__main__':unittest.main()
