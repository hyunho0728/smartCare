/* 회원가입과 대리 등록에서 사용하는 기저질환 복수 선택. */
(() => {
  const choices = group => [...group.querySelectorAll('input[type=checkbox]')];
  function updateOther(group, focus = false) {
    const other = document.getElementById(group.dataset.other);
    const selected = choices(group).some(input => input.value === '기타' && input.checked);
    other.hidden = !selected;
    other.required = selected;
    if (!selected) other.value = '';
    else if (focus) other.focus();
  }
  for (const group of document.querySelectorAll('[data-disease-selection]')) {
    group.addEventListener('change', event => {
      const changed = event.target;
      if (!changed.matches('input[type=checkbox]')) return;
      if (changed.checked) {
        for (const input of choices(group)) {
          if (changed.value === '없음' && input !== changed) input.checked = false;
          else if (changed.value !== '없음' && input.value === '없음') input.checked = false;
        }
      }
      updateOther(group, changed.value === '기타' && changed.checked);
    });
    updateOther(group);
  }
  window.DiseaseSelection = {
    reset(id) {
      const group = document.getElementById(id);
      for (const input of choices(group)) input.checked = input.value === '없음';
      updateOther(group);
    },
    read(id) {
      const group = document.getElementById(id);
      const selected = choices(group).filter(input => input.checked).map(input => input.value);
      if (!selected.length) throw new Error('기저질환 또는 없음을 선택해주세요.');
      if (selected.includes('없음')) return {hasDisease: false, note: '없음'};
      const names = selected.filter(value => value !== '기타');
      if (selected.includes('기타')) {
        const otherNames = document.getElementById(group.dataset.other).value.split(',').map(value => value.trim()).filter(Boolean);
        if (!otherNames.length || otherNames.some(value => value === '없음' || value === '기타')) {
          throw new Error('기타 질환명을 직접 입력해주세요. 여러 질환은 쉼표로 구분해주세요.');
        }
        names.push(...otherNames);
      }
      return {hasDisease: true, note: [...new Set(names.map(value => value.trim()))].join(', ')};
    },
  };
})();
