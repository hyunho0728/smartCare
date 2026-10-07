/* 모델 선택은 브라우저에, 사용량 기준은 서버의 공통 기록에 둔다. */
(() => {
  if (typeof registrationMode !== 'undefined' && registrationMode) return;
  const ids = ['gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-2.5-flash', 'gemini-3.5-flash-lite'];
  const labels = ['Gemini 3.8 Flash', 'Gemini 3.6 Flash', 'Gemini 2.5 Flash', 'Gemini 3.5 Flash Lite'];
  let saved; try { saved = localStorage.getItem('smartcare_ai_model'); } catch (_) { /* 저장 불가 환경 */ }
  let selected = ids.includes(saved) ? saved : 'gemini-3.6-flash';
  let catalog = [], lastUsage = null, loading = false, catalogError = '', timer = null;
  const dialog = document.createElement('dialog'); dialog.id = 'ai-model-settings';
  dialog.innerHTML = `<header><h2>AI 모델 선택·앱 사용량</h2><button type="button" data-ai-close>닫기</button></header>
    <p>선택한 모델은 건강 종합 분석과 검진표 인식에 공통 적용됩니다. 변경은 다음 요청부터 적용됩니다.</p>
    <label>사용할 모델 <select data-ai-select aria-label="사용할 AI 모델"></select></label>
    <p class="ai-usage-notice">앱 호출 기준 예상치이며 AI Studio·다른 프로그램의 사용량은 포함하지 않음. 한도는 사진 기준 설정값입니다.</p>
    <a href="https://aistudio.google.com/rate-limit" target="_blank" rel="noopener">AI Studio에서 실제 프로젝트 사용량 확인</a>
    <p data-ai-status role="status" aria-live="polite"></p>
    <p data-ai-error role="alert"></p><button type="button" data-ai-refresh>사용량 새로고침</button>
    <div data-ai-usage></div><p>실패·대기·재시도도 요청 횟수에 포함합니다. 토큰 정보가 없는 호출은 잔여 토큰 확인 불가로 표시합니다.</p>`;
  document.body.append(dialog);
  const find = name => dialog.querySelector(`[data-ai-${name}]`);
  const label = model => labels[ids.indexOf(model)] || model;
  const el = (tag, text, className) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; };
  function updateLabels() {
    document.querySelectorAll('[data-ai-model-label]').forEach(node => { node.textContent = `선택 모델: ${label(selected)}`; });
    find('select').value = selected;
  }
  function save() { try { localStorage.setItem('smartcare_ai_model', selected); } catch (_) { /* 현재 화면에서 유지 */ } }
  async function api(url) {
    const response = await fetch(url); const body = await response.json();
    if (!response.ok || !body.success) throw new Error(body.message || '모델 정보를 불러오지 못했습니다.');
    return body;
  }
  async function initialize() {
    try {
      const body = await api('/api/admin/ai/models'); catalog = body.models;
      if (!ids.includes(saved)) selected = ids.includes(body.default_model) ? body.default_model : 'gemini-3.6-flash';
      find('select').replaceChildren();
      for (const item of catalog) { const option = el('option', item.label); option.value = item.id; find('select').append(option); }
      catalogError = ''; find('select').disabled = false; save(); updateLabels();
    } catch (error) { catalogError = error.message; find('select').disabled = true; }
  }
  let ready = initialize();
  function renderUsage() {
    if (!lastUsage) return;
    find('usage').replaceChildren();
    for (const model of lastUsage.models) {
      const card = el('section', undefined, 'ai-usage-card'); card.append(el('h3', model.label));
      for (const [key, title] of [['rpm', '최근 60초 요청'], ['tpm', '최근 60초 입력 토큰'], ['rpd', '태평양 시간 오늘 요청']]) {
        const unknown = key === 'tpm' && model.unknown_token_calls > 0;
        const line = el('p', `${unknown ? '최근 60초 확인된 입력 토큰' : title}: ${model.used[key].toLocaleString()} / ${model.limits[key].toLocaleString()} · 예상 잔여 ${unknown ? '확인 불가' : model.remaining[key].toLocaleString()}${model.exceeded[key] ? ' · 설정 한도 도달/초과' : ''}`);
        if (model.exceeded[key]) line.className = 'ai-usage-exceeded'; card.append(line);
        if (!unknown) {
          const meter = el('progress'); meter.max = model.limits[key]; meter.value = Math.min(model.used[key], model.limits[key]); meter.setAttribute('aria-label', `${model.label} ${title}`); card.append(meter);
        }
      }
      if (model.unknown_token_calls) card.append(el('p', `토큰 미확인 호출 ${model.unknown_token_calls}건`));
      find('usage').append(card);
    }
    find('status').textContent = `마지막 조회: ${new Date(lastUsage.as_of).toLocaleString()} · 집계 시작: ${lastUsage.tracking_started_at ? new Date(lastUsage.tracking_started_at).toLocaleString() : '아직 호출 없음'} · 다음 일일 초기화: ${new Date(lastUsage.next_daily_reset_at).toLocaleString()} (태평양 자정)`;
  }
  async function refresh() {
    if (loading) return; loading = true; find('refresh').disabled = true; find('error').textContent = '';
    dialog.setAttribute('aria-busy', 'true');
    if (!lastUsage) find('status').textContent = '사용량을 불러오는 중…';
    try {
      if (catalogError) { ready = initialize(); await ready; }
      if (catalogError) throw new Error(catalogError);
      lastUsage = await api('/api/admin/ai/usage'); renderUsage();
    } catch (error) {
      find('error').textContent = `갱신 실패: ${error.message} 이전 조회값은 최신 값이 아닙니다.`;
      if (!lastUsage) find('status').textContent = '아직 사용량을 확인하지 못했습니다.';
    } finally { loading = false; find('refresh').disabled = false; dialog.setAttribute('aria-busy', 'false'); }
  }
  function open() {
    if (!dialog.open) dialog.showModal();
    ready.then(refresh); clearInterval(timer); timer = setInterval(() => { if (dialog.open) refresh(); }, 30000);
  }
  find('close').onclick = () => dialog.close();
  dialog.addEventListener('close', () => { clearInterval(timer); timer = null; });
  find('refresh').onclick = refresh;
  find('select').onchange = () => { selected = find('select').value; saved = selected; save(); updateLabels(); };
  window.addEventListener('storage', event => { if (event.key === 'smartcare_ai_model' && ids.includes(event.newValue)) { selected = event.newValue; saved = selected; updateLabels(); } });
  window.AIModels = {
    label, open,
    getModel: async () => { await ready; if (catalogError) throw new Error(catalogError); return selected; },
    mount: target => {
      const line = el('div', undefined, 'ai-model-control'); const text = el('span'); text.dataset.aiModelLabel = '';
      const button = el('button', '모델 선택·사용량', 'mini-btn'); button.type = 'button'; button.onclick = open;
      line.append(text, button); target.append(line); updateLabels();
    },
    usageChanged: () => refresh(),
  };
  const header = document.querySelector('#dashboard .tools');
  if (header) { const button = el('button', 'AI 모델·사용량', 'mini-btn'); button.id = 'ai-model-open'; button.type = 'button'; button.onclick = open; header.append(button); }
})();
