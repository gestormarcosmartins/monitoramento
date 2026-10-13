"""Fast package integrity + Python syntax check. Does not touch the checkout."""
from pathlib import Path
import ast
import sys
root=Path(__file__).resolve().parents[1]
required=[
 'services/backend/central_monitor/business.py',
 'services/backend/central_monitor/business_p1.py',
 'services/backend/central_monitor/business_evaluation.py',
 'services/backend/central_monitor/migrations/045_business_monitor_mvp.sql',
 'services/backend/central_monitor/migrations/046_business_workflows_p1.sql',
 'services/backend/central_monitor/migrations/047_business_hardening_v02.sql',
 'services/web/src/business-monitor.jsx',
 'services/web/src/business-p1.jsx',
 'agent/Nexora.Agent/BusinessReader.cs',
 'agent/Nexora.Agent/BusinessQueue.cs',
 'tools/apply_business_monitor.py',
 'tools/apply_business_monitor_v02.py',
]
missing=[x for x in required if not (root/x).is_file()]
if missing:raise SystemExit(f'Missing required files: {missing}')
for file in root.rglob('*.py'):
    ast.parse(file.read_text('utf-8'),str(file))
for path in ['business.py','business_p1.py']:
    data=(root/'services/backend/central_monitor'/path).read_text('utf-8')
    if 'tenant_connection' not in data or 'HTTPException(403' not in data:
        raise SystemExit(f'Authorization contract missing: {path}')
assert 'CREATE POLICY business_tenant' in (root/required[4]).read_text('utf-8')
print('Package integrity OK:',len(required),'required files; Python parsed; tenant auth/RLS anchors found')
