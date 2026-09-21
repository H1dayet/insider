const state = { data: null, filter: 'all', search: '', sort: 'score', investment: 10000 };

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const fmtPct = value => value == null ? '—' : `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%`;
const fmtMoney = value => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);
const fmtCompact = value => value == null ? '—' : new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
const cls = value => value == null ? '' : value >= 0 ? 'positive' : 'negative';
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]);

function switchView(name, updateHash = true) {
  $$('.view').forEach(view => view.classList.toggle('active', view.id === `view-${name}`));
  $$('.nav-button').forEach(button => button.classList.toggle('active', button.dataset.view === name));
  if (updateHash) history.replaceState(null, '', name === 'screener' ? '#' : `#${name}`);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function statusClass(row) {
  if (row.status === 'Qualified') return 'good';
  if (row.status.includes('market filter')) return 'warn';
  return 'bad';
}

function renderOverview() {
  const { meta, market, screen } = state.data;
  const qualified = screen.filter(row => row.qualified).length;
  $('#strategy-version').textContent = meta.strategy_version;
  $('#timestamp').textContent = `Latest completed session: ${meta.latest_completed_session}`;
  $('#data-pill').innerHTML = `<span></span> Data through ${escapeHtml(meta.latest_completed_session)}`;
  $('#footer-provider').textContent = `${meta.provider}. Retrieved ${new Date(meta.generated_at).toLocaleString()}.`;
  $('#market-state').textContent = market.active ? 'Risk-on' : 'Filter inactive';
  $('#market-detail').textContent = `SPY ${fmtMoney(market.close)} · SMA200 ${fmtMoney(market.sma200)}`;
  $('#market-card').classList.toggle('inactive', !market.active);
  $('#overview-stats').innerHTML = [
    [meta.universe_count, 'Eligible listings'],
    [meta.available_count, 'Evaluated successfully'],
    [qualified, 'Qualified today'],
    [market.active ? 'ON' : 'OFF', 'Market filter'],
  ].map(([value, label]) => `<div class="stat"><strong>${value}</strong><span>${label}</span></div>`).join('');
}

function filteredRows() {
  let rows = state.data.screen.filter(row => {
    const matchesText = `${row.ticker} ${row.name}`.toLowerCase().includes(state.search);
    const matchesFilter = state.filter === 'all' || (state.filter === 'qualified' ? row.qualified : !row.qualified);
    return matchesText && matchesFilter;
  });
  const sorters = {
    score: (a, b) => (b.score ?? -1) - (a.score ?? -1),
    momentum: (a, b) => (b.m6 ?? -99) - (a.m6 ?? -99),
    liquidity: (a, b) => (b.adv20 ?? -1) - (a.adv20 ?? -1),
    ticker: (a, b) => a.ticker.localeCompare(b.ticker),
  };
  return rows.sort(sorters[state.sort]);
}

function renderTable() {
  const rows = filteredRows();
  const visibleRows = rows.slice(0, 250);
  $('#screen-empty').hidden = rows.length > 0;
  $('#stock-table').innerHTML = visibleRows.map(row => {
    const trendChecks = ['price_above_sma200', 'sma50_above_sma200', 'slope_positive'];
    return `<tr>
      <td><div class="company-cell"><div class="ticker-avatar">${escapeHtml(row.ticker.slice(0, 4))}</div><div><b>${escapeHtml(row.name)}</b><small>${escapeHtml(row.ticker)} · ${escapeHtml(row.sector)}</small></div></div></td>
      <td><span class="badge ${statusClass(row)}">${escapeHtml(row.status)}</span></td>
      <td><span class="score">${row.score == null ? '—' : row.score.toFixed(0)}</span></td>
      <td><span class="number ${cls(row.m6)}">${fmtPct(row.m6)}</span></td>
      <td><span class="number ${cls(row.relative_m6)}">${fmtPct(row.relative_m6)}</span></td>
      <td><div class="trend-stack" title="Price above SMA200, SMA50 above SMA200, positive slope">${trendChecks.map(key => `<i class="${row.checks?.[key] ? 'pass' : ''}"></i>`).join('')}</div></td>
      <td><span class="risk"><b>${fmtPct(row.vol63)}</b>Volatility</span></td>
      <td><button class="details-button" data-ticker="${escapeHtml(row.ticker)}" aria-label="View ${escapeHtml(row.ticker)} details">→</button></td>
    </tr>`;
  }).join('');
  $('#results-note').textContent = rows.length > 250
    ? `Showing 250 of ${rows.length.toLocaleString()} matching stocks. Search or filter to narrow the list. Score is a relative rank, not a probability of profit.`
    : `Showing ${rows.length.toLocaleString()} matching stocks. Score is a relative rank, not a probability of profit.`;
  $$('.details-button', $('#stock-table')).forEach(button => button.addEventListener('click', () => openStock(button.dataset.ticker)));
}

const checkLabels = {
  history: '253 sessions available', price: 'Raw close ≥ $10', liquidity: 'ADV20 ≥ $20M',
  price_above_sma200: 'Price > SMA200', sma50_above_sma200: 'SMA50 > SMA200',
  slope_positive: 'SMA200 slope > 0', momentum_positive: '6M momentum > 0', relative_positive: '6M return > SPY',
};

function openStock(ticker) {
  const row = state.data.screen.find(item => item.ticker === ticker);
  if (!row) return;
  $('#dialog-content').innerHTML = `<div class="dialog-inner">
    <div class="dialog-header"><p class="eyebrow">${escapeHtml(row.permanent_id)}</p><h2 id="dialog-title">${escapeHtml(row.ticker)} · ${row.score == null ? 'Unranked' : `Score ${row.score.toFixed(0)}`}</h2><p>${escapeHtml(row.name)} · ${escapeHtml(row.sector)}<br>${escapeHtml(row.status)} · ${escapeHtml(row.status_change)}</p></div>
    <div class="dialog-metrics">
      <div class="dialog-metric"><small>6M momentum</small><b class="${cls(row.m6)}">${fmtPct(row.m6)}</b></div>
      <div class="dialog-metric"><small>Distance above SMA200</small><b class="${cls(row.distance200)}">${fmtPct(row.distance200)}</b></div>
      <div class="dialog-metric"><small>252D drawdown</small><b class="${cls(row.drawdown252)}">${fmtPct(row.drawdown252)}</b></div>
      <div class="dialog-metric"><small>12M momentum</small><b class="${cls(row.m12)}">${fmtPct(row.m12)}</b></div>
      <div class="dialog-metric"><small>ADV20</small><b>$${fmtCompact(row.adv20)}</b></div>
      <div class="dialog-metric"><small>Annualized volatility</small><b>${fmtPct(row.vol63)}</b></div>
    </div>
    <p class="kicker">Every rule explained</p>
    <div class="check-list">${Object.entries(checkLabels).map(([key, label]) => `<div class="check ${row.checks?.[key] ? 'pass' : ''}">${escapeHtml(label)}</div>`).join('')}</div>
    ${row.warnings?.length ? `<p class="negative">Data warning: ${escapeHtml(row.warnings.join(', '))}</p>` : ''}
  </div>`;
  $('#stock-dialog').showModal();
}

function renderTrackRecord() {
  const track = state.data.track_record;
  const amount = state.investment;
  const endValue = amount * (1 + (track.strategy_return ?? 0));
  const profit = endValue - amount;
  const benchmarkValue = amount * (1 + (track.benchmark_return ?? 0));
  $('#track-kpis').innerHTML = `
    <div class="track-kpi primary"><span>Hypothetical profit</span><strong class="${cls(profit)}">${profit >= 0 ? '+' : '−'}${fmtMoney(Math.abs(profit))}</strong></div>
    <div class="track-kpi"><span>Ending value</span><strong>${fmtMoney(endValue)}</strong></div>
    <div class="track-kpi"><span>Northstar return</span><strong class="${cls(track.strategy_return)}">${fmtPct(track.strategy_return)}</strong></div>
    <div class="track-kpi"><span>SPY ending value</span><strong>${fmtMoney(benchmarkValue)}</strong></div>`;
  $('#record-count').textContent = `${track.evaluations} evaluations · ${track.forward_evaluations} forward-recorded`;
  $('#simulation-note').textContent = `Uses next-session opening prices, 0% cash return, and ${track.cost_bps_per_side} bps per transaction side. Taxes are excluded. Reconstructed periods use today’s universe and are not an unbiased backtest.`;
  renderChart(track.equity_curve, amount);
  renderCohorts(track.cohorts);
}

function renderChart(points, amount) {
  const svg = $('#equity-chart');
  $('#chart-empty').hidden = points.length > 0;
  svg.hidden = points.length === 0;
  if (!points.length) return;
  const series = [{ date: points[0].date, strategy: 1, benchmark: 1 }, ...points];
  const values = series.flatMap(p => [p.strategy * amount, p.benchmark * amount]);
  let min = Math.min(...values), max = Math.max(...values);
  const padding = Math.max((max - min) * .18, amount * .03);
  min -= padding; max += padding;
  const x = index => 72 + index * (890 / Math.max(series.length - 1, 1));
  const y = value => 24 + (max - value) / (max - min) * 282;
  const path = key => series.map((point, index) => `${index ? 'L' : 'M'}${x(index).toFixed(1)},${y(point[key] * amount).toFixed(1)}`).join(' ');
  const grid = [0, .25, .5, .75, 1].map(fraction => {
    const value = min + (max - min) * (1 - fraction);
    const yy = 24 + 282 * fraction;
    return `<line class="chart-grid" x1="72" y1="${yy}" x2="962" y2="${yy}"/><text class="chart-label" x="4" y="${yy + 4}">${fmtMoney(value)}</text>`;
  }).join('');
  const labels = series.filter((_, i) => i === 0 || i === series.length - 1 || i % Math.ceil(series.length / 4) === 0).map((point, index, arr) => {
    const originalIndex = series.indexOf(point);
    const anchor = index === 0 ? 'start' : index === arr.length - 1 ? 'end' : 'middle';
    return `<text class="chart-label" text-anchor="${anchor}" x="${x(originalIndex)}" y="340">${new Date(`${point.date}T00:00:00`).toLocaleDateString('en-US', { month: 'short', year: '2-digit' })}</text>`;
  }).join('');
  svg.innerHTML = `${grid}${labels}<path class="chart-line benchmark" d="${path('benchmark')}"/><path class="chart-line strategy" d="${path('strategy')}"/><circle class="chart-dot" cx="${x(series.length - 1)}" cy="${y(series.at(-1).strategy * amount)}" r="5"/>`;
}

function renderCohorts(cohorts) {
  const container = $('#cohort-list');
  if (!cohorts.length) {
    container.innerHTML = '<div class="empty-state">No formal evaluations have been recorded yet.</div>';
    return;
  }
  container.innerHTML = cohorts.map(cohort => `<article class="cohort">
    <div><time datetime="${cohort.evaluation_session}">${new Date(`${cohort.evaluation_session}T00:00:00`).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}</time><span class="record-type">${escapeHtml(cohort.record_type)}</span></div>
    <div class="cohort-meta">${cohort.selected.length ? cohort.selected.map(item => `<span class="holding-chip" title="${escapeHtml(item.name)}: ${fmtPct(item.current_return)}">${escapeHtml(item.ticker)} · ${fmtPct(item.current_return)}</span>`).join('') : '<span class="holding-chip">Cash · market filter inactive or no selections</span>'}</div>
    <div class="cohort-summary"><strong class="${cls(cohort.cohort_return)}">${fmtPct(cohort.cohort_return)}</strong><small>${cohort.selected.length} selected</small></div>
  </article>`).join('');
}

function renderMethod() {
  const { meta, thresholds, methodology } = state.data;
  $('#universe-description').textContent = `${meta.universe_definition}. ${meta.available_count.toLocaleString()} of ${meta.universe_count.toLocaleString()} eligible listings had usable provider data in the latest run; ${(meta.failed_count ?? 0).toLocaleString()} failed. This is a current-listing universe, not a point-in-time historical reconstruction.`;
  $('#data-definitions').innerHTML = [
    ['Cₜ', 'Raw closing price'], ['Vₜ', 'Raw share volume'], ['Pₜ', 'Split-adjusted close, dividends excluded'], ['Tₜ', 'Total-return index with cash distributions'], ['Bₜ', 'SPY total-return index'], ['ID', 'SEC CIK issuer identifier'],
  ].map(([term, definition]) => `<div class="definition"><b>${term}</b><span>${definition}</span></div>`).join('');
  $('#rules-grid').innerHTML = [
    [`Raw close ≥ $${thresholds.minimum_raw_close}`, 'Price eligibility'], [`ADV20 ≥ $${fmtCompact(thresholds.minimum_adv20)}`, 'Liquidity eligibility'], [`At least ${thresholds.minimum_observations} sessions`, 'History eligibility'], ['P > SMA200', 'Established price trend'], ['SMA50 > SMA200', 'Intermediate trend'], ['Slope200 > 0', 'Long trend is rising'], ['M6 > 0', 'Positive momentum'], ['M6 − SPY M6 > 0', 'Market outperformance'],
  ].map(([rule, label]) => `<div class="rule"><b>${rule}</b><span>${label}</span></div>`).join('');
  $('#formula-list').innerHTML = methodology.formulas.map(formula => `<code>${escapeHtml(formula)}</code>`).join('');
  $('#limitations').innerHTML = methodology.limitations.map(limit => `<li>${escapeHtml(limit)}</li>`).join('');
}

function bindEvents() {
  $$('.nav-button').forEach(button => button.addEventListener('click', () => switchView(button.dataset.view)));
  $$('[data-link-view]').forEach(link => link.addEventListener('click', event => { event.preventDefault(); switchView(link.dataset.linkView); }));
  $$('.filter').forEach(button => button.addEventListener('click', () => {
    state.filter = button.dataset.filter;
    $$('.filter').forEach(item => item.classList.toggle('active', item === button));
    renderTable();
  }));
  $('#search').addEventListener('input', event => { state.search = event.target.value.toLowerCase().trim(); renderTable(); });
  $('#sort').addEventListener('change', event => { state.sort = event.target.value; renderTable(); });
  $('#investment').addEventListener('input', event => {
    const value = Number(event.target.value.replace(/[^0-9.]/g, ''));
    if (Number.isFinite(value) && value >= 0) { state.investment = value; renderTrackRecord(); }
  });
  $('.dialog-close').addEventListener('click', () => $('#stock-dialog').close());
  $('#stock-dialog').addEventListener('click', event => { if (event.target === $('#stock-dialog')) $('#stock-dialog').close(); });
}

async function init() {
  bindEvents();
  try {
    const response = await fetch(`data.json?v=${Date.now()}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    renderOverview(); renderTable(); renderTrackRecord(); renderMethod();
    const hash = location.hash.replace('#', '');
    if (['track', 'method'].includes(hash)) switchView(hash, false);
  } catch (error) {
    $('#data-pill').textContent = 'Data unavailable';
    $('#stock-table').innerHTML = `<tr><td colspan="8" class="empty-state">The research data could not be loaded. ${escapeHtml(error.message)}</td></tr>`;
    console.error(error);
  }
}

init();

