/* SearchPipe 前端工具 —— 无外部依赖
   主题切换 / fetch 封装 / SVG 图表 / 剪贴板复制 */
(function () {
  'use strict';

  /* ---------- 主题 ---------- */
  function getTheme() {
    return document.documentElement.getAttribute('data-theme') || 'light';
  }
  function setTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem('searchpipe-theme', t); } catch (e) {}
    document.querySelectorAll('.theme-toggle .tt-label').forEach(function (el) {
      el.textContent = t === 'dark' ? '浅色模式' : '深色模式';
    });
    document.querySelectorAll('.theme-toggle .tt-icon').forEach(function (el) {
      el.innerHTML = t === 'dark' ? ICON_SUN : ICON_MOON;
    });
  }
  function toggleTheme() { setTheme(getTheme() === 'dark' ? 'light' : 'dark'); }

  var ICON_SUN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4"/></svg>';
  var ICON_MOON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';

  /* ---------- fetch 封装（同源 cookie） ---------- */
  async function api(path, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    var r;
    try {
      r = await fetch(path, opts);
    } catch (e) {
      throw new Error('网络错误，请稍后重试');
    }
    var data = null;
    var text = await r.text();
    if (text) { try { data = JSON.parse(text); } catch (e) { data = null; } }
    if (!r.ok) {
      var detail = (data && data.detail) || ('请求失败 (' + r.status + ')');
      if (r.status === 401) { window.location.href = '/dashboard/login'; }
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return data;
  }

  /* ---------- 复制 ---------- */
  function copy(text, btn) {
    function done() {
      if (!btn) return;
      var old = btn.textContent;
      btn.textContent = '已复制 ✓';
      setTimeout(function () { btn.textContent = old; }, 1500);
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done);
    } else {
      var ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); } catch (e) {}
      document.body.removeChild(ta); done();
    }
  }

  // 代码块一键复制：.code-block[data-copy] 自动挂按钮
  function initCopyBlocks() {
    document.querySelectorAll('.code-block[data-copy]').forEach(function (block) {
      if (block.querySelector('.copy-btn')) return;
      var btn = document.createElement('button');
      btn.className = 'copy-btn';
      btn.textContent = '复制';
      btn.onclick = function () {
        copy(block.querySelector('code') ? block.querySelector('code').textContent : block.textContent, btn);
      };
      block.appendChild(btn);
    });
  }

  /* ---------- 服务条款勾选框 ----------
     未勾选时用 setCustomValidity 给出明确提示（覆盖浏览器原生"请勾选此框"），
     勾选后清空自定义校验。login.html / register.html 的 #agree_terms 自动生效。 */
  var TERMS_REQUIRED_MSG = '请同意《服务条款》';
  function initTermsCheckbox() {
    var box = document.getElementById('agree_terms');
    if (!box) return;
    function sync() {
      box.setCustomValidity(box.checked ? '' : TERMS_REQUIRED_MSG);
    }
    box.addEventListener('change', sync);
    sync();
  }

  /* ---------- HTML 转义 ---------- */
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* ---------- SVG 柱状图（用量曲线） ----------
     renderBarChart(el, points, {labelKey, series:[{key, color, name}]})
     points: [{bucket:'2026-08-01T00:00:00', count:12, credits:24, ...}] */
  function renderBarChart(el, points, opts) {
    opts = opts || {};
    var key = opts.key || 'count';
    if (!points || !points.length) {
      el.innerHTML = '<div class="empty">暂无用量数据</div>';
      return;
    }
    var W = 720, H = 220, padL = 44, padB = 34, padT = 14, padR = 10;
    var iw = W - padL - padR, ih = H - padT - padB;
    var max = Math.max.apply(null, points.map(function (p) { return p[key]; }).concat([1]));
    max = niceCeil(max);
    var n = points.length;
    var bw = Math.min(38, (iw / n) * 0.62);
    var step = iw / n;
    var accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#4f46e5';
    var border = getComputedStyle(document.documentElement).getPropertyValue('--border').trim() || '#e5e7eb';
    var faint = getComputedStyle(document.documentElement).getPropertyValue('--faint').trim() || '#9aa3b2';

    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" preserveAspectRatio="xMidYMid meet" role="img">';
    // 横向网格线 + y 轴刻度（4 档）
    for (var i = 0; i <= 4; i++) {
      var y = padT + ih - (ih * i / 4);
      var v = Math.round(max * i / 4);
      svg += '<line x1="' + padL + '" y1="' + y + '" x2="' + (W - padR) + '" y2="' + y + '" stroke="' + border + '" stroke-width="1"' + (i === 0 ? '' : ' stroke-dasharray="3 4"') + '/>';
      svg += '<text x="' + (padL - 8) + '" y="' + (y + 4) + '" text-anchor="end" font-size="10" fill="' + faint + '">' + v + '</text>';
    }
    // 柱 + x 轴标签（稀疏显示）
    var labelEvery = Math.ceil(n / 8);
    points.forEach(function (p, i) {
      var x = padL + step * i + (step - bw) / 2;
      var h = Math.max(1, ih * (p[key] / max));
      var y = padT + ih - h;
      svg += '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="3" fill="' + accent + '" opacity="0.88"><title>' + escAttr(fmtBucket(p.bucket, opts.granularity)) + '：' + p[key] + '</title></rect>';
      if (i % labelEvery === 0) {
        svg += '<text x="' + (padL + step * i + step / 2) + '" y="' + (H - 10) + '" text-anchor="middle" font-size="10" fill="' + faint + '">' + esc(fmtBucketShort(p.bucket, opts.granularity)) + '</text>';
      }
    });
    svg += '</svg>';
    el.innerHTML = svg;
  }

  function niceCeil(v) {
    if (v <= 5) return 5;
    var mag = Math.pow(10, Math.floor(Math.log10(v)));
    return Math.ceil(v / mag) * mag;
  }
  function fmtBucket(iso, gran) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (gran === 'hour') return (d.getMonth() + 1) + '-' + d.getDate() + ' ' + d.getHours() + ':00';
    return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate();
  }
  function fmtBucketShort(iso, gran) {
    if (!iso) return '';
    var d = new Date(iso);
    if (gran === 'hour') return d.getHours() + ':00';
    return (d.getMonth() + 1) + '/' + d.getDate();
  }
  function escAttr(s) { return String(s).replace(/"/g, '&quot;'); }

  /* ---------- 导出 ---------- */
  window.AIS = {
    api: api,
    copy: copy,
    esc: esc,
    renderBarChart: renderBarChart,
    toggleTheme: toggleTheme,
    initCopyBlocks: initCopyBlocks,
  };

  document.addEventListener('DOMContentLoaded', function () {
    initCopyBlocks();
    initTermsCheckbox();
    document.querySelectorAll('.theme-toggle').forEach(function (b) {
      b.addEventListener('click', toggleTheme);
    });
    setTheme(getTheme()); // 同步按钮文案/图标
  });
})();
