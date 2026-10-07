(() => {
  const layout = document.getElementById('dashboardLayout');
  const sidebar = document.getElementById('mainSidebar');
  const toggle = document.getElementById('mobile-menu-toggle');
  const backdrop = document.getElementById('mobile-menu-backdrop');
  const media = window.matchMedia('(max-width:768px)');
  let previousFocus = null;
  function close(restoreFocus = true) {
    layout.classList.remove('mobile-menu-open'); backdrop.classList.remove('open');
    toggle.setAttribute('aria-expanded', 'false');
    if (media.matches) sidebar.setAttribute('inert', '');
    if (restoreFocus && previousFocus) previousFocus.focus();
  }
  function open() {
    previousFocus = document.activeElement;
    layout.classList.add('mobile-menu-open'); backdrop.classList.add('open');
    sidebar.removeAttribute('inert'); toggle.setAttribute('aria-expanded', 'true');
    sidebar.querySelector('button').focus();
  }
  toggle.onclick = () => layout.classList.contains('mobile-menu-open') ? close() : open();
  backdrop.onclick = () => close();
  sidebar.querySelector('.sidebar-toggle').addEventListener('click', event => {
    if (media.matches) { event.stopImmediatePropagation(); close(); }
  }, true);
  sidebar.querySelectorAll('.nav button').forEach(button => button.addEventListener('click', () => {
    if (media.matches) {
      close(false);
      const title = document.getElementById('pageTitle'); title.tabIndex = -1; title.focus();
    }
  }));
  document.addEventListener('keydown', event => {
    if (!layout.classList.contains('mobile-menu-open')) return;
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    if (event.key === 'Tab') {
      const buttons = Array.from(sidebar.querySelectorAll('button, a, input')).filter(element => !element.disabled && element.getClientRects().length);
      const first = buttons[0], last = buttons[buttons.length-1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  function resize() { close(false); if (!media.matches) sidebar.removeAttribute('inert'); }
  media.addEventListener('change', resize); resize();
  const labels = ['어르신', '나이', '위험 상태', '건강 상태', '최근 기록', '관리'];
  const elderTable = document.getElementById('tbody').closest('table');
  elderTable.classList.add('elder-cards');
  function labelRows() {
    elderTable.querySelectorAll('.main-row').forEach(row => Array.from(row.cells).forEach((cell, index) => { cell.dataset.label = labels[index]; }));
  }
  new MutationObserver(labelRows).observe(elderTable.querySelector('tbody'), {childList: true}); labelRows();
  document.querySelectorAll('.table:not(.elder-cards)').forEach(table => {
    const wrapper = document.createElement('div'); wrapper.className = 'table-scroll';
    wrapper.tabIndex = 0; wrapper.setAttribute('role', 'region'); wrapper.setAttribute('aria-label', '목록 표 (좌우 스크롤 가능)');
    table.before(wrapper); wrapper.append(table);
  });
  let checking = false;
  async function checkSession() {
    if (registrationMode || checking || document.getElementById('dashboard').style.display !== 'block') return;
    checking = true;
    try {
      const response = await fetch('/api/auth/session');
      if (!response.ok) return;
      const session = await response.json();
      if (!session.valid || session.role !== 'worker') { sessionStorage.clear(); window.location.replace('/login'); }
    } catch (error) { /* 연결 장애 중에는 기존 화면을 유지한다. */ }
    finally { checking = false; }
  }
  setInterval(checkSession, 15000); window.addEventListener('focus', checkSession);
})();
