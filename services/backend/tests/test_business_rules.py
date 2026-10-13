"""Run: python -m unittest services/backend/tests/test_business_rules.py"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'central_monitor'))
from business_rules import evaluate, validate_thresholds

class BusinessRuleTests(unittest.TestCase):
    NOW=datetime(2026,10,13,12,0,tzinfo=timezone.utc)
    def run_rule(self, value, collected=None, **kwargs):
        return evaluate(value, collected or self.NOW, now=self.NOW, stale_after_seconds=300,
                        direction='above',warning=Decimal('10'),critical=Decimal('20'), **kwargs)
    def test_normal_attention_critical(self):
        self.assertEqual(self.run_rule(Decimal('9')),'normal')
        self.assertEqual(self.run_rule(Decimal('10')),'atencao')
        self.assertEqual(self.run_rule(Decimal('19')),'atencao')
        self.assertEqual(self.run_rule(Decimal('20')),'critico')
    def test_missing_stale_and_future_not_healthy(self):
        self.assertEqual(self.run_rule(None),'sem_dados')
        self.assertEqual(self.run_rule(Decimal('1'),self.NOW-timedelta(hours=1)),'sem_dados')
        self.assertEqual(self.run_rule(Decimal('1'),self.NOW+timedelta(minutes=6)),'sem_dados')
    def test_below_direction(self):
        p={'now':self.NOW,'stale_after_seconds':300,'direction':'below',
           'warning':Decimal('20'),'critical':Decimal('10')}
        self.assertEqual(evaluate(Decimal('30'),self.NOW,**p),'normal')
        self.assertEqual(evaluate(Decimal('20'),self.NOW,**p),'atencao')
        self.assertEqual(evaluate(Decimal('10'),self.NOW,**p),'critico')
    def test_unset_thresholds_does_not_invent_alerts(self):
        self.assertEqual(evaluate(Decimal('999'),self.NOW,now=self.NOW,stale_after_seconds=300,
                                  direction='above',warning=None,critical=None),'normal')
    def test_invalid_threshold_pairs(self):
        for direction,warning,critical in [('above',Decimal(20),Decimal(10)),('below',Decimal(10),Decimal(20)),('above',Decimal(10),None)]:
            with self.assertRaises(ValueError): validate_thresholds(direction,warning,critical)
    def test_suspension(self):
        self.assertEqual(self.run_rule(Decimal('100'),suspended=True),'suspenso')

if __name__=='__main__':unittest.main()
