"""P0/P1 security and business policy tests. Run after installing backend dependencies."""
import sys
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    from central_monitor.business_p1 import safe_config,has_scope,RuleConfig
    from central_monitor.business_evaluation import compute_state,within_window
    IMPORT_ERROR=None
except ImportError as exc:
    IMPORT_ERROR=exc

@unittest.skipIf(IMPORT_ERROR is not None, 'Backend dependencies not installed')
class BusinessPolicyTests(unittest.TestCase):
    def test_reject_unsafe_rest(self):
        for url in ['http://example.com/feed','https://127.0.0.1/x','https://localhost/x',
                    'https://example.com:8080/feed','https://admin:password@example.com/data']:
            with self.subTest(url=url),self.assertRaises(ValueError):
                safe_config('rest_get',{'url':url,'json_property':'total','poll_seconds':300})

    def test_reject_sql_write_and_secrets(self):
        for config in [
            {'schema':'public','view':'pedidos','column':'total','poll_seconds':300},
            {'schema':'public','view':'vw_nexora_a;DROP TABLE','column':'total','poll_seconds':300},
            {'schema':'public','view':'vw_nexora_a','column':'total','password':'x'},
        ]:
            with self.subTest(config=config),self.assertRaises(ValueError):safe_config('postgres_view',config)

    def test_approved_view_and_rest(self):
        self.assertEqual(safe_config('sql_view',{'schema':'dbo','view':'vw_nexora_pedidos','column':'qtd'})['poll_seconds'],300)
        self.assertEqual(safe_config('rest_get',{'url':'https://example.com/api','json_property':'total','poll_seconds':300})['max_bytes'],65536)

    def test_scopes_never_bypass(self):
        actor={'id':'user-a'};row={'owner_user_id':'user-b','unit_id':'unit-x','department_id':'dept-x','process_id':'process-x'}
        self.assertFalse(has_scope(row,actor,{'scope_type':'own','scope_id':None}))
        self.assertTrue(has_scope(row,actor,{'scope_type':'unit','scope_id':'unit-x'}))
        self.assertFalse(has_scope(row,actor,{'scope_type':'unit','scope_id':'unit-y'}))
        self.assertFalse(has_scope(row,actor,{'scope_type':'process','scope_id':None}))
        self.assertFalse(has_scope(row,actor,{'scope_type':'unknown','scope_id':None}))

    def test_rule_calendar_rejects_invalid_timezones_and_limits(self):
        with self.assertRaises(ValueError):RuleConfig(indicator_id='11111111-1111-1111-1111-111111111111',timezone='Mars/Nowhere')
        with self.assertRaises(ValueError):RuleConfig(indicator_id='11111111-1111-1111-1111-111111111111',
            warning_value=Decimal('20'),critical_value=Decimal('10'),direction='above')

    def test_business_window_and_no_data(self):
        now=datetime(2026,10,13,12,0,tzinfo=UTC)
        indicator={'active':True,'direction':'above','warning_value':Decimal('10'),
            'critical_value':Decimal('20'),'stale_after_seconds':300}
        current={'state':'normal','last_value':Decimal('0'), 'last_collected_at':now-timedelta(seconds=301),
                 'evaluated_at':now-timedelta(seconds=30),'state_changed_at':now-timedelta(seconds=200)}
        self.assertEqual(compute_state(indicator,current,None,now),'sem_dados')
        self.assertEqual(compute_state({**indicator,'active':False},current,None,now),'suspenso')
        policy={**indicator,'active':True,'timezone':'UTC','weekdays':[2], 'check_from':None,'check_until':None,
                'hysteresis':Decimal('0'),'cooldown_seconds':0}
        self.assertTrue(within_window(policy,now))
        self.assertFalse(within_window({**policy,'weekdays':[1]},now))

if __name__=='__main__':unittest.main()
