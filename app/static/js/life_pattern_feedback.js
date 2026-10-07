/* 어르신별 요청 상태를 유지하고 서버에 저장된 결과를 복원한다. */
(() => {
  const states = new Map();
  const stateFor = id => {
    id = String(id);
    if (!states.has(id)) states.set(id, {pending: false, loading: false, loaded: false, record: null, currentStatus: null, history: [], error: ''});
    return states.get(id);
  };
  function element(tag, text, className) {
    const node = document.createElement(tag); if (text !== undefined) node.textContent = text;
    if (className) node.className = className; return node;
  }
  function section(panel, title, lines) {
    panel.append(element('h5', title));
    const list = element('ul'); for (const line of lines) list.append(element('li', line));
    if (!lines.length) list.append(element('li', '없음')); panel.append(list);
  }
  const dateText = value => value ? value.replace('T', ' ').slice(0, 19) : '미상';
  function sourceText(source) {
    const d = source.data;
    if (source.kind === '건강검진') return `${d.name}: ${d.value} ${d.unit || '(단위 미상)'} · 참고범위 ${d.reference_range || '미상'} · 검진일 ${d.checkup_date || '미상'}`;
    if (source.kind === '등록기저질환') return `등록 질환: ${d.diseases.join(', ') || '분류 정보 부족'}`;
    if (source.kind === '접속기록') return `최근 7일 접속 ${d.recent_7d_count}회 · 이전 23일 ${d.previous_23d_count}회 · 마지막 접속 ${dateText(d.last_login_at)}`;
    if (source.kind === '시스템점수') return `시스템 생활패턴 ${d.label} · ${d.score}점 · 기준 ${dateText(d.as_of)} · 점수 산정 근거`;
    if (source.kind === '생활기간비교') return `서버가 계산한 최근 7일/이전 23일 관측값 비교 · 기록 ${d.recent.records}/${d.previous.records}개`;
    if (source.kind === '검진변화') return `${d.name} 검진 변화 · ${d.reason || `수치 차이 ${d.delta}`} · 건강 확인 근거`;
    if (source.kind === '직전분석비교') return `직전 분석 대비 입력 확인 · ${d.same_inputs ? '같은 자료' : '입력 자료 변경'} · 새 생활 기록 ${d.new_life_records}개`;
    const meals = d.meals || {};
    return `${dateText(d.recorded_at)} (${d.period}) · 건강 상태 ${d.condition_level} · 아침 ${meals.breakfast || '미상'}/점심 ${meals.lunch || '미상'}/저녁 ${meals.dinner || '미상'} · 혈압 ${d.blood_pressure || '미상'} · 혈당 ${d.blood_sugar ?? '미상'}`;
  }
  const valueText = value => value === null || value === undefined ? '비교 불가' : String(value);
  const deltaText = (recent, previous) => recent == null || previous == null ? '비교 불가' : `${recent - previous > 0 ? '+' : ''}${Math.round((recent - previous) * 10000) / 10000}`;
  function appendSources(block, refs, snapshot) {
    const expanded = new Set(refs);
    for (const ref of refs) {
      const source = snapshot.sources.find(s => s.ref === ref);
      for (const underlying of source?.source_refs || []) expanded.add(underlying);
    }
    for (const ref of expanded) {
      const source = snapshot.sources.find(s => s.ref === ref); if (!source) continue;
      const line = element('p', `근거 ${ref}: ${sourceText(source)}`, 'health-analysis-source');
      if (source.doc_id) {
        const button = element('button', `검진표 ${source.doc_id} · ${source.page || '?'}페이지 원본 확인`, 'mini-btn');
        button.type = 'button'; button.addEventListener('click', () => window.openCheckupReview(source.doc_id)); line.append(button);
      }
      block.append(line);
    }
  }
  function renderStatus(panel, status, title) {
    panel.append(element('h5', title));
    if (!status) { panel.append(element('p', title.startsWith('현재') ? '현재 상태를 확인하지 못했습니다.' : '이 분석에는 상태 정보가 저장되지 않았습니다.')); return; }
    const block = element('div', undefined, 'health-status-card');
    block.append(element('strong', `${status.label} · ${status.score}점 / 100점`),
      element('p', `기준 시각: ${dateText(status.as_of)} · 점수는 높을수록 안전합니다.`, 'health-analysis-meta'));
    for (const item of status.breakdown) block.append(element('p', `${item.item}: ${item.points}점`));
    if (!status.breakdown.length) block.append(element('p', '계산된 감점 항목 없음'));
    block.append(element('p', '기존 시스템 생활패턴 지표입니다. 검진 수치나 종합 AI 설명은 이 점수를 변경하지 않습니다.', 'health-analysis-meta'));
    panel.append(block);
  }
  function renderComparisons(panel, snapshot) {
    panel.append(element('h5', '이전 기록 대비 변화'));
    const comparisons = snapshot.comparisons;
    if (!comparisons) { panel.append(element('p', '이 분석에는 비교 정보가 저장되지 않았습니다.')); return; }
    const previous = comparisons.previous_analysis;
    const block = element('div', undefined, 'health-comparison-card');
    block.append(element('strong', '직전 성공 분석 대비'));
    if (!previous.available) block.append(element('p', '첫 분석으로 비교할 이전 분석이 없습니다.'));
    else {
      block.append(element('p', `비교 분석 #${previous.analysis_id} · ${dateText(previous.analyzed_at)}`));
      block.append(element('p', previous.same_inputs ? '같은 자료로 재분석했습니다. 새 건강 변화가 확인된 것은 아닙니다.' : `입력 자료 변경: 새 생활 기록 ${previous.new_life_records}개 · 검진 문서/확정 버전 ${previous.documents_changed ? '변경' : '동일'} · 등록 질환 ${previous.diseases_changed ? '변경' : '동일'}`));
      if (previous.status) {
        const s = previous.status;
        block.append(element('p', `${s.previous.label} ${s.previous.score}점 → ${s.recent.label} ${s.recent.score}점 · 차이 ${deltaText(s.recent.score, s.previous.score)}점`));
        for (const item of s.breakdown_changes) block.append(element('p', `${item.item}: ${item.previous} → ${item.recent}점`));
        block.append(element('p', '0~100점 보정으로 감점 항목 변화 합계와 실제 점수 차이는 다를 수 있습니다.', 'health-analysis-meta'));
      } else block.append(element('p', '이전 분석에 상태 정보가 없어 점수 변화는 비교할 수 없습니다.'));
    }
    panel.append(block);
    const life = comparisons.life, before = life.previous, recent = life.recent;
    const lifeBlock = element('div', undefined, 'health-comparison-card');
    lifeBlock.append(element('strong', '생활 기간 비교: 이전 23일 → 최근 7일'));
    lifeBlock.append(element('p', `이전: ${dateText(before.start)} ~ ${dateText(before.end_exclusive)} 직전\n최근: ${dateText(recent.start)} ~ ${dateText(snapshot.window_end)}`));
    lifeBlock.append(element('p', `생활 기록 ${before.records} → ${recent.records}개 · 미기록 날짜 ${before.missing_days.length} → ${recent.missing_days.length}일`));
    for (const [key, label, unit] of [['condition_level', '건강 상태(1~5)', ''], ['systolic', '수축기혈압', 'mmHg'], ['diastolic', '이완기혈압', 'mmHg'], ['blood_sugar', '혈당(측정 조건 미확인)', 'mg/dL']]) {
      const b = before.metrics[key], r = recent.metrics[key];
      lifeBlock.append(element('p', `${label} 평균: ${valueText(b.mean)} (표본 ${b.count}) → ${valueText(r.mean)} (표본 ${r.count}) ${unit} · 차이 ${deltaText(r.mean, b.mean)}`));
    }
    lifeBlock.append(element('p', `결식 비율: ${valueText(before.meals.skip_percent)}% (${before.meals.skipped}/${before.meals.known}식) → ${valueText(recent.meals.skip_percent)}% (${recent.meals.skipped}/${recent.meals.known}식) · 차이 ${deltaText(recent.meals.skip_percent, before.meals.skip_percent)}%p`));
    lifeBlock.append(element('p', `접속한 날짜: ${before.login_days}/${before.calendar_days}일 → ${recent.login_days}/${recent.calendar_days}일 · 관측 가능한 날짜 대비 비율 ${valueText(before.login_day_percent)}% → ${valueText(recent.login_day_percent)}%`));
    lifeBlock.append(element('p', `이전 미기록 날짜: ${before.missing_days.join(', ') || '없음'}\n최근 미기록 날짜: ${recent.missing_days.join(', ') || '없음'}`, 'health-analysis-meta'));
    lifeBlock.append(element('p', '기간과 표본 수가 다릅니다. 미기록은 정상 상태를 뜻하지 않으며 숫자 증감만으로 호전·악화를 단정하지 않습니다. 식사 예정은 분모에서 제외합니다.', 'health-analysis-meta'));
    panel.append(lifeBlock);
    panel.append(element('h5', '검진 수치 비교 · 건강 확인 근거'));
    if (!comparisons.checkups.length) panel.append(element('p', '비교할 두 개의 확정 검진 자료가 없습니다.'));
    for (const c of comparisons.checkups) {
      const card = element('div', undefined, 'health-comparison-card');
      const format = rows => rows.map(d => `${d.value} ${d.unit || '(단위 미상)'} · ${d.checkup_date || '검진일 미상'}`).join(' / ') || '항목 없음';
      card.append(element('strong', c.name), element('p', `이전 ${format(c.previous)} → 최신 ${format(c.recent)}`),
        element('p', c.reason || `수치 차이: ${deltaText(c.delta, 0)} · 건강 상태의 호전·악화를 뜻하는 판정이 아닙니다.`));
      appendSources(card, c.source_refs, snapshot); panel.append(card);
    }
  }
  function render(id) {
    const state = stateFor(id);
    document.querySelectorAll(`[data-life-ai-user-id="${id}"]`).forEach(panel => {
      panel.replaceChildren(); panel.setAttribute('aria-busy', String(state.pending || state.loading));
      panel.append(element('b', 'AI 건강 종합 분석'));
      window.AIModels?.mount(panel);
      if (state.record) {
        const record = state.record, snapshot = record.input_snapshot, report = record.result;
        panel.append(element('p', `분석에 사용한 모델: ${window.AIModels?.label(record.model) || record.model || '기록 없음'}`, 'health-analysis-meta'));
        panel.append(element('p', `분석 시각: ${dateText(record.analyzed_at)} · 과거 자료를 기준으로 작성된 참고 결과입니다.`, 'health-analysis-meta'));
        panel.append(element('p', `생활 기록 범위: ${dateText(snapshot.window_start)} ~ ${dateText(snapshot.window_end)}`, 'health-analysis-meta'));
        const label = element('label', '분석 이력 ');
        const select = element('select'); select.setAttribute('aria-label', '건강 종합 분석 이력');
        select.disabled = state.pending || state.loading;
        for (const h of state.history) {
          const option = element('option', `${dateText(h.analyzed_at)} (#${h.analysis_id})`);
          option.value = h.analysis_id; option.selected = h.analysis_id === record.analysis_id; select.append(option);
        }
        select.addEventListener('change', () => load(id, select.value)); label.append(select); panel.append(label);
        const refresh = element('button', '현재 자료·저장 결과 새로고침', 'mini-btn'); refresh.type = 'button'; refresh.disabled = state.pending || state.loading;
        refresh.addEventListener('click', () => load(id, record.analysis_id)); panel.append(refresh);
        renderStatus(panel, snapshot.system_status, '분석 당시 상태 · 점수 산정 근거');
        renderStatus(panel, state.currentStatus, '현재 조회 상태 · 시스템 생활패턴 지표');
        if (state.currentStatusError) panel.append(element('p', state.currentStatusError, 'ai-error'));
        if (record.needs_reanalysis) panel.append(element('p', '분석 이후 입력 자료나 검진 확정 버전이 변경되었습니다. 재분석이 필요합니다. 과거 값은 아래에 보존됩니다.', 'health-reanalysis-note'));
        panel.append(element('h5', '사회복지사 우선 확인 · 위험등급과 별개인 확인 순서'));
        if (!report.priority_actions) panel.append(element('p', '이전 형식의 결과입니다. 아래 권장 확인을 참고하세요.'));
        for (const action of report.priority_actions || []) {
          const card = element('div', undefined, 'health-priority-card');
          card.append(element('strong', `${action.priority}: ${action.action}`), element('p', action.reason));
          appendSources(card, action.source_refs, snapshot); panel.append(card);
        }
        renderComparisons(panel, snapshot);
        panel.append(element('h5', '종합 요약'), element('p', report.summary));
        panel.append(element('h5', '확인 사항과 근거'));
        if (!report.findings.length) panel.append(element('p', 'AI가 제시한 확인 사항 없음. 자료 부족 여부는 아래 한계를 확인하세요.'));
        for (const finding of report.findings) {
          const block = element('div', undefined, 'health-analysis-finding');
          block.append(element('strong', finding.title), element('p', finding.detail));
          appendSources(block, finding.source_refs, snapshot);
          panel.append(block);
        }
        section(panel, '사회복지사 권장 확인', report.recommended_actions);
        section(panel, '데이터 한계', report.limitations);
        panel.append(element('h5', '사용한 검진 자료'));
        panel.append(element('p', '원본 확인창에는 현재 확정 결과가 표시됩니다. 재확정되었다면 아래 분석 당시 버전·값과 다를 수 있습니다.', 'health-analysis-meta'));
        if (!snapshot.documents.length) panel.append(element('p', '확정된 검진 자료 없음'));
        for (const doc of snapshot.documents) {
          const line = element('p', `문서 ${doc.doc_id} · 검진일 ${doc.checkup_date || '미상'} · 확정 버전 ${doc.confirmed_revision} · 판독 불가/누락 ${doc.unreadable_items}개 · 제외 ${doc.excluded_items}개 `);
          const button = element('button', '검진표 확인', 'mini-btn'); button.type = 'button';
          button.addEventListener('click', () => window.openCheckupReview(doc.doc_id)); line.append(button); panel.append(line);
        }
      } else if (!state.loading && !state.pending) panel.append(element('p', 'AI 분석을 실행하면 확정된 검진·생활 기록·등록 질환을 함께 분석합니다.'));
      if (state.pending || state.loading) {
        const status = element('div', undefined, 'ai-loading'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
        const spinner = element('span', undefined, 'ai-spinner'); spinner.setAttribute('aria-hidden', 'true');
        status.append(spinner, document.createTextNode(state.pending ? `AI 분석 중… ${window.AIModels?.label(state.pendingModel) || state.pendingModel || ''} · 완료까지 시간이 걸릴 수 있습니다.` : '저장된 분석을 불러오는 중…')); panel.append(status);
      }
      if (state.error) {
        const error = element('p', state.error, 'ai-error'); error.setAttribute('role', 'alert'); panel.append(error);
        const retry = element('button', '저장 결과 다시 불러오기', 'mini-btn'); retry.type = 'button'; retry.disabled = state.pending || state.loading;
        retry.addEventListener('click', () => load(id)); panel.append(retry);
      }
    });
    document.querySelectorAll(`[data-life-ai-button-id="${id}"]`).forEach(button => {
      button.disabled = state.pending || state.loading;
      button.textContent = state.pending ? '분석 중…' : state.error ? '다시 시도' : state.record ? '재분석' : 'AI 분석';
    });
  }
  async function request(url, options) {
    const response = await fetch(url, options);
    if (response.status === 401) { sessionStorage.clear(); window.location.replace('/login'); throw new Error('로그인이 필요합니다.'); }
    const body = await response.json();
    if (!response.ok || !body.success) throw new Error(body.message || 'AI 분석에 실패했습니다.');
    return body;
  }
  function message(error) {
    return error instanceof TypeError || error instanceof SyntaxError ? '서버 응답을 확인하지 못했습니다. 다시 시도해주세요.' : error.message;
  }
  async function load(id, selected) {
    const state = stateFor(id); if (state.loading || state.pending) return;
    state.loading = true; state.loaded = true; state.error = ''; render(id);
    try {
      const body = await request(`/api/admin/elders/${id}/health-analysis${selected ? `?analysis_id=${encodeURIComponent(selected)}` : ''}`);
      state.record = body.selected; state.history = body.history; state.currentStatus = body.current_status; state.currentStatusError = body.current_status_error;
    } catch (error) { state.error = message(error); }
    finally { state.loading = false; render(id); }
  }
  function sync() {
    const ids = new Set(Array.from(document.querySelectorAll('[data-life-ai-user-id]')).map(panel => panel.dataset.lifeAiUserId));
    for (const id of ids) { if (!stateFor(id).loaded) load(id); else render(id); }
  }
  // 과거 sessionStorage 문자열을 새 종합 분석 결과로 사용하지 않는다.
  window.setLifePatternAnalysisText = id => { if (!stateFor(id).loaded) load(id); else render(id); };
  window.setLifePatternAIButtons = id => render(id);
  window.analyzeLifePatternAI = async (id, elderName) => {
    const state = stateFor(id); if (state.pending || state.loading) return;
    let model;
    try { model = window.AIModels ? await window.AIModels.getModel() : 'gemini-3.6-flash'; }
    catch (error) { state.error = message(error); render(id); return; }
    if (state.pending || state.loading) return;
    if (!confirm(`${elderName} 어르신의 최근 30일 생활·접속 기록, 확정된 검진 항목, 정규화된 등록 질환, 시스템 점수와 서버가 계산한 변화 자료를 Gemini로 전송해 분석합니다. 이름·전화번호·주소·기관명·원문·자유 메모는 전송하지 않습니다.\n\n계속 진행하시겠습니까?`)) return;
    state.pending = true; state.pendingModel = model; state.error = ''; render(id);
    try {
      const body = await request(`/api/admin/elders/${id}/life-pattern-ai`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({model})});
      state.record = body;
      state.currentStatus = body.current_status;
      state.currentStatusError = null;
      state.history = [{analysis_id: body.analysis_id, analyzed_at: body.analyzed_at}, ...state.history.filter(h => h.analysis_id !== body.analysis_id)].slice(0, 20);
      state.loaded = true;
    } catch (error) { state.error = message(error); }
    finally { state.pending = false; render(id); window.AIModels?.usageChanged(); }
  };
  new MutationObserver(records => {
    if (records.some(record => Array.from(record.addedNodes).some(node => node.nodeType === 1 &&
      (node.matches('[data-life-ai-user-id], [data-life-ai-button-id]') || node.querySelector('[data-life-ai-user-id], [data-life-ai-button-id]'))))) sync();
  }).observe(document.getElementById('dashboard'), {childList: true, subtree: true});
  sync();
})();
