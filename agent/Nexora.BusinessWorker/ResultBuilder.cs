using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Nexora.BusinessWorker;

/// <summary>
/// Builds the tabular JSON result row by row, applying the platform contract (spec section 7): every row is one
/// object with every configured column, SQL NULL stays null (never 0 or ""), decimals travel as exact text, dates
/// are ISO 8601 with an explicit zone, and the row/byte caps are enforced while reading, not afterwards.
/// </summary>
public sealed class ResultBuilder
{
    private readonly IReadOnlyList<ColumnSpec> _schema;
    private readonly JobConstraints _limits;
    private readonly bool _assumeUtc;
    private readonly List<JsonObject> _rows = new();
    private long _bytes;

    public bool Truncated { get; private set; }

    public ResultBuilder(IReadOnlyList<ColumnSpec> schema, JobConstraints limits, bool assumeUtcForUnspecified = true)
    {
        _schema = schema;
        _limits = limits;
        _assumeUtc = assumeUtcForUnspecified;
    }

    /// <summary>The query must return exactly the published columns, in the published order (names compared ignoring case).</summary>
    public void ValidateColumns(IReadOnlyList<string> returned)
    {
        if (returned.Count != _schema.Count) throw new ResultContractException("schema_mismatch");
        for (var i = 0; i < _schema.Count; i++)
            if (!string.Equals(returned[i], _schema[i].Name, StringComparison.OrdinalIgnoreCase))
                throw new ResultContractException("schema_mismatch");
    }

    /// <summary>Adds a row. Returns false when the row cap was already reached (the result is then flagged truncated).</summary>
    public bool AddRow(IReadOnlyList<object?> values)
    {
        if (_rows.Count >= _limits.MaxRows)
        {
            Truncated = true;
            return false;
        }
        var row = new JsonObject();
        for (var i = 0; i < _schema.Count; i++)
            row[_schema[i].Name] = Convert(_schema[i].Type, values[i]);
        _bytes += System.Text.Encoding.UTF8.GetByteCount(row.ToJsonString());
        if (_bytes > _limits.MaxBytes) throw new ResultContractException("result_too_large");
        _rows.Add(row);
        return true;
    }

    public ResultBody Build(DateTimeOffset startedAt, int durationMs) => new()
    {
        StartedAt = startedAt, DurationMs = durationMs,
        Columns = _schema.Select(c => new ColumnSpec { Name = c.Name, Type = c.Type }).ToList(),
        Rows = _rows, RowCount = _rows.Count, Truncated = Truncated,
    };

    private JsonNode? Convert(string type, object? value)
    {
        if (value is null or DBNull) return null;
        try
        {
            switch (type)
            {
                case "string":
                    return JsonValue.Create(value is IFormattable f ? f.ToString(null, CultureInfo.InvariantCulture) : value.ToString());
                case "integer":
                {
                    // Numbers only, and no silent rounding: 1.5 in an integer column is a contract violation.
                    if (value is string or bool or char) throw new ResultContractException("type_mismatch");
                    var number = System.Convert.ToDecimal(value, CultureInfo.InvariantCulture);
                    if (number != decimal.Truncate(number)) throw new ResultContractException("type_mismatch");
                    return JsonValue.Create(System.Convert.ToInt64(number));
                }
                case "decimal":
                    return JsonValue.Create(System.Convert.ToDecimal(value, CultureInfo.InvariantCulture).ToString(CultureInfo.InvariantCulture));
                case "boolean":
                    return value is bool b ? JsonValue.Create(b) : throw new ResultContractException("type_mismatch");
                case "date":
                    return JsonValue.Create(value switch
                    {
                        DateOnly date => date.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                        DateTime dt => dt.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                        DateTimeOffset dto => dto.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                        _ => throw new ResultContractException("type_mismatch"),
                    });
                case "datetime":
                    return JsonValue.Create(ToZoned(value).ToString("yyyy-MM-dd'T'HH:mm:ss.ffffffzzz", CultureInfo.InvariantCulture));
                default:
                    throw new ResultContractException("type_mismatch");
            }
        }
        catch (Exception ex) when (ex is FormatException or InvalidCastException or OverflowException)
        {
            throw new ResultContractException("type_mismatch");
        }
    }

    private DateTimeOffset ToZoned(object value) => value switch
    {
        DateTimeOffset dto => dto,
        DateTime dt when dt.Kind == DateTimeKind.Utc => new DateTimeOffset(dt),
        DateTime dt when dt.Kind == DateTimeKind.Local => new DateTimeOffset(dt),
        DateTime dt => _assumeUtc ? new DateTimeOffset(DateTime.SpecifyKind(dt, DateTimeKind.Utc)) : new DateTimeOffset(DateTime.SpecifyKind(dt, DateTimeKind.Local)),
        _ => throw new ResultContractException("type_mismatch"),
    };
}
