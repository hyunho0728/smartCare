/* 최신 DB 추세와 저장된 분석 스냅샷은 서로 다른 상태로 관리한다. */
(() => {
  const cache = new Map(), views = new Map();
  const colors = ['#2563eb', '#0f766e'];
  const dateText = value => value ? value.replace('T', ' ').slice(0, 19) : '미상';
  const el = (tag, text, cls) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (cls) node.className = cls; return node; };
  const svgEl = (tag, attrs = {}, text) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    if (text != null) node.textContent = text; return node;
  };
  function state(id, days) {
    const key = `${id}:${days}`;
    if (!cache.has(key)) cache.set(key, {data: null, pending: false, error: '', checkedAt: 0});
    return cache.get(key);
  }
  function view(key, days) {
    if (!views.has(key)) views.set(key, {days, metric: 'condition', checkup: '', tables: {}, point: {}});
    return views.get(key);
  }
  const metrics = {
    score: ['시스템 점수', '점', [['value', '점수']]],
    condition: ['건강 상태', '1~5', [['condition', '건강 상태']]],
    pressure: ['혈압', 'mmHg', [['systolic', '수축기'], ['diastolic', '이완기']]],
    sugar: ['생활 혈당 · 측정 조건 미확인', 'mg/dL', [['blood_sugar', '혈당']]],
    meals: ['결식 비율', '%', [['skip_percent', '결식 비율']]],
  };
  function selector(title, options, selected, change) {
    const label = el('label', title), select = el('select'); select.setAttribute('aria-label', title);
    for (const [value, text] of options) { const option = el('option', text); option.value = value; select.append(option); }
    select.value = selected; select.onchange = () => change(select.value); label.append(select); return label;
  }
  function table(parent, title, headers, rows, config, key) {
    const details = el('details', undefined, 'health-trend-table'); details.open = !!config.tables[key];
    const summary = el('summary', title); summary.onclick = () => { config.tables[key] = !details.open; };
    const scroll = el('div', undefined, 'health-trend-table-scroll'), grid = el('table');
    grid.append(el('caption', title)); const head = el('thead'), tr = el('tr');
    for (const header of headers) { const th = el('th', header); th.scope = 'col'; tr.append(th); } head.append(tr); grid.append(head);
    const body = el('tbody'); for (const values of rows) {
      const row = el('tr'); for (const value of values) { const td = el('td'); td.append(value instanceof Node ? value : document.createTextNode(String(value ?? '미기록'))); row.append(td); } body.append(row);
    }
    grid.append(body); scroll.append(grid); details.append(summary, scroll); parent.append(details);
  }
  function chart(parent, rows, columns, title, unit, config, key, options = {}) {
    parent.append(el('h5', `${title}${unit ? ` (${unit})` : ''}`));
    const valid = rows.flatMap(row => columns.map(([field]) => row[field])).filter(value => typeof value === 'number' && Number.isFinite(value));
    if (!valid.length) { parent.append(el('p', '그래프로 표시할 수치 기록이 없습니다.')); return; }
    const width = 700, height = 270, left = 125, right = 630, top = 26, bottom = 217;
    const min = options.min ?? 0, max = options.max ?? Math.max(1, ...valid) * 1.12;
    const times = rows.map(row => Date.parse(row.date)); const start = Math.min(...times), end = Math.max(...times);
    const x = row => start === end ? (left + right) / 2 : left + (Date.parse(row.date) - start) / (end - start) * (right - left);
    const y = value => bottom - (value - min) / (max - min) * (bottom - top);
    const svg = svgEl('svg', {viewBox: `0 0 ${width} ${height}`, class: 'health-trend-svg', role: 'group', 'aria-label': `${title} 추세 그래프`});
    svg.append(svgEl('title', {}, `${title} · ${rows[0]?.date} ~ ${rows.at(-1)?.date}`));
    for (const value of [min, (min + max) / 2, max]) {
      svg.append(svgEl('line', {x1:left, x2:right, y1:y(value), y2:y(value), stroke:'#dbe7f6'}), svgEl('text', {x:left-9, y:y(value)+5, 'text-anchor':'end'}, Math.round(value * 10) / 10));
    }
    if (key === 'score') for (const [threshold, label] of [[80,'안전'], [60,'주의'], [40,'경고']]) {
      svg.append(svgEl('line', {x1:left,x2:right,y1:y(threshold),y2:y(threshold),stroke:'#94a3b8','stroke-dasharray':'5 5'}),
        svgEl('text', {x:right,y:y(threshold)-4,'text-anchor':'end'}, `${label} ≥${threshold}`));
    }
    const indices = [...new Set([0, Math.floor((rows.length - 1) / 2), rows.length - 1])];
    for (const index of indices) if (rows[index]) svg.append(svgEl('text', {x:x(rows[index]),y:247,
      'text-anchor': index === 0 ? 'start' : index === rows.length - 1 ? 'end' : 'middle'},
      options.fullDates ? rows[index].date : rows[index].date.slice(5)));
    const info = el('p', config.point[key] || '그래프의 점을 누르거나 키보드로 선택하면 날짜와 값을 확인할 수 있습니다.', 'health-trend-point-info'); info.setAttribute('role', 'status'); info.setAttribute('aria-live', 'polite');
    columns.forEach(([field, label], column) => {
      let segment = [];
      const flush = () => { if (segment.length > 1) svg.append(svgEl('polyline', {points:segment.join(' '),fill:'none',stroke:colors[column], 'stroke-width':3})); segment = []; };
      for (const row of rows) {
        const value = row[field];
        if (typeof value !== 'number' || !Number.isFinite(value)) { flush(); continue; }
        segment.push(`${x(row)},${y(value)}`);
      } flush();
      for (const row of rows) {
        const value = row[field]; if (typeof value !== 'number' || !Number.isFinite(value)) continue;
        const note = row.kind === 'calculated' ? '기준 시각에 계산한 점수 · DB 저장 없음' : row.kind === 'stored' ? '저장된 점수' : '';
        const text = `${row.date} · ${label} ${value} ${unit} ${note}${row.recorded_at || row.as_of ? ` · ${dateText(row.recorded_at || row.as_of)}` : ''}`;
        const dot = svgEl('circle', {cx:x(row),cy:y(value),r:5,fill:row.kind === 'calculated' ? '#fff' : colors[column],stroke:colors[column], 'stroke-width':3, tabindex:0, role:'button','aria-label':text});
        dot.append(svgEl('title', {}, text));
        const show = () => { config.point[key] = text; info.textContent = text; };
        // 투명한 44px 터치 영역. 확대/축소해도 stroke 폭은 화면 픽셀 기준이다.
        const hit = svgEl('path', {d:`M ${x(row)} ${y(value)} l 0.01 0`, fill:'none', stroke:'transparent',
          'stroke-width':44, 'stroke-linecap':'round', 'vector-effect':'non-scaling-stroke',
          'pointer-events':'stroke', 'aria-hidden':'true'});
        hit.addEventListener('click', show); svg.append(hit);
        dot.addEventListener('focus', show); dot.addEventListener('click', show);
        dot.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); show(); } }); svg.append(dot);
      }
    });
    parent.append(svg);
    const legend = el('p', undefined, 'health-trend-legend'); columns.forEach(([,label], index) => { const span = el('span', `● ${label}`); span.style.color = colors[index]; legend.append(span); }); parent.append(legend, info);
  }
  function renderData(target, data, config, rerender, compact) {
    const startDate = data.life.slice(-config.days)[0]?.date || data.start_date;
    target.append(el('p', `기준 시각: ${dateText(data.as_of)} · 점수·생활 날짜 ${startDate} ~ ${data.end_date}`, 'health-analysis-meta'));
    if (data.scores_available) {
      const rows = data.scores.slice(-config.days);
      chart(target, rows, metrics.score[2], '시스템 점수 · 높을수록 안전', '점', config, 'score', {min:0,max:100});
      target.append(el('p', '안전 80점 이상 · 주의 60~79점 · 경고 40~59점 · 위험 40점 미만. 빈 구간은 미기록입니다.', 'health-analysis-meta'));
      table(target, '점수 수치 표', ['날짜','점수','자료 구분','기준 시각'], rows.map(row => [row.date,row.value,
        row.kind === 'calculated' ? '기준 시각 계산값 · 저장 없음' : row.kind === 'stored' ? '저장 점수' : '미기록',dateText(row.as_of)]), config, 'scores');
    } else target.append(el('p', '점수 추세가 저장되지 않았습니다.'));
    if (compact) return;
    target.append(selector('생활 지표', Object.entries(metrics).filter(([key]) => key !== 'score').map(([key,value]) => [key,value[0]]), config.metric, value => { config.metric=value; rerender(); }));
    const [title,unit,columns] = metrics[config.metric], rows = data.life.slice(-config.days);
    chart(target, rows, columns, title, unit, config, config.metric, config.metric === 'condition' ? {min:1,max:5} : config.metric === 'meals' ? {min:0,max:100} : {});
    table(target, '생활 지표 수치 표', ['대상 날짜',...columns.map(([,label]) => label),'입력 시각','확인된 식사/결식'], rows.map(row => [row.date,...columns.map(([field]) => row[field]),dateText(row.recorded_at),`${row.known_meals}식 / ${row.skipped_meals}식`]), config, 'life');
    target.append(el('h5', '확정 검진 추세 · 생활 측정과 별도'));
    const groups = data.checkups.series;
    if (!groups.length) target.append(el('p', '표시할 확정 검진 항목이 없습니다.'));
    else {
      const ids = groups.map(group => JSON.stringify([group.name,group.unit]));
      if (!ids.includes(config.checkup)) {
        const numeric = group => group.entries.some(entry => !entry.reason && typeof entry.value === 'number');
        const glucose = groups.findIndex(group => group.name === '공복혈당' && numeric(group));
        const firstNumeric = groups.findIndex(numeric);
        config.checkup = ids[glucose >= 0 ? glucose : firstNumeric >= 0 ? firstNumeric : 0];
      }
      target.append(selector('검진 지표·단위', groups.map((group,index) => [ids[index],`${group.name} · ${group.unit || '단위 미상'}`]), config.checkup, value => { config.checkup=value; rerender(); }));
      const group = groups[ids.indexOf(config.checkup)];
      const byDate = new Map();
      for (const entry of group.entries.filter(entry => entry.date)) {
        const old = byDate.get(entry.date);
        if (!old || old.reason && !entry.reason) byDate.set(entry.date, entry);
      }
      const dated = [...byDate.values()].map(entry => ({...entry,value:entry.reason ? null : entry.value}));
      chart(target, dated, [['value',group.name]], group.name, group.unit || '단위 미상', config, `checkup:${config.checkup}`, {fullDates:true});
      table(target, '검진 값·제외 이유·원본', ['검진일','원래 값','단위','문서 참고범위','확정 버전','제외·주의','원본'], group.entries.map(entry => {
        const button = el('button', `검진표 ${entry.doc_id} · ${entry.page || '?'}페이지`, 'mini-btn'); button.type='button';
        button.onclick = () => window.openCheckupReview(entry.doc_id, {expectedRevision:entry.confirmed_revision});
        return [entry.date || '미상',entry.raw_value,entry.unit || '미상',entry.reference_range || '미상',entry.confirmed_revision,
          [entry.reason,entry.old ? '1년 이상 지난 자료' : '', !entry.unit ? '단위 미상 · 단위가 확인된 자료와 비교하지 않음' : ''].filter(Boolean).join(' · ') || '없음', button];
      }), config, 'checkups');
    }
    if (data.checkups.total_documents > data.checkups.document_limit) target.append(el('p', `확정 문서 ${data.checkups.total_documents}개 중 최신 ${data.checkups.document_limit}개 사용`, 'health-analysis-meta'));
    if (data.checkups.unsupported_items) target.append(el('p', `미지원 검진 항목 ${data.checkups.unsupported_items}개 제외`, 'health-analysis-meta'));
    target.append(el('p', '검진은 같은 항목·단위끼리 표시하며 단위 환산은 하지 않습니다. 원본 확인창은 현재 확정 결과를 보여줍니다.', 'health-analysis-meta'));
    for (const text of data.limitations) target.append(el('p', text, 'health-analysis-meta'));
  }
  function renderLatest(target) {
    const id = target.dataset.healthTrendUserId, key = target.dataset.healthTrendView || `detail:${id}`;
    const config = view(key, target.dataset.healthTrendCompact === 'true' ? 7 : 30), cached = state(id,config.days);
    const wrapper = target.closest('[data-current-trend-details]');
    if (wrapper && !wrapper._healthTrendBound) {
      wrapper._healthTrendBound = true; wrapper.open = !!config.expanded;
      wrapper.querySelector('summary').addEventListener('click', () => { config.expanded = !wrapper.open; });
    }
    const rerender = () => { renderLatest(target); ensureLatest(target); };
    target._healthRenderedKey = `${id}:${config.days}`;
    target.replaceChildren(); target.classList.add('health-trend'); target.setAttribute('aria-busy', String(cached.pending));
    target.append(el('b', '현재 건강 추세 · 최신 DB 자료'));
    const controls = el('div', undefined, 'health-trend-controls');
    controls.append(selector('조회 기간', [['7','최근 7일'],['30','최근 30일']], String(config.days), value => { config.days=Number(value); config.point={}; rerender(); }));
    const refresh = el('button','새로고침','mini-btn'); refresh.type='button'; refresh.disabled=cached.pending; refresh.onclick=()=>load(id,config.days); controls.append(refresh); target.append(controls);
    if (cached.data) renderData(target,cached.data,config,()=>renderLatest(target),target.dataset.healthTrendCompact === 'true');
    if (cached.pending) { const line=el('p','건강 추세를 불러오는 중…'); line.setAttribute('role','status'); target.append(line); }
    if (cached.error) { const line=el('p',`${cached.error} 이전 그래프가 있다면 마지막 성공 조회값입니다.`, 'ai-error'); line.setAttribute('role','alert'); target.append(line); }
  }
  function visible(target) { return target.isConnected && target.getClientRects().length > 0; }
  async function load(id, days) {
    const cached=state(id,days); if (cached.pending) return;
    cached.pending=true; cached.dirty=false; cached.error=''; cached.checkedAt=Date.now(); renderTargets(id,days);
    try {
      const response=await fetch(`/api/admin/elders/${encodeURIComponent(id)}/health-trends?days=${days}`);
      if (response.status === 401) { sessionStorage.clear(); window.location.replace('/login'); return; }
      const body=await response.json(); if (!response.ok || !body.success) throw new Error(body.message || '건강 추세 조회 실패');
      if (!body.trends || !Array.isArray(body.trends.scores) || !Array.isArray(body.trends.life) || !Array.isArray(body.trends.checkups?.series)) throw new Error('추세 응답 형식을 확인하지 못했습니다.');
      cached.data=body.trends;
    } catch (error) { cached.error=error instanceof TypeError || error instanceof SyntaxError ? '서버 응답을 확인하지 못했습니다.' : error.message; }
    finally {
      cached.pending=false; cached.checkedAt=cached.dirty ? 0 : Date.now(); renderTargets(id,days);
      if (cached.dirty) scan();
    }
  }
  function renderTargets(id, days) {
    for (const target of document.querySelectorAll('[data-health-trend-user-id]')) {
      const key=target.dataset.healthTrendView || `detail:${target.dataset.healthTrendUserId}`, config=view(key,target.dataset.healthTrendCompact === 'true' ? 7 : 30);
      if (target.dataset.healthTrendUserId === String(id) && config.days === days) renderLatest(target);
    }
  }
  function ensureLatest(target) {
    if (!visible(target)) return;
    const config=view(target.dataset.healthTrendView || `detail:${target.dataset.healthTrendUserId}`,target.dataset.healthTrendCompact === 'true' ? 7 : 30);
    const cached=state(target.dataset.healthTrendUserId,config.days);
    if (!cached.pending && Date.now()-cached.checkedAt >= 30000) load(target.dataset.healthTrendUserId,config.days);
  }
  function scan() {
    for (const target of document.querySelectorAll('[data-health-trend-user-id]')) {
      const config=view(target.dataset.healthTrendView || `detail:${target.dataset.healthTrendUserId}`,target.dataset.healthTrendCompact === 'true' ? 7 : 30);
      if (target._healthRenderedKey !== `${target.dataset.healthTrendUserId}:${config.days}`) renderLatest(target);
      ensureLatest(target);
    }
  }
  window.HealthTrends = {
    snapshot: (target, record) => {
      target.classList.add('health-trend');
      const config=view(`analysis:${record.analysis_id}`,30), data=record.trends || record.input_snapshot.trends;
      const render = () => {
        target.replaceChildren();
        target.append(el('p','분석 당시 자료입니다. 현재 기록이나 재확정된 검진값으로 갱신하지 않습니다. 그래프의 7일·30일은 기준 날짜를 포함한 달력 날짜이며, 시각 기준의 AI 기간 비교와 경계가 다를 수 있습니다.', 'health-analysis-meta'));
        if (!data) { target.append(el('p','이 분석에는 그래프 자료가 저장되지 않았습니다.')); return; }
        target.append(selector('분석 당시 조회 기간',[['7','최근 7일'],['30','최근 30일']],String(config.days),value=>{config.days=Number(value);config.point={};render();}));
        renderData(target,data,config,render,false);
      }; render();
    },
    invalidate: id => {
      for (const days of [7,30]) { const cached=state(id,days); cached.checkedAt=0; cached.dirty=true; }
      scan();
    }, scan,
  };
  const dashboard=document.getElementById('dashboard');
  if (!dashboard) return;
  new MutationObserver(records => {
    if (records.some(record => record.type === 'attributes' || [...record.addedNodes].some(node => node.nodeType === 1 &&
      (node.matches('[data-health-trend-user-id]') || node.querySelector('[data-health-trend-user-id]'))))) scan();
  }).observe(dashboard,{childList:true,subtree:true,attributes:true,attributeFilter:['class','open','style','data-health-trend-user-id']});
  setInterval(scan,30000); scan();
})();
