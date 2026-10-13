"""Safe idempotent overlay integrator for the approved Nexora main branch.

Run from checkout root *after* copying all package files. No git push, migrations or deploy.
All anchors validated before any file is written.
"""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def once(text:str,old:str,new:str,path:str)->str:
    if new in text:return text
    if text.count(old)!=1:raise RuntimeError(f'{path}: anchor mismatch (count={text.count(old)}): {old[:85]!r}')
    return text.replace(old,new,1)

def plan(path:str,changes:list[tuple[str,str]]):
    p=ROOT/path
    if not p.is_file():raise RuntimeError(f'Missing original file: {path}. Copy to a Nexora checkout first.')
    data=p.read_text('utf-8')
    for old,new in changes:data=once(data,old,new,path)
    return p,data

def main():
    # Current old v0.1 installer is included for users who previously applied V0.1.
    existing=(ROOT/'services/backend/central_monitor/main.py').read_text('utf-8')
    if 'business_router' not in existing:
        import importlib.util
        sp=ROOT/'tools/apply_business_monitor.py'
        if not sp.exists():raise RuntimeError('Missing V0.1 integration script')
        spec=importlib.util.spec_from_file_location('business_v01_apply',sp)
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        # We intentionally do not automatically execute v0.1, because v0.1 may be
        # obsolete relative to the checkout. Run it explicitly first.
        raise RuntimeError('V0.1 not yet integrated. First run: python tools/apply_business_monitor.py; then re-run V0.2 integrator.')
    changes=[]
    changes.append(plan('services/backend/central_monitor/main.py',[
        ('from .business import router as business_router, agent_router as business_agent_router',
         'from .business import router as business_router, agent_router as business_agent_router\nfrom .business_p1 import router as business_p1_router'),
        ('app.include_router(business_agent_router)\n',
         'app.include_router(business_agent_router)\napp.include_router(business_p1_router)\n'),
    ]))
    changes.append(plan('services/backend/central_monitor/worker.py',[
        ('def main() -> None:', '''def main() -> None:'''), # deliberate no-op anchor
        ('    while running:\n        process_outbox()', '''    next_business_eval=0.0
    while running:
        if time.monotonic() >= next_business_eval:
            try:
                from .business_evaluation import evaluate_business_tenants
                evaluate_business_tenants(connect,tenant_connection)
            except Exception:
                logger.exception('business_worker_failed')
            next_business_eval=time.monotonic()+60
        process_outbox()'''),
    ]))
    changes.append(plan('agent/Nexora.Agent/Program.cs',[
        ('builder.Services.AddSingleton<BusinessCollector>();',
         'builder.Services.AddSingleton<BusinessReader>();\nbuilder.Services.AddSingleton<BusinessCollector>();')
    ]))
    changes.append(plan('agent/Nexora.Agent/Nexora.Agent.csproj',[
        ('<PackageReference Include="Npgsql" Version="8.0.6" Condition="\'$(RuntimeIdentifier)\' == \'linux-x64\' Or \'$(RuntimeIdentifiers)\' == \'linux-x64\'" />',
         '<PackageReference Include="Npgsql" Version="8.0.6" />\n    <PackageReference Include="Microsoft.Data.Sqlite" Version="8.0.8" />'),
    ]))
    changes.append(plan('services/web/src/business-monitor.jsx',[
        ("import './business-monitor.css';","import './business-monitor.css';\nimport BusinessP1 from './business-p1.jsx';"),
        ("[['visao-geral','Visão Geral'],['monitoramentos','Monitoramentos'],['ocorrencias','Ocorrências']]", "[['visao-geral','Visão Geral'],['monitoramentos','Monitoramentos'],['ocorrencias','Ocorrências'],['processos','Processos'],['regras','Regras'],['conectores','Conectores'],['unidades','Unidades'],['notificacoes','Notificações'],['relatorios','Relatórios'],['permissoes','Permissões']].filter(([key])=>key!=='conectores'||has(allowed,'business.source.manage')).filter(([key])=>key!=='relatorios'||has(allowed,'business.export')).filter(([key])=>key!=='permissoes'||has(allowed,'business.grants.manage')).filter(([key])=>key!=='regras'||has(allowed,'business.view'))") ,
        ("    ) : page==='monitoramentos' ? <ListPage", "    ) : ['processos','regras','conectores','unidades','notificacoes','relatorios','permissoes'].includes(page) ? <BusinessP1 page={page} tenantId={tenantId} access={access} request={requestRef.current} ui={ui} /> : page==='monitoramentos' ? <ListPage"),
    ]))
    # Validate first; no partial mutation when a code version differs.
    for p,new in changes:
        original=p.read_text('utf-8')
        if new!=original:p.write_text(new,'utf-8');print('PATCHED',p.relative_to(ROOT))
        else:print('ALREADY APPLIED',p.relative_to(ROOT))
    print('V0.2 integration complete. Run builds, tests and HML migration before any release.')
if __name__=='__main__':main()
