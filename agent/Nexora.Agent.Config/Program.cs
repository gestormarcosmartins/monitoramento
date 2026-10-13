using System.Diagnostics;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.Data.SqlClient;

namespace Nexora.Agent.Config;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();
        Application.Run(new ConfigurationForm());
    }
}

internal sealed class ConfigurationForm : Form
{
    private const string DefaultServer = "https://nexora.simmservices.com.br";
    private const string ServiceName = "NexoraAgent";
    private static readonly string InstallDirectory = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Nexora Agent");
    private static readonly string SettingsPath = Path.Combine(InstallDirectory, "agentsettings.json");

    private readonly TextBox _server = new() { Text = DefaultServer };
    private readonly TextBox _token = new() { UseSystemPasswordChar = true };
    private readonly TextBox _name = new() { Text = $"Morada - {Environment.MachineName}" };
    private readonly TextBox _snapshot = new() { Text = @"C:\ProgramData\Nexora\Monitor\archive" };
    private readonly NumericUpDown _sendSeconds = new() { Minimum = 10, Maximum = 86400, Value = 30 };
    private readonly NumericUpDown _servicesSeconds = new() { Minimum = 10, Maximum = 86400, Value = 60 };
    private readonly NumericUpDown _hostSeconds = new() { Minimum = 60, Maximum = 86400, Value = 300 };
    private readonly NumericUpDown _sqlHealthSeconds = new() { Minimum = 60, Maximum = 86400, Value = 600 };
    private readonly NumericUpDown _sqlPerformanceSeconds = new() { Minimum = 900, Maximum = 604800, Value = 21600 };
    private readonly TextBox _services = new() { Text = "MSSQLSERVER, SQLSERVERAGENT" };
    private readonly CheckBox _sqlEnabled = new() { Text = "Habilitar monitoramento SQL Server", Checked = true, AutoSize = true };
    // Left blank on purpose: an unfilled field is how "this machine has no SQL
    // Server" is expressed (see EffectiveSqlEnabled), so it must not start
    // pre-filled with a value the user never actually typed.
    private readonly TextBox _sqlServer = new();
    private readonly TextBox _sqlDatabase = new() { Text = "master" };
    private readonly CheckBox _integrated = new() { Text = "Usar autenticação integrada do Windows", AutoSize = true };
    private readonly TextBox _sqlUser = new();
    private readonly TextBox _sqlPassword = new() { UseSystemPasswordChar = true };
    private readonly CheckBox _sqlEncrypt = new() { Text = "Criptografar conexão", Checked = true, AutoSize = true };
    private readonly CheckBox _trustCertificate = new() { Text = "Confiar no certificado do servidor", Checked = true, AutoSize = true };
    private readonly Button _testSql = new() { Text = "Testar conexão SQL", AutoSize = true };
    // Shared ingest credentials for the Benner diagnostic push (see Nexora.Agent/Benner/): a
    // single token/tenant used by every agent that enables this feature, not a per-agent
    // enrollment credential. Deliberately left blank here (never hardcoded in source - this
    // app ships to client machines and its binary/source are not a safe place for a live
    // secret) - whoever installs the agent pastes the current shared token in, same as the
    // SQL password field below.
    private readonly CheckBox _bennerEnabled = new() { Text = "Habilitar diagnóstico avançado Benner (19 checks de saúde do SQL Server)", AutoSize = true };
    private readonly NumericUpDown _bennerInterval = new() { Minimum = 300, Maximum = 86400, Value = 1800 };
    private readonly TextBox _bennerIngestToken = new() { UseSystemPasswordChar = true };
    private readonly TextBox _bennerTenantId = new();
    private readonly Label _status = new() { AutoSize = true, ForeColor = Color.FromArgb(55, 71, 89) };
    private readonly Button _install = new() { Text = "Salvar e instalar", AutoSize = true };
    private readonly Button _test = new() { Text = "Testar conexão", AutoSize = true };
    // EnrollmentToken is write-only in the UI (masked, never redisplayed), so when the
    // field is left blank on a later save — e.g. reopening just to tweak the interval —
    // we must keep whatever token is already on disk instead of overwriting it with "".
    private string _existingToken = "";
    // Same masked-field preservation pattern as _existingToken: the ingest token box is
    // never pre-filled with the real secret, so leaving it blank on save must keep whatever
    // is already on disk instead of wiping it out.
    private string _existingBennerIngestToken = "";

    public ConfigurationForm()
    {
        Text = "Nexora Agent — Configuração Morada";
        ClientSize = new Size(760, 560);
        MinimumSize = new Size(640, 460);
        StartPosition = FormStartPosition.CenterScreen;
        Font = new Font("Segoe UI", 10F);

        var root = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 2, ColumnCount = 1 };
        root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 78));
        Controls.Add(root);
        var tabs = new TabControl { Dock = DockStyle.Fill };
        var parameters = new TabPage("Parâmetros") { Padding = new Padding(22), AutoScroll = true };
        var database = new TabPage("Banco de Dados") { Padding = new Padding(22), AutoScroll = true };
        tabs.TabPages.Add(parameters);
        tabs.TabPages.Add(database);
        root.Controls.Add(tabs, 0, 0);

        var grid = new TableLayoutPanel { Dock = DockStyle.Top, AutoSize = true, ColumnCount = 2, Padding = new Padding(0) };
        grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 210));
        grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        parameters.Controls.Add(grid);

        Add(grid, "Servidor Nexora", _server, "Endereço HTTPS de produção. O domínio aponta para a Oracle 163.176.67.170.");
        Add(grid, "Token de ativação", _token, "Token de uso único gerado no Nexora para cadastrar este agente.");
        Add(grid, "Nome do agente", _name, "Nome que aparecerá no cadastro de agentes.");
        Add(grid, "Pasta de diagnósticos", _snapshot, "Pasta onde o monitor local grava os arquivos JSON.");
        Add(grid, "Intervalo de envio (segundos)", _sendSeconds, "Define de quanto em quanto tempo o agente coleta e envia os dados. Mínimo: 10 segundos.");
        Add(grid, "Serviços essenciais (segundos)", _servicesSeconds, "Intervalo independente para verificar os serviços Windows.");
        Add(grid, "Métricas do servidor (segundos)", _hostSeconds, "CPU, memória, discos e rede usam agenda própria.");
        Add(grid, "Serviços Windows", _services, "Nomes dos serviços separados por vírgula.");

        var sqlGrid = new TableLayoutPanel { Dock = DockStyle.Top, AutoSize = true, ColumnCount = 2 };
        sqlGrid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 210));
        sqlGrid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        database.Controls.Add(sqlGrid);
        Add(sqlGrid, "Monitor SQL", _sqlEnabled, "O agente conecta diretamente ao SQL Server e envia diagnósticos ao Nexora.");
        Add(sqlGrid, "Servidor / instância", _sqlServer, "Ex.: 172.19.19.219, 172.19.19.219\\INSTANCIA ou servidor,porta. Deixe em branco se esta máquina não tiver SQL Server.");
        Add(sqlGrid, "Banco inicial", _sqlDatabase, "Use master para saúde da instância; os bancos online serão descobertos automaticamente.");
        Add(sqlGrid, "Autenticação", _integrated, "Desmarcado: autenticação SQL Server com usuário e senha.");
        Add(sqlGrid, "Usuário SQL", _sqlUser, "Conta dedicada de leitura para o monitoramento.");
        Add(sqlGrid, "Senha SQL", _sqlPassword, "A senha é cifrada pelo Windows e nunca é enviada à Central.");
        Add(sqlGrid, "Segurança TLS", _sqlEncrypt, "Mantém o tráfego SQL criptografado.");
        Add(sqlGrid, "Certificado interno", _trustCertificate, "Use somente quando o SQL Server possuir certificado interno confiável pela empresa.");
        Add(sqlGrid, "Saúde SQL (segundos)", _sqlHealthSeconds, "Padrão: 600 segundos. Consultas operacionais leves.");
        Add(sqlGrid, "Desempenho SQL (segundos)", _sqlPerformanceSeconds, "Padrão: 21600 segundos (6 horas). Nunca executado a cada heartbeat.");
        Add(sqlGrid, "Validação", _testSql, "Valida autenticação, servidor e banco antes da instalação.");
        _testSql.Click += async (_, _) => await TestSqlAsync();
        Add(sqlGrid, "Diagnóstico Benner", _bennerEnabled, "Porta os 19 checks de saúde do SQL Server (bloqueios, backups, I/O, tempdb, etc.) para o painel \"Diagnóstico\" do Nexora. Exige Monitor SQL habilitado.");
        Add(sqlGrid, "Intervalo Benner (segundos)", _bennerInterval, "Padrão: 1800 segundos (30 minutos), igual ao coletor original.");
        Add(sqlGrid, "Token de ingestão Benner", _bennerIngestToken, "Token compartilhado (não é o token de cadastro do agente). Deixe em branco para manter o valor já salvo nesta máquina.");
        Add(sqlGrid, "Tenant Benner", _bennerTenantId, "GUID do tenant Nexora que recebe os diagnósticos.");

        var footer = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, Padding = new Padding(18, 12, 18, 10), BackColor = Color.WhiteSmoke };
        footer.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        footer.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        var actions = new FlowLayoutPanel { AutoSize = true, FlowDirection = FlowDirection.LeftToRight, WrapContents = false, Anchor = AnchorStyles.Right };
        _install.MinimumSize = new Size(140, 38);
        _test.MinimumSize = new Size(125, 38);
        _test.Click += async (_, _) => await TestConnectionAsync();
        _install.Click += (_, _) => Install();
        actions.Controls.Add(_install);
        actions.Controls.Add(_test);
        _status.Anchor = AnchorStyles.Left;
        footer.Controls.Add(_status, 0, 0);
        footer.Controls.Add(actions, 1, 0);
        root.Controls.Add(footer, 0, 1);
        LoadExisting();
    }

    private static void Add(TableLayoutPanel grid, string label, Control input, string help)
    {
        input.Dock = DockStyle.Top;
        input.Margin = new Padding(0, 5, 0, 2);
        var title = new Label { Text = label, AutoSize = true, Anchor = AnchorStyles.Left, Font = new Font("Segoe UI Semibold", 10F) };
        var panel = new TableLayoutPanel { Dock = DockStyle.Top, AutoSize = true, RowCount = 2, ColumnCount = 1 };
        panel.Controls.Add(input, 0, 0);
        panel.Controls.Add(new Label { Text = help, AutoSize = true, ForeColor = Color.DimGray, MaximumSize = new Size(470, 0) }, 0, 1);
        var row = grid.RowCount++;
        grid.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        grid.Controls.Add(title, 0, row);
        grid.Controls.Add(panel, 1, row);
    }

    private void LoadExisting()
    {
        if (!File.Exists(SettingsPath)) return;
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(SettingsPath));
            var agent = document.RootElement.GetProperty("Agent");
            _existingToken = String(agent, "EnrollmentToken", "");
            _server.Text = String(agent, "ServerUrl", DefaultServer);
            _name.Text = String(agent, "AgentName", _name.Text);
            _snapshot.Text = String(agent, "SnapshotDirectory", _snapshot.Text);
            _sendSeconds.Value = Number(agent, "PollSeconds", 30, _sendSeconds.Minimum, _sendSeconds.Maximum);
            _servicesSeconds.Value = Number(agent, "ServicesSeconds", 60, _servicesSeconds.Minimum, _servicesSeconds.Maximum);
            _hostSeconds.Value = Number(agent, "HostMetricsSeconds", 300, _hostSeconds.Minimum, _hostSeconds.Maximum);
            _sqlHealthSeconds.Value = Number(agent, "SqlHealthSeconds", 600, _sqlHealthSeconds.Minimum, _sqlHealthSeconds.Maximum);
            _sqlPerformanceSeconds.Value = Number(agent, "SqlPerformanceSeconds", 21600, _sqlPerformanceSeconds.Minimum, _sqlPerformanceSeconds.Maximum);
            _sqlServer.Text = String(agent, "SqlServer", _sqlServer.Text); _sqlDatabase.Text = String(agent, "SqlDatabase", _sqlDatabase.Text); _sqlUser.Text = String(agent, "SqlUser", "");
            _sqlEnabled.Checked = Boolean(agent,"SqlEnabled",true); _integrated.Checked=Boolean(agent,"SqlIntegratedSecurity",false); _sqlEncrypt.Checked=Boolean(agent,"SqlEncrypt",true); _trustCertificate.Checked=Boolean(agent,"SqlTrustServerCertificate",true);
            if(agent.TryGetProperty("EssentialServices",out var serviceList)&&serviceList.ValueKind==JsonValueKind.Array)_services.Text=string.Join(", ",serviceList.EnumerateArray().Select(x=>x.GetString()).Where(x=>!string.IsNullOrWhiteSpace(x)));
            _bennerEnabled.Checked = Boolean(agent, "BennerEnabled", false);
            _bennerInterval.Value = Number(agent, "BennerIntervalSeconds", 1800, _bennerInterval.Minimum, _bennerInterval.Maximum);
            _existingBennerIngestToken = String(agent, "BennerIngestToken", "");
            _bennerTenantId.Text = String(agent, "BennerTenantId", "");
            _status.Text = "Configuração instalada carregada.";
        }
        catch (Exception error) { _status.Text = $"Não foi possível ler a configuração atual: {error.Message}"; }
    }

    private static string String(JsonElement parent, string property, string fallback) =>
        parent.TryGetProperty(property, out var value) ? value.GetString() ?? fallback : fallback;
    private static decimal Number(JsonElement parent, string property, decimal fallback, decimal min, decimal max) =>
        parent.TryGetProperty(property, out var value) && value.TryGetDecimal(out var number) ? Math.Clamp(number, min, max) : fallback;
    private static bool Boolean(JsonElement parent,string property,bool fallback)=>parent.TryGetProperty(property,out var value)&&value.ValueKind is JsonValueKind.True or JsonValueKind.False?value.GetBoolean():fallback;

    // A machine with no SQL Server simply leaves the field blank — that must not be
    // treated as a connection failure, only an explicit server value opts into SQL
    // monitoring. The "Monitor SQL" checkbox alone isn't enough since it stays
    // checked by default.
    private bool EffectiveSqlEnabled => _sqlEnabled.Checked && !string.IsNullOrWhiteSpace(_sqlServer.Text);

    private SqlConnectionStringBuilder SqlConnectionString()
    {
        if(string.IsNullOrWhiteSpace(_sqlServer.Text))throw new InvalidOperationException("Informe o servidor ou a instância SQL Server.");
        var builder=new SqlConnectionStringBuilder { DataSource=_sqlServer.Text.Trim(),InitialCatalog=string.IsNullOrWhiteSpace(_sqlDatabase.Text)?"master":_sqlDatabase.Text.Trim(),IntegratedSecurity=_integrated.Checked,Encrypt=_sqlEncrypt.Checked,TrustServerCertificate=_trustCertificate.Checked,ConnectTimeout=12,ApplicationName="Nexora Agent Config" };
        if(!_integrated.Checked){if(string.IsNullOrWhiteSpace(_sqlUser.Text))throw new InvalidOperationException("Informe o usuário SQL Server.");builder.UserID=_sqlUser.Text.Trim();builder.Password=string.IsNullOrEmpty(_sqlPassword.Text)?LoadSqlPassword():_sqlPassword.Text;if(string.IsNullOrEmpty(builder.Password))throw new InvalidOperationException("Informe a senha SQL Server.");}
        return builder;
    }

    private async Task TestSqlAsync()
    {
        if (string.IsNullOrWhiteSpace(_sqlServer.Text))
        {
            _status.Text = "Nenhum servidor SQL informado: este agente não vai monitorar banco de dados nesta máquina.";
            return;
        }
        try{_testSql.Enabled=false;_status.Text="Testando SQL Server...";await using var connection=new SqlConnection(SqlConnectionString().ConnectionString);await connection.OpenAsync();await using var command=new SqlCommand("SELECT @@SERVERNAME, DB_NAME()",connection){CommandTimeout=15};await command.ExecuteScalarAsync();_status.Text="Conexão com SQL Server aprovada.";}
        catch(Exception error){_status.Text=$"Falha SQL: {error.Message}";MessageBox.Show(this,error.Message,"Conexão SQL reprovada",MessageBoxButtons.OK,MessageBoxIcon.Error);}
        finally{_testSql.Enabled=true;}
    }

    private static void SaveSqlPassword(string password)
    {
        if(string.IsNullOrEmpty(password))return;
        var path=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),"Nexora","Agent","sql-password.dat");Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var clear=Encoding.UTF8.GetBytes(password);var encrypted=ProtectedData.Protect(clear,null,DataProtectionScope.LocalMachine);File.WriteAllBytes(path,encrypted);CryptographicOperations.ZeroMemory(clear);
    }
    private static string LoadSqlPassword()
    {
        var path=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),"Nexora","Agent","sql-password.dat");if(!File.Exists(path))return "";
        return Encoding.UTF8.GetString(ProtectedData.Unprotect(File.ReadAllBytes(path),null,DataProtectionScope.LocalMachine));
    }

    private Uri ValidServer()
    {
        if (!Uri.TryCreate(_server.Text.Trim().TrimEnd('/'), UriKind.Absolute, out var uri) || uri.Scheme is not ("http" or "https"))
            throw new InvalidOperationException("Informe um endereço HTTP ou HTTPS válido para o servidor Nexora.");
        return uri;
    }

    private async Task TestConnectionAsync()
    {
        try
        {
            _test.Enabled = false;
            _status.Text = "Testando conexão...";
            using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(12) };
            var server = ValidServer();
            if(EffectiveSqlEnabled)SqlConnectionString();
            // The configuration screen validates network/TLS reachability only.
            // Readiness also checks optional server dependencies and may return 503
            // even though this agent can communicate with the Nexora API.
            using var response = await client.GetAsync(new Uri(server.ToString().TrimEnd('/') + "/api/v1/health/live"));
            _status.Text = response.IsSuccessStatusCode
                ? $"Conexão aprovada ({(int)response.StatusCode})."
                : $"Servidor respondeu HTTP {(int)response.StatusCode}. Verifique o endereço e a liberação da porta.";
        }
        catch (HttpRequestException error) when (error.Message.Contains("SSL", StringComparison.OrdinalIgnoreCase))
        {
            _status.Text = "Falha SSL: o certificado deve ser válido para o domínio configurado. Não use HTTPS diretamente pelo IP.";
        }
        catch (Exception error) { _status.Text = $"Falha de conexão: {error.Message}"; }
        finally { _test.Enabled = true; }
    }

    private void Install()
    {
        try
        {
            var server = ValidServer();
            var sourceAgent = Path.Combine(AppContext.BaseDirectory, "Nexora.Agent.exe");
            if (!File.Exists(sourceAgent)) throw new FileNotFoundException("Nexora.Agent.exe não foi encontrado ao lado do configurador.");
            var credentialsPath = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Nexora", "Agent", "credentials.dat");
            var enrollmentToken = string.IsNullOrWhiteSpace(_token.Text) ? _existingToken : _token.Text.Trim();
            if (!File.Exists(credentialsPath) && string.IsNullOrWhiteSpace(enrollmentToken))
                throw new InvalidOperationException("Informe o token de ativação no primeiro cadastro deste computador.");
            Directory.CreateDirectory(InstallDirectory);
            StopAndDeleteService();
            File.Copy(sourceAgent, Path.Combine(InstallDirectory, "Nexora.Agent.exe"), true);
            // Microsoft.Data.SqlClient needs its native SNI library sitting next to the
            // exe at runtime; PublishSingleFile does not embed it. Without this file the
            // service enrolls and sends heartbeats fine but every SQL query throws
            // "The type initializer for 'Microsoft.Data.SqlClient.TdsParser' threw an
            // exception" — easy to miss since nothing at install time catches it.
            const string sniLibrary = "Microsoft.Data.SqlClient.SNI.dll";
            var sourceSni = Path.Combine(AppContext.BaseDirectory, sniLibrary);
            if (File.Exists(sourceSni)) File.Copy(sourceSni, Path.Combine(InstallDirectory, sniLibrary), true);
            var payload = new
            {
                Agent = new
                {
                    ServerUrl = server.ToString().TrimEnd('/'), TenantId = "", EnrollmentToken = enrollmentToken,
                    AgentName = _name.Text.Trim(), SnapshotDirectory = _snapshot.Text.Trim(),
                    DataDirectory = @"C:\ProgramData\Nexora\Agent", PollSeconds = (int)_sendSeconds.Value,
                    HeartbeatSeconds = (int)_sendSeconds.Value, ServicesSeconds=(int)_servicesSeconds.Value, HostMetricsSeconds=(int)_hostSeconds.Value,
                    SqlHealthSeconds=(int)_sqlHealthSeconds.Value,SqlPerformanceSeconds=(int)_sqlPerformanceSeconds.Value,
                    EssentialServices=_services.Text.Split(',',StringSplitOptions.TrimEntries|StringSplitOptions.RemoveEmptyEntries),
                    SqlEnabled=EffectiveSqlEnabled,SqlServer=_sqlServer.Text.Trim(),SqlDatabase=_sqlDatabase.Text.Trim(),SqlIntegratedSecurity=_integrated.Checked,
                    SqlUser=_sqlUser.Text.Trim(),SqlEncrypt=_sqlEncrypt.Checked,SqlTrustServerCertificate=_trustCertificate.Checked,SqlCommandTimeoutSeconds=30,
                    // Benner only ever makes sense alongside SQL monitoring - a checked box with
                    // no SQL Server configured would just fail every check, so it rides on the
                    // same effective-enabled gate rather than its own independent condition.
                    BennerEnabled=EffectiveSqlEnabled&&_bennerEnabled.Checked,BennerIntervalSeconds=(int)_bennerInterval.Value,
                    BennerIngestToken=string.IsNullOrWhiteSpace(_bennerIngestToken.Text)?_existingBennerIngestToken:_bennerIngestToken.Text.Trim(),
                    BennerTenantId=_bennerTenantId.Text.Trim()
                }
            };
            File.WriteAllText(SettingsPath, JsonSerializer.Serialize(payload, new JsonSerializerOptions { WriteIndented = true }));
            if(!_integrated.Checked)SaveSqlPassword(_sqlPassword.Text);
            InstallScheduledTask(Path.Combine(InstallDirectory, "Nexora.Agent.exe"));
            _token.Clear();
            _status.Text = "Agente instalado e iniciado. A comunicação ocorrerá no intervalo configurado.";
            MessageBox.Show(this, "Agente Nexora instalado e iniciado com sucesso.", "Nexora Agent", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
        catch (Exception error)
        {
            _status.Text = $"Falha na instalação: {error.Message}";
            MessageBox.Show(this, error.Message, "Não foi possível instalar", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private static void StopAndDeleteService()
    {
        // Clean up a legacy sc.exe-based install (pre-dates the switch to a Scheduled
        // Task) so it can't keep running stale settings alongside the new one.
        RunSc($"stop {ServiceName}", false);
        RunSc($"delete {ServiceName}", false);
        Thread.Sleep(600);
    }

    private static void RunSc(string arguments, bool required = true)
    {
        using var process = Process.Start(new ProcessStartInfo("sc.exe", arguments) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardError = true, RedirectStandardOutput = true });
        if (process is null) throw new InvalidOperationException("Não foi possível executar o gerenciador de serviços do Windows.");
        process.WaitForExit(15000);
        if (required && process.ExitCode != 0) throw new InvalidOperationException(process.StandardError.ReadToEnd() + process.StandardOutput.ReadToEnd());
    }

    // Running Nexora.Agent.exe as an actual Windows Service (sc.exe/SCM) reproducibly
    // broke Microsoft.Data.SqlClient's native SNI library on at least one production
    // server — identical binary, identical SYSTEM account, but "The type initializer
    // for 'Microsoft.Data.SqlClient.TdsParser' threw an exception" every single time,
    // while the exact same exe ran perfectly as SYSTEM via a Scheduled Task. A Scheduled
    // Task that starts at boot, runs as SYSTEM and restarts itself if it ever exits gives
    // the same "always on" behavior as a service without whatever SCM-specific isolation
    // was causing that failure.
    private static void InstallScheduledTask(string exePath)
    {
        var script = $"""
            $ErrorActionPreference = 'Stop'
            Unregister-ScheduledTask -TaskName 'NexoraAgent' -Confirm:$false -ErrorAction SilentlyContinue
            $action = New-ScheduledTaskAction -Execute '{exePath}'
            $trigger = New-ScheduledTaskTrigger -AtStartup
            $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
            $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
            Register-ScheduledTask -TaskName 'NexoraAgent' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Coleta diagnosticos locais e os envia com seguranca para a Central Nexora.' -Force | Out-Null
            Start-ScheduledTask -TaskName 'NexoraAgent'
            """;
        var scriptPath = Path.Combine(Path.GetTempPath(), $"nexora-install-task-{Guid.NewGuid():N}.ps1");
        File.WriteAllText(scriptPath, script);
        try
        {
            using var process = Process.Start(new ProcessStartInfo(
                "powershell.exe", $"-NoProfile -ExecutionPolicy Bypass -File \"{scriptPath}\"")
            { UseShellExecute = false, CreateNoWindow = true, RedirectStandardError = true, RedirectStandardOutput = true });
            if (process is null) throw new InvalidOperationException("Não foi possível executar o PowerShell para criar a tarefa agendada.");
            process.WaitForExit(30000);
            if (process.ExitCode != 0) throw new InvalidOperationException(process.StandardError.ReadToEnd() + process.StandardOutput.ReadToEnd());
        }
        finally
        {
            File.Delete(scriptPath);
        }
    }
}
