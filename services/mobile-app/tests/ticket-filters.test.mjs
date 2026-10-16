import test from 'node:test';
import assert from 'node:assert/strict';
import { NO_FILTER, isFiltered, ticketFilterParams, withFilters, filterSummary } from '../src/ticket-filters.ts';

test('sem filtro não altera a URL', () => {
  assert.equal(isFiltered(NO_FILTER), false);
  assert.equal(withFilters('/api/v1/tickets?page=1', NO_FILTER), '/api/v1/tickets?page=1');
});
test('status e criticidade viram parâmetros do servidor', () => {
  assert.equal(ticketFilterParams({ status: 'in_progress', priority: 'critical' }), 'status=in_progress&priority=critical');
  assert.equal(ticketFilterParams({ status: 'open', priority: '' }), 'actionable=true');
  assert.equal(withFilters('/api/v1/tickets/consolidated?page_size=50', { status: '', priority: 'high' }), '/api/v1/tickets/consolidated?page_size=50&priority=high');
  assert.equal(withFilters('/x', { status: 'new', priority: '' }), '/x?status=new');
});
test('valores desconhecidos são ignorados (sem injeção na URL)', () => {
  assert.equal(ticketFilterParams({ status: 'x&admin=1', priority: '../y' }), '');
});
test('resumo legível', () => {
  assert.equal(filterSummary({ status: 'resolved', priority: 'low' }), 'Resolvido · Criticidade Baixa');
  assert.equal(filterSummary(NO_FILTER), '');
});
