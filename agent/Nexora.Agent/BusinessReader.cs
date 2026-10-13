using System.Globalization;
using System.IO.Compression;
using System.Net;
using System.Text.Json;
using System.Xml.Linq;
using Microsoft.Extensions.Options;
using Npgsql;

namespace Nexora.Agent;

/// <summary>Read-only, bounded generic collectors using HUB-local credentials and approved views/files.</summary>
public sealed class BusinessReader
{
    private readonly SqlServerCollector _sql;
    private readonly AgentOptions _options;
    private static readonly HttpClient Rest = new(new HttpClientHandler{AllowAutoRedirect=false,UseCookies=false})
       {Timeout=TimeSpan.FromSeconds(12)};
    private const int MaxDataBytes=1024*1024;

    public BusinessReader(SqlServerCollector sql, IOptions<AgentOptions> options)
    { _sql=sql; _options=options.Value; }

    public async Task<decimal> ReadAsync(JsonElement job,CancellationToken token)
    {
        var kind = Required(job,"kind");
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token);
        timeout.CancelAfter(TimeSpan.FromSeconds(15));
        return kind switch
        {
            "sql_view" => await SqlServerAsync(job,timeout.Token),
            "postgres_view" => await PostgresAsync(job,timeout.Token),
            "rest_get" => await RestAsync(job,timeout.Token),
            "csv" => await CsvAsync(job,timeout.Token),
            "xlsx" => await XlsxAsync(job,timeout.Token),
            _ => throw new InvalidDataException("Unsupported Business collector kind")
        };
    }

    private static string Required(JsonElement job,string key)
    {
        if (!job.TryGetProperty(key,out var v) || v.ValueKind!=JsonValueKind.String || string.IsNullOrWhiteSpace(v.GetString()))
            throw new InvalidDataException($"Invalid collector field: {key}");
        return v.GetString()!;
    }
    private static string SqlName(string v) =>
        System.Text.RegularExpressions.Regex.IsMatch(v,@"^[A-Za-z][A-Za-z0-9_]{0,80}$")? v : throw new InvalidDataException("Invalid SQL identifier");
    private static decimal ToDecimal(string? v) =>
        decimal.TryParse(v,NumberStyles.Float,CultureInfo.InvariantCulture,out var n) ? n :
           throw new InvalidDataException("Collected numeric value is not valid");

    private async Task<decimal> SqlServerAsync(JsonElement job,CancellationToken token)
    {
        if(_options.SqlTrustServerCertificate)throw new InvalidOperationException(
            "Business SQL collection requires verified SQL Server certificate; set SqlTrustServerCertificate=false");
        return await _sql.ReadBusinessViewAsync(Required(job,"schema"),Required(job,"view"),Required(job,"column"),token);
    }

    private static async Task<decimal> PostgresAsync(JsonElement job,CancellationToken token)
    {
        var suffix=job.TryGetProperty("collector_id",out var id)
           ? (id.GetString()??"").Replace("-","_").ToUpperInvariant() : "";
        var connectionString=Environment.GetEnvironmentVariable("NEXORA_BUSINESS_POSTGRES_DSN_"+suffix);
        if (string.IsNullOrWhiteSpace(connectionString) && suffix.Length==0)
            connectionString=Environment.GetEnvironmentVariable("NEXORA_BUSINESS_POSTGRES_DSN");
        if (string.IsNullOrWhiteSpace(connectionString)) throw new InvalidOperationException("PostgreSQL credential not provisioned in HUB");
        var schema=SqlName(Required(job,"schema"));var view=SqlName(Required(job,"view"));var col=SqlName(Required(job,"column"));
        if (!view.StartsWith("vw_nexora_",StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("Unapproved view");
        var builder=new NpgsqlConnectionStringBuilder(connectionString)
            {CommandTimeout=10,Timeout=5,Pooling=true,SslMode=SslMode.VerifyFull};
        // All DB connections must use a read-only DB role with SELECT granted only on approved views.
        await using var c=new NpgsqlConnection(builder.ConnectionString);
        await c.OpenAsync(token);
        await using var cmd=new NpgsqlCommand($"SELECT \"{col}\" FROM \"{schema}\".\"{view}\" LIMIT 2",c){CommandTimeout=10};
        await using var r=await cmd.ExecuteReaderAsync(token);
        if (!await r.ReadAsync(token)) throw new InvalidDataException("View returned no rows");
        var val=ToDecimal(Convert.ToString(r.GetValue(0),CultureInfo.InvariantCulture));
        if (await r.ReadAsync(token)) throw new InvalidDataException("View must return exactly one row");
        return val;
    }

    private static async Task<decimal> RestAsync(JsonElement job,CancellationToken token)
    {
        var url=Required(job,"url");
        if(!Uri.TryCreate(url,UriKind.Absolute,out var uri) || uri.Scheme!=Uri.UriSchemeHttps || uri.UserInfo.Length>0)
            throw new InvalidDataException("REST URL must be HTTPS");
        if(IPAddress.TryParse(uri.Host,out _) || uri.IsLoopback || uri.Host.EndsWith(".local",StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("REST IP or local host is not allowed");
        var allowHosts=(Environment.GetEnvironmentVariable("NEXORA_BUSINESS_REST_ALLOWED_HOSTS")??"")
            .Split(',',StringSplitOptions.TrimEntries|StringSplitOptions.RemoveEmptyEntries);
        if (!allowHosts.Contains(uri.Host,StringComparer.OrdinalIgnoreCase))
            throw new InvalidDataException("REST host not in HUB-approved allowlist");
        // Production networks should additionally enforce egress DNS/IP allowlists at firewall level.
        using var request = new HttpRequestMessage(HttpMethod.Get,uri);
        var suffix=job.TryGetProperty("collector_id",out var id)
           ? (id.GetString()??"").Replace("-","_").ToUpperInvariant() : "";
        var tokenRef=Environment.GetEnvironmentVariable("NEXORA_BUSINESS_REST_TOKEN_"+suffix);
        if (!string.IsNullOrEmpty(tokenRef))request.Headers.Authorization=new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer",tokenRef);
        using var response=await Rest.SendAsync(request,HttpCompletionOption.ResponseHeadersRead,token);
        if((int)response.StatusCode>=300 && (int)response.StatusCode<400) throw new InvalidDataException("REST redirects denied");
        response.EnsureSuccessStatusCode();
        if(response.Content.Headers.ContentLength > MaxDataBytes)throw new InvalidDataException("REST response too large");
        await using var source=await response.Content.ReadAsStreamAsync(token);
        using var data=new MemoryStream();
        var buffer=new byte[8192];int read;
        while((read=await source.ReadAsync(buffer.AsMemory(),token))>0)
        {if(data.Length+read>MaxDataBytes)throw new InvalidDataException("REST response too large");await data.WriteAsync(buffer.AsMemory(0,read),token);}
        data.Position=0;
        using var json=await JsonDocument.ParseAsync(data,cancellationToken:token);
        var path=Required(job,"json_property").Split('.');var node=json.RootElement;
        foreach(var segment in path)
        {if(node.ValueKind!=JsonValueKind.Object || !node.TryGetProperty(segment,out node))throw new InvalidDataException("JSON numeric property missing");}
        if(node.ValueKind is not (JsonValueKind.Number or JsonValueKind.String))throw new InvalidDataException("JSON value must be numeric");
        return ToDecimal(node.ToString());
    }

    private static string FilePath(JsonElement job)
    {
        var root=Environment.GetEnvironmentVariable("NEXORA_BUSINESS_DATA_DIRECTORY");
        if(string.IsNullOrWhiteSpace(root))throw new InvalidOperationException("Approved data directory not configured");
        var relative=Required(job,"path");
        if(Path.IsPathRooted(relative) || relative.Contains(':'))throw new InvalidDataException("Absolute paths not allowed");
        var full=Path.GetFullPath(Path.Combine(root,relative));
        var baseDir=Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar)+Path.DirectorySeparatorChar;
        if(!full.StartsWith(baseDir,OperatingSystem.IsWindows()?StringComparison.OrdinalIgnoreCase:StringComparison.Ordinal))
            throw new InvalidDataException("Path outside approved collection directory");
        var info=new FileInfo(full);if(!info.Exists || info.Length>MaxDataBytes)throw new InvalidDataException("Source file missing or too large");
        // Harden further by forbidding symlinks and mounting read-only approved data directory.
        if(info.LinkTarget is not null)throw new InvalidDataException("Symlink source forbidden");
        return full;
    }

    private static async Task<decimal> CsvAsync(JsonElement job,CancellationToken token)
    {
        var file=FilePath(job);var key=Required(job,"column");
        using var reader=new StreamReader(file);
        var header=(await reader.ReadLineAsync(token))?.Split(',') ?? throw new InvalidDataException("CSV has no header");
        var idx=Array.FindIndex(header,x=>x.Trim(' ','\"')==key);
        if(idx<0)throw new InvalidDataException("CSV column missing");
        var row=(await reader.ReadLineAsync(token))?.Split(',') ?? throw new InvalidDataException("CSV has no data");
        if(row.Length<=idx)throw new InvalidDataException("CSV data incomplete");
        if(await reader.ReadLineAsync(token) is not null)throw new InvalidDataException("CSV must have one data row");
        return ToDecimal(row[idx].Trim(' ','\"'));
    }

    private static async Task<decimal> XlsxAsync(JsonElement job,CancellationToken token)
    {
        var file=FilePath(job);var sheet=Required(job,"sheet");var cell=Required(job,"cell");
        using var zip=ZipFile.OpenRead(file);
        XNamespace ns="http://schemas.openxmlformats.org/spreadsheetml/2006/main";
        XNamespace r="http://schemas.openxmlformats.org/officeDocument/2006/relationships";
        var workbook=ReadXml(zip,"xl/workbook.xml");
        var selected=workbook.Descendants(ns+"sheet").FirstOrDefault(x=>(string?)x.Attribute("name")==sheet)
          ?? throw new InvalidDataException("Sheet not found");
        var relId=(string?)selected.Attribute(r+"id")??throw new InvalidDataException("Sheet relationship missing");
        var rels=ReadXml(zip,"xl/_rels/workbook.xml.rels");
        var rel=rels.Descendants().FirstOrDefault(x=>(string?)x.Attribute("Id")==relId)
          ?? throw new InvalidDataException("Worksheet relationship missing");
        var target=(string?)rel.Attribute("Target") ?? throw new InvalidDataException("Worksheet target missing");
        var entry=target.StartsWith("/xl/",StringComparison.Ordinal)?target.TrimStart('/'):
                  "xl/"+target.TrimStart('/');
        if(entry.Contains(".."))throw new InvalidDataException("Invalid worksheet path");
        var sheetXml=ReadXml(zip,entry);
        var c=sheetXml.Descendants(ns+"c").FirstOrDefault(x=>(string?)x.Attribute("r")==cell)
          ?? throw new InvalidDataException("Cell missing");
        string? value=c.Element(ns+"v")?.Value;
        if((string?)c.Attribute("t")=="s")
        {
            var shared=ReadXml(zip,"xl/sharedStrings.xml");
            var strings=shared.Descendants(ns+"si").Select(x=>string.Concat(x.Descendants(ns+"t").Select(t=>t.Value))).ToList();
            if(!int.TryParse(value,out var index)||index<0||index>=strings.Count)throw new InvalidDataException("Invalid shared string index");
            value=strings[index];
        }
        await Task.CompletedTask;
        return ToDecimal(value);
    }
    private static XDocument ReadXml(ZipArchive z,string path)
    {
        var entry=z.GetEntry(path)??throw new InvalidDataException("Workbook component missing");
        if(entry.Length>MaxDataBytes)throw new InvalidDataException("Spreadsheet component too large");
        using var stream=entry.Open();
        using var reader=System.Xml.XmlReader.Create(stream,new System.Xml.XmlReaderSettings{DtdProcessing=System.Xml.DtdProcessing.Prohibit,XmlResolver=null});
        return XDocument.Load(reader);
    }
}
