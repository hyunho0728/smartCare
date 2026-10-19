/* 원본과 대조 가능한 검진표 확인 화면. AI 문자열은 HTML로 삽입하지 않는다. */
(() => {
  const pending = new Set();
  let active = null;
  const dialog = document.createElement('dialog');
  dialog.id = 'checkup-review';
  dialog.innerHTML = `
    <header><h2>건강검진표 판독·확인</h2><button type="button" data-close>닫기</button></header>
    <p data-status role="status" aria-live="polite"></p>
    <p>현재 기저질환: <span data-current-disease></span></p>
    <div data-model-control></div><p data-model-used></p>
    <div class="checkup-review-grid"><section data-original></section><section>
      <button type="button" data-analyze>AI 분석 / 다시 분석</button>
      <div data-editor hidden>
        <p>읽힌 값은 원본과 대조해주세요. 판독 불가 항목은 값 없이 저장됩니다.</p>
        <div data-metadata></div>
        <ul data-issues></ul>
        <div class="checkup-table-scroll"><table><thead><tr>
          <th>검사 항목</th><th>결과</th><th>단위</th><th>참고범위</th><th>판독 불가</th><th>원문 / 페이지</th>
        </tr></thead><tbody data-items></tbody></table></div>
        <section data-disease-section>
          <h3>검진표에 명시된 기저질환</h3>
          <p>원본과 대조한 뒤 추가할 질환을 선택해주세요. 선택한 질환은 확정·저장할 때 현재 목록에 추가됩니다.</p>
          <div data-diseases></div>
        </section>
        <p>문서 이름이 담당 어르신 이름과 동일해야 확정·저장할 수 있습니다.</p>
        <label><input type="checkbox" data-reviewed> 확인 필요 사항을 포함해 원본과 대조했습니다.</label>
        <button type="button" data-confirm>수정 결과 확정·저장</button>
      </div>
      <section data-confirmed></section>
    </section></div>`;
  document.body.append(dialog);
  const find = attr => dialog.querySelector(`[data-${attr}]`);
  window.AIModels?.mount(find('model-control'));
  const status = text => {
    const target = find('status'); target.replaceChildren();
    if (dialog.getAttribute('aria-busy') === 'true') {
      const spinner = document.createElement('span'); spinner.className = 'ai-spinner'; spinner.setAttribute('aria-hidden', 'true');
      target.append(spinner);
    }
    target.append(document.createTextNode(text));
  };
  const busy = value => {
    find('analyze').disabled = value;
    find('confirm').disabled = value;
    dialog.setAttribute('aria-busy', String(value));
    if (!value) find('status').querySelector('.ai-spinner')?.remove();
  };
  const input = (value, label, maxLength = 300) => {
    const element = document.createElement('input');
    element.type = 'text'; element.value = value ?? '';
    element.setAttribute('aria-label', label); element.maxLength = maxLength;
    return element;
  };
  const nullable = element => element.value.trim() || null;
  async function api(url, options = {}) {
    const response = await fetch(url, options);
    let body;
    try { body = await response.json(); }
    catch (error) { throw new Error('서버 응답을 확인할 수 없습니다. 다시 시도해주세요.'); }
    if (!response.ok || !body.success) throw new Error(body.message || '요청에 실패했습니다.');
    return body;
  }
  function render(state, result) {
    if (active !== state) return;
    state.result = result;
    find('current-disease').textContent = result.current_disease || '없음';
    find('model-used').textContent = `판독 모델: ${result.extraction_model || '기록 없음'} · 확정 당시 모델: ${result.confirmed_model || '기록 없음'}`;
    find('editor').hidden = !result.extraction;
    find('metadata').replaceChildren(); find('items').replaceChildren();
    find('issues').replaceChildren(); find('confirmed').replaceChildren();
    find('diseases').replaceChildren(); state.diseases = [];
    find('reviewed').checked = false;
    if (!result.extraction) { status('저장된 판독 결과가 없습니다. AI 분석을 실행해주세요.'); return; }
    const sameRevision = result.confirmed_revision === result.revision;
    const draft = sameRevision && result.confirmed_result ? result.confirmed_result : result.extraction;
    state.metadata = {};
    for (const [key, label, max] of [['patient_name', '이름', 100], ['checkup_date', '검진일 (YYYY-MM-DD)', 100], ['institution', '검진기관', 200]]) {
      const wrapper = document.createElement('label'); wrapper.textContent = label;
      const field = input(draft[key], label, max); state.metadata[key] = field;
      wrapper.append(field); find('metadata').append(wrapper);
    }
    for (const issue of result.validation_issues || []) {
      const li = document.createElement('li'); li.textContent = issue.message; find('issues').append(li);
    }
    state.rows = draft.items.map((item, index) => {
      const tr = document.createElement('tr'); const fields = {};
      for (const key of ['name', 'value', 'unit', 'reference_range']) {
        const td = document.createElement('td');
        fields[key] = input(item[key], `${item.name} ${key}`, key === 'name' || key === 'unit' ? 100 : 300);
        td.append(fields[key]); tr.append(td);
      }
      const td = document.createElement('td'); const unreadable = document.createElement('input');
      unreadable.type = 'checkbox'; unreadable.checked = item.unreadable;
      unreadable.setAttribute('aria-label', `${item.name} 판독 불가`);
      fields.value.disabled = unreadable.checked;
      unreadable.onchange = () => { fields.value.disabled = unreadable.checked; };
      td.append(unreadable); tr.append(td);
      const source = document.createElement('td'); source.textContent = `${item.raw_text} (${item.page}쪽)`;
      tr.append(source); find('items').append(tr);
      return {fields, unreadable, source: result.extraction.items[index]};
    });
    state.diseases = (draft.diseases || []).map((disease, index) => {
      const row = document.createElement('div'); row.className = 'checkup-disease-row';
      const label = document.createElement('label');
      const selected = document.createElement('input'); selected.type = 'checkbox';
      selected.setAttribute('aria-label', `${disease.name} 현재 목록에 추가`);
      label.append(selected, document.createTextNode(' 현재 목록에 추가'));
      const name = input(disease.name, `기저질환 ${index + 1} 질환명`, 100);
      const source = document.createElement('p'); source.textContent = `${disease.raw_text} (${disease.page}쪽)`;
      row.append(label, name, source); find('diseases').append(row);
      return {name, selected, source: result.extraction.diseases[index]};
    });
    if (!state.diseases.length) find('diseases').textContent = '추출된 기저질환이 없습니다. 기존 검진표는 재분석하면 질환 후보를 확인할 수 있습니다.';
    if (result.confirmed_result) {
      const title = document.createElement('h3');
      title.textContent = `저장된 확정 결과 · ${result.confirmed_at} · 담당자 #${result.confirmed_by}`;
      find('confirmed').append(title);
      if (state.expectedRevision != null && state.expectedRevision !== result.confirmed_revision) {
        const notice = document.createElement('p'); notice.className = 'health-reanalysis-note';
        notice.textContent = `분석 당시 확정 버전 ${state.expectedRevision} · 현재 확정 버전 ${result.confirmed_revision}. 이 창에는 현재 확정 결과가 표시됩니다.`;
        find('confirmed').append(notice);
      }
      const identity = document.createElement('p');
      identity.textContent = `이름: ${result.confirmed_result.patient_name || '판독 불가'} / 검진일: ${result.confirmed_result.checkup_date || '미상'} / 기관: ${result.confirmed_result.institution || '미상'}`;
      find('confirmed').append(identity);
      if (!sameRevision) {
        const notice = document.createElement('p'); notice.textContent = '새 판독 결과가 있습니다. 이전 확정값은 유지되며, 다시 확정하면 변경됩니다.';
        find('confirmed').append(notice);
      }
      const list = document.createElement('ul');
      for (const item of result.confirmed_result.items) {
        const li = document.createElement('li');
        li.textContent = `${item.name}: ${item.unreadable ? '판독 불가' : item.value ?? '미상'} ${item.unit || ''} / 참고범위: ${item.reference_range || '미기재'}`;
        list.append(li);
      }
      find('confirmed').append(list);
    }
    status(result.analysis || '판독 결과를 확인해주세요.');
  }
  async function open(docId, shouldAnalyze, options = {}) {
    if (active && (pending.has(active.docId) || active.saving)) {
      if (!dialog.open) dialog.showModal();
      return;
    }
    const state = {docId, result: null, expectedRevision: options.expectedRevision}; active = state;
    find('model-used').textContent = ''; find('current-disease').textContent = '불러오는 중...';
    find('editor').hidden = true; find('confirmed').replaceChildren(); find('original').replaceChildren();
    const originalUrl = `/api/admin/checkup/${docId}/original`;
    const frame = document.createElement('iframe'); frame.src = originalUrl; frame.title = '건강검진표 원본';
    const link = document.createElement('a'); link.href = originalUrl; link.target = '_blank'; link.rel = 'noopener';
    link.textContent = '원본을 새 창에서 보기 (PDF 페이지 이동 가능)';
    find('original').append(link, frame);
    if (!dialog.open) dialog.showModal();
    busy(true); status('저장된 결과를 불러오는 중...');
    try { render(state, await api(`/api/admin/checkup/${docId}/result`)); }
    catch (error) { if (active === state) status(error.message); return; }
    finally { if (active === state) busy(pending.has(docId)); }
    if (shouldAnalyze && active === state) await analyze();
  }
  async function analyze() {
    const state = active;
    if (!state || pending.has(state.docId)) return;
    let model;
    try { model = window.AIModels ? await window.AIModels.getModel() : 'gemini-3.6-flash'; }
    catch (error) { status(error.message); return; }
    if (active !== state || pending.has(state.docId)) return;
    pending.add(state.docId); busy(true); status(`AI 분석 중... ${window.AIModels?.label(model) || model} · 완료까지 시간이 걸릴 수 있습니다.`);
    try {
      const result = await api(`/api/admin/checkup/analyze/${state.docId}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({model})});
      render(state, result);
    } catch (error) { if (active === state) status(`${error.message} 기존 저장 결과는 유지됩니다.`); }
    finally { pending.delete(state.docId); if (active === state) busy(false); window.AIModels?.usageChanged(); }
  }
  async function confirm() {
    const state = active;
    if (!state?.result?.extraction || state.saving) return;
    const result = structuredClone(state.result.extraction);
    for (const [key, field] of Object.entries(state.metadata)) result[key] = nullable(field);
    result.items = state.rows.map(({fields, unreadable, source}) => ({
      ...source, name: fields.name.value.trim(), value: unreadable.checked ? null : nullable(fields.value),
      unit: nullable(fields.unit), reference_range: nullable(fields.reference_range), unreadable: unreadable.checked,
    }));
    result.diseases = state.diseases.map(({name, source}) => ({...source, name: name.value.trim()}));
    const selectedDiseaseIndices = state.diseases.flatMap(({selected}, index) => selected.checked ? [index] : []);
    state.saving = true; busy(true); status('확정 결과 저장 중...');
    try {
      const saved = await api(`/api/admin/checkup/${state.docId}/confirm`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({revision: state.result.revision, result,
          selected_disease_indices: selectedDiseaseIndices,
          issues_reviewed: find('reviewed').checked}),
      });
      render(state, saved); if (active === state) status(selectedDiseaseIndices.length ? '확정 결과를 저장하고 선택한 질환을 현재 목록에 반영했습니다.' : '확정 결과를 저장했습니다.');
      window.dispatchEvent(new CustomEvent('checkup-confirmed'));
    } catch (error) { if (active === state) status(error.message); }
    finally { state.saving = false; if (active === state) busy(false); }
  }
  find('close').onclick = () => dialog.close();
  find('analyze').onclick = analyze; find('confirm').onclick = confirm;
  window.openCheckupReview = (docId, options) => open(docId, false, options);
  window.analyzeCheckupDoc = docId => open(docId, true);
})();
