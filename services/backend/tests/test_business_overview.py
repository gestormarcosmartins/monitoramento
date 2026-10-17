"""Visão Geral de Negócios: classificação, contagens, ordenação e contrato. Sem banco.

Run: python -m unittest tests.test_business_overview
"""
import sys
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from fastapi import HTTPException
    from central_monitor import business, business_overview as ov
    IMPORT_ERROR = None
except ImportError as exc:
    IMPORT_ERROR = exc

NOW = datetime(2026, 10, 17, 18, 0, tzinfo=UTC)
D = Decimal


def indicator(code="ind", name="Indicador", direction="above", warning=D("10"), critical=D("20"),
              stale=1800, active=True, **extra):
    return {"id": f"id-{code}", "code": code, "name": name, "description": "", "unit": "BRL", "direction": direction,
            "warning_value": warning, "critical_value": critical, "stale_after_seconds": stale, "active": active,
            "version": 3, "owner_user_id": "u1", "unit_id": None, "department_id": None, "process_id": None, **extra}


def state(value, collected=NOW - timedelta(minutes=1), st="sem_dados"):
    return {"state": st, "last_value": value, "last_collected_at": collected, "evaluated_at": NOW,
            "state_changed_at": NOW}


def status_of(ind, value, **kw):
    cur = state(value, **kw) if value is not None or kw else None
    return ov.classify(ind, None, cur, NOW)["statusPrincipal"]


@unittest.skipIf(IMPORT_ERROR is not None, "Backend dependencies not installed")
class ClassificationTests(unittest.TestCase):
    def test_maior_piora_fronteiras(self):
        ind = indicator()
        for value, expected in [("9.99", "NORMAL"), ("10", "ATENCAO"), ("19.99", "ATENCAO"), ("20", "CRITICO"),
                                ("20.01", "CRITICO"), ("0", "NORMAL"), ("-5", "NORMAL")]:
            with self.subTest(value=value):
                self.assertEqual(status_of(ind, D(value)), expected)

    def test_menor_piora_fronteiras(self):
        ind = indicator(direction="below", warning=D("20"), critical=D("10"))
        for value, expected in [("20.01", "NORMAL"), ("20", "ATENCAO"), ("10.01", "ATENCAO"), ("10", "CRITICO"),
                                ("9.99", "CRITICO"), ("0", "CRITICO")]:
            with self.subTest(value=value):
                self.assertEqual(status_of(ind, D(value)), expected)

    def test_exemplo_vencido_90_com_limites_de_teste(self):
        # AC-18: 49.180.486,93 com 10 mil / 20 mil em MAIOR_PIORA é crítico (dados de teste, nunca gravados).
        r = ov.build_item(indicator(code="vencido_90", warning=D("10000"), critical=D("20000")), None, state(D("49180486.930000")), None, NOW, ["VISUALIZAR"])
        self.assertEqual((r["statusPrincipal"], r["qualidadeColeta"], r["valorAtual"]), ("CRITICO", "VALIDA", "49180486.93"))
        self.assertEqual((r["limiteAlerta"], r["limiteCritico"], r["direcao"]), ("10000.00", "20000.00", "MAIOR_PIORA"))

    def test_limites_ausentes_nunca_normais_nem_zero(self):
        ind = indicator(warning=None, critical=None)
        r = ov.build_item(ind, None, state(D("999")), None, NOW, [])
        self.assertEqual(r["statusPrincipal"], "SEM_REGRA")
        self.assertEqual(r["qualidadeColeta"], "SEM_REGRA")
        self.assertIsNone(r["limiteAlerta"])
        self.assertIsNone(r["limiteCritico"])
        self.assertFalse(r["configurada"])
        self.assertEqual(r["valorAtual"], "999.00")  # valor válido continua visível (spec 6)

    def test_regra_incompleta_ou_inconsistente_vira_sem_regra(self):
        for kw in [dict(warning=D("10"), critical=None), dict(warning=None, critical=D("10")),
                   dict(warning=D("20"), critical=D("10")),                       # above com crítico menor
                   dict(direction="below", warning=D("10"), critical=D("20")),    # below com crítico maior
                   dict(warning=D("10"), critical=D("10")), dict(direction="sideways")]:
            with self.subTest(kw=kw):
                self.assertEqual(status_of(indicator(**kw), D("15")), "SEM_REGRA")

    def test_zero_e_limite_valido(self):
        ind = indicator(direction="below", warning=D("0"), critical=D("-10"))
        self.assertEqual(status_of(ind, D("1")), "NORMAL")
        self.assertEqual(status_of(ind, D("0")), "ATENCAO")
        self.assertEqual(ov.build_item(ind, None, state(D("1")), None, NOW, [])["limiteAlerta"], "0.00")

    def test_sla_do_proprio_indicador(self):
        ind = indicator(stale=3600)
        self.assertEqual(status_of(ind, D("1"), collected=NOW - timedelta(seconds=3600)), "NORMAL")      # no limite do SLA
        self.assertEqual(status_of(ind, D("1"), collected=NOW - timedelta(seconds=3601)), "DESATUALIZADO")
        self.assertEqual(status_of(indicator(stale=86400), D("1"), collected=NOW - timedelta(hours=23)), "NORMAL")

    def test_desatualizado_nao_mostra_valor_atual_e_guarda_ultimo_conhecido(self):
        old = NOW - timedelta(days=3)
        r = ov.build_item(indicator(), None, state(D("950.50"), collected=old), None, NOW, [])
        self.assertEqual(r["statusPrincipal"], "DESATUALIZADO")
        self.assertIsNone(r["valorAtual"])
        self.assertEqual(r["ultimoValorConhecido"], "950.50")
        self.assertEqual(r["coletadoEm"], old.isoformat())
        self.assertIn("fora do prazo", r["motivoQualidade"])

    def test_sem_dados_nunca_houve_valor(self):
        for cur in (None, state(None, collected=None)):
            r = ov.build_item(indicator(), None, cur, None, NOW, [])
            self.assertEqual(r["statusPrincipal"], "SEM_DADOS")
            self.assertIsNone(r["valorAtual"])
            self.assertIsNone(r["ultimoValorConhecido"])
            self.assertTrue(r["motivoQualidade"])

    def test_serie_historica_preenche_ultimo_valor_apos_revisao_de_regra(self):
        # editar a regra zera cm_business_states; a série em cm_business_values ainda é o último valor conhecido.
        past = NOW - timedelta(hours=1)
        r = ov.build_item(indicator(), None, state(None, collected=None), {"value": D("7.5"), "collected_at": past,
                          "source_name": "Contas a receber"}, NOW, [])
        self.assertEqual((r["statusPrincipal"], r["valorAtual"], r["ultimoValorConhecido"], r["fonte"]),
                         ("SEM_DADOS", None, "7.50", "Contas a receber"))

    def test_coleta_futura_nao_e_saudavel(self):
        self.assertEqual(status_of(indicator(), D("1"), collected=NOW + timedelta(minutes=6)), "SEM_DADOS")
        self.assertEqual(status_of(indicator(), D("1"), collected=NOW + timedelta(minutes=4)), "NORMAL")

    def test_politica_ativa_substitui_cadastro_e_inativa_nao(self):
        policy = {"id": "p", "active": True, "version": 7, "warning_value": D("100"), "critical_value": D("200"),
                  "direction": "above", "hysteresis": D("0"), "cooldown_seconds": 0, "timezone": "America/Sao_Paulo",
                  "weekdays": [1, 2, 3, 4, 5, 6, 7], "check_from": None, "check_until": None, "stale_after_seconds": 60}
        r = ov.build_item(indicator(), policy, state(D("150")), None, NOW, [])
        self.assertEqual((r["statusPrincipal"], r["limiteAlerta"], r["versaoRegra"], r["origemRegra"]),
                         ("ATENCAO", "100.00", 7, "POLITICA"))
        self.assertEqual(r["validadeColetaSegundos"], 60)
        r = ov.build_item(indicator(), {**policy, "active": False}, state(D("150")), None, NOW, [])
        self.assertEqual((r["statusPrincipal"], r["origemRegra"]), ("CRITICO", "INDICADOR"))

    def test_decimal_em_texto_sem_float(self):
        self.assertEqual(ov.decimal_text(D("49180486.930000")), "49180486.93")
        self.assertEqual(ov.decimal_text(D("950.500000")), "950.50")
        self.assertEqual(ov.decimal_text(D("20000")), "20000.00")
        self.assertEqual(ov.decimal_text(D("0.123456")), "0.123456")
        self.assertEqual(ov.decimal_text(D("1E+3")), "1000.00")
        self.assertIsNone(ov.decimal_text(None))


@unittest.skipIf(IMPORT_ERROR is not None, "Backend dependencies not installed")
class OverviewContractTests(unittest.TestCase):
    def items(self):
        old = NOW - timedelta(days=5)
        specs = [("c1", "Beta crítico", D("30"), NOW - timedelta(minutes=5)), ("c2", "Alfa crítico", D("30"), NOW - timedelta(minutes=5)),
                 ("c3", "Zeta crítico recente", D("30"), NOW - timedelta(minutes=1)),
                 ("a1", "Atenção", D("15"), NOW), ("n1", "Normal", D("1"), NOW), ("n2", "Ação normal", D("2"), NOW - timedelta(minutes=2)),
                 ("d1", "Desatualizado", D("1"), old), ("s1", "Sem dados", None, None)]
        out = [ov.build_item(indicator(code=c, name=n), None, state(v, collected=t) if v is not None else None, None, NOW, [])
               for c, n, v, t in specs]
        out.append(ov.build_item(indicator(code="r1", name="Sem regra", warning=None, critical=None), None, state(D("5")), None, NOW, []))
        return out

    def test_totais_somam_o_total_e_ignoram_busca_filtro_e_pagina(self):
        items = self.items()
        base = ov.build_overview(items)
        self.assertEqual(base["totais"], {"total": 9, "CRITICO": 3, "ATENCAO": 1, "DESATUALIZADO": 1, "SEM_DADOS": 1,
                                          "SEM_REGRA": 1, "NORMAL": 2})
        self.assertEqual(sum(base["totais"][s] for s in ov.STATUS_ORDER), base["totais"]["total"])
        for kw in [dict(q="alfa"), dict(status="NORMAL"), dict(page=2, page_size=10), dict(q="zzz", status="CRITICO")]:
            with self.subTest(kw=kw):
                self.assertEqual(ov.build_overview(items, **kw)["totais"], base["totais"])

    def test_ordenacao_padrao_e_desempate(self):
        codes = [i["codigo"] for i in ov.build_overview(self.items(), page_size=50)["itens"]]
        # classe -> coleta mais recente -> nome; c1/c2 empatam em coleta e decidem por nome (Alfa antes de Beta)
        self.assertEqual(codes, ["c3", "c2", "c1", "a1", "d1", "s1", "r1", "n1", "n2"])
        again = [i["codigo"] for i in ov.build_overview(list(reversed(self.items())), page_size=50)["itens"]]
        self.assertEqual(codes, again)  # determinístico independente da ordem de entrada

    def test_empate_total_decide_por_id(self):
        a = ov.build_item(indicator(code="x", name="Igual"), None, state(D("1")), None, NOW, [])
        b = {**a, "id": "id-a-menor"}
        self.assertEqual([i["id"] for i in ov.build_overview([a, b])["itens"]], ["id-a-menor", "id-x"])

    def test_filtro_busca_por_nome_codigo_sem_acento(self):
        items = self.items()
        self.assertEqual([i["codigo"] for i in ov.build_overview(items, status="CRITICO")["itens"]], ["c3", "c2", "c1"])
        self.assertEqual([i["codigo"] for i in ov.build_overview(items, q="acao")["itens"]], ["n2"])   # "Ação" sem acento
        self.assertEqual([i["codigo"] for i in ov.build_overview(items, q="  ATENÇÃO ")["itens"]], ["a1"])
        self.assertEqual([i["codigo"] for i in ov.build_overview(items, q="s1")["itens"]], ["s1"])
        empty = ov.build_overview(items, q="inexistente")
        self.assertEqual((empty["totalFiltrado"], empty["itens"], empty["totalPaginas"]), (0, [], 1))

    def test_paginacao_no_servidor(self):
        items = [ov.build_item(indicator(code=f"k{n:02d}", name=f"Item {n:02d}"), None, state(D("1")), None, NOW, []) for n in range(23)]
        p1 = ov.build_overview(items, page=1, page_size=10)
        p3 = ov.build_overview(items, page=3, page_size=10)
        self.assertEqual((len(p1["itens"]), p1["totalFiltrado"], p1["totalPaginas"]), (10, 23, 3))
        self.assertEqual(len(p3["itens"]), 3)
        self.assertEqual(ov.build_overview(items, page=99, page_size=10)["pagina"], 3)  # página fora do intervalo é limitada
        self.assertEqual(len(ov.build_overview(items, page_size=25)["itens"]), 23)
        ids = [i["id"] for p in (1, 2, 3) for i in ov.build_overview(items, page=p, page_size=10)["itens"]]
        self.assertEqual(len(set(ids)), 23)  # sem duplicidade nem perda entre páginas

    def test_campos_do_contrato(self):
        item = self.items()[0]
        for key in ("id", "codigo", "nome", "descricao", "unidade", "direcao", "limiteAlerta", "limiteCritico", "valorAtual",
                    "ultimoValorConhecido", "coletadoEm", "qualidadeColeta", "motivoQualidade", "severidade",
                    "statusPrincipal", "versaoRegra", "configurada", "fonte", "acoesPermitidas"):
            self.assertIn(key, item)
        self.assertIn(item["statusPrincipal"], ov.STATUS_ORDER)
        self.assertIsInstance(item["valorAtual"], str)


class FakeResult:
    def __init__(self, rows): self.rows = rows
    def fetchall(self): return self.rows
    def fetchone(self): return self.rows[0] if self.rows else None


class FakeDb:
    """Devolve só as linhas do tenant informado no primeiro parâmetro, como o RLS faria."""
    def __init__(self, rows): self.rows, self.calls = rows, []
    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return FakeResult([r for r in self.rows if str(r["tenant_id"]) == str(params[0])])


def db_row(tenant, code, value, collected=NOW - timedelta(minutes=1), **kw):
    base = indicator(code=code, name=code.upper(), **kw)
    base.update(tenant_id=tenant, s_state="sem_dados", s_last_value=value, s_last_collected_at=collected,
                s_evaluated_at=NOW, s_state_changed_at=NOW, p_id=None, v_value=value, v_collected_at=collected, v_source_name=None)
    return base


@unittest.skipIf(IMPORT_ERROR is not None, "Backend dependencies not installed")
class EndpointTests(unittest.TestCase):
    def call(self, rows, tenant="T1", perms=None, scope="company", **params):
        db = FakeDb(rows)

        @contextmanager
        def conn(tenant_id):
            yield db

        args = dict(q="", status=None, page=1, page_size=10, tenant_id=tenant, identity=object())
        args.update(params)
        with mock.patch.object(ov, "_require", return_value=({"id": "u1"}, scope)), \
             mock.patch.object(ov, "tenant_connection", conn), \
             mock.patch.object(ov, "datetime", wraps=datetime) as dt, \
             mock.patch.object(ov, "_allowed_indicator_ids", lambda db_, t, a, s: {r["id"] for r in rows if r["tenant_id"] == t}), \
             mock.patch.object(ov, "_permissions", lambda *a: perms or {}):
            dt.now.return_value = NOW
            return ov.business_overview(**args), db

    def test_isolamento_por_empresa(self):
        rows = [db_row("T1", "a", D("1")), db_row("T1", "b", D("30")), db_row("T2", "c", D("30"))]
        out, db = self.call(rows, tenant="T1")
        self.assertEqual({i["codigo"] for i in out["itens"]}, {"a", "b"})
        self.assertEqual(out["totais"]["total"], 2)
        self.assertEqual(out["empresaId"], "T1")
        self.assertEqual(out["ultimaAvaliacaoEm"], NOW.isoformat())
        self.assertEqual(out["consultadoEm"], NOW.isoformat())
        self.assertTrue(all(c[1][0] == "T1" for c in db.calls))
        out2, _ = self.call(rows, tenant="T2")
        self.assertEqual([i["codigo"] for i in out2["itens"]], ["c"])

    def test_inativos_ficam_fora_dos_totais_e_sao_informados(self):
        rows = [db_row("T1", "a", D("1")), db_row("T1", "z", D("1"), active=False)]
        out, _ = self.call(rows)
        self.assertEqual((out["totais"]["total"], out["indicadoresSuspensos"]), (1, 1))

    def test_acoes_vem_do_servidor(self):
        rows = [db_row("T1", "a", D("1"))]
        self.assertEqual(self.call(rows)[0]["itens"][0]["acoesPermitidas"], ["VISUALIZAR"])
        self.assertEqual(self.call(rows, perms={"business.monitor.update": "company"})[0]["itens"][0]["acoesPermitidas"],
                         ["VISUALIZAR", "EDITAR"])
        self.assertEqual(self.call(rows, perms={"business.monitor.update": "own"})[0]["itens"][0]["acoesPermitidas"],
                         ["VISUALIZAR", "EDITAR"])  # dono do indicador é u1
        other = [db_row("T1", "a", D("1"), owner_user_id="u2")]
        self.assertEqual(self.call(other, perms={"business.monitor.update": "own"})[0]["itens"][0]["acoesPermitidas"], ["VISUALIZAR"])

    def test_sem_business_view_nao_recebe_nada(self):
        @contextmanager
        def conn(tenant_id):
            yield mock.Mock(execute=lambda *a, **k: FakeResult([]))

        with mock.patch.object(business, "_actor", return_value={"id": "u1"}), mock.patch.object(business, "tenant_connection", conn), \
             mock.patch.object(business, "_enabled", return_value=True):
            with self.assertRaises(HTTPException) as ctx:
                ov.business_overview(q="", status=None, page=1, page_size=10, tenant_id="T1", identity=object())
        self.assertEqual(ctx.exception.status_code, 403)

    def test_rota_registrada_com_validacao_de_parametros(self):
        paths = {r.path: r for r in ov.router.routes}
        self.assertIn("/api/v1/business/overview", paths)
        names = {p.name for p in paths["/api/v1/business/overview"].dependant.query_params}
        self.assertEqual(names, {"q", "status", "page", "page_size"})


if __name__ == "__main__":
    unittest.main()
