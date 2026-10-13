using System.Globalization;
using System.Text.RegularExpressions;

namespace Nexora.Agent;

public sealed partial class SqlServerCollector
{
    // Only schema-qualified pre-approved monitoring views may be read.
    // The SQL login MUST separately have SELECT only on approved views.
    public async Task<decimal> ReadBusinessViewAsync(string schema, string view, string column, CancellationToken token)
    {
        if (!_options.SqlEnabled) throw new InvalidOperationException("SQL Server collection is disabled");
        if (!Regex.IsMatch(schema, "^[A-Za-z][A-Za-z0-9_]{0,62}$") ||
            !Regex.IsMatch(view, "^vw_nexora_[A-Za-z0-9_]{1,80}$") ||
            !Regex.IsMatch(column, "^[A-Za-z][A-Za-z0-9_]{0,62}$"))
            throw new ArgumentException("Unapproved SQL identifier for a Business collection");
        var sql = $"SELECT TOP (2) [{column}] AS business_value FROM [{schema}].[{view}]";
        var rows = await QueryAsync(sql, token);
        if (rows.Count != 1 || !rows[0].TryGetValue("business_value", out var raw) || raw is null)
            throw new InvalidOperationException("Business view must return exactly one numeric value");
        return Convert.ToDecimal(raw, CultureInfo.InvariantCulture);
    }
}
