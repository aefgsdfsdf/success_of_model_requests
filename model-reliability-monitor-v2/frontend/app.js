let chart;
let selectedGroup = '';
const cards = document.querySelector('#cards');
const detail = document.querySelector('#detail');
const groups = document.querySelector('#groups');
const pct = value => value == null ? '—' : `${Number(value).toFixed(2)}%`;

async function loadGroups() {
  const response = await fetch('/api/groups');
  if (!response.ok) throw new Error(`分组接口错误：${response.status}`);
  const data = await response.json();
  const all = [{ name: '', label: '所有分组', multiplier: null }, ...data.map(group => ({ ...group, label: group.name }))];
  groups.innerHTML = all.map(group => `<button class="group-filter${group.name === selectedGroup ? ' active' : ''}" data-group="${escapeAttr(group.name)}"><span>${escapeHtml(group.label)}</span>${group.multiplier == null ? '' : `<small>x${group.multiplier}</small>`}</button>`).join('');
  groups.querySelectorAll('button').forEach(button => button.onclick = () => {
    selectedGroup = button.dataset.group;
    groups.querySelectorAll('button').forEach(item => item.classList.toggle('active', item === button));
    loadModels();
  });
}

async function loadModels() {
  const query = selectedGroup ? `?group=${encodeURIComponent(selectedGroup)}` : '';
  const response = await fetch(`/api/models${query}`);
  if (!response.ok) throw new Error(`模型接口错误：${response.status}`);
  const data = await response.json();
  cards.innerHTML = data.map(model => `<article class="card"><div class="card-heading"><div><span class="group-label">${escapeHtml(model.group_name)}</span><h2>${escapeHtml(model.model_name)}</h2></div><span class="multiplier">x${multiplierFor(model.group_name)}</span></div><div class="metric"><span>成功率</span><strong>${pct(model.success_rate)}</strong></div><div class="metric"><span>总请求</span><strong>${model.total_count}</strong></div><div class="metric"><span>成功 / 失败</span><strong>${model.success_count} / ${model.failure_count}</strong></div><button data-model="${escapeAttr(model.model_name)}" data-group="${escapeAttr(model.group_name)}">查看详情</button></article>`).join('') || '<p class="muted">暂无统计数据</p>';
  cards.querySelectorAll('button').forEach(button => button.onclick = () => showDetail(button.dataset.model, button.dataset.group));
}

async function showDetail(model, group) {
  document.querySelector('#detail-title').textContent = `${group} / ${model}`;
  cards.classList.add('hidden');
  document.querySelector('.sidebar').classList.add('hidden');
  document.querySelector('.layout').classList.add('detail-mode');
  detail.classList.remove('hidden');
  const pointsResponse = await fetch(`/api/models/${encodeURIComponent(model)}/minutes?group=${encodeURIComponent(group)}`);
  if (!pointsResponse.ok) throw new Error(`分钟数据接口错误：${pointsResponse.status}`);
  const points = await pointsResponse.json();
  if (chart) chart.destroy();
  const chartWrap = document.querySelector('.chart-wrap');
  chartWrap.innerHTML = '<canvas id="chart"></canvas>';
  if (!points.some(point => point.success_rate != null)) {
    chartWrap.innerHTML = '<div class="empty-chart">最近一小时暂无请求数据</div>';
    return;
  }
  const labels = points.map(point => new Date(point.minute).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
  chart = new Chart(document.querySelector('#chart'), { type: 'line', data: { labels, datasets: [{ label: '请求成功率', data: points.map(point => point.success_rate), borderColor: '#19d39a', backgroundColor: '#19d39a', pointBackgroundColor: '#19d39a', pointBorderColor: '#19d39a', pointRadius: 4, pointHoverRadius: 7, spanGaps: false, tension: .25 }] }, options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false }, scales: { y: { min: 0, max: 100, ticks: { callback: value => value + '%' } }, x: { ticks: { maxTicksLimit: 12 } } }, plugins: { tooltip: { enabled: true, displayColors: false, callbacks: { title: items => `时间：${labels[items[0].dataIndex]}`, label: context => context.raw == null ? '成功率：无请求' : `成功率：${Number(context.raw).toFixed(2)}%` } } } } });
}

document.querySelector('#back').onclick = () => { detail.classList.add('hidden'); cards.classList.remove('hidden'); document.querySelector('.sidebar').classList.remove('hidden'); document.querySelector('.layout').classList.remove('detail-mode'); };
document.querySelector('#refresh').onclick = async () => { await loadGroups(); await loadModels(); };
function multiplierFor(group) { const button = [...groups.querySelectorAll('button')].find(item => item.dataset.group === group); return button?.querySelector('small')?.textContent.slice(1) || '1'; }
function escapeHtml(value) { return String(value).replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character])); }
function escapeAttr(value) { return escapeHtml(value); }
cards.innerHTML = '<p class="muted">正在加载模型数据…</p>';
loadGroups().then(loadModels).catch(error => {
  cards.innerHTML = `<p class="error">加载失败：${escapeHtml(error.message)}<br>请确认服务已从 D 盘项目目录启动，然后点击刷新。</p>`;
  console.error(error);
});
