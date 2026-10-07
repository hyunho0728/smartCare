/* 화면이 다시 그려져도 어르신별 요청·기존 결과·실패 상태를 유지한다. */
(() => {
  const states = new Map();
  const stateFor = id => {
    id = String(id);
    if (!states.has(id)) states.set(id, {pending: false, result: sessionStorage.getItem(`life_ai_desc_${id}`), error: ''});
    return states.get(id);
  };
  function render(id) {
    const state = stateFor(id);
    document.querySelectorAll(`[data-life-ai-user-id="${id}"]`).forEach(panel => {
      panel.replaceChildren(); panel.setAttribute('aria-busy', String(state.pending));
      const title = document.createElement('b'); title.textContent = 'AI 생활 패턴 분석'; panel.append(title);
      const result = document.createElement('span');
      result.textContent = state.result || (state.pending ? '' : 'AI 분석을 실행하면 결과가 표시됩니다.'); panel.append(result);
      if (state.pending) {
        const status = document.createElement('div'); status.className = 'ai-loading';
        status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
        const spinner = document.createElement('span'); spinner.className = 'ai-spinner'; spinner.setAttribute('aria-hidden', 'true');
        status.append(spinner, document.createTextNode('AI 분석 중… 완료까지 시간이 걸릴 수 있습니다.')); panel.append(status);
      }
      if (state.error) {
        const error = document.createElement('p'); error.className = 'ai-error'; error.setAttribute('role', 'alert');
        error.textContent = state.error; panel.append(error);
      }
    });
    document.querySelectorAll(`[data-life-ai-button-id="${id}"]`).forEach(button => {
      button.disabled = state.pending;
      button.textContent = state.pending ? '분석 중…' : state.error ? '다시 시도' : state.result ? '재분석' : 'AI 분석';
    });
  }
  function sync() {
    const ids = new Set(Array.from(document.querySelectorAll('[data-life-ai-user-id]')).map(panel => panel.dataset.lifeAiUserId));
    for (const id of ids) render(id);
  }
  window.setLifePatternAnalysisText = (id, text) => {
    const state = stateFor(id); if (!state.result) state.result = text; render(id);
  };
  window.setLifePatternAIButtons = id => render(id);
  window.analyzeLifePatternAI = async (id, elderName) => {
    const state = stateFor(id); if (state.pending) return;
    if (!confirm(`${elderName} 어르신의 이름, 전화번호, 주소를 제외한 생활 패턴 요약 데이터를 Gemini로 전송해 분석합니다.\n\n계속 진행하시겠습니까?`)) return;
    state.pending = true; state.error = ''; render(id);
    try {
      const response = await fetch(`/api/admin/elders/${id}/life-pattern-ai`, {method: 'POST'});
      if (response.status === 401) { sessionStorage.clear(); window.location.replace('/login'); return; }
      const result = await response.json();
      if (!response.ok || !result.success || typeof result.analysis !== 'string') throw new Error(result.message || 'AI 분석에 실패했습니다.');
      state.result = result.analysis; sessionStorage.setItem(`life_ai_desc_${id}`, result.analysis);
    } catch (error) {
      state.error = error instanceof TypeError || error instanceof SyntaxError ? '서버에 연결할 수 없습니다. 다시 시도해주세요.' : error.message;
    } finally { state.pending = false; render(id); }
  };
  new MutationObserver(records => {
    if (records.some(record => Array.from(record.addedNodes).some(node => node.nodeType === 1 &&
      (node.matches('[data-life-ai-user-id], [data-life-ai-button-id]') || node.querySelector('[data-life-ai-user-id], [data-life-ai-button-id]'))))) sync();
  }).observe(document.getElementById('dashboard'), {childList: true, subtree: true});
  sync();
})();
