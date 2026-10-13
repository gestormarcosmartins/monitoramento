"""Apply small, guarded integration edits after overlaying the Business ZIP into Nexora.

Run from repository root: python tools/apply_business_monitor.py
This script checks every expected anchor before changing ANY file.
"""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(s: str, old: str, new: str, filename: str) -> str:
    if new in s:
        return s
    count=s.count(old)
    if count != 1:
        raise RuntimeError(f"{filename}: expected anchor once, found {count}: {old[:90]!r}. Aborting; review against updated main.")
    return s.replace(old,new,1)


def plan(path: str, replacements: list[tuple[str,str]]) -> tuple[Path,str]:
    p=ROOT/path
    if not p.is_file():
        raise RuntimeError(f"File not found: {p}. Run from an overlaid Nexora checkout.")
    s=p.read_text(encoding='utf-8')
    for a,b in replacements:s=replace_once(s,a,b,path)
    return p,s


def main():
    changes=[]
    changes.append(plan('services/backend/central_monitor/main.py',[
        ('from .agent_api import router as agent_router',
         'from .agent_api import router as agent_router\nfrom .business import router as business_router, agent_router as business_agent_router'),
        ('app.include_router(agent_router)\n',
         'app.include_router(agent_router)\napp.include_router(business_router)\napp.include_router(business_agent_router)\n'),
    ]))
    changes.append(plan('agent/Nexora.Agent/Program.cs',[
        ('builder.Services.AddSingleton<LocalQueue>();',
         'builder.Services.AddSingleton<LocalQueue>();\nbuilder.Services.AddSingleton<BusinessQueue>();\nbuilder.Services.AddSingleton<BusinessCollector>();'),
    ]))
    changes.append(plan('agent/Nexora.Agent/SqlServerCollector.cs',[
        ('public sealed class SqlServerCollector', 'public sealed partial class SqlServerCollector'),
    ]))
    changes.append(plan('agent/Nexora.Agent/CentralClient.cs',[
        ('public sealed class CentralClient', 'public sealed partial class CentralClient'),
    ]))
    changes.append(plan('agent/Nexora.Agent/AgentWorker.cs',[
        ('    private readonly SqlServerCollector _sql;',
         '    private readonly SqlServerCollector _sql;\n    private readonly BusinessCollector _business;'),
        ('HostCollector host, SqlServerCollector sql,',
         'HostCollector host, SqlServerCollector sql, BusinessCollector business,'),
        ('        _sql = sql;', '        _sql = sql;\n        _business = business;'),
        ('var config = await _central.ConfigurationAsync(stoppingToken);',
         'var config = await CachedConfigurationAsync(stoppingToken);'),
        ('                await _central.HeartbeatAsync(health,stoppingToken);',
         '                await _business.CollectAsync(config,stoppingToken);\n                await _central.HeartbeatAsync(health,stoppingToken);'),
        ('                await FlushAsync(stoppingToken);',
         '                await FlushAsync(stoppingToken);\n                await _business.FlushAsync(stoppingToken);'),
        ('    private async Task CollectAsync(AgentConfiguration config, CancellationToken cancellationToken)',
         '''    private async Task<AgentConfiguration> CachedConfigurationAsync(CancellationToken token)
    {
        var path=Path.Combine(AgentPaths.DataDirectory(_options), "last-agent-config.json");
        try
        {
            var config=await _central.ConfigurationAsync(token);
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            var tmp=path+".tmp";
            await File.WriteAllTextAsync(tmp,System.Text.Json.JsonSerializer.Serialize(config),token);
            File.Move(tmp,path,true);
            return config;
        }
        catch (Exception ex) when (!token.IsCancellationRequested)
        {
            _logger.LogWarning("Using last approved cached agent configuration: {ErrorType}",ex.GetType().Name);
            if (!File.Exists(path)) throw;
            var cached=System.Text.Json.JsonSerializer.Deserialize<AgentConfiguration>(await File.ReadAllTextAsync(path,token));
            return cached ?? throw new InvalidDataException("No valid cached configuration");
        }
    }

    private async Task CollectAsync(AgentConfiguration config, CancellationToken cancellationToken)'''),
    ]))
    changes.append(plan('services/web/src/main.jsx',[
        ('import { isTenantResponseCurrent } from "./tenant-request-guard.js";',
         'import { isTenantResponseCurrent } from "./tenant-request-guard.js";\nimport BusinessMonitor from "./business-monitor.jsx";'),
        ('    ["monitoring", "Monitoramento", "activity"],',
         '    ["monitoring", "Monitoramento", "activity"],\n    ["business", "Monitoramento de Negócios", "report"],'),
        ('  overview: "/visao-geral",',
         '  overview: "/visao-geral",\n  business: "/negocios/visao-geral",'),
        ('  const path = location.pathname.replace(/\\/+$/, "") || "/";',
         '  const path = location.pathname.replace(/\\/+$/, "") || "/";\n  if (path.startsWith("/negocios/")) return { view: "business" };'),
        ('    [currentUser, setCurrentUser] = useState(null),',
         '    [currentUser, setCurrentUser] = useState(null),\n    [businessAccess, setBusinessAccess] = useState({enabled:false,capabilities:[]}),'),
        ('  const viewAvailable = (candidate) => {',
         '  const viewAvailable = (candidate) => {\n    if (candidate === "business") return businessAccess.enabled && businessAccess.capabilities?.includes("business.view");'),
        ('  const activeTenantRef=useRef(tenantId);',
         '''  useEffect(() => {
    let active=true;
    setBusinessAccess({enabled:false,capabilities:[]});
    if (token && tenantId) api("/api/v1/business/me",token,{},tenantId)
      .then(data=>{if(active)setBusinessAccess(data);})
      .catch(()=>{if(active)setBusinessAccess({enabled:false,capabilities:[]});});
    return ()=>{active=false;};
  },[token,tenantId]);
  const activeTenantRef=useRef(tenantId);'''),
        ('          {view === "overview" && (',
         '''          {view === "business" && tenantId && <BusinessMonitor key={tenantId} tenantId={tenantId}
            access={businessAccess} request={(path,opts={})=>api(path,token,opts,tenantId)}
            ui={{PageHeader,MetricGroup,ListPage,FilterBar,DataGrid,FormPage,FormSection,FormActions}} />}
          {view === "overview" && ('''),
    ]))
    # Delayed commit: all anchors validated first.
    changed=0
    for p,modified in changes:
        original=p.read_text(encoding='utf-8')
        if modified!=original:
            p.write_text(modified,encoding='utf-8');changed+=1
            print(f"PATCHED: {p.relative_to(ROOT)}")
        else:print(f"UNCHANGED: {p.relative_to(ROOT)}")
    print(f"Done. Changed {changed} existing files. New addon files must already be overlaid.")

if __name__=='__main__':main()
