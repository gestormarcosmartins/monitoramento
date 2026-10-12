CREATE TABLE IF NOT EXISTS cm_workspaces (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    authz_version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO cm_workspaces(code,name)
VALUES ('default','Nexora')
ON CONFLICT (code) DO NOTHING;

ALTER TABLE cm_tenants ADD COLUMN IF NOT EXISTS workspace_id uuid;
ALTER TABLE cm_tenants ADD COLUMN IF NOT EXISTS company_kind text NOT NULL DEFAULT 'customer';
ALTER TABLE cm_tenants ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

UPDATE cm_tenants
SET workspace_id=(SELECT id FROM cm_workspaces WHERE code='default')
WHERE workspace_id IS NULL;

ALTER TABLE cm_tenants ALTER COLUMN workspace_id SET NOT NULL;

DO $$ BEGIN
    ALTER TABLE cm_tenants ADD CONSTRAINT cm_tenants_workspace_fk
        FOREIGN KEY (workspace_id) REFERENCES cm_workspaces(id) ON DELETE RESTRICT;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE cm_tenants ADD CONSTRAINT cm_tenants_company_kind_check
        CHECK (company_kind IN ('operator','customer','both'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_cm_tenants_workspace_id ON cm_tenants(workspace_id,id);

CREATE TABLE IF NOT EXISTS cm_service_engagements (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES cm_workspaces(id) ON DELETE RESTRICT,
    tenant_id uuid NOT NULL,
    operator_company_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('internal','managed_service')),
    name text NOT NULL,
    starts_at timestamptz NOT NULL DEFAULT now(),
    ends_at timestamptz,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (workspace_id,tenant_id) REFERENCES cm_tenants(workspace_id,id) ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id,operator_company_id) REFERENCES cm_tenants(workspace_id,id) ON DELETE RESTRICT,
    CHECK (ends_at IS NULL OR ends_at > starts_at),
    UNIQUE (workspace_id,id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_cm_service_engagements_identity
    ON cm_service_engagements(workspace_id,tenant_id,operator_company_id,kind)
    WHERE active;

INSERT INTO cm_service_engagements(workspace_id,tenant_id,operator_company_id,kind,name)
SELECT workspace_id,id,id,'internal','Atendimento interno'
FROM cm_tenants
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS cm_access_grants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES cm_workspaces(id) ON DELETE CASCADE,
    tenant_id uuid NOT NULL,
    user_id uuid NOT NULL REFERENCES cm_users(id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('admin','manager','operator','viewer','client','auditor')),
    row_scope text NOT NULL DEFAULT 'company' CHECK (row_scope IN ('own','department','team_service','company')),
    engagement_id uuid,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_until timestamptz,
    granted_by uuid REFERENCES cm_users(id) ON DELETE SET NULL,
    reason text NOT NULL DEFAULT 'Migrado do vínculo existente',
    active boolean NOT NULL DEFAULT true,
    authz_version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (workspace_id,tenant_id) REFERENCES cm_tenants(workspace_id,id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id,engagement_id) REFERENCES cm_service_engagements(workspace_id,id) ON DELETE CASCADE,
    CHECK (valid_until IS NULL OR valid_until > valid_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_cm_access_grants_active
    ON cm_access_grants(workspace_id,tenant_id,user_id,role,row_scope,COALESCE(engagement_id,'00000000-0000-0000-0000-000000000000'::uuid))
    WHERE active;

INSERT INTO cm_access_grants(workspace_id,tenant_id,user_id,role,row_scope,reason)
SELECT t.workspace_id,ut.tenant_id,ut.user_id,ut.role,'company','Migrado de cm_user_tenants'
FROM cm_user_tenants ut
JOIN cm_tenants t ON t.id=ut.tenant_id
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS cm_permission_catalog (
    code text PRIMARY KEY,
    description text NOT NULL,
    object_scope text NOT NULL CHECK (object_scope IN ('workspace','company','object')),
    sensitive boolean NOT NULL DEFAULT false
);

INSERT INTO cm_permission_catalog(code,description,object_scope,sensitive) VALUES
('company.read','Consultar empresa','company',false),
('company.manage','Administrar empresa e vínculos','company',true),
('ticket.read','Consultar chamado autorizado','object',false),
('ticket.create','Criar chamado','company',false),
('ticket.update','Editar chamado autorizado','object',false),
('ticket.assign','Atribuir chamado','object',false),
('ticket.resolve','Solucionar chamado','object',false),
('note.read_private','Consultar nota privada','object',true),
('note.write_private','Registrar nota privada','object',true),
('contract.read','Consultar contrato','object',true),
('billing.approve','Aprovar pré-faturamento','object',true),
('automation.execute','Executar automação','object',true),
('grant.manage','Gerenciar concessões','company',true),
('audit.read','Consultar auditoria','company',true)
ON CONFLICT (code) DO NOTHING;

CREATE TABLE IF NOT EXISTS cm_role_permissions (
    role text NOT NULL,
    permission_code text NOT NULL REFERENCES cm_permission_catalog(code) ON DELETE CASCADE,
    PRIMARY KEY(role,permission_code)
);

INSERT INTO cm_role_permissions(role,permission_code)
SELECT role,permission_code FROM (VALUES
('admin','company.read'),('admin','company.manage'),('admin','ticket.read'),('admin','ticket.create'),('admin','ticket.update'),('admin','ticket.assign'),('admin','ticket.resolve'),('admin','note.read_private'),('admin','note.write_private'),('admin','contract.read'),('admin','billing.approve'),('admin','automation.execute'),('admin','grant.manage'),('admin','audit.read'),
('manager','company.read'),('manager','ticket.read'),('manager','ticket.create'),('manager','ticket.update'),('manager','ticket.assign'),('manager','ticket.resolve'),('manager','note.read_private'),('manager','note.write_private'),('manager','contract.read'),('manager','audit.read'),
('operator','company.read'),('operator','ticket.read'),('operator','ticket.create'),('operator','ticket.update'),('operator','ticket.assign'),('operator','ticket.resolve'),('operator','note.read_private'),('operator','note.write_private'),
('viewer','company.read'),('viewer','ticket.read'),
('client','company.read'),('client','ticket.read'),('client','ticket.create'),
('auditor','company.read'),('auditor','ticket.read'),('auditor','contract.read'),('auditor','audit.read')
) AS permissions(role,permission_code)
ON CONFLICT DO NOTHING;

ALTER TABLE cm_service_engagements ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_service_engagements FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_access_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_access_grants FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON cm_service_engagements;
CREATE POLICY tenant_isolation ON cm_service_engagements
    USING (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

DROP POLICY IF EXISTS tenant_isolation ON cm_access_grants;
CREATE POLICY tenant_isolation ON cm_access_grants
    USING (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

CREATE INDEX IF NOT EXISTS ix_cm_engagements_tenant_active ON cm_service_engagements(tenant_id,active);
CREATE INDEX IF NOT EXISTS ix_cm_access_grants_user_active ON cm_access_grants(user_id,tenant_id,active,valid_until);
