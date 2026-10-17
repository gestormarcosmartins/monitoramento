using System.Text.Json.Nodes;
using Nexora.Agent;
using Xunit;

namespace Nexora.BusinessWorker.Tests;

/// <summary>Reconfigurar o agente não pode perder o que já estava gravado.</summary>
public class AgentSettingsMergeTests
{
    private static BusinessSource Source(string reference = "jri_hub") =>
        new() { Ref = reference, Server = "srvjribd\\SQLEXPRESS", Database = "JRI_HUB", User = "nexora_ro", TrustServerCertificate = true };

    private static JsonObject Agent(string json) => JsonNode.Parse(json)!["Agent"]!.AsObject();

    [Fact]
    public void Unknown_keys_and_the_tenant_id_survive_a_reconfiguration()
    {
        var existing = """{"Agent":{"TenantId":"b83a2863","ChaveouConnectionString":"x","FutureSetting":42},"Logging":{"LogLevel":{"Default":"Warning"}}}""";
        var json = AgentSettingsMerge.Merge(existing, new { ServerUrl = "https://n", TenantId = "", PollSeconds = 30 }, Source(), "");
        var agent = Agent(json);
        Assert.Equal("b83a2863", agent["TenantId"]!.ToString());
        Assert.Equal("x", agent["ChaveouConnectionString"]!.ToString());
        Assert.Equal(42, (int)agent["FutureSetting"]!);
        Assert.Equal("https://n", agent["ServerUrl"]!.ToString());
        Assert.Equal("Warning", JsonNode.Parse(json)!["Logging"]!["LogLevel"]!["Default"]!.ToString());
    }

    [Fact]
    public void Renaming_the_logical_name_replaces_the_old_source_instead_of_duplicating_it()
    {
        var existing = """{"Agent":{"BusinessEnabled":true,"BusinessSources":[{"Ref":"jri","Server":"old","Database":"d","User":"u"},{"Ref":"outra","Server":"s2","Database":"d2","User":"u2"}]}}""";
        var agent = Agent(AgentSettingsMerge.Merge(existing, new { }, Source("jri_hub"), "jri"));
        var refs = agent["BusinessSources"]!.AsArray().Select(n => n!["Ref"]!.ToString()).ToList();
        Assert.Equal(new[] { "jri_hub", "outra" }, refs);
        Assert.True((bool)agent["BusinessEnabled"]!);
    }

    [Fact]
    public void Disabling_business_keeps_the_configured_sources_for_later()
    {
        var existing = """{"Agent":{"BusinessEnabled":true,"BusinessSources":[{"Ref":"jri_hub","Server":"s","Database":"d","User":"u"}]}}""";
        var agent = Agent(AgentSettingsMerge.Merge(existing, new { }, null, "jri_hub"));
        Assert.False((bool)agent["BusinessEnabled"]!);
        Assert.Single(agent["BusinessSources"]!.AsArray());
    }

    [Fact]
    public void A_first_install_or_a_broken_file_starts_clean_without_throwing()
    {
        Assert.Equal("jri_hub", Agent(AgentSettingsMerge.Merge(null, new { PollSeconds = 30 }, Source(), ""))["BusinessSources"]![0]!["Ref"]!.ToString());
        Assert.Equal(30, (int)Agent(AgentSettingsMerge.Merge("{ not json", new { PollSeconds = 30 }, null, ""))["PollSeconds"]!);
    }

    [Fact]
    public void The_password_is_typed_or_saved_or_carried_over_from_the_previous_name()
    {
        Assert.Equal("novo", BusinessPasswordChoice.Effective("novo", "salva", "anterior"));
        Assert.Equal("salva", BusinessPasswordChoice.Effective("", "salva", "anterior"));
        Assert.Equal("anterior", BusinessPasswordChoice.Effective("", "", "anterior"));
        Assert.Equal("", BusinessPasswordChoice.Effective("", "", ""));
    }
}
