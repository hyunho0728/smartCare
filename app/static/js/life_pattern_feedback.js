/* 어르신별 요청 상태를 유지하고 서버에 저장된 결과를 복원한다. */
(() => {
  const states = new Map();
  const stateFor = id => {
    id = String(id);
    if (!states.has(id)) states.set(id, {pending: false, loading: false, loaded: false, record: null, history: [], error: ''});
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
    const meals = d.meals || {};
    return `${dateText(d.recorded_at)} (${d.period}) · 건강 상태 ${d.condition_level} · 아침 ${meals.breakfast || '미상'}/점심 ${meals.lunch || '미상'}/저녁 ${meals.dinner || '미상'} · 혈압 ${d.blood_pressure || '미상'} · 혈당 ${d.blood_sugar ?? '미상'}`;
  }
  function render(id) {
    const state = stateFor(id);
    document.querySelectorAll(`[data-life-ai-user-id="${id}"]`).forEach(panel => {
      panel.replaceChildren(); panel.setAttribute('aria-busy', String(state.pending || state.loading));
      panel.append(element('b', 'AI 건강 종합 분석'));
      if (state.record) {
        const record = state.record, snapshot = record.input_snapshot, report = record.result;
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
        panel.append(element('h5', '종합 요약'), element('p', report.summary));
        panel.append(element('h5', '확인 사항과 근거'));
        if (!report.findings.length) panel.append(element('p', 'AI가 제시한 확인 사항 없음. 자료 부족 여부는 아래 한계를 확인하세요.'));
        for (const finding of report.findings) {
          const block = element('div', undefined, 'health-analysis-finding');
          block.append(element('strong', finding.title), element('p', finding.detail));
          for (const ref of finding.source_refs) {
            const source = snapshot.sources.find(s => s.ref === ref); if (!source) continue;
            const line = element('p', `근거 ${ref}: ${sourceText(source)}`, 'health-analysis-source');
            if (source.doc_id) {
              const button = element('button', `검진표 ${source.doc_id} · ${source.page || '?'}페이지 원본 확인`, 'mini-btn');
              button.type = 'button'; button.addEventListener('click', () => window.openCheckupReview(source.doc_id)); line.append(button);
            }
            block.append(line);
          }
          panel.append(block);
        }
        section(panel, '사회복지사 권장 확인', report.recommended_actions);
        section(panel, '데이터 한계', report.limitations);
        panel.append(element('h5', '사용한 검진 자료'));
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
        status.append(spinner, document.createTextNode(state.pending ? 'AI 분석 중… 완료까지 시간이 걸릴 수 있습니다.' : '저장된 분석을 불러오는 중…')); panel.append(status);
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
      state.record = body.selected; state.history = body.history;
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
    if (!confirm(`${elderName} 어르신의 최근 30일 생활·접속 기록, 확정된 검진 항목, 정규화된 등록 질환을 Gemini로 전송해 분석합니다. 이름·전화번호·주소·기관명·원문·자유 메모는 전송하지 않습니다.\n\n계속 진행하시겠습니까?`)) return;
    state.pending = true; state.error = ''; render(id);
    try {
      const body = await request(`/api/admin/elders/${id}/life-pattern-ai`, {method: 'POST'});
      state.record = body;
      state.history = [{analysis_id: body.analysis_id, analyzed_at: body.analyzed_at}, ...state.history.filter(h => h.analysis_id !== body.analysis_id)].slice(0, 20);
      state.loaded = true;
    } catch (error) { state.error = message(error); }
    finally { state.pending = false; render(id); }
  };
  new MutationObserver(records => {
    if (records.some(record => Array.from(record.addedNodes).some(node => node.nodeType === 1 &&
      (node.matches('[data-life-ai-user-id], [data-life-ai-button-id]') || node.querySelector('[data-life-ai-user-id], [data-life-ai-button-id]'))))) sync();
  }).observe(document.getElementById('dashboard'), {childList: true, subtree: true});
  sync();
})();
