"""Business rule evaluation; runs within tenant-isolated DB transactions.

Business incident lifecycle intentionally differs from GLPI ticket lifecycle.
"""
from __future__ import annotations
from datetime import UTC,datetime,timedelta
from decimal import Decimal
from typing import Any
from psycopg.types.json import Jsonb
from zoneinfo import ZoneInfo

from .business_glpi_outbox import enqueue
from .business_rules import evaluate

VALID_STATES={'normal','atencao','critico','sem_dados','suspenso'}


def within_window(policy:dict[str,Any],now:datetime)->bool:
    local=now.astimezone(ZoneInfo(policy.get('timezone') or 'America/Sao_Paulo'))
    if local.isoweekday() not in (policy.get('weekdays') or []):return False
    start,end=policy.get('check_from'),policy.get('check_until')
    if start is not None and end is not None and not (start<=local.time().replace(tzinfo=None)<end):return False
    return True


def compute_state(indicator:dict[str,Any],current:dict[str,Any]|None,policy:dict[str,Any]|None,now:datetime)->str:
    if not indicator['active']:return 'suspenso'
    config=policy if policy and policy['active'] else indicator
    if policy and policy['active'] and not within_window(policy,now):
        # No evaluation outside explicitly configured business window, unless no recent data.
        if current and current['last_collected_at'] is not None:
            return current['state']
        return 'sem_dados'
    value=current['last_value'] if current else None
    collected=current['last_collected_at'] if current else None
    direction=config['direction'];warn=config['warning_value'];critical=config['critical_value']
    state=evaluate(value,collected,now=now,stale_after_seconds=config['stale_after_seconds'],
                   direction=direction,warning=warn,critical=critical)
    if state=='sem_dados' or not policy or not policy['active'] or not current:return state
    previous=current['state']
    hysteresis=Decimal(policy['hysteresis'])
    if previous in ('atencao','critico') and state=='normal' and warn is not None:
        if direction=='above' and value>Decimal(warn)-hysteresis:return 'atencao'
        if direction=='below' and value<Decimal(warn)+hysteresis:return 'atencao'
    if previous in ('atencao','critico') and state=='normal' and policy['cooldown_seconds']:
        if now-current.get('state_changed_at',current['evaluated_at'])<timedelta(seconds=policy['cooldown_seconds']):return previous
    return state


def _notify(db:Any,tenant_id:str,indicator:dict[str,Any],occurrence_id:Any,severity:str)->None:
    """In-app only; no sensitive values, emails or blanket notification to IT operators."""
    recipients=db.execute('''SELECT sub.user_id FROM cm_business_notification_subscriptions sub
        JOIN cm_business_grants g ON g.tenant_id=sub.tenant_id AND g.user_id=sub.user_id
          AND g.capability='business.view' AND g.active
        JOIN cm_access_grants a ON a.tenant_id=sub.tenant_id AND a.user_id=sub.user_id AND a.active
          AND a.valid_from<=now() AND (a.valid_until IS NULL OR a.valid_until>now())
        WHERE sub.tenant_id=%s AND sub.indicator_id=%s AND sub.enabled
          AND (g.scope_type='company' OR (g.scope_type='own' AND %s=sub.user_id)
           OR (g.scope_type='unit' AND g.scope_id=%s)
           OR (g.scope_type='department' AND g.scope_id=%s)
           OR (g.scope_type='process' AND g.scope_id=%s))
        GROUP BY sub.user_id''',(tenant_id,indicator['id'],indicator['owner_user_id'],
            indicator.get('unit_id'),indicator.get('department_id'),indicator.get('process_id'))).fetchall()
    for recipient in recipients:
        db.execute('''INSERT INTO cm_business_notifications
           (tenant_id,recipient_user_id,occurrence_id,title,message)
           VALUES(%s,%s,%s,%s,%s)''',
           (tenant_id,recipient['user_id'],occurrence_id,'Ocorrência de negócio',
             f'Indicador {indicator["name"]}: {severity}. Consulte o Nexora autorizado.'))


def apply_evaluation(db:Any,tenant_id:str,indicator:dict[str,Any],now:datetime)->str:
    current=db.execute('''SELECT * FROM cm_business_states WHERE tenant_id=%s AND indicator_id=%s FOR UPDATE''',
                       (tenant_id,indicator['id'])).fetchone()
    policy=db.execute('''SELECT * FROM cm_business_rule_policies
        WHERE tenant_id=%s AND indicator_id=%s''',(tenant_id,indicator['id'])).fetchone()
    state=compute_state(indicator,current,policy,now)
    prev=current['state'] if current else None
    db.execute('''INSERT INTO cm_business_states(tenant_id,indicator_id,state,evaluated_at)
        VALUES(%s,%s,%s,%s) ON CONFLICT(tenant_id,indicator_id)
        DO UPDATE SET state=excluded.state,evaluated_at=excluded.evaluated_at,
        state_changed_at=CASE WHEN cm_business_states.state IS DISTINCT FROM excluded.state
          THEN excluded.evaluated_at ELSE cm_business_states.state_changed_at END''',
        (tenant_id,indicator['id'],state,now))
    if policy and policy['active'] and (state!=prev or state=='sem_dados'):
        db.execute('''INSERT INTO cm_business_rule_evaluations
            (tenant_id,rule_id,rule_version,indicator_id,state,reason,value,evaluated_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
            (tenant_id,policy['id'],policy['version'],indicator['id'],state,
             'evaluation' if state!= 'sem_dados' else 'stale_or_missing',
             current['last_value'] if current else None,now))
    if state in ('critico','atencao'):
        occurrence=db.execute('''SELECT id,severity FROM cm_business_occurrences
            WHERE tenant_id=%s AND indicator_id=%s AND state IN ('open','acknowledged') FOR UPDATE''',
            (tenant_id,indicator['id'])).fetchone()
        if occurrence:
            if occurrence['severity']!=state:
                db.execute('''UPDATE cm_business_occurrences SET severity=%s WHERE tenant_id=%s AND id=%s''',
                           (state,tenant_id,occurrence['id']))
                enqueue(db,tenant_id,occurrence['id'],'escalated')
        else:
            occurrence=db.execute('''INSERT INTO cm_business_occurrences(tenant_id,indicator_id,state,severity)
               VALUES(%s,%s,'open',%s) RETURNING id''',(tenant_id,indicator['id'],state)).fetchone()
            db.execute('''INSERT INTO cm_business_occurrence_history(tenant_id,occurrence_id,action,details)
               VALUES(%s,%s,'opened',%s)''',(tenant_id,occurrence['id'],Jsonb({'severity':state})))
            _notify(db,tenant_id,indicator,occurrence['id'],state)
            enqueue(db,tenant_id,occurrence['id'],'opened')
    elif state=='normal':
        for done in db.execute('''UPDATE cm_business_occurrences SET state='normalized',normalized_at=%s
          WHERE tenant_id=%s AND indicator_id=%s AND state IN ('open','acknowledged') RETURNING id''',
          (now,tenant_id,indicator['id'])).fetchall():
            enqueue(db,tenant_id,done['id'],'normalized')
    # Missing data never silently normalizes/auto-closes a business incident.
    return state


def evaluate_business_tenants(connect:Any,tenant_connection:Any)->dict[str,int]:
    """Invoked by existing Nexora worker. Periodically catches stale or changed rules."""
    with connect() as db:
        tenants=[str(row['id']) for row in db.execute('SELECT id FROM cm_tenants WHERE active').fetchall()]
    counts={'companies':0,'indicators':0}
    for tenant_id in tenants:
        with tenant_connection(tenant_id) as db:
            flag=db.execute("SELECT mode FROM cm_feature_flags WHERE tenant_id=%s AND feature='business_monitor'",(tenant_id,)).fetchone()
            if not flag or flag['mode'] not in ('enabled','write_pilot'): continue
            counts['companies']+=1
            for indicator in db.execute('''SELECT * FROM cm_business_indicators WHERE tenant_id=%s''',(tenant_id,)).fetchall():
                apply_evaluation(db,tenant_id,indicator,datetime.now(UTC))
                counts['indicators']+=1
    return counts
