using System.Text.Json;
using Nexora.BusinessWorker;
using Xunit;

namespace Nexora.BusinessWorker.Tests;

public class ResultBuilderTests
{
    private static List<ColumnSpec> Schema(params (string Name, string Type)[] columns) =>
        columns.Select(c => new ColumnSpec { Name = c.Name, Type = c.Type }).ToList();

    private static readonly List<ColumnSpec> Five = Schema(("documento", "string"), ("cliente", "string"), ("cpf", "string"),
        ("celular", "string"), ("valor_vencido", "decimal"));

    private static JobConstraints Limits(int rows = 500, int bytes = 1_000_000) => new() { MaxRows = rows, MaxBytes = bytes, TimeoutSeconds = 15 };

    [Fact]
    public void Five_columns_are_five_properties_of_one_object_and_nulls_are_preserved()
    {
        var builder = new ResultBuilder(Five, Limits());
        builder.ValidateColumns(new[] { "DOCUMENTO", "cliente", "cpf", "celular", "valor_vencido" });
        builder.AddRow(new object?[] { "CR-001", "Exemplo", DBNull.Value, null, 1200.50m });
        var result = builder.Build(DateTimeOffset.UtcNow, 12);
        var row = result.Rows.Single();
        Assert.Equal(new[] { "documento", "cliente", "cpf", "celular", "valor_vencido" }, row.Select(p => p.Key).ToArray());
        Assert.Null(row["cpf"]);
        Assert.Null(row["celular"]);
        Assert.Equal("1200.50", row["valor_vencido"]!.GetValue<string>());   // exact decimal, no float
        Assert.Equal(1, result.RowCount);
        Assert.False(result.Truncated);
    }

    [Fact]
    public void An_empty_result_is_a_valid_result_with_the_columns_present()
    {
        var result = new ResultBuilder(Five, Limits()).Build(DateTimeOffset.UtcNow, 3);
        Assert.Empty(result.Rows);
        Assert.Equal(0, result.RowCount);
        Assert.Equal(5, result.Columns.Count);
        var json = JsonSerializer.Serialize(result);
        Assert.Contains("\"rows\":[]", json);
        Assert.Contains("\"truncated\":false", json);
    }

    [Fact]
    public void The_row_cap_is_enforced_while_reading_and_flags_truncation_only_when_more_rows_exist()
    {
        var exact = new ResultBuilder(Schema(("n", "integer")), Limits(rows: 3));
        for (var i = 0; i < 3; i++) Assert.True(exact.AddRow(new object?[] { i }));
        Assert.False(exact.Truncated);                       // exactly at the cap: nothing was cut

        var more = new ResultBuilder(Schema(("n", "integer")), Limits(rows: 3));
        for (var i = 0; i < 3; i++) more.AddRow(new object?[] { i });
        Assert.False(more.AddRow(new object?[] { 99 }));     // a 4th row proves truncation
        var result = more.Build(DateTimeOffset.UtcNow, 1);
        Assert.True(result.Truncated);
        Assert.Equal(3, result.RowCount);
    }

    [Fact]
    public void The_byte_cap_stops_oversized_results()
    {
        var builder = new ResultBuilder(Schema(("t", "string")), Limits(bytes: 100));
        builder.AddRow(new object?[] { new string('a', 40) });
        var ex = Assert.Throws<ResultContractException>(() => builder.AddRow(new object?[] { new string('b', 200) }));
        Assert.Equal("result_too_large", ex.Code);
    }

    [Theory]
    [InlineData("documento")]
    [InlineData("documento;cliente;cpf;celular;outra")]
    [InlineData("cliente;documento;cpf;celular;valor_vencido")]
    public void A_query_must_return_exactly_the_published_columns_in_order(string returned)
    {
        Assert.Equal("schema_mismatch", Assert.Throws<ResultContractException>(() => new ResultBuilder(Five, Limits()).ValidateColumns(returned.Split(';'))).Code);
    }

    [Fact]
    public void Types_are_converted_to_the_contract_representation()
    {
        var schema = Schema(("s", "string"), ("i", "integer"), ("d", "decimal"), ("b", "boolean"), ("dt", "date"), ("ts", "datetime"));
        var builder = new ResultBuilder(schema, Limits());
        builder.AddRow(new object?[] { 42, 7L, 3.10m, true, new DateTime(2026, 10, 14, 15, 30, 0), new DateTime(2026, 10, 14, 10, 0, 0, DateTimeKind.Unspecified) });
        var row = builder.Build(DateTimeOffset.UtcNow, 1).Rows.Single();
        Assert.Equal("42", row["s"]!.GetValue<string>());
        Assert.Equal(7, row["i"]!.GetValue<long>());
        Assert.Equal("3.10", row["d"]!.GetValue<string>());
        Assert.True(row["b"]!.GetValue<bool>());
        Assert.Equal("2026-10-14", row["dt"]!.GetValue<string>());
        Assert.Equal("2026-10-14T10:00:00.000000+00:00", row["ts"]!.GetValue<string>());  // zone is mandatory; unspecified is read as UTC
    }

    [Fact]
    public void A_datetime_with_an_offset_keeps_it()
    {
        var builder = new ResultBuilder(Schema(("ts", "datetime")), Limits());
        builder.AddRow(new object?[] { new DateTimeOffset(2026, 10, 14, 10, 0, 0, TimeSpan.FromHours(-3)) });
        Assert.Equal("2026-10-14T10:00:00.000000-03:00", builder.Build(DateTimeOffset.UtcNow, 1).Rows.Single()["ts"]!.GetValue<string>());
    }

    [Theory]
    [InlineData("integer", 1.5)]
    [InlineData("boolean", 1)]
    [InlineData("date", "not a date")]
    [InlineData("datetime", 5)]
    public void Values_that_do_not_fit_the_declared_type_fail_the_run(string type, object value)
    {
        var builder = new ResultBuilder(Schema(("x", type)), Limits());
        Assert.Equal("type_mismatch", Assert.Throws<ResultContractException>(() => builder.AddRow(new object?[] { value })).Code);
    }
}
