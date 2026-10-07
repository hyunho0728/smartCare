/* 어르신별 요청 상태를 유지하고 서버에 저장된 결과를 복원한다. */
(() => {
  const states = new Map();
  const stateFor = id => {
    id = String(id);
    if (!states.has(id)) states.set(id, {pending: false, loading: false, loaded: false, record: null, currentStatus: null, history: [], error: '', folds: {}});
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
  let renderingState;
  function fold(parent, title, key) {
    const state = renderingState;
    const details = element('details', undefined, 'health-details');
    details.open = !!state.folds[key];
    const summary = element('summary', title);
    // 클릭 시 바로 저장하여 비동기 toggle 이벤트 전의 화면 갱신에도 유지한다.
    summary.addEventListener('click', () => { state.folds[key] = !details.open; });
    details.append(summary);
    parent.append(details); return details;
  }
  function appendSources(block, refs, snapshot) {
    if (!refs?.length) return;
    block = fold(block, '관련 기록 보기', `sources:${refs.join(',')}`);
    const expanded = new Set(refs);
    for (const ref of refs) {
      const source = snapshot.sources.find(s => s.ref === ref);
      for (const underlying of source?.source_refs || []) expanded.add(underlying);
    }
    for (const ref of expanded) {
      const source = snapshot.sources.find(s => s.ref === ref); if (!source) continue;
      const line = element('p', sourceText(source), 'health-analysis-source');
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
    if (previous.available) panel.append(block);
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
  function renderContact(panel, id) {
    const contact = element('div', undefined, 'health-contact-actions');
    for (const [title, kind] of [['연락처 복사', 'contact'], ['조치 결과 작성', 'feedback']]) {
      const button = element('button', title, 'mini-btn'); button.type = 'button';
      button.onclick = () => window.healthAnalysisAction(id, kind); contact.append(button);
    }
    panel.append(contact);
  }
  function renderCoreChanges(panel, snapshot) {
    const comparisons = snapshot.comparisons;
    panel.append(element('h5', '핵심 변화'));
    if (!comparisons) { panel.append(element('p', '이 분석에는 비교 정보가 저장되지 않았습니다.')); return; }
    const previous = comparisons.previous_analysis;
    if (previous.available) panel.append(element('p', previous.same_inputs
      ? '같은 자료로 재분석했습니다. 새 건강 변화가 확인된 것은 아닙니다.'
      : `직전 분석 이후 새 생활 기록 ${previous.new_life_records}개 · 검진 ${previous.documents_changed ? '변경' : '동일'} · 등록 질환 ${previous.diseases_changed ? '변경' : '동일'}`));
    const {previous: before, recent} = comparisons.life;
    const metrics = element('div', undefined, 'health-core-metrics');
    const add = (title, text) => { const card = element('div'); card.append(element('strong', title), element('p', text)); metrics.append(card); };
    const b = before.metrics.condition_level, r = recent.metrics.condition_level;
    if (b.count || r.count) add('건강 상태 평균 (1~5)', `${valueText(b.mean)} → ${valueText(r.mean)} · 표본 ${b.count}/${r.count}`);
    if (before.meals.known || recent.meals.known) add('결식 비율', `${valueText(before.meals.skip_percent)}% → ${valueText(recent.meals.skip_percent)}%`);
    for (const [key, label, unit] of [['systolic', '수축기혈압', 'mmHg'], ['diastolic', '이완기혈압', 'mmHg'], ['blood_sugar', '혈당 · 측정 조건 미확인', 'mg/dL']]) {
      const b = before.metrics[key], r = recent.metrics[key];
      if (b.count || r.count) add(label, `${b.count ? b.mean : '미기록'} → ${r.count ? r.mean : '미기록'} ${unit} · 표본 ${b.count}/${r.count}`);
    }
    add('생활 기록', `이전 23일 ${before.records}개 → 최근 7일 ${recent.records}개`);
    add('접속한 날짜', `${before.login_days}/${before.calendar_days}일 → ${recent.login_days}/${recent.calendar_days}일`);
    panel.append(metrics);
    const missing = [];
    if (!before.metrics.systolic.count && !recent.metrics.systolic.count && !before.metrics.diastolic.count && !recent.metrics.diastolic.count) missing.push('혈압');
    if (!before.metrics.blood_sugar.count && !recent.metrics.blood_sugar.count) missing.push('혈당');
    if (missing.length) panel.append(element('p', `${missing.join('·')} 기록 없음`, 'health-analysis-meta'));
    panel.append(element('p', '이전 23일 → 최근 7일 비교입니다. 기간·표본 수가 다르며, 증감만으로 호전·악화를 판단하지 않습니다.', 'health-analysis-meta'));
  }
  function render(id) {
    const state = stateFor(id);
    renderingState = state;
    document.querySelectorAll(`[data-life-ai-user-id="${id}"]`).forEach(panel => {
      panel.replaceChildren(); panel.setAttribute('aria-busy', String(state.pending || state.loading));
      panel.append(element('b', '건강 상태와 AI 분석'));
      const settings = fold(panel, '모델 설정·분석 이력', 'settings');
      window.AIModels?.mount(settings);
      const refresh = element('button', '현재 자료·저장 결과 새로고침', 'mini-btn'); refresh.type = 'button'; refresh.disabled = state.pending || state.loading;
      refresh.addEventListener('click', () => load(id, state.record?.analysis_id)); settings.append(refresh);
      const status = state.currentStatus || state.record?.input_snapshot.system_status;
      const statusCard = element('div', undefined, 'health-current-status');
      statusCard.dataset.level = status?.level || '';
      statusCard.append(element('h5', state.currentStatus ? '현재 상태' : status ? '분석 당시 상태 · 현재 조회 불가' : '현재 상태'));
      if (status) {
        statusCard.append(element('strong', `${status.label} · ${status.score}점 / 100점`),
          element('p', `기준 ${dateText(status.as_of)} · 점수는 높을수록 안전합니다.`, 'health-analysis-meta'));
        const reasons = [...status.breakdown].filter(item => item.points < 0).sort((a,b) => a.points-b.points).slice(0,2);
        statusCard.append(element('p', reasons.length ? `주요 감점: ${reasons.map(item => item.item).join(' · ')}` : '계산된 감점 항목 없음'));
      } else statusCard.append(element('p', '현재 상태를 확인하지 못했습니다.'));
      panel.append(statusCard);
      if (state.currentStatusError) panel.append(element('p', state.currentStatusError, 'ai-error'));
      if (state.record) {
        const record = state.record, snapshot = record.input_snapshot, report = record.result;
        settings.append(element('p', `분석에 사용한 모델: ${window.AIModels?.label(record.model) || record.model || '기록 없음'}`, 'health-analysis-meta'));
        settings.append(element('p', `생활 기록 범위: ${dateText(snapshot.window_start)} ~ ${dateText(snapshot.window_end)}`, 'health-analysis-meta'));
        const label = element('label', '분석 이력 ');
        const select = element('select'); select.setAttribute('aria-label', '건강 종합 분석 이력'); select.disabled = state.pending || state.loading;
        for (const h of state.history) {
          const option = element('option', `${dateText(h.analyzed_at)} (#${h.analysis_id})`);
          option.value = h.analysis_id; option.selected = h.analysis_id === record.analysis_id; select.append(option);
        }
        select.addEventListener('change', () => load(id, select.value)); label.append(select); settings.append(label);
        panel.append(element('p', `AI 분석 시각: ${dateText(record.analyzed_at)} · 저장된 자료 기준 참고 결과`, 'health-analysis-meta'));
        if (record.needs_reanalysis) panel.append(element('p', '분석 이후 자료가 변경되었습니다. 최신 자료로 재분석해주세요.', 'health-reanalysis-note'));
        panel.append(element('h5', '우선 확인할 일'));
        const actions = [...(report.priority_actions || [])].sort((a,b) => (a.priority === '우선 확인' ? 0 : 1) - (b.priority === '우선 확인' ? 0 : 1));
        let moreActions;
        if (actions.length) for (const [index, action] of actions.entries()) {
          const card = element('div', undefined, 'health-priority-card');
          card.append(element('strong', `${action.priority}: ${action.action}`), element('p', action.reason));
          appendSources(card, action.source_refs, snapshot);
          if (index < 2) panel.append(card);
          else { moreActions ||= fold(panel, `추가 확인할 일 ${actions.length - 2}개`, 'actions'); moreActions.append(card); }
        } else {
          panel.append(element('p', '이전 형식의 결과입니다. 권장 확인을 참고하세요.', 'health-analysis-meta'));
          section(panel, '권장 확인', report.recommended_actions || []);
        }
        renderContact(panel, id);
        panel.append(element('h5', '종합 요약'), element('p', report.summary.length > 180 ? report.summary.slice(0,180) + '…' : report.summary));
        if (report.summary.length > 180) fold(panel, '전체 요약 보기', 'summary').append(element('p', report.summary));
        renderCoreChanges(panel, snapshot);
        const scoreDetails = fold(panel, '점수 산정 근거·분석 당시 상태', 'scores');
        renderStatus(scoreDetails, state.currentStatus, '현재 조회 상태 · 시스템 생활패턴 지표');
        renderStatus(scoreDetails, snapshot.system_status, '분석 당시 상태 · 점수 산정 근거');
        const changes = fold(panel, '변화 수치·기간 자세히 보기', 'changes'); renderComparisons(changes, snapshot);
        const findings = fold(panel, '전체 확인 사항·관련 근거', 'findings');
        if (!report.findings.length) findings.append(element('p', 'AI가 제시한 확인 사항 없음'));
        for (const finding of report.findings) {
          const block = element('div', undefined, 'health-analysis-finding');
          block.append(element('strong', finding.title), element('p', finding.detail));
          appendSources(block, finding.source_refs, snapshot); findings.append(block);
        }
        const data = fold(panel, '사용한 검진 자료·데이터 한계', 'data');
        section(data, '데이터 한계', report.limitations);
        data.append(element('h5', '사용한 검진 자료'), element('p', '원본 확인창에는 현재 확정 결과가 표시됩니다. 재확정되었다면 분석 당시 버전·값과 다를 수 있습니다.', 'health-analysis-meta'));
        if (!snapshot.documents.length) data.append(element('p', '확정된 검진 자료 없음'));
        for (const doc of snapshot.documents) {
          const line = element('p', `검진일 ${doc.checkup_date || '미상'} · 확정 버전 ${doc.confirmed_revision} · 판독 불가/누락 ${doc.unreadable_items}개 · 제외 ${doc.excluded_items}개 `);
          const button = element('button', '검진표 확인', 'mini-btn'); button.type = 'button';
          button.onclick = () => window.openCheckupReview(doc.doc_id); line.append(button); data.append(line);
        }
      } else if (!state.loading && !state.pending) panel.append(element('p', 'AI 분석을 실행하면 확정된 검진·생활 기록·등록 질환을 함께 분석합니다.'));
      if (!state.record) renderContact(panel, id);
      const notice = fold(panel, '외부 AI 전송 안내', 'notice');
      notice.append(element('p', '최근 30일 생활·접속 기록, 확정 검진 항목, 정규화된 등록 질환, 시스템 점수와 서버가 계산한 변화 자료를 전송합니다. 이름·전화번호·주소·기관명·원문·자유 메모는 전송하지 않습니다. AI 결과는 참고 자료이며 긴급 조치 여부는 복지사가 최종 확인합니다.'));
      if (state.pending || state.loading) {
        const loading = element('div', undefined, 'ai-loading'); loading.setAttribute('role', 'status'); loading.setAttribute('aria-live', 'polite');
        const spinner = element('span', undefined, 'ai-spinner'); spinner.setAttribute('aria-hidden', 'true');
        loading.append(spinner, document.createTextNode(state.pending ? `AI 분석 중… ${window.AIModels?.label(state.pendingModel) || state.pendingModel || ''} · 완료까지 시간이 걸릴 수 있습니다.` : '저장된 분석을 불러오는 중…')); panel.append(loading);
      }
      if (state.error) {
        const error = element('p', state.error, 'ai-error'); error.setAttribute('role', 'alert'); panel.append(error);
        const retry = element('button', '저장 결과 다시 불러오기', 'mini-btn'); retry.type = 'button'; retry.disabled = state.pending || state.loading;
        retry.onclick = () => load(id); panel.append(retry);
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
    finally { state.loading = false; window.updateHealthAnalysisStatus?.(id, state.currentStatus); render(id); }
  }
  function sync() {
    const ids = new Set(Array.from(document.querySelectorAll('[data-life-ai-user-id]')).map(panel => panel.dataset.lifeAiUserId));
    for (const id of ids) { if (!stateFor(id).loaded) load(id); else render(id); }
  }
  window.syncHealthAnalysisStatus = user => {
    const state = stateFor(user.id);
    if (!user.status_as_of || !state.loaded || (state.currentStatus && Date.parse(state.currentStatus.as_of) >= Date.parse(user.status_as_of))) return;
    const labels = {safe:'안전',watch:'주의',warn:'경고',danger:'위험'};
    if (!labels[user.risk]) return;
    state.currentStatus = {score:user.score, level:user.risk.toUpperCase(), label:labels[user.risk], as_of:user.status_as_of, breakdown:user.score_breakdown || []};
  };
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
    finally { state.pending = false; window.updateHealthAnalysisStatus?.(id, state.currentStatus); render(id); window.AIModels?.usageChanged(); }
  };
  new MutationObserver(records => {
    if (records.some(record => Array.from(record.addedNodes).some(node => node.nodeType === 1 &&
      (node.matches('[data-life-ai-user-id], [data-life-ai-button-id]') || node.querySelector('[data-life-ai-user-id], [data-life-ai-button-id]'))))) sync();
  }).observe(document.getElementById('dashboard'), {childList: true, subtree: true});
  sync();
})();
