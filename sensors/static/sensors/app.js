// Energy Monitor – wspólne funkcje stron
window.EM = (function () {
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const num = (v, digits = 0) => Number(v ?? 0).toLocaleString('pl-PL', {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
  const pad = (n) => String(n).padStart(2, '0');
  const dateTime = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;

  // wspólne opcje wykresów Chart.js, kolory pobierane z aktualnego motywu
  function chartOptions(extra = {}) {
    const text = css('--text-2');
    const grid = css('--chart-grid');
    return Object.assign({
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: text, maxTicksLimit: 10, maxRotation: 0 }, grid: { color: grid } },
        y: { beginAtZero: true, ticks: { color: text }, grid: { color: grid } },
      },
    }, extra);
  }

  function readJSON(id) {
    const el = document.getElementById(id);
    return el ? JSON.parse(el.textContent) : null;
  }

  document.addEventListener('DOMContentLoaded', () => {
    const toggle = document.querySelector('.nav-toggle');
    if (toggle) toggle.addEventListener('click', () => document.querySelector('.nav').classList.toggle('open'));
  });

  return { css, num, dateTime, chartOptions, readJSON };
})();
