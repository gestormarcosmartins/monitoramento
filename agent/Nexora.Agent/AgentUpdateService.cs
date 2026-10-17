using System.Diagnostics;
using System.Text.Json;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using Nexora.BusinessWorker;

namespace Nexora.Agent;

/// <summary>
/// Atualização automática do agente (Windows). Pergunta à Central, VERIFICA a assinatura e o vínculo, baixa, confere tamanho e SHA-256 e
/// apenas PREPARA o pacote. Quem troca o executável é a tarefa "NexoraAgentUpdate" (script de aplicação), com a verificação repetida,
/// período de teste e volta automática. Nada aqui substitui o binário em execução. Qualquer falha é registrada e tentada de novo na
/// próxima verificação; nunca derruba o agente.
/// </summary>
public sealed class AgentUpdateService : BackgroundService
{
    private readonly AgentOptions _options;
    private readonly CentralClient _central;
    private readonly UpdateGate _gate;
    private readonly ILogger<AgentUpdateService> _logger;

    public AgentUpdateService(IOptions<AgentOptions> options, CentralClient central, UpdateGate gate, ILogger<AgentUpdateService> logger)
    {
        _options = options.Value; _central = central; _gate = gate; _logger = logger;
    }

    private string UpdatesDirectory => Path.Combine(AgentPaths.DataDirectory(_options), "updates");
    private string ReadyDirectory => Path.Combine(UpdatesDirectory, "ready");

    protected override async Task ExecuteAsync(CancellationToken stop)
    {
        if (!OperatingSystem.IsWindows() || _options.UpdateCheckMinutes <= 0) return;
        try
        {
            await ReportOutcomeAsync(stop);
            await Task.Delay(TimeSpan.FromSeconds(90 + Random.Shared.Next(30)), stop);  // deixa os primeiros heartbeats chegarem
            await ConfirmHealthyAsync(stop);
            while (!stop.IsCancellationRequested)
            {
                try { await CheckOnceAsync(stop); }
                catch (Exception ex) when (ex is not OperationCanceledException)
                {
                    _logger.LogWarning("Verificação de atualização falhou: {Error}", ex.GetType().Name);
                    BusinessLog.Write($"Atualização do agente: verificação falhou ({ex.GetType().Name}).");
                }
                await Task.Delay(TimeSpan.FromMinutes(_options.UpdateCheckMinutes), stop);
            }
        }
        catch (OperationCanceledException) when (stop.IsCancellationRequested) { }
    }

    private async Task CheckOnceAsync(CancellationToken stop)
    {
        var credentials = _central.Credentials;
        if (credentials is null) return;
        var platform = UpdateStager.CurrentPlatform();
        if (platform.Length == 0) return;
        var offer = await _central.CheckUpdateAsync(platform, stop);
        if (offer is null) return;

        AgentUpdateManifest manifest;
        try
        {
            manifest = AgentUpdateVerifier.VerifyManifest(offer.Payload, offer.Signature, _options.UpdateSigningPublicKey, Guid.Parse(credentials.TenantId),
                Guid.Parse(credentials.AgentId), AgentVersion.Current, platform, DateTimeOffset.UtcNow, staged: false);
        }
        catch (UpdateRejectedException rejected)
        {
            _logger.LogWarning("Oferta de atualização recusada: {Code}", rejected.Code);
            BusinessLog.Write($"Atualização do agente: oferta recusada ({rejected.Code}).");
            await TryReportAsync(VersionOf(offer.Payload), "FAILED", rejected.Code, stop);
            return;
        }

        if (!AlreadyStaged(manifest) && !await StageAsync(manifest, offer, platform, stop)) return;
        TriggerApply(manifest.Version);
    }

    private async Task<bool> StageAsync(AgentUpdateManifest manifest, JobEnvelope offer, string platform, CancellationToken stop)
    {
        var incoming = Path.Combine(UpdatesDirectory, "incoming");
        Directory.CreateDirectory(incoming);
        var partial = Path.Combine(incoming, manifest.Version + ".part");
        try
        {
            await using (var file = new FileStream(partial, FileMode.Create, FileAccess.Write, FileShare.None))
                await _central.DownloadUpdateAsync(manifest.Version, platform, file, manifest.SizeBytes, stop);
            var info = new FileInfo(partial);
            if (info.Length != manifest.SizeBytes) throw new UpdateRejectedException("size_mismatch");
            using (var check = info.OpenRead())
                if (UpdateStager.Sha256Hex(check) != manifest.Sha256) throw new UpdateRejectedException("hash_mismatch");
            Directory.CreateDirectory(ReadyDirectory);
            var pendingPath = Path.Combine(ReadyDirectory, "pending.json");
            if (File.Exists(pendingPath)) File.Delete(pendingPath);  // nunca deixa um manifesto ao lado de um pacote que ele não descreve
            File.Move(partial, Path.Combine(ReadyDirectory, "package.pkg"), overwrite: true);
            File.WriteAllText(pendingPath, JsonSerializer.Serialize(new PendingAgentUpdate
            {
                Version = manifest.Version, Platform = platform, Sha256 = manifest.Sha256, Payload = offer.Payload, Signature = offer.Signature, StagedAt = DateTimeOffset.UtcNow,
            }));
        }
        catch (Exception ex) when (ex is UpdateRejectedException or IOException or HttpRequestException or UnauthorizedAccessException or TaskCanceledException)
        {
            var code = ex is UpdateRejectedException r ? r.Code : ex.GetType().Name;
            _logger.LogWarning("Atualização {Version} não foi preparada: {Code}", manifest.Version, code);
            BusinessLog.Write($"Atualização do agente {manifest.Version}: não preparada ({code}).");
            try { if (File.Exists(partial)) File.Delete(partial); } catch (IOException) { }
            await TryReportAsync(manifest.Version, "FAILED", code, stop);
            return false;
        }
        await TryReportAsync(manifest.Version, "DOWNLOADED", "", stop);
        BusinessLog.Write($"Atualização do agente {manifest.Version}: pacote verificado e preparado.");
        return true;
    }

    private bool AlreadyStaged(AgentUpdateManifest manifest)
    {
        try
        {
            var pendingPath = Path.Combine(ReadyDirectory, "pending.json");
            if (!File.Exists(pendingPath)) return false;
            var pending = JsonSerializer.Deserialize<PendingAgentUpdate>(File.ReadAllText(pendingPath));
            return pending is not null && pending.Version == manifest.Version && pending.Sha256 == manifest.Sha256;
        }
        catch (Exception ex) when (ex is JsonException or IOException) { return false; }
    }

    /// <summary>Dispara a tarefa agendada que aplica o pacote (ela para este processo, troca os arquivos e o religa).</summary>
    private void TriggerApply(string version)
    {
        try
        {
            using var process = Process.Start(new ProcessStartInfo("schtasks.exe", $"/Run /TN \"{_options.UpdateTaskName}\"")
            { UseShellExecute = false, CreateNoWindow = true, RedirectStandardError = true, RedirectStandardOutput = true });
            process?.WaitForExit(15000);
            if (process is null || process.ExitCode != 0) throw new InvalidOperationException("update_task_missing");
            _logger.LogInformation("Atualização {Version} entregue à tarefa de aplicação.", version);
            BusinessLog.Write($"Atualização do agente {version}: tarefa de aplicação acionada.");
        }
        catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            _logger.LogWarning("Não foi possível acionar a tarefa {Task}: reinstale o agente com o configurador novo.", _options.UpdateTaskName);
            BusinessLog.Write($"Atualização do agente {version}: tarefa {_options.UpdateTaskName} não encontrada. Rode o configurador novo uma vez.");
        }
    }

    private async Task ReportOutcomeAsync(CancellationToken stop)
    {
        var path = Path.Combine(UpdatesDirectory, "last-applied.json");
        try
        {
            if (!File.Exists(path)) return;
            var outcome = JsonSerializer.Deserialize<AgentApplyOutcome>(await File.ReadAllTextAsync(path, stop));
            if (outcome is not null && UpdateStager.TryParseVersion(outcome.Version, out _) && outcome.Result is "APPLIED" or "ROLLED_BACK" or "FAILED")
            {
                await _central.ReportUpdateAsync(outcome.Version, outcome.Result, outcome.Error ?? "", stop);
                File.Delete(path);
                BusinessLog.Write($"Atualização do agente {outcome.Version}: resultado {outcome.Result} {outcome.Error}".Trim() + ".");
            }
        }
        catch (Exception ex) when (ex is JsonException or IOException or UnauthorizedAccessException or HttpRequestException)
        {
            _logger.LogWarning("Não foi possível reportar o resultado da última atualização: {Error}", ex.GetType().Name);
        }
    }

    /// <summary>O marcador que o script de aplicação espera antes de dar a versão nova como boa (período de teste).</summary>
    private async Task ConfirmHealthyAsync(CancellationToken stop)
    {
        for (var i = 0; i < 24 && !_gate.Healthy; i++) await Task.Delay(TimeSpan.FromSeconds(5), stop);
        if (!_gate.Healthy) return;
        try
        {
            Directory.CreateDirectory(UpdatesDirectory);
            await File.WriteAllTextAsync(Path.Combine(UpdatesDirectory, "confirmed"), AgentVersion.Current, stop);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            _logger.LogWarning("Não foi possível gravar o marcador de saúde: {Error}", ex.GetType().Name);
        }
    }

    private static string? VersionOf(string payload)
    {
        try { return JsonSerializer.Deserialize<AgentUpdateManifest>(payload)?.Version; } catch (JsonException) { return null; }
    }

    private async Task TryReportAsync(string? version, string status, string error, CancellationToken stop)
    {
        if (version is null || !UpdateStager.TryParseVersion(version, out _)) return;
        try { await _central.ReportUpdateAsync(version, status, error, stop); }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException) { /* reportado na próxima tentativa */ }
    }
}
