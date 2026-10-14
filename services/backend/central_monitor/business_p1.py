"""BM-02..BM-06: business processes, rules, connectors, Hub Studio and notifications.

All endpoints require a concrete tenant; all DB calls run with tenant_connection/RLS.
This module never grants access from a technical/ITSM role.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator, model_validator

from .auth import Identity, current_identity, tenant_header
from .business import _actor, _enabled, _require_grant_admin
from .business_rules import validate_thresholds
from .db import tenant_connection

router = APIRouter(prefix='/api/v1/business', tags=['business-monitor-p1'])
IDENTIFIER = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,80}$')
PERMITTED_CONNECTORS = {'sql_view','postgres_view','rest_get','csv','xlsx'}
FORBIDDEN_KEYS = {'password','secret','token','api_key','authorization','connection_string','access_key','private_key'}


def authorize(identity: Identity, tenant_id: str, capability: str) -> tuple[dict[str, Any], dict[str, Any]]:
    actor = _actor(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        if not _enabled(db, tenant_id):
            raise HTTPException(404, 'Business Monitor is disabled')
        grant = db.execute('''SELECT scope_type,scope_id FROM cm_business_grants
           WHERE tenant_id=%s AND user_id=%s AND capability=%s AND active''',
           (tenant_id,actor['id'],capability)).fetchone()
    if not grant:
        raise HTTPException(403, 'Business capability denied')
    return actor, grant


def has_scope(row: dict[str,Any], actor: dict[str,Any], grant: dict[str,Any]) -> bool:
    """Fail-closed object filter, including restricted view of aggregates."""
    kind=grant['scope_type']; ident=str(grant.get('scope_id') or '')
    if kind=='company': return True
    if kind=='own': return str(row.get('owner_user_id'))==str(actor['id'])
    if kind in ('unit','department','process'):
        key={'unit':'unit_id','department':'department_id','process':'process_id'}[kind]
        return bool(ident) and str(row.get(key))==ident
    return False


def visible(rows: list[dict[str,Any]], actor: dict[str,Any], grant: dict[str,Any]) -> list[dict[str,Any]]:
    return [r for r in rows if has_scope(r,actor,grant)]


def eligible_owner(db:Any, tenant_id:str, user_id:UUID) -> bool:
    return bool(db.execute('''SELECT 1 FROM cm_access_grants
      WHERE tenant_id=%s AND user_id=%s AND active AND valid_from<=now()
        AND (valid_until IS NULL OR valid_until>now()) LIMIT 1''', (tenant_id,user_id)).fetchone())


def audit(db:Any,tenant_id:str,actor:dict[str,Any],action:str,obj:str,oid:Any,details:dict|None=None):
    db.execute('''INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details)
       VALUES(%s,%s,%s,%s,%s,%s)''',(tenant_id,actor['id'],action,obj,str(oid),Jsonb(details or {})))


class UnitCreate(BaseModel):
    name: str = Field(min_length=2,max_length=150)

class ProcessCreate(BaseModel):
    name: str = Field(min_length=2,max_length=180)
    description: str = Field(default='',max_length=4000)
    unit_id: UUID | None = None
    department_id: UUID | None = None

class StepCreate(BaseModel):
    step_code: str = Field(pattern=r'^[a-z][a-z0-9_]{1,63}$')
    name: str = Field(min_length=2,max_length=180)
    ordinal: int = Field(ge=1,le=1000)
    due_at: datetime | None = None
    predecessor_id: UUID | None = None

    @field_validator('due_at')
    @classmethod
    def tz_only(cls,v):
        if v is not None and v.tzinfo is None: raise ValueError('Timezone required')
        return v

class StepAction(BaseModel):
    state: Literal['running','done','blocked','cancelled']
    expected_state: Literal['pending','running','done','blocked','cancelled']

class RuleConfig(BaseModel):
    indicator_id: UUID
    direction: Literal['above','below'] = 'above'
    warning_value: Decimal | None = None
    critical_value: Decimal | None = None
    hysteresis: Decimal = Field(default=Decimal('0'),ge=0)
    cooldown_seconds: int = Field(default=0,ge=0,le=604800)
    stale_after_seconds: int = Field(default=1800,ge=30,le=2592000)
    timezone: str = Field(default='America/Sao_Paulo', max_length=64)
    weekdays: list[int] = Field(default_factory=lambda:[1,2,3,4,5,6,7],min_length=1,max_length=7)
    check_from: str | None = None
    check_until: str | None = None
    expected_version: int | None = Field(default=None,ge=1)

    @model_validator(mode='after')
    def validate(self):
        validate_thresholds(self.direction,self.warning_value,self.critical_value)
        try: ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as e: raise ValueError('Invalid timezone') from e
        if len(set(self.weekdays))!=len(self.weekdays) or any(x not in range(1,8) for x in self.weekdays):
            raise ValueError('weekdays must be unique ISO weekday numbers')
        if bool(self.check_from) != bool(self.check_until):raise ValueError('Both working hours required')
        for v in (self.check_from,self.check_until):
            if v is not None and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',v):
                raise ValueError('Hour must be HH:MM')
        if self.check_from is not None and self.check_from>=self.check_until:
            raise ValueError('Working hours invalid')
        return self

class ConnectorCreate(BaseModel):
    agent_id:UUID
    indicator_id:UUID
    kind:Literal['sql_view','postgres_view','rest_get','csv','xlsx']
    name:str=Field(min_length=3,max_length=180)
    config:dict[str,Any]

class CollectorPublish(BaseModel):
    expected_revision:int=Field(ge=0)
    reason:str=Field(min_length=5,max_length=300)

class GrantScope(BaseModel):
    user_id:UUID
    capability:Literal['business.view','business.monitor.create','business.monitor.update','business.monitor.inactivate',
        'business.occurrence.manage','business.source.manage','business.grants.manage','business.rule.manage',
        'business.process.manage','business.collector.test','business.collector.publish','business.export','business.notifications.manage']
    scope_type:Literal['company','own','unit','department','process']
    scope_id:UUID | None = None
    active:bool=True
    reason:str=Field(min_length=5,max_length=500)

    @model_validator(mode='after')
    def scoped(self):
        if self.scope_type in ('company','own') and self.scope_id is not None: raise ValueError('Scope target not permitted')
        if self.scope_type in ('unit','department','process') and self.scope_id is None: raise ValueError('Scope target required')
        return self


def safe_config(kind:str, raw:dict[str,Any]) -> dict[str,Any]:
    """Keep only known connector keys, never persist secret values or raw SQL."""
    allowed={
       'sql_view':{'schema','view','column','poll_seconds'},
       'postgres_view':{'schema','view','column','poll_seconds'},
       'rest_get':{'url','json_property','poll_seconds','max_bytes'},
       'csv':{'path','column','poll_seconds'},
       'xlsx':{'path','sheet','cell','poll_seconds'},
    }[kind]
    if not raw or set(raw)-allowed: raise ValueError('Unsupported or sensitive connector configuration keys')
    if any(key.lower() in FORBIDDEN_KEYS for key in raw):raise ValueError('Credentials must remain in HUB secret store')
    config={k:raw[k] for k in sorted(raw)}
    p=config.get('poll_seconds',300)
    if not isinstance(p,int) or isinstance(p,bool) or not 60<=p<=86400:raise ValueError('Invalid collection cadence')
    config['poll_seconds']=p
    if kind in ('sql_view','postgres_view'):
        for key in ('schema','view','column'):
            if not isinstance(config.get(key),str) or not IDENTIFIER.fullmatch(config[key]):raise ValueError('Invalid view identifier')
        if not config['view'].lower().startswith('vw_nexora_'):raise ValueError('Only approved vw_nexora_* views are allowed')
    if kind=='rest_get':
        uri=urlsplit(str(config.get('url','')))
        if uri.scheme!='https' or not uri.hostname or uri.username or uri.password or uri.fragment:
            raise ValueError('REST endpoint must be HTTPS without embedded credentials')
        if uri.port not in (None,443,8443):raise ValueError('Unapproved REST port')
        try: ipaddress.ip_address(uri.hostname);raise ValueError('Literal IPs are forbidden')
        except ValueError as exc:
            if str(exc)=='Literal IPs are forbidden': raise
        if uri.hostname.lower() in ('localhost','localhost.localdomain') or uri.hostname.endswith(('.local','.internal')):
            raise ValueError('Local/internal host requires separate restricted connector policy')
        prop=config.get('json_property')
        if not isinstance(prop,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]{0,127}',prop):raise ValueError('JSON property required')
        m=config.get('max_bytes',65536)
        if not isinstance(m,int) or not 256<=m<=1048576:raise ValueError('max_bytes out of bounds')
        config['max_bytes']=m
    if kind in ('csv','xlsx'):
        path=config.get('path')
        if not isinstance(path,str) or len(path)>512 or '..' in path.replace('\\','/').split('/') or not path:
            raise ValueError('Invalid allowed data file path')
        # Agent must resolve against a configured read-only base directory.
        if path.startswith(('/', '\\')) or ':' in path: raise ValueError('Only relative paths inside approved data directory')
    if kind=='csv' and (not isinstance(config.get('column'),str) or not IDENTIFIER.fullmatch(config['column'])):
        raise ValueError('CSV column required')
    if kind=='xlsx':
        if not re.fullmatch(r'[A-Za-z0-9 _-]{1,80}',str(config.get('sheet',''))):raise ValueError('Sheet name required')
        if not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,5}',str(config.get('cell',''))):raise ValueError('Cell must be A1 style')
    return config


@router.get('/units')
def units(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,grant=authorize(identity,tenant_id,'business.view')
    with tenant_connection(tenant_id) as db:
        rows=list(db.execute('SELECT * FROM cm_business_units WHERE tenant_id=%s AND active ORDER BY name',(tenant_id,)).fetchall())
    if grant['scope_type']=='company': return rows
    if grant['scope_type']=='unit': return [x for x in rows if x['id']==grant['scope_id']]
    return []

@router.post('/units',status_code=201)
def create_unit(payload:UnitCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,grant=authorize(identity,tenant_id,'business.process.manage')
    if grant['scope_type']!='company': raise HTTPException(403,'Company scope required to create units')
    with tenant_connection(tenant_id) as db:
        row=db.execute('INSERT INTO cm_business_units(tenant_id,name) VALUES(%s,%s) RETURNING *',(tenant_id,payload.name.strip())).fetchone()
        audit(db,tenant_id,actor,'create','business_unit',row['id'])
        return row

@router.get('/processes')
def processes(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,grant=authorize(identity,tenant_id,'business.view')
    with tenant_connection(tenant_id) as db:
        rows=list(db.execute('''SELECT p.*,p.id AS process_id,u.name AS unit_name,d.name AS department_name,
         (SELECT count(*) FROM cm_business_process_steps s WHERE s.tenant_id=p.tenant_id AND s.process_id=p.id) AS step_count
         FROM cm_business_processes p LEFT JOIN cm_business_units u ON (u.tenant_id=p.tenant_id AND u.id=p.unit_id)
         LEFT JOIN cm_departments d ON (d.tenant_id=p.tenant_id AND d.id=p.department_id)
         WHERE p.tenant_id=%s AND p.active ORDER BY p.name LIMIT 5000''',(tenant_id,)).fetchall())
    return visible(rows,actor,grant)[:500]  # scope first, then cap

@router.post('/processes',status_code=201)
def create_process(payload:ProcessCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,grant=authorize(identity,tenant_id,'business.process.manage')
    prototype={'owner_user_id':actor['id'],'unit_id':payload.unit_id,'department_id':payload.department_id,'process_id':None}
    if grant['scope_type'] not in ('company','own','unit','department') or not has_scope(prototype,actor,grant):
        raise HTTPException(403,'Process outside grant scope')
    with tenant_connection(tenant_id) as db:
        if payload.unit_id and not db.execute('SELECT 1 FROM cm_business_units WHERE tenant_id=%s AND id=%s AND active',(tenant_id,payload.unit_id)).fetchone():
            raise HTTPException(404,'Unit not found')
        if payload.department_id and not db.execute('SELECT 1 FROM cm_departments WHERE tenant_id=%s AND id=%s',(tenant_id,payload.department_id)).fetchone():
            raise HTTPException(404,'Department not found')
        row=db.execute('''INSERT INTO cm_business_processes(tenant_id,name,description,unit_id,department_id,owner_user_id)
         VALUES(%s,%s,%s,%s,%s,%s) RETURNING *''',(tenant_id,payload.name,payload.description,payload.unit_id,payload.department_id,actor['id'])).fetchone()
        audit(db,tenant_id,actor,'create','business_process',row['id'])
        return row

@router.get('/processes/{process_id}/steps')
def steps(process_id:UUID,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,grant=authorize(identity,tenant_id,'business.view')
    with tenant_connection(tenant_id) as db:
        parent=db.execute('SELECT *,id AS process_id FROM cm_business_processes WHERE tenant_id=%s AND id=%s',(tenant_id,process_id)).fetchone()
        if not parent or not has_scope(parent,actor,grant):raise HTTPException(404,'Process not found')
        return list(db.execute('''SELECT * FROM cm_business_process_steps
          WHERE tenant_id=%s AND process_id=%s ORDER BY ordinal''',(tenant_id,process_id)).fetchall())

@router.post('/processes/{process_id}/steps',status_code=201)
def create_step(process_id:UUID,payload:StepCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,grant=authorize(identity,tenant_id,'business.process.manage')
    with tenant_connection(tenant_id) as db:
        parent=db.execute('SELECT *,id AS process_id FROM cm_business_processes WHERE tenant_id=%s AND id=%s FOR UPDATE',(tenant_id,process_id)).fetchone()
        if not parent or not has_scope(parent,actor,grant):raise HTTPException(404,'Process not found')
        if payload.predecessor_id:
            pred=db.execute('SELECT id,ordinal FROM cm_business_process_steps WHERE tenant_id=%s AND process_id=%s AND id=%s',
                            (tenant_id,process_id,payload.predecessor_id)).fetchone()
            if not pred or pred['ordinal'] >= payload.ordinal:raise HTTPException(422,'Predecessor must exist at an earlier position')
        row=db.execute('''INSERT INTO cm_business_process_steps
          (tenant_id,process_id,step_code,name,ordinal,due_at,owner_user_id)
          VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
          (tenant_id,process_id,payload.step_code,payload.name,payload.ordinal,payload.due_at,actor['id'])).fetchone()
        if payload.predecessor_id:
            db.execute('''INSERT INTO cm_business_step_dependencies(tenant_id,predecessor_id,successor_id)
              VALUES(%s,%s,%s)''',(tenant_id,payload.predecessor_id,row['id']))
        audit(db,tenant_id,actor,'create','business_step',row['id'])
        return row

@router.post('/steps/{step_id}/state')
def change_step(step_id:UUID,payload:StepAction,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,grant=authorize(identity,tenant_id,'business.process.manage')
    with tenant_connection(tenant_id) as db:
        row=db.execute('''SELECT s.*,p.owner_user_id AS process_owner,p.unit_id,p.department_id,p.id AS process_id
          FROM cm_business_process_steps s JOIN cm_business_processes p ON p.tenant_id=s.tenant_id AND p.id=s.process_id
          WHERE s.tenant_id=%s AND s.id=%s FOR UPDATE OF s''',(tenant_id,step_id)).fetchone()
        if not row or not has_scope({**row,'owner_user_id':row['process_owner']},actor,grant):raise HTTPException(404,'Step not found')
        if row['state']!=payload.expected_state:raise HTTPException(409,'Step status changed; reload')
        if row['state'] in ('done','cancelled'):raise HTTPException(409,'Terminal step cannot transition')
        if payload.state=='done':
            blocking=db.execute('''SELECT 1 FROM cm_business_step_dependencies dep
              JOIN cm_business_process_steps prev ON prev.tenant_id=dep.tenant_id AND prev.id=dep.predecessor_id
              WHERE dep.tenant_id=%s AND dep.successor_id=%s AND prev.state<>'done' LIMIT 1''',(tenant_id,step_id)).fetchone()
            if blocking: raise HTTPException(409,'Predecessor not finished')
        row=db.execute('''UPDATE cm_business_process_steps SET state=%s,updated_at=now(),
          finished_at=CASE WHEN %s='done' THEN now() ELSE NULL END WHERE tenant_id=%s AND id=%s RETURNING *''',
          (payload.state,payload.state,tenant_id,step_id)).fetchone()
        audit(db,tenant_id,actor,'transition','business_step',step_id,{'state':payload.state})
        return row

@router.get('/rules')
def rules(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,grant=authorize(identity,tenant_id,'business.view')
    with tenant_connection(tenant_id) as db:
        rows=list(db.execute('''SELECT r.*,i.name AS indicator_name,i.owner_user_id,i.unit_id,i.department_id,i.process_id
          FROM cm_business_rule_policies r JOIN cm_business_indicators i ON i.tenant_id=r.tenant_id AND i.id=r.indicator_id
          WHERE r.tenant_id=%s ORDER BY i.name''',(tenant_id,)).fetchall())
    return visible(rows,actor,grant)

@router.put('/rules')
def upsert_rule(payload:RuleConfig,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,grant=authorize(identity,tenant_id,'business.rule.manage')
    with tenant_connection(tenant_id) as db:
        indicator=db.execute('SELECT * FROM cm_business_indicators WHERE tenant_id=%s AND id=%s AND active',(tenant_id,payload.indicator_id)).fetchone()
        if not indicator or not has_scope(indicator,actor,grant):raise HTTPException(404,'Indicator not found')
        previous=db.execute('SELECT id,version FROM cm_business_rule_policies WHERE tenant_id=%s AND indicator_id=%s FOR UPDATE',(tenant_id,payload.indicator_id)).fetchone()
        if previous and payload.expected_version!=previous['version']:raise HTTPException(409,'Rule revision conflict')
        if not previous and payload.expected_version is not None:raise HTTPException(409,'Rule does not exist')
        params=(payload.direction,payload.warning_value,payload.critical_value,payload.hysteresis,
                payload.cooldown_seconds,payload.timezone,payload.weekdays,payload.check_from,payload.check_until,
                payload.stale_after_seconds)
        if previous:
            row=db.execute('''UPDATE cm_business_rule_policies SET direction=%s,warning_value=%s,critical_value=%s,
              hysteresis=%s,cooldown_seconds=%s,timezone=%s,weekdays=%s,check_from=%s,check_until=%s,
              stale_after_seconds=%s,version=version+1,updated_at=now()
              WHERE tenant_id=%s AND indicator_id=%s RETURNING *''',(*params,tenant_id,payload.indicator_id)).fetchone()
        else:
            row=db.execute('''INSERT INTO cm_business_rule_policies
              (tenant_id,indicator_id,owner_user_id,direction,warning_value,critical_value,hysteresis,cooldown_seconds,
               timezone,weekdays,check_from,check_until,stale_after_seconds)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
              (tenant_id,payload.indicator_id,actor['id'],*params)).fetchone()
        db.execute('UPDATE cm_business_states SET state=\'sem_dados\',evaluated_at=now() WHERE tenant_id=%s AND indicator_id=%s',
                   (tenant_id,payload.indicator_id))
        audit(db,tenant_id,actor,'publish','business_rule',row['id'],{'version':row['version']})
        return row

@router.get('/collectors')
def collectors(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,grant=authorize(identity,tenant_id,'business.source.manage')
    with tenant_connection(tenant_id) as db:
        rows=list(db.execute('''SELECT c.*,i.owner_user_id,i.unit_id,i.department_id,i.process_id,i.name AS indicator_name
         FROM cm_business_collector_defs c JOIN cm_business_indicators i ON i.tenant_id=c.tenant_id AND i.id=c.indicator_id
         WHERE c.tenant_id=%s ORDER BY c.updated_at DESC''',(tenant_id,)).fetchall())
    return visible(rows,actor,grant)

@router.post('/collectors',status_code=201)
def create_collector(payload:ConnectorCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,grant=authorize(identity,tenant_id,'business.source.manage')
    try: config=safe_config(payload.kind,payload.config)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    with tenant_connection(tenant_id) as db:
        indicator=db.execute('SELECT * FROM cm_business_indicators WHERE tenant_id=%s AND id=%s AND active',
                             (tenant_id,payload.indicator_id)).fetchone()
        if not indicator or not has_scope(indicator,actor,grant):raise HTTPException(404,'Indicator not found')
        agent=db.execute('SELECT id FROM cm_agents WHERE tenant_id=%s AND id=%s AND status<>\'disabled\'',
                         (tenant_id,payload.agent_id)).fetchone()
        if not agent:raise HTTPException(404,'HUB not found')
        row=db.execute('''INSERT INTO cm_business_collector_defs
         (tenant_id,agent_id,indicator_id,kind,name,config,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
         (tenant_id,payload.agent_id,payload.indicator_id,payload.kind,payload.name,Jsonb(config),actor['id'])).fetchone()
        audit(db,tenant_id,actor,'create','business_collector',row['id'])
        return row

@router.post('/collectors/{collector_id}/test')
def test_collector(collector_id:UUID,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,grant=authorize(identity,tenant_id,'business.collector.test')
    with tenant_connection(tenant_id) as db:
        row=db.execute('''SELECT c.*,i.owner_user_id,i.unit_id,i.department_id,i.process_id
          FROM cm_business_collector_defs c JOIN cm_business_indicators i ON i.tenant_id=c.tenant_id AND i.id=c.indicator_id
          WHERE c.tenant_id=%s AND c.id=%s''',(tenant_id,collector_id)).fetchone()
        if not row or not has_scope(row,actor,grant):raise HTTPException(404,'Collector not found')
        # Testing cannot open sockets from the central server; it schedules a safe validation by HUB.
        return {'status':'configuration_validated','collector_id':row['id'],
                'note':'Remote execution requires a HUB command queue and explicit test-run correlation; no source query was executed.'}

@router.post('/collectors/{collector_id}/publish')
def publish_collector(collector_id:UUID,payload:CollectorPublish,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,grant=authorize(identity,tenant_id,'business.collector.publish')
    with tenant_connection(tenant_id) as db:
        row=db.execute('''SELECT c.*,i.owner_user_id,i.unit_id,i.department_id,i.process_id
          FROM cm_business_collector_defs c JOIN cm_business_indicators i ON i.tenant_id=c.tenant_id AND i.id=c.indicator_id
          WHERE c.tenant_id=%s AND c.id=%s FOR UPDATE OF c''',(tenant_id,collector_id)).fetchone()
        if not row or not has_scope(row,actor,grant):raise HTTPException(404,'Collector not found')
        if row['created_by']==actor['id']:raise HTTPException(403,'Collector author cannot self-approve')
        if row['current_revision']!=payload.expected_revision:raise HTTPException(409,'Collector revision conflict')
        if row['state']=='suspended':raise HTTPException(409,'Suspended connector cannot be published')
        try: config=safe_config(row['kind'],row['config'])
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
        rev=row['current_revision']+1
        digest=hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        db.execute('''INSERT INTO cm_business_collector_revisions(tenant_id,collector_id,revision,config,sha256,approved_by)
          VALUES(%s,%s,%s,%s,%s,%s)''',(tenant_id,collector_id,rev,Jsonb(config),digest,actor['id']))
        db.execute('''UPDATE cm_business_collector_defs SET current_revision=%s,state='published',updated_at=now()
          WHERE tenant_id=%s AND id=%s''',(rev,tenant_id,collector_id))
        # Explicit binding survives HUB reboot; only published revisions distributed.
        db.execute('''INSERT INTO cm_business_agent_bindings(tenant_id,agent_id,indicator_id,created_by)
           VALUES(%s,%s,%s,%s) ON CONFLICT(tenant_id,agent_id,indicator_id) DO UPDATE SET active=true''',
           (tenant_id,row['agent_id'],row['indicator_id'],actor['id']))
        agent=db.execute('SELECT configuration FROM cm_agents WHERE tenant_id=%s AND id=%s FOR UPDATE',
                         (tenant_id,row['agent_id'])).fetchone()
        if not agent:raise HTTPException(409,'Agent decommissioned')
        old=dict(agent['configuration'] or {})
        jobs=[x for x in old.get('business_collectors',[]) if str(x.get('indicator_id'))!=str(row['indicator_id'])]
        jobs.append({'indicator_id':str(row['indicator_id']),'collector_id':str(collector_id),'revision':rev,'kind':row['kind'],**config})
        old['business_collectors']=jobs
        db.execute('UPDATE cm_agents SET configuration=%s,updated_at=now() WHERE tenant_id=%s AND id=%s',
                   (Jsonb(old),tenant_id,row['agent_id']))
        audit(db,tenant_id,actor,'publish','business_collector',collector_id,{'revision':rev,'reason':payload.reason})
        return {'status':'published','revision':rev,'collector_id':collector_id}

@router.post('/collectors/{collector_id}/rollback')
def rollback_collector(collector_id:UUID,payload:CollectorPublish,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,grant=authorize(identity,tenant_id,'business.collector.publish')
    with tenant_connection(tenant_id) as db:
        row=db.execute('''SELECT c.*,i.owner_user_id,i.unit_id,i.department_id,i.process_id
          FROM cm_business_collector_defs c JOIN cm_business_indicators i ON i.tenant_id=c.tenant_id AND i.id=c.indicator_id
          WHERE c.tenant_id=%s AND c.id=%s FOR UPDATE OF c''',(tenant_id,collector_id)).fetchone()
        if not row or not has_scope(row,actor,grant):raise HTTPException(404,'Collector not found')
        if payload.expected_revision!=row['current_revision']:raise HTTPException(409,'Revision conflict')
        if row['current_revision']<2:raise HTTPException(409,'No earlier revision available')
        old=db.execute('''SELECT config FROM cm_business_collector_revisions WHERE tenant_id=%s AND collector_id=%s
          AND revision=%s''',(tenant_id,collector_id,row['current_revision']-1)).fetchone()
        if not old:raise HTTPException(409,'Prior revision missing')
        # Rollback is a NEW revision so audit history never rewrites previous versions.
        try: config=safe_config(row['kind'],old['config'])
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
        rev=row['current_revision']+1
        digest=hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        db.execute('''INSERT INTO cm_business_collector_revisions(tenant_id,collector_id,revision,config,sha256,approved_by)
          VALUES(%s,%s,%s,%s,%s,%s)''',(tenant_id,collector_id,rev,Jsonb(config),digest,actor['id']))
        db.execute('''UPDATE cm_business_collector_defs SET config=%s,current_revision=%s,state='published',updated_at=now()
          WHERE tenant_id=%s AND id=%s''',(Jsonb(config),rev,tenant_id,collector_id))
        agent=db.execute('SELECT configuration FROM cm_agents WHERE tenant_id=%s AND id=%s FOR UPDATE',(tenant_id,row['agent_id'])).fetchone()
        if not agent:raise HTTPException(409,'HUB missing')
        cfg=dict(agent['configuration'] or {})
        jobs=[x for x in cfg.get('business_collectors',[]) if str(x.get('indicator_id'))!=str(row['indicator_id'])]
        jobs.append({'indicator_id':str(row['indicator_id']),'collector_id':str(collector_id),'revision':rev,'kind':row['kind'],**config})
        cfg['business_collectors']=jobs
        db.execute('UPDATE cm_agents SET configuration=%s,updated_at=now() WHERE tenant_id=%s AND id=%s',(Jsonb(cfg),tenant_id,row['agent_id']))
        audit(db,tenant_id,actor,'rollback','business_collector',collector_id,{'revision':rev,'reason':payload.reason})
        return {'status':'rolled_back_as_new_revision','revision':rev}

@router.get('/notifications')
def notifications(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,grant=authorize(identity,tenant_id,'business.view')
    with tenant_connection(tenant_id) as db:
        rows=list(db.execute('''SELECT n.*,i.owner_user_id,i.unit_id,i.department_id,i.process_id
          FROM cm_business_notifications n JOIN cm_business_occurrences o
            ON o.tenant_id=n.tenant_id AND o.id=n.occurrence_id
          JOIN cm_business_indicators i ON i.tenant_id=o.tenant_id AND i.id=o.indicator_id
          WHERE n.tenant_id=%s AND n.recipient_user_id=%s ORDER BY n.created_at DESC LIMIT 1000''',
          (tenant_id,actor['id'])).fetchall())
    return visible(rows,actor,grant)[:100]

@router.get('/reports/indicators')
def indicator_report(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity),
                     limit:int=Query(default=100,ge=1,le=500)):
    actor,grant=authorize(identity,tenant_id,'business.export')
    with tenant_connection(tenant_id) as db:
        rows=list(db.execute('''SELECT i.*,s.state,s.last_value,s.last_collected_at,s.evaluated_at
          FROM cm_business_indicators i LEFT JOIN cm_business_states s ON s.tenant_id=i.tenant_id AND s.indicator_id=i.id
          WHERE i.tenant_id=%s ORDER BY i.name LIMIT 5000''',(tenant_id,)).fetchall())
    rows=visible(rows,actor,grant)[:limit]
    return {'data_as_of':datetime.now(UTC),'rows':rows,'count':len(rows),
            'financial_exposure':None,'financial_exposure_reason':'No verified financial methodology'}

@router.put('/grants-scoped')
def change_grant_scope(payload:GrantScope,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor=_actor(identity,tenant_id)
    if str(actor['id'])==str(payload.user_id):raise HTTPException(403,'Self-grant forbidden')
    with tenant_connection(tenant_id) as db:
        _require_grant_admin(db,tenant_id,actor,payload.capability)
        if payload.scope_type!='company' and payload.capability=='business.grants.manage':
            raise HTTPException(422,'Grant administration requires company scope')
        if not eligible_owner(db,tenant_id,payload.user_id):raise HTTPException(404,'User lacks company access')
        if payload.scope_id is not None:
            table={'unit':'cm_business_units','department':'cm_departments','process':'cm_business_processes'}[payload.scope_type]
            if not db.execute(f'SELECT id FROM {table} WHERE tenant_id=%s AND id=%s',(tenant_id,payload.scope_id)).fetchone():
                raise HTTPException(404,'Scope target not found')
        db.execute('''INSERT INTO cm_business_grants(tenant_id,user_id,capability,scope_type,scope_id,active,granted_by,reason)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT (tenant_id,user_id,capability) DO UPDATE SET scope_type=excluded.scope_type,
          scope_id=excluded.scope_id,active=excluded.active,granted_by=excluded.granted_by,reason=excluded.reason,
          granted_at=now()''',(tenant_id,payload.user_id,payload.capability,payload.scope_type,payload.scope_id,
                         payload.active,actor['id'],payload.reason))
        audit(db,tenant_id,actor,'business_grant','user',payload.user_id,{'capability':payload.capability,'scope':payload.scope_type,'reason':payload.reason})
        return {'status':'saved'}


@router.get('/eligible-users')
def eligible_users(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor=_actor(identity,tenant_id)
    with tenant_connection(tenant_id) as db:
        _require_grant_admin(db,tenant_id,actor)
        return list(db.execute("""SELECT DISTINCT u.id,u.display_name,u.email FROM cm_users u
            JOIN cm_access_grants g ON g.user_id=u.id
            WHERE g.tenant_id=%s AND g.active AND u.active
             AND g.valid_from<=now() AND (g.valid_until IS NULL OR g.valid_until>now())
            ORDER BY u.display_name LIMIT 300""",(tenant_id,)).fetchall())

class SubscriptionChange(BaseModel):
    indicator_id:UUID
    enabled:bool=True

@router.put('/notifications/subscriptions')
def change_subscription(payload:SubscriptionChange,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,grant=authorize(identity,tenant_id,'business.view')
    with tenant_connection(tenant_id) as db:
        ind=db.execute('SELECT * FROM cm_business_indicators WHERE tenant_id=%s AND id=%s',
                      (tenant_id,payload.indicator_id)).fetchone()
        if not ind or not has_scope(ind,actor,grant):raise HTTPException(404,'Indicator unavailable')
        db.execute("""INSERT INTO cm_business_notification_subscriptions(tenant_id,user_id,indicator_id,enabled)
            VALUES(%s,%s,%s,%s) ON CONFLICT(tenant_id,user_id,indicator_id) DO UPDATE SET enabled=excluded.enabled""",
            (tenant_id,actor['id'],payload.indicator_id,payload.enabled))
        return {'status':'saved'}

@router.get('/hubs')
def business_hubs(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,grant=authorize(identity,tenant_id,'business.source.manage')
    if grant['scope_type']!='company':
        # The HUB may serve many indicators; its existence is not safely object-scoped here.
        raise HTTPException(403,'Company-scope source grant required')
    with tenant_connection(tenant_id) as db:
        return list(db.execute('''SELECT id,name,status,version,capabilities,last_seen_at
             FROM cm_agents WHERE tenant_id=%s AND status<>'disabled' ORDER BY name''',(tenant_id,)).fetchall())
