(() => {
  if (typeof registrationMode !== 'undefined' && registrationMode) return;
  if (!document.querySelector('[data-status-alert-dashboard]')) return;
  let items = [], unreadItems = [], queriedAt = null, backgroundAt = null, initialized = false, pending = false;
  let currentId = null, detailToken = 0, error = '', readError = '', readVersion = 0, detail = null;
  const seen = new Set(), reading = new Set();
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const date = value => value ? value.replace('T', ' ').slice(0,19) : '기록 없음';
  const button = (text, action) => {
    const node = el('button', text, 'mini-btn'); node.type = 'button'; node.addEventListener('click', action); return node;
  };
  const navButton = document.querySelector('.nav button[onclick*="alerts"]');
  if (navButton) {
    const badge = el('span', '0'); badge.dataset.statusAlertCount = ''; badge.setAttribute('aria-label', '미읽음 알림 수'); navButton.append(badge);
  }
  const notice = el('div', undefined, 'status-alert-notice'); notice.hidden = true;
  notice.setAttribute('role', 'status'); document.body.append(notice);
  let noticeTimer;
  const dialog = el('dialog', undefined, 'status-alert-dialog'); dialog.id = 'status-alert-detail';
  dialog.setAttribute('aria-labelledby', 'status-alert-detail-title');
  const header = el('header'), title = el('h3', '상태 변화 알림'); title.id = 'status-alert-detail-title';
  header.append(title, button('닫기', () => dialog.close()));
  const content = el('div'); dialog.append(header, content); document.body.append(dialog);
  dialog.addEventListener('close', () => { currentId = null; detailToken += 1; });
  dialog.addEventListener('click', event => {
    if (event.target === dialog) {
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    }
  });

  async function request(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok || !data.success) {
      if (response.status === 401) window.location.replace('/login');
      throw new Error(data.message || '요청에 실패했습니다.');
    }
    return data;
  }
  function transition(a) {
    return `${a.previous_status.label} ${a.previous_status.score}점 → ${a.current_status.label} ${a.current_status.score}점`;
  }
  function cause(a) {
    return a.changes.length ? a.changes.map(c => `${c.item}: ${c.previous_points} → ${c.current_points}점`).join(' · ') : '등급 변화와 당시 점수 산정 근거를 확인해주세요.';
  }
  function card(a) {
    const node = el('article', undefined, `status-alert-card${a.is_read ? ' is-read' : ''}`);
    node.dataset.statusAlertId = a.alert_id;
    node.append(el('h4', `${a.user_name} · ${a.is_read ? '읽음' : '미읽음'}`), el('strong', transition(a)),
      el('p', `감지 ${date(a.detected_at)}`, 'status-alert-meta'), el('p', cause(a)));
    const actions = el('div', undefined, 'status-alert-actions');
    actions.append(button('원인·관련 기록', () => open(a.alert_id)));
    if (!a.is_read) {
      const read = button('읽음 처리', () => markRead(a.alert_id)); read.disabled = reading.has(a.alert_id); actions.append(read);
    }
    node.append(actions); return node;
  }
  function render() {
    const update = () => {
      for (const [selector, list] of [
        ['[data-status-alert-dashboard]', unreadItems.slice(0,3)],
        ['[data-status-alert-recent]', unreadItems],
        ['[data-status-alert-history]', items]]) {
        const target = document.querySelector(selector);
        const signature = JSON.stringify({list, initialized, reading: [...reading]});
        if (target && target.dataset.renderSignature !== signature) {
          target.replaceChildren(...(list.length ? list.map(card) : [el('p', initialized ? '상태 변화 알림이 없습니다.' : '알림을 불러오는 중…', 'status-alert-meta')]));
          target.dataset.renderSignature = signature;
        }
      }
      document.querySelectorAll('[data-status-alert-query]').forEach(n => {
        n.textContent = `조회 ${date(queriedAt)} · 정기 검사 성공 ${date(backgroundAt)} · 감지 시각은 실제 악화 발생 시각과 다를 수 있습니다.`;
      });
      document.querySelectorAll('[data-status-alert-error]').forEach(n => { n.textContent = [error,readError].filter(Boolean).join(' '); });
      document.querySelectorAll('[data-status-alert-refresh]').forEach(n => { n.disabled = pending; });
    };
    if (typeof preserveViewportDuringRender === 'function') preserveViewportDuringRender(update); else update();
  }
  async function refresh() {
    if (pending || document.hidden) return;
    pending = true;
    const version = readVersion;
    let retry = false;
    document.querySelectorAll('[data-status-alert-refresh]').forEach(n => { n.disabled = true; });
    try {
      const data = await request('/api/admin/status-alerts');
      if (version !== readVersion) { retry = true; return; }
      const received = [...data.data, ...data.unread_data];
      const newestSeen = Math.max(0, ...seen);
      const fresh = data.data.filter(a => !seen.has(a.alert_id) && a.alert_id > newestSeen);
      if (initialized && fresh.length) {
        notice.textContent = `새 상태 변화 알림 ${fresh.length}개가 있습니다. 알림 및 기록 관리에서 확인해주세요.`;
        notice.hidden = false; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { notice.hidden = true; }, 8000);
      }
      received.forEach(a => seen.add(a.alert_id));
      items = data.data; unreadItems = data.unread_data;
      queriedAt = data.queried_at; backgroundAt = data.last_background_success_at;
      initialized = true; error = '';
      document.querySelectorAll('[data-status-alert-count]').forEach(n => { n.textContent = data.unread_count; });
    } catch (failure) { error = `갱신 실패: ${failure.message} 마지막 성공 자료를 유지합니다.`; }
    finally { pending = false; render(); if (retry) queueMicrotask(refresh); }
  }
  async function markRead(id) {
    if (reading.has(id)) return;
    readVersion += 1; readError = '';
    reading.add(id); render(); if (detail?.alert_id === id) renderDetail(detail);
    let success = false;
    try {
      const response = await request(`/api/admin/status-alerts/${id}/read`, {method:'POST'});
      items = items.map(a => a.alert_id === id ? response.data : a);
      unreadItems = unreadItems.filter(a => a.alert_id !== id);
      document.querySelectorAll('[data-status-alert-count]').forEach(n => { n.textContent = response.unread_count; });
      if (detail?.alert_id === id) detail = {...detail, ...response.data};
      success = true;
    } catch (failure) {
      readError = failure.message;
    } finally {
      readVersion += 1;
      reading.delete(id); render();
      if (dialog.open && detail?.alert_id === id) renderDetail(detail);
      if (success) refresh();
    }
  }
  async function elder(id) {
    dialog.close();
    nav('live', document.querySelector('.nav button[onclick*="live"]'));
    await loadEldersData();
    const row = document.querySelector(`.detail-row[data-user-id="${id}"]`);
    if (row) {
      sessionStorage.setItem('openDetailUserId', String(id)); row.classList.add('open');
      row.scrollIntoView({block:'start', behavior:'instant'});
    }
  }
  function renderDetail(a) {
    content.replaceChildren(el('h4', `${a.user_name} · ${transition(a)}`),
      el('p', `감지 시각 ${date(a.detected_at)} · 실제 악화 발생 시각은 확인되지 않았습니다.`, 'status-alert-meta'),
      el('p', `점수 변화 ${a.score_difference > 0 ? '+' : ''}${a.score_difference}점 · 점수는 높을수록 안전합니다. 검진 수치나 AI 설명은 이 점수를 변경하지 않습니다.`),
      el('h4', '변경된 점수 산정 근거'), el('p', cause(a)),
      el('p', '점수의 0~100 보정 때문에 감점 변화 합계와 실제 점수 차이가 다를 수 있습니다.', 'status-alert-meta'));
    for (const [key,label] of [['previous','변경 전'],['current','변경 후']]) {
      const snapshot = a.snapshot[key];
      content.append(el('h4', `${label} · 기준 ${date(snapshot.status.as_of)}`));
      const list = el('ul');
      snapshot.status.breakdown.forEach(b => list.append(el('li', `${b.item}: ${b.points}점`)));
      content.append(list);
      snapshot.pattern_insights.forEach(p => content.append(el('p', `${p.title}: ${p.detail}`)));
      const records = el('details'); records.append(el('summary', `${label} 관련 기록 보기`));
      snapshot.health_records.forEach(r => records.append(el('p', `건강 #${r.record_id} · ${date(r.recorded_at)} · 대상 날짜 ${r.target_date} · 건강 상태 ${r.condition_level} · 아침 ${r.breakfast}/점심 ${r.lunch}/저녁 ${r.dinner} · 혈압 ${r.blood_pressure ?? '미상'} · 혈당 ${r.blood_sugar ?? '미상'}`, 'status-alert-record')));
      snapshot.login_records.forEach(r => records.append(el('p', `접속 #${r.record_id} · ${date(r.at)}`, 'status-alert-record')));
      if (!snapshot.health_records.length && !snapshot.login_records.length) records.append(el('p', '관련 기록 없음'));
      records.append(el('p', `관련 기록은 당시 최근 ${snapshot.record_limit}개까지 보존합니다. 기록 누락은 정상 상태를 뜻하지 않습니다.`, 'status-alert-meta'));
      content.append(records);
    }
    const actions = el('div', undefined, 'status-alert-actions');
    actions.append(button('어르신 현재 상세 보기', () => elder(a.user_id)));
    if (!a.is_read) {
      const read = button('읽음 처리', () => markRead(a.alert_id)); read.disabled = reading.has(a.alert_id); actions.append(read);
    } else actions.append(el('p', `읽음 ${date(a.read_at)}`));
    content.append(actions);
    if (error || readError) content.append(el('p', [error,readError].filter(Boolean).join(' '), 'status-alert-error'));
  }
  async function open(id) {
    const token = ++detailToken; currentId = id; detail = null;
    content.replaceChildren(el('p', '알림 상세를 불러오는 중…', 'status-alert-meta'));
    if (!dialog.open) dialog.showModal();
    try {
      const response = await request(`/api/admin/status-alerts/${id}`);
      if (token !== detailToken || currentId !== id || !dialog.open) return;
      detail = response.data; renderDetail(detail);
    } catch (failure) {
      if (token !== detailToken || !dialog.open) return;
      content.replaceChildren(el('p', failure.message, 'status-alert-error'), button('다시 시도', () => open(id)));
    }
  }
  document.querySelectorAll('[data-status-alert-refresh]').forEach(n => n.addEventListener('click', refresh));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.StatusAlerts = {refresh, open};
  render(); refresh(); setInterval(refresh, 10000);
})();
