(() => {
  const form = document.getElementById('login-form');
  const role = document.getElementById('worker-role');
  const phone = document.getElementById('phone');
  const id = document.getElementById('worker-id');
  const password = document.getElementById('worker-password');
  const error = document.getElementById('login-error');
  const status = document.getElementById('login-status');
  const submit = document.getElementById('login-submit');
  const register = document.getElementById('register-link');
  let pending = false;
  function formatPhone() {
    const digits = phone.value.replace(/\D/g, '').slice(0, 11);
    phone.value = digits.length > 7
      ? `${digits.slice(0, 3)}-${digits.slice(3, 7)}-${digits.slice(7)}`
      : digits.length > 3 ? `${digits.slice(0, 3)}-${digits.slice(3)}` : digits;
  }
  phone.addEventListener('input', formatPhone);
  phone.addEventListener('change', formatPhone);
  function configure() {
    const worker = role.checked;
    document.getElementById('user-fields').hidden = worker;
    document.getElementById('worker-fields').hidden = !worker;
    phone.disabled = worker; phone.required = !worker;
    id.disabled = !worker; id.required = worker;
    password.disabled = !worker; password.required = worker;
    document.getElementById('role-description').textContent = worker ? '사회복지사 건강 모니터링 서비스' : '어르신 안부 확인 서비스';
    register.href = worker ? '/register/worker' : '/register/user';
    register.textContent = worker ? '사회복지사 회원가입' : '처음 오셨나요? 어르신 등록';
  }
  role.addEventListener('change', () => {
    phone.value = id.value = password.value = ''; error.textContent = status.textContent = '';
    configure(); (role.checked ? id : phone).focus();
  });
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (pending) return;
    const worker = role.checked;
    if (!worker) formatPhone();
    const payload = worker ? {admin_id: id.value.trim(), password: password.value} : {phone_number: phone.value.trim()};
    if (!worker && phone.value.replace(/\D/g, '').length < 9) {
      error.textContent = '전화번호를 올바르게 입력해주세요.'; return;
    }
    pending = true; error.textContent = ''; status.textContent = '로그인 중…';
    form.setAttribute('aria-busy', 'true');
    for (const field of [role, phone, id, password, submit]) field.disabled = true;
    register.setAttribute('aria-disabled', 'true');
    try {
      const response = await fetch(worker ? '/api/admin/login' : '/api/user/login', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok || !result.success) throw new Error(result.message || '로그인에 실패했습니다.');
      // 앞 사용자의 임시 분석 결과와 탭 상태를 다음 계정에 노출하지 않는다.
      sessionStorage.clear();
      window.location.replace(worker ? '/admin' : '/user');
    } catch (failure) {
      error.textContent = failure instanceof TypeError || failure instanceof SyntaxError ? '서버에 연결할 수 없습니다. 다시 시도해주세요.' : failure.message;
      status.textContent = ''; pending = false; form.setAttribute('aria-busy', 'false');
      role.disabled = submit.disabled = false; register.removeAttribute('aria-disabled'); configure();
    }
  });
  register.addEventListener('click', event => { if (pending) event.preventDefault(); });
  configure();
})();
