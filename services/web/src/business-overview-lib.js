// Funções puras da Visão Geral de Negócios (formatação pt-BR, rótulos de status, KPIs e consulta).
// Nada aqui classifica indicadores: o statusPrincipal, os limites e as contagens vêm prontos do backend.

export const STATUS_ORDER = ['CRITICO', 'ATENCAO', 'DESATUALIZADO', 'SEM_DADOS', 'SEM_REGRA', 'NORMAL'];
export const PAGE_SIZES = [10, 25, 50];

// Ícone + texto + cor: o ícone é escolhido pelo componente a partir de `icon`.
export const STATUS_META = {
  CRITICO: {label: 'Crítico', plural: 'Críticos', tone: 'critical', icon: 'critical'},
  ATENCAO: {label: 'Em atenção', plural: 'Em atenção', tone: 'warning', icon: 'warning'},
  DESATUALIZADO: {label: 'Desatualizado', plural: 'Desatualizados', tone: 'stale', icon: 'clock'},
  SEM_DADOS: {label: 'Sem dados', plural: 'Sem dados', tone: 'nodata', icon: 'nodata'},
  SEM_REGRA: {label: 'Sem regra', plural: 'Sem regra', tone: 'norule', icon: 'norule'},
  NORMAL: {label: 'Normal', plural: 'Normais', tone: 'ok', icon: 'ok'},
};
export const statusMeta = (status) => STATUS_META[status] || {label: 'Indefinido', plural: 'Indefinidos', tone: 'nodata', icon: 'nodata'};

const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;

/** Formata um decimal em texto sem passar por float (preserva a precisão monetária). */
export function formatDecimal(text, {minFrac = 0, maxFrac = 6} = {}) {
  const match = DECIMAL.exec(String(text ?? '').trim());
  if (!match) return null;
  const [, sign, intPart, fracPart = ''] = match;
  let digits = BigInt(intPart + fracPart);
  let frac = fracPart.length;
  if (frac > maxFrac) { // arredonda metade para longe do zero
    const divisor = 10n ** BigInt(frac - maxFrac);
    const remainder = digits % divisor;
    digits = digits / divisor + (remainder * 2n >= divisor ? 1n : 0n);
    frac = maxFrac;
  }
  let raw = digits.toString().padStart(frac + 1, '0');
  let whole = raw.slice(0, raw.length - frac) || '0';
  let decimals = raw.slice(raw.length - frac);
  while (decimals.length > minFrac && decimals.endsWith('0')) decimals = decimals.slice(0, -1);
  while (decimals.length < minFrac) decimals += '0';
  whole = whole.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  const isZero = !/[1-9]/.test(whole + decimals);
  return `${sign && !isZero ? '-' : ''}${whole}${decimals ? ',' + decimals : ''}`;
}

const isBrl = (unit) => ['BRL', 'R$'].includes(String(unit ?? '').trim().toUpperCase());

/** Valor de um indicador na unidade dele: BRL com moeda (R$ 49.180.486,93); demais unidades sem inventar símbolo. */
export function formatIndicatorValue(value, unit) {
  if (value === null || value === undefined || value === '') return '—';
  if (isBrl(unit)) {
    const text = formatDecimal(value, {minFrac: 2, maxFrac: 2});
    if (text === null) return '—';
    return text.startsWith('-') ? `-R$ ${text.slice(1)}` : `R$ ${text}`;
  }
  const text = formatDecimal(value, {minFrac: 0, maxFrac: 6});
  if (text === null) return '—';
  const label = String(unit ?? '').trim();
  if (!label) return text;
  return label === '%' ? `${text}%` : `${text} ${label}`;
}

export const NOT_CONFIGURED = 'Não configurado';

/** Limite de alerta/crítico: null significa "não cadastrado", nunca zero. */
export const formatLimit = (value, unit) => value === null || value === undefined || value === '' ? NOT_CONFIGURED : formatIndicatorValue(value, unit);

const dateFormat = new Intl.DateTimeFormat('pt-BR', {timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'});

/** dd/mm/aaaa hh:mm no fuso de Brasília; ausente ou inválido vira "—". */
export function formatDateTime(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  const parts = Object.fromEntries(dateFormat.formatToParts(date).map((p) => [p.type, p.value]));
  return `${parts.day}/${parts.month}/${parts.year} ${parts.hour}:${parts.minute}`;
}

/**
 * O que a coluna "Valor atual" mostra. Só uma leitura VALIDA (ou SEM_REGRA, que tem leitura válida) é "Valor atual";
 * para Sem dados e Desatualizado o número, se existir, vira "Último valor conhecido" com a data.
 */
export function describeValue(item) {
  const known = item.ultimoValorConhecido;
  if (item.valorAtual !== null && item.valorAtual !== undefined) {
    return {label: 'Valor atual', text: formatIndicatorValue(item.valorAtual, item.unidade), lastKnown: null};
  }
  const lastKnown = known !== null && known !== undefined
    ? {label: 'Último valor conhecido', text: formatIndicatorValue(known, item.unidade), at: formatDateTime(item.coletadoEm)}
    : null;
  return {label: 'Valor atual', text: '—', lastKnown};
}

/** Uma linha de texto com a origem da leitura (fonte e última coleta), para a informação secundária. */
export function sourceLine(item) {
  const parts = [];
  if (item.fonte) parts.push(`Fonte: ${item.fonte}`);
  parts.push(`Última coleta: ${formatDateTime(item.coletadoEm)}`);
  return parts.join(' · ');
}

/** Cartões KPI. Total e as 4 classes principais sempre aparecem; Desatualizados e Sem regra só quando existirem (ou estiverem filtrados). */
export function buildKpis(totais, selectedStatus = null) {
  if (!totais) return [];
  const count = (status) => totais[status] ?? 0;
  const show = (status) => count(status) > 0 || selectedStatus === status;
  const classes = ['CRITICO', 'ATENCAO', 'NORMAL', 'SEM_DADOS', 'DESATUALIZADO', 'SEM_REGRA']
    .filter((status) => ['CRITICO', 'ATENCAO', 'NORMAL', 'SEM_DADOS'].includes(status) || show(status));
  return [{key: null, label: 'Total', value: totais.total ?? 0}, ...classes.map((status) => ({key: status, label: statusMeta(status).plural, value: count(status)}))];
}

export function buildQuery({q = '', status = null, page = 1, pageSize = 10} = {}) {
  const params = new URLSearchParams();
  if (String(q).trim()) params.set('q', String(q).trim());
  if (status && STATUS_ORDER.includes(status)) params.set('status', status);
  params.set('page', String(Math.max(1, Number(page) || 1)));
  params.set('page_size', String(PAGE_SIZES.includes(Number(pageSize)) ? Number(pageSize) : 10));
  return params.toString();
}

/** Chips removíveis dos filtros ativos. */
export function activeFilters({q = '', status = null} = {}) {
  const chips = [];
  if (String(q).trim()) chips.push({key: 'q', label: `Busca: ${String(q).trim()}`});
  if (status && STATUS_ORDER.includes(status)) chips.push({key: 'status', label: `Status: ${statusMeta(status).label}`});
  return chips;
}

/** Intervalo exibido na paginação (1-based) com o total filtrado. */
export function pageRange(page, pageSize, total) {
  if (!total) return {from: 0, to: 0, total: 0};
  const from = (page - 1) * pageSize + 1;
  return {from, to: Math.min(page * pageSize, total), total};
}
