(() => {
  const sidebar = document.getElementById('sidebar');
  const toggle = document.getElementById('menuToggle');
  const overlay = document.getElementById('sidebarOverlay');
  const closeMenu = () => {
    sidebar?.classList.remove('sidebar-open');
    overlay?.classList.remove('show');
  };
  toggle?.addEventListener('click', () => {
    sidebar?.classList.toggle('sidebar-open');
    overlay?.classList.toggle('show');
  });
  overlay?.addEventListener('click', closeMenu);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeMenu();
  });

  // Keep curriculum choices restricted to the selected programme.
  const programSelect = document.querySelector('[data-curriculum-program-filter]');
  const curriculumSelect = document.querySelector('[data-curriculum-program-options]');
  if (programSelect && curriculumSelect) {
    const filterCurricula = () => {
      const selectedProgram = programSelect.value;
      Array.from(curriculumSelect.options).forEach((option) => {
        if (!option.value) return;
        const matches = option.dataset.programId === selectedProgram;
        option.hidden = !matches;
        option.disabled = !matches;
      });
      const selected = curriculumSelect.selectedOptions[0];
      if (selected && selected.value && selected.disabled) curriculumSelect.value = '';
    };
    programSelect.addEventListener('change', filterCurricula);
    filterCurricula();
  }

  // Suggest an initial term only for a brand-new student. For existing students,
  // require an explicit semester selection rather than guessing progression.
  const enrollmentForm = document.querySelector('[data-student-enrollment-form]');
  if (enrollmentForm && enrollmentForm.dataset.preserveForm !== 'true') {
    const enrollmentType = enrollmentForm.querySelector('[name="enrollment_type"]');
    const semesterSelect = enrollmentForm.querySelector('[name="semester_number"]');
    const hasExistingEnrollment = Number(enrollmentForm.dataset.existingEnrollmentCount || '0') > 0;
    if (semesterSelect && hasExistingEnrollment) {
      semesterSelect.value = '';
    } else {
      const applyInitialSemester = () => {
        if (enrollmentType && semesterSelect) semesterSelect.value = enrollmentType.value === 'DSE' ? '3' : '1';
      };
      enrollmentType?.addEventListener('change', applyInitialSemester);
      applyInitialSemester();
    }
  }

  // Chart uses persisted enrollments supplied by the server, not illustrative values.
  const canvas = document.getElementById('semesterChart');
  const dataNode = document.getElementById('semester-data');
  if (canvas && dataNode && window.Chart) {
    try {
      const data = JSON.parse(dataNode.textContent || '{}');
      new window.Chart(canvas, {
        type: 'bar',
        data: {
          labels: data.labels || [],
          datasets: [{
            label: 'Enrollments',
            data: data.values || [],
            backgroundColor: '#7896e8',
            hoverBackgroundColor: '#496fd0',
            borderRadius: 6,
            borderSkipped: false,
            maxBarThickness: 34,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false }, tooltip: { displayColors: false } },
          scales: {
            x: { grid: { display: false }, border: { display: false }, ticks: { color: '#8691a4', font: { family: 'DM Sans' } } },
            y: { beginAtZero: true, grid: { color: '#edf0f5' }, border: { display: false }, ticks: { precision: 0, color: '#8691a4', font: { family: 'DM Sans' }, padding: 10 } },
          },
        },
      });
    } catch (error) {
      console.error('Enrollment chart could not be initialized.', error);
    }
  }
})();
