(function () {
  'use strict';

  var bridge = (window.chrome && window.chrome.webview) ? window.chrome.webview : null;
  function post(msg) { try { if (bridge) bridge.postMessage(msg); } catch (e) { } }
  function log(msg) { try { post({ type: 'log', message: String(msg) }); } catch (e) { } }
  function $(id) { return document.getElementById(id); }

  window.addEventListener('error', function (ev) {
    log('JS错误: ' + ev.message + ' @ ' + (ev.filename || '?') + ':' + ev.lineno);
  });
  window.addEventListener('unhandledrejection', function (ev) {
    log('Promise拒绝: ' + (ev.reason && ev.reason.message ? ev.reason.message : ev.reason));
  });

  var LEVEL_TEXT = { top: '置顶', normal: '普通', desk: '贴桌面' };
  var LEVEL_ORDER = ['top', 'normal', 'desk'];
  var THEME_ORDER = ['light', 'dark', 'system'];

  /* 预置分类图案：书本 / 电脑 / 文档 / 待办 / 实验 / 生活 等常用场景 */
  var MARKS_EMOJI = ['📚', '💻', '📄', '✅', '📝', '🎯', '💡', '🧪', '🔧', '🎨',
    '📅', '⏰', '💰', '🛒', '🏠', '🍚', '🏃', '🎵',
    '✈️', '🧳', '📞', '🎓', '❤️', '⭐', '🌱', '🧠', '🔒'];

  /* 预置分类颜色 */
  var MARKS_COLOR = ['#d0534a', '#e08a3c', '#d3ad35', '#5aa469',
    '#3f8fbf', '#6f6fc0', '#a86fbf', '#8c8c86', '#4aa3a0'];

  /* 日期 / 时间 / 地点识别。地点优先，其次日期，最后时间，重叠时高优先级者胜 */
  var RE_PLACE = /@[\u4e00-\u9fa5A-Za-z0-9]{1,12}|[A-Za-z]?\d{1,3}\s*号?楼|[A-Z]{0,2}\d{3,4}\s*室|图书馆|会议室|教学楼|实验楼|食堂|宿舍|体育馆|报告厅|办公室|自习室|操场|机房|快递站/g;
  var RE_DATE = /\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}月\d{1,2}[日号]|(?:今|明|后|昨|前)天|(?:本|下|上|这)(?:周|星期)[一二三四五六日天]?|(?:周|星期|礼拜)[一二三四五六日天]|\d{1,2}\/\d{1,2}(?![\d\/])/g;
  var RE_TIME = /\d{1,2}[:：]\d{2}(?:\s*[-~–—]\s*\d{1,2}[:：]\d{2})?|(?:上午|下午|中午|晚上|早上|凌晨|傍晚)\s*\d{1,2}\s*[点时](?:\s*\d{1,2}\s*分|\s*半)?|\d{1,2}\s*[点时](?:\s*\d{1,2}\s*分|\s*半)/g;
  // 截止日期：ddl{任意内容}。花括号在渲染时被涂成透明，源码里保留。
  var RE_DDL = /ddl\s*\{[^}\n]*\}/gi;
  // 刚打完 ddl（还没补花括号）
  var RE_DDL_TAIL = /(?:^|[^A-Za-z])ddl$/i;

  var state = {
    notes: [],
    contents: new Map(),
    scroll: new Map(),
    activeId: null,
    vditor: null,
    suppress: false,
    saveTimer: 0,
    dirty: false,
    booted: false,
    renaming: false,
    filterMark: '*',
    hasCats: false,
    tab: 'notes',
    reminders: [],
    remindHit: null,
    remindMute: {},
    ignored: {},
    linkHits: [],
    decoTimer: 0,
    pillRaf: 0,
    paletteFor: null,
    ddlBusy: false,
    settings: {
      theme: 'system',
      fontSize: 14,
      level: 'top',
      systemDark: false,
      sidebar: true,
      fixedTop: true,
      autoHighlight: true,
      autoLink: true
    },
    hotkeyOk: true
  };

  /* ---------------- 分类标记 ---------------- */

  function parseMark(mark) {
    if (!mark) return { kind: 'none', value: '' };
    if (mark.indexOf('emoji:') === 0) return { kind: 'emoji', value: mark.slice(6) };
    if (mark.indexOf('color:') === 0) return { kind: 'color', value: mark.slice(6) };
    return { kind: 'none', value: '' };
  }

  function markLabel(mark) {
    var m = parseMark(mark);
    if (m.kind === 'emoji') return m.value;
    if (m.kind === 'color') return '●';
    return '未分类';
  }

  function findNote(id) {
    for (var i = 0; i < state.notes.length; i++) {
      if (state.notes[i].id === id) return state.notes[i];
    }
    return null;
  }

  /* ---------------- 主题 ---------------- */

  function isDark() {
    if (state.settings.theme === 'dark') return true;
    if (state.settings.theme === 'light') return false;
    return !!state.settings.systemDark;
  }

  function applyTheme() {
    var dark = isDark();
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    if (state.vditor) {
      try {
        state.vditor.setTheme(dark ? 'dark' : 'classic',
          dark ? 'dark' : 'light',
          dark ? 'github-dark' : 'github');
      } catch (e) { }
    }
    updateThemeBtn();
  }

  function applyFontSize() {
    document.documentElement.style.setProperty('--fs-editor', state.settings.fontSize + 'px');
  }

  /* ---------------- 编辑器 ---------------- */

  function ensureEditor(done) {
    if (state.vditor) { done(); return; }
    var dark = isDark();
    state.vditor = new Vditor('editor', {
      cdn: '/vditor',
      mode: 'ir',
      lang: 'zh_CN',
      theme: dark ? 'dark' : 'classic',
      icon: 'ant',
      cache: { enable: false },
      toolbar: [],
      counter: { enable: false },
      resize: { enable: false },
      outline: { enable: false },
      height: '100%',
      minHeight: 0,
      tab: '  ',
      value: '',
      preview: {
        math: { engine: 'KaTeX', inlineDigit: true },
        hljs: { enable: true, lineNumber: false, style: dark ? 'github-dark' : 'github' },
        theme: { current: dark ? 'dark' : 'light' },
        markdown: { toc: false, mark: true, autoSpace: true, footnotes: true },
        actions: []
      },
      input: onEditorInput,
      after: function () {
        log('vditor ready');
        applyFontSize();
        done();
      }
    });
  }

  function editorRoot() {
    return document.querySelector('.vditor-ir pre.vditor-reset');
  }

  function onEditorInput(value) {
    if (state.suppress || !state.activeId) return;
    state.contents.set(state.activeId, value);
    state.dirty = true;
    setStatus('正在输入…');
    updateCount(value);
    scheduleSave();
    scheduleDecorate(420);
  }

  function scheduleSave() {
    clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(flushSave, 600);
  }

  function flushSave() {
    clearTimeout(state.saveTimer);
    if (!state.dirty || !state.activeId) return;
    var id = state.activeId;
    var content = state.contents.get(id) || '';
    state.dirty = false;
    post({ type: 'save', id: id, content: content });
  }

  /** 统一改写正文：保留滚动位置，标脏后立即落盘。 */
  function setEditorValue(v, saveNow) {
    if (!state.activeId || !state.vditor) return;
    var before = editorRoot();
    var st = before ? before.scrollTop : 0;
    state.suppress = true;
    try { state.vditor.setValue(v, true); } catch (e) { log('setValue 失败 ' + e.message); }
    state.contents.set(state.activeId, v);
    state.scroll.set(state.activeId, st);
    state.dirty = true;
    updateCount(v);
    setTimeout(function () {
      state.suppress = false;
      var r = editorRoot();
      if (r) r.scrollTop = st;
      decorate();
    }, 70);
    if (saveNow) {
      state.dirty = true;
      flushSave();
    }
  }

  function loadIntoEditor() {
    if (!state.activeId) {
      showEmpty(true);
      renderList();
      return;
    }
    showEmpty(false);
    state.suppress = true;
    clearPills();
    clearHighlights();
    ensureEditor(function () {
      var id = state.activeId;
      var content = state.contents.get(id) || '';
      try { state.vditor.setValue(content, true); } catch (e) { }
      updateCount(content);
      setTimeout(function () {
        state.suppress = false;
        var r = editorRoot();
        if (r) r.scrollTop = state.scroll.get(id) || 0;
        decorate();
      }, 80);
      renderList();
    });
  }

  function showEmpty(show) {
    $('emptyState').hidden = !show;
    $('editor').style.visibility = show ? 'hidden' : 'visible';
    if (show) { clearPills(); clearHighlights(); }
  }

  /* ---------------- 便签列表 ---------------- */

  function sortedNotes() {
    var arr = state.notes.slice();
    // 置顶优先；Array.prototype.sort 在 Chromium 中稳定，其余保持原顺序
    arr.sort(function (a, b) {
      var pa = a.pin ? 1 : 0, pb = b.pin ? 1 : 0;
      return pb - pa;
    });
    return arr;
  }

  function filteredNotes() {
    var f = state.filterMark;
    return sortedNotes().filter(function (n) {
      if (f === '*') return true;
      if (f === '') return !n.mark;
      return n.mark === f;
    });
  }

  function renderCats() {
    var bar = $('catBar');
    if (!bar) return;

    var seen = {}, order = [], unmarked = 0;
    state.notes.forEach(function (n) {
      var k = n.mark || '';
      if (!k) { unmarked++; return; }
      if (!seen[k]) { seen[k] = 0; order.push(k); }
      seen[k]++;
    });

    bar.innerHTML = '';
    state.hasCats = order.length > 0;
    syncCatBar();
    if (!state.hasCats) return;

    if (state.filterMark !== '*' && state.filterMark !== '' && !seen[state.filterMark])
      state.filterMark = '*';

    bar.appendChild(makeCatChip('*', '全部'));
    if (unmarked) bar.appendChild(makeCatChip('', '未分类'));
    order.forEach(function (k) { bar.appendChild(makeCatChip(k, null)); });
  }

  function makeCatChip(key, text) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'cat-chip' + (state.filterMark === key ? ' is-on' : '');
    if (text !== null) {
      b.textContent = text;
    } else {
      var m = parseMark(key);
      if (m.kind === 'color') {
        var dot = document.createElement('span');
        dot.className = 'dot';
        dot.style.cssText = 'display:inline-block;width:7px;height:7px;border-radius:50%;background:' + m.value;
        b.appendChild(dot);
      } else {
        b.textContent = m.value;
      }
      b.title = '只看该分类';
    }
    b.addEventListener('click', function () {
      state.filterMark = key;
      renderCats();
      renderList();
    });
    return b;
  }

  function renderList() {
    if (state.renaming) return;
    var box = $('noteList');
    box.innerHTML = '';

    var list = filteredNotes();
    if (!list.length) {
      var hint = document.createElement('div');
      hint.className = 'note-empty';
      hint.textContent = state.notes.length ? '该分类下暂无便签' : '还没有便签';
      box.appendChild(hint);
      return;
    }

    list.forEach(function (n) {
      var item = document.createElement('div');
      item.className = 'note-item'
        + (n.id === state.activeId ? ' is-active' : '')
        + (n.pin ? ' is-pinned' : '');
      item.dataset.id = n.id;

      var mk = parseMark(n.mark);
      var markBtn = document.createElement('button');
      markBtn.type = 'button';
      markBtn.className = 'note-mark' + (mk.kind === 'none' ? ' is-none' : '');
      markBtn.title = '设置分类标记';
      if (mk.kind === 'color') {
        var dot = document.createElement('span');
        dot.className = 'dot';
        dot.style.background = mk.value;
        markBtn.appendChild(dot);
      } else {
        markBtn.textContent = mk.kind === 'emoji' ? mk.value : '○';
      }
      markBtn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        openPalette(n.id, markBtn);
      });

      var title = document.createElement('span');
      title.className = 'note-title';
      title.textContent = n.title || '未命名便签';
      title.title = n.title || '未命名便签';

      var pin = document.createElement('button');
      pin.type = 'button';
      pin.className = 'note-pin' + (n.pin ? ' is-on' : '');
      pin.title = n.pin ? '取消置顶' : '置顶到列表最上方';
      pin.textContent = n.pin ? '▲' : '△';
      pin.addEventListener('click', function (ev) {
        ev.stopPropagation();
        togglePin(n.id);
      });

      var del = document.createElement('button');
      del.type = 'button';
      del.className = 'note-del';
      del.title = '删除';
      del.textContent = '×';
      del.addEventListener('click', function (ev) {
        ev.stopPropagation();
        deleteNote(n.id);
      });

      item.appendChild(markBtn);
      item.appendChild(title);
      item.appendChild(pin);
      item.appendChild(del);
      item.addEventListener('click', function () { selectNote(n.id); });
      item.addEventListener('dblclick', function (ev) {
        ev.stopPropagation();
        startRename(item, n);
      });
      box.appendChild(item);
    });
  }

  function selectNote(id) {
    if (id === state.activeId) return;
    var cur = editorRoot();
    if (cur && state.activeId) state.scroll.set(state.activeId, cur.scrollTop);
    flushSave();
    state.activeId = id;
    post({ type: 'active', id: id });
    loadIntoEditor();
  }

  function createNote(kind) {
    flushSave();
    post({ type: 'create', kind: kind || '' });
  }

  function togglePin(id) {
    var note = findNote(id);
    if (!note) return;
    note.pin = !note.pin;
    renderList();
    post({ type: 'pin', id: id, on: note.pin });
    setStatus(note.pin ? '已置顶' : '已取消置顶');
  }

  function setMark(id, mark) {
    var note = findNote(id);
    if (!note) return;
    note.mark = mark || '';
    renderCats();
    renderList();
    post({ type: 'mark', id: id, mark: note.mark });
  }

  function deleteNote(id) {
    var idx = -1;
    for (var i = 0; i < state.notes.length; i++) {
      if (state.notes[i].id === id) { idx = i; break; }
    }
    if (idx < 0) return;

    if (id === state.activeId) {
      clearTimeout(state.saveTimer);
      state.dirty = false;
    }
    state.notes.splice(idx, 1);
    state.contents.delete(id);
    state.scroll.delete(id);

    if (state.activeId === id) {
      var next = state.notes[Math.min(idx, state.notes.length - 1)];
      state.activeId = next ? next.id : null;
      if (state.activeId) {
        loadIntoEditor();
        post({ type: 'active', id: state.activeId });
      } else { showEmpty(true); renderList(); }
    } else {
      renderList();
    }
    renderCats();

    post({ type: 'delete', id: id });
    setStatus('已删除，可在 data\\trash 中找回');
  }

  function startRename(item, note) {
    if (state.renaming) return;
    state.renaming = true;

    var input = document.createElement('input');
    input.className = 'note-rename';
    input.type = 'text';
    input.value = note.title || '';
    input.maxLength = 60;

    var titleEl = item.querySelector('.note-title');
    titleEl.replaceWith(input);
    input.focus();
    input.select();

    var settled = false;
    function finish(commit) {
      if (settled) return;
      settled = true;
      state.renaming = false;
      var val = input.value.trim();
      if (commit && val && val !== note.title) applyRename(note, val);
      else renderList();
    }

    input.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
      else if (ev.key === 'Escape') { ev.preventDefault(); finish(false); }
      ev.stopPropagation();
    });
    input.addEventListener('blur', function () { finish(true); });
    input.addEventListener('click', function (ev) { ev.stopPropagation(); });
  }

  function applyRename(note, title) {
    var content = state.contents.get(note.id) || '';
    var lines = content.split('\n');
    var replaced = false;
    for (var i = 0; i < lines.length && i < 8; i++) {
      if (lines[i].trim() === '') continue;
      if (lines[i].indexOf('# ') === 0) {
        lines[i] = '# ' + title;
        replaced = true;
      }
      break;
    }
    content = replaced ? lines.join('\n') : ('# ' + title + '\n\n' + content);

    state.contents.set(note.id, content);
    note.title = title;

    if (note.id === state.activeId) {
      state.suppress = true;
      try { state.vditor.setValue(content, true); } catch (e) { }
      setTimeout(function () { state.suppress = false; decorate(); }, 80);
      updateCount(content);
    }

    state.dirty = false;
    post({ type: 'save', id: note.id, content: content });
    post({ type: 'rename', id: note.id, title: title });
    renderList();
    setStatus('已重命名');
  }

  /* ---------------- 状态栏 ---------------- */

  function setStatus(text) {
    $('statusSave').textContent = text;
    $('statusSave').classList.toggle('saving', text.indexOf('…') > -1);
  }

  function timeStr(ms) {
    try {
      var d = new Date(ms);
      var h = String(d.getHours()).padStart(2, '0');
      var m = String(d.getMinutes()).padStart(2, '0');
      return h + ':' + m;
    } catch (e) { return ''; }
  }

  function updateCount(content) {
    if (content === undefined) content = state.contents.get(state.activeId) || '';
    var plain = String(content)
      .replace(/```[\s\S]*?```/g, ' ')
      .replace(/\$\$[\s\S]*?\$\$/g, ' ')
      .replace(/[#>*_`~\-\[\]\(\)!]/g, ' ');
    var cjk = (plain.match(/[\u3400-\u9fff]/g) || []).length;
    var words = (plain.match(/[A-Za-z0-9]+/g) || []).length;
    $('statusCount').textContent = (cjk + words) + ' 字';
  }

  /* ---------------- 顶部按钮 ---------------- */

  function updateLevelBtn() {
    var lv = state.settings.level || 'top';
    $('levelText').textContent = LEVEL_TEXT[lv] || '置顶';
    $('btnLevel').classList.toggle('is-pinned', lv === 'top');
    $('btnLevel').title = '当前层级：' + (LEVEL_TEXT[lv] || '置顶') + '（点击切换）';
  }

  function updatePinBtn() {
    var on = !!state.settings.fixedTop;
    var b = $('btnPin');
    b.textContent = (on ? '✳' : '✧') + ' 固定';
    b.classList.toggle('is-on', on);
    b.title = on
      ? '固定置顶已开启：窗口始终压在最上层，点击关闭'
      : '固定置顶已关闭：点击锁定在所有窗口之上';
  }

  function updateThemeBtn() {
    var t = state.settings.theme;
    $('btnTheme').textContent = t === 'light' ? '☀' : (t === 'dark' ? '☾' : '◐');
    $('btnTheme').title = '配色：' + (t === 'light' ? '浅色' : t === 'dark' ? '深色' : '跟随系统');
  }

  function updateMini() {
    $('btnHL').classList.toggle('is-on', !!state.settings.autoHighlight);
    $('btnLK').classList.toggle('is-on', !!state.settings.autoLink);
  }

  function cycleLevel() {
    var cur = state.settings.level || 'top';
    var next = LEVEL_ORDER[(LEVEL_ORDER.indexOf(cur) + 1) % LEVEL_ORDER.length];
    state.settings.level = next;
    // 手动降层即认为不想再固定
    if (next !== 'top' && state.settings.fixedTop) {
      state.settings.fixedTop = false;
      updatePinBtn();
    }
    updateLevelBtn();
    post({ type: 'level', level: next });
    setStatus('层级已切换为「' + LEVEL_TEXT[next] + '」');
  }

  function cycleTheme() {
    var cur = state.settings.theme;
    var next = THEME_ORDER[(THEME_ORDER.indexOf(cur) + 1) % THEME_ORDER.length];
    state.settings.theme = next;
    applyTheme();
    post({ type: 'theme', theme: next });
  }

  function toggleFixedTop() {
    state.settings.fixedTop = !state.settings.fixedTop;
    if (state.settings.fixedTop) {
      state.settings.level = 'top';
      updateLevelBtn();
    } else {
      state.settings.level = 'normal';
      updateLevelBtn();
    }
    updatePinBtn();
    post({ type: 'fixedTop', on: state.settings.fixedTop });
    setStatus(state.settings.fixedTop ? '已固定置顶' : '已取消固定置顶');
  }

  /* ---------------- 浮层：新建菜单 / 标记面板 ---------------- */

  function closePops() {
    $('newMenu').hidden = true;
    $('markPalette').hidden = true;
    state.paletteFor = null;
  }

  function positionPop(pop, anchor, preferBelow) {
    pop.hidden = false;
    pop.style.visibility = 'hidden';
    pop.style.left = '0px';
    pop.style.top = '0px';

    var r = anchor.getBoundingClientRect();
    var pw = pop.offsetWidth, ph = pop.offsetHeight;
    var vw = window.innerWidth, vh = window.innerHeight;

    var left = preferBelow ? r.left : r.right + 6;
    if (left + pw > vw - 6) left = Math.max(6, vw - pw - 6);

    var top = preferBelow ? r.bottom + 4 : r.top - 4;
    if (top + ph > vh - 6) top = Math.max(6, vh - ph - 6);

    pop.style.left = Math.round(left) + 'px';
    pop.style.top = Math.round(top) + 'px';
    pop.style.visibility = '';
  }

  function openNewMenu() {
    var menu = $('newMenu');
    if (!menu.hidden) { closePops(); return; }
    closePops();
    positionPop(menu, $('btnNew'), true);
  }

  function openPalette(noteId, anchor) {
    if (state.paletteFor === noteId && !$('markPalette').hidden) { closePops(); return; }
    closePops();
    state.paletteFor = noteId;

    var note = findNote(noteId);
    var cur = parseMark(note ? note.mark : '');

    var eg = $('palEmoji');
    eg.innerHTML = '';
    MARKS_EMOJI.forEach(function (e) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = e;
      if (cur.kind === 'emoji' && cur.value === e) b.className = 'is-on';
      b.addEventListener('click', function () {
        setMark(noteId, 'emoji:' + e);
        closePops();
      });
      eg.appendChild(b);
    });

    var cg = $('palColor');
    cg.innerHTML = '';
    MARKS_COLOR.forEach(function (c) {
      var b = document.createElement('button');
      b.type = 'button';
      b.title = c;
      var inner = document.createElement('span');
      inner.style.background = c;
      b.appendChild(inner);
      if (cur.kind === 'color' && cur.value.toLowerCase() === c.toLowerCase())
        b.className = 'is-on';
      b.addEventListener('click', function () {
        setMark(noteId, 'color:' + c);
        closePops();
      });
      cg.appendChild(b);
    });

    $('palClear').onclick = function () {
      setMark(noteId, '');
      closePops();
    };

    positionPop($('markPalette'), anchor, false);
  }

  /* ---------------- 侧栏分区与提醒列表 ---------------- */

  function syncCatBar() {
    var bar = $('catBar');
    if (bar) bar.hidden = state.tab !== 'notes' || !state.hasCats;
  }

  function switchTab(tab) {
    state.tab = tab === 'remind' ? 'remind' : 'notes';
    $('tabNotes').classList.toggle('is-on', state.tab === 'notes');
    $('tabRemind').classList.toggle('is-on', state.tab === 'remind');
    $('noteList').hidden = state.tab !== 'notes';
    $('remindList').hidden = state.tab !== 'remind';
    syncCatBar();
    closePops();
    if (state.tab === 'remind') renderReminders();
  }

  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  function fmtWhen(ms) {
    var d = new Date(ms), now = new Date();
    var hm = pad2(d.getHours()) + ':' + pad2(d.getMinutes());
    if (d.toDateString() === now.toDateString()) return '今天 ' + hm;
    if (d.toDateString() === new Date(now.getTime() + 864e5).toDateString()) return '明天 ' + hm;
    var sameYear = d.getFullYear() === now.getFullYear();
    var head = sameYear ? (d.getMonth() + 1) + '月' + d.getDate() + '日' : d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
    return head + ' ' + hm;
  }

  function toLocalInput(d) {
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate())
      + 'T' + pad2(d.getHours()) + ':' + pad2(d.getMinutes());
  }

  function updateRemindBadge() {
    var n = 0;
    for (var i = 0; i < state.reminders.length; i++) {
      if (!state.reminders[i].fired) n++;
    }
    var b = $('remindCount');
    b.textContent = n ? String(n) : '';
    b.hidden = !n;
  }

  function renderReminders() {
    var box = $('remindList');
    if (!box) return;
    box.innerHTML = '';

    var list = state.reminders.slice();
    list.sort(function (a, b) {
      if (!!a.fired !== !!b.fired) return a.fired ? 1 : -1;
      return a.dueAt - b.dueAt;
    });

    if (!list.length) {
      var empty = document.createElement('div');
      empty.className = 'rem-empty';
      empty.innerHTML = '暂无提醒<br>在正文里写下时间或日期，<br>旁边的 ⏰ 可以直接建提醒';
      box.appendChild(empty);
      updateRemindBadge();
      return;
    }

    list.forEach(function (r) {
      var item = document.createElement('div');
      item.className = 'rem-item' + (r.fired ? ' is-fired' : '');
      item.title = r.fired ? '已提醒' : '待提醒';

      var top = document.createElement('div');
      top.className = 'rem-top';

      var when = document.createElement('span');
      when.className = 'rem-when';
      when.textContent = fmtWhen(r.dueAt);

      var note = document.createElement('span');
      note.className = 'rem-note';
      note.textContent = r.noteTitle || '';

      top.appendChild(when);
      top.appendChild(note);

      var text = document.createElement('div');
      text.className = 'rem-text';
      text.textContent = r.text;

      var del = document.createElement('button');
      del.type = 'button';
      del.className = 'rem-del';
      del.title = '删除提醒';
      del.textContent = '×';
      del.addEventListener('click', function (ev) {
        ev.stopPropagation();
        removeReminder(r.id);
      });

      item.appendChild(top);
      item.appendChild(text);
      item.appendChild(del);
      item.addEventListener('click', function () {
        if (r.noteId && findNote(r.noteId)) {
          switchTab('notes');
          selectNote(r.noteId);
        } else {
          setStatus('来源便签已删除');
        }
      });
      box.appendChild(item);
    });

    updateRemindBadge();
  }

  function removeReminder(id) {
    state.reminders = state.reminders.filter(function (r) { return r.id !== id; });
    renderReminders();
    post({ type: 'removeReminder', id: id });
  }

  /* ---------------- 时间解析 ---------------- */

  var WEEKDAY = { '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '日': 0, '天': 0 };

  function applyPeriod(period, h) {
    if (!period) return h;
    if (period === '下午' || period === '晚上' || period === '傍晚' || period === '中午') {
      if (h < 12) return h + 12;
      if (period === '中午' && h === 12) return 12;
      return h;
    }
    return h; // 凌晨 / 早上 / 上午 保持原值
  }

  /** 从一行自然语言里猜出提醒时间；猜不出就给「一小时后」。用户可在对话框里改。 */
  function parseWhen(line, now) {
    var y = now.getFullYear(), mo = now.getMonth(), day = now.getDate();
    var hasDate = false, hasTime = false, m;

    if ((m = /(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/.exec(line))) {
      y = +m[1]; mo = +m[2] - 1; day = +m[3]; hasDate = true;
    } else if ((m = /(\d{1,2})月(\d{1,2})[日号]/.exec(line))) {
      mo = +m[1] - 1; day = +m[2]; hasDate = true;
    } else if ((m = /(大后天|后天|明天|今天)/.exec(line))) {
      var off = { '今天': 0, '明天': 1, '后天': 2, '大后天': 3 }[m[1]];
      var t = new Date(y, mo, day + off);
      y = t.getFullYear(); mo = t.getMonth(); day = t.getDate();
      hasDate = true;
    } else if ((m = /(?:周|星期|礼拜)([一二三四五六日天])/.exec(line))) {
      var target = WEEKDAY[m[1]];
      var cur = new Date(y, mo, day).getDay();
      var diff = (target - cur + 7) % 7;
      if (diff === 0) diff = 7;
      var t2 = new Date(y, mo, day + diff);
      y = t2.getFullYear(); mo = t2.getMonth(); day = t2.getDate();
      hasDate = true;
    }

    var h = 9, mi = 0;
    if ((m = /(上午|下午|中午|晚上|早上|凌晨|傍晚)?\s*(\d{1,2})\s*[:：]\s*(\d{2})/.exec(line))) {
      h = applyPeriod(m[1], +m[2]); mi = +m[3]; hasTime = true;
    } else if ((m = /(上午|下午|中午|晚上|早上|凌晨|傍晚)?\s*(\d{1,2})\s*[点时](半|\d{1,2}\s*分)?/.exec(line))) {
      h = applyPeriod(m[1], +m[2]);
      mi = m[3] === '半' ? 30 : (m[3] ? parseInt(m[3], 10) : 0);
      hasTime = true;
    }

    if (!hasDate && !hasTime) return new Date(now.getTime() + 3600e3);

    var out = new Date(y, mo, day, h, mi, 0, 0);

    // 只给了时间且已经过去 → 顺延到明天
    if (!hasDate && hasTime && out.getTime() <= now.getTime()) {
      out = new Date(y, mo, day + 1, h, mi, 0, 0);
    }
    return out;
  }

  /* ---------------- 提醒确认条 ---------------- */

  function caretInfo() {
    var sel = window.getSelection();
    if (!sel || sel.rangeCount !== 1 || !sel.isCollapsed) return null;
    var node = sel.anchorNode;
    if (!node || node.nodeType !== 3) return null;
    var root = editorRoot();
    if (!root || !root.contains(node)) return null;
    if (isSkipped(node, root) || inHeading(node)) return null;
    return { node: node, offset: sel.anchorOffset };
  }

  function whenMatches(node) {
    var text = node.nodeValue;
    var out = [], taken = [];
    var cats = [['date', RE_DATE], ['time', RE_TIME]];
    for (var i = 0; i < cats.length; i++) {
      var re = cats[i][1];
      re.lastIndex = 0;
      var m;
      while ((m = re.exec(text)) !== null) {
        if (!m[0]) { re.lastIndex++; continue; }
        var raw = m[0];
        var lead = raw.length - raw.replace(/^\s+/, '').length;
        var trail = raw.length - raw.replace(/\s+$/, '').length;
        var s = m.index + lead, e = m.index + raw.length - trail;
        if (e <= s) continue;
        if (overlaps(taken, s, e)) continue;
        taken.push([s, e]);
        out.push({ s: s, e: e, kind: cats[i][0], text: text.slice(s, e) });
      }
    }
    return out;
  }

  /** 只对光标刚敲完、最靠近的那一处时间/日期提示，避免满篇弹提示。 */
  function findRemindCandidate() {
    var ci = caretInfo();
    if (!ci) return null;
    var ms = whenMatches(ci.node);
    if (!ms.length) return null;

    var best = null, bestD = 1e9;
    for (var i = 0; i < ms.length; i++) {
      var mm = ms[i];
      var d = ci.offset < mm.s ? mm.s - ci.offset : (ci.offset > mm.e ? ci.offset - mm.e : 0);
      if (d < bestD) { bestD = d; best = mm; }
    }
    if (!best || bestD > 8) return null;

    var key = (state.activeId || '') + '|' + best.s + '|' + best.text;
    if (state.remindMute[key]) return null;

    var r;
    try {
      r = document.createRange();
      r.setStart(ci.node, best.s);
      r.setEnd(ci.node, best.e);
    } catch (e) { return null; }

    return { range: r, node: ci.node, text: best.text, key: key };
  }

  function lineTextOf(node) {
    var root = editorRoot();
    var el = node.parentElement;
    while (el && el.parentElement && el.parentElement !== root) el = el.parentElement;
    var t = el ? (el.textContent || '') : (node.nodeValue || '');
    return t.replace(/\s+/g, ' ').trim();
  }

  function openReminderDialog(hit) {
    closePops();
    var line = lineTextOf(hit.node);
    var when = parseWhen(line, new Date());
    $('remWhen').value = toLocalInput(when);
    $('remText').value = (line || hit.text).slice(0, 60);
    $('remHint').textContent = '到点会弹出 Windows 通知';
    $('remHint').classList.remove('warn');
    $('remDialog').hidden = false;
    state.remindPending = hit.key;
    setTimeout(function () { $('remText').focus(); $('remText').select(); }, 30);
  }

  function closeReminderDialog() {
    $('remDialog').hidden = true;
    state.remindPending = null;
  }

  function saveReminder() {
    if (!state.activeId) { closeReminderDialog(); return; }
    var raw = $('remWhen').value;
    var d = new Date(raw);
    if (!raw || isNaN(d.getTime())) {
      $('remHint').textContent = '请填写有效的提醒时间';
      $('remHint').classList.add('warn');
      return;
    }
    if (d.getTime() <= Date.now() + 5000) {
      $('remHint').textContent = '提醒时间需要晚于现在';
      $('remHint').classList.add('warn');
      return;
    }

    var text = ($('remText').value || '').trim() || '提醒';
    post({
      type: 'addReminder',
      noteId: state.activeId,
      text: text.slice(0, 80),
      dueAt: d.getTime()
    });

    if (state.remindPending) state.remindMute[state.remindPending] = 1;
    closeReminderDialog();
    setStatus('已添加提醒 · ' + fmtWhen(d.getTime()));
    state.remindHit = null;
    placeMarks();
  }

  function isSkipped(node, root) {
    var e = node.parentElement;
    while (e && e !== root) {
      var tag = e.tagName;
      if (tag === 'CODE' || tag === 'SCRIPT' || tag === 'STYLE') return true;
      var dt = e.getAttribute ? e.getAttribute('data-type') : null;
      if (dt === 'code-block' || dt === 'math-block') return true;
      if (e.classList) {
        if (e.classList.contains('katex') || e.classList.contains('katex-display')) return true;
        if (e.classList.contains('vditor-ir__marker')) return true;
      }
      e = e.parentElement;
    }
    return false;
  }

  function collectTextNodes() {
    var root = editorRoot();
    if (!root) return [];
    var out = [];
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var n;
    while ((n = walker.nextNode())) {
      var v = n.nodeValue;
      if (!v || !/\S/.test(v)) continue;
      if (isSkipped(n, root)) continue;
      out.push(n);
    }
    return out;
  }

  function overlaps(list, s, e) {
    for (var i = 0; i < list.length; i++) {
      if (s < list[i][1] && e > list[i][0]) return true;
    }
    return false;
  }

  /** 文本节点是否位于标题内。标题是结构，不做正文级的底色标注。 */
  function inHeading(node) {
    var root = editorRoot();
    var e = node.parentElement;
    while (e && e !== root) {
      if (/^H[1-6]$/.test(e.tagName)) return true;
      e = e.parentElement;
    }
    return false;
  }

  /** 在一个文本节点内按优先级抓取日期 / 时间 / 地点区间。 */
  function rangesInNode(node, bucket) {
    var text = node.nodeValue;
    var cats = [['fn-place', RE_PLACE], ['fn-date', RE_DATE], ['fn-time', RE_TIME]];
    var taken = [];

    for (var ci = 0; ci < cats.length; ci++) {
      var name = cats[ci][0], re = cats[ci][1];
      re.lastIndex = 0;
      var m;
      while ((m = re.exec(text)) !== null) {
        if (!m[0]) { re.lastIndex++; continue; }

        var raw = m[0];
        var lead = raw.length - raw.replace(/^\s+/, '').length;
        var trail = raw.length - raw.replace(/\s+$/, '').length;
        var s = m.index + lead;
        var e = m.index + raw.length - trail;
        if (e - s < 1) continue;
        if (overlaps(taken, s, e)) continue;

        taken.push([s, e]);
        try {
          var r = document.createRange();
          r.setStart(node, s);
          r.setEnd(node, e);
          bucket[name].push(r);
        } catch (err) { }
      }
    }
  }

  /** 抓取 ddl{...} 截止日期。整段染色，花括号单独再染一层透明。 */
  function ddlRangesInNode(node, main, braces) {
    var text = node.nodeValue;
    if (!text || text.indexOf('ddl') < 0 && text.indexOf('DDL') < 0) return;
    RE_DDL.lastIndex = 0;
    var m;
    while ((m = RE_DDL.exec(text)) !== null) {
      var s = m.index, e = m.index + m[0].length;
      try {
        var r = document.createRange();
        r.setStart(node, s);
        r.setEnd(node, e);
        main.push(r);

        var ob = text.indexOf('{', s);
        if (ob >= 0 && ob < e) {
          var rb = document.createRange();
          rb.setStart(node, ob);
          rb.setEnd(node, ob + 1);
          braces.push(rb);
        }
        var cb = text.lastIndexOf('}', e);
        if (cb >= s && cb < e) {
          var rc = document.createRange();
          rc.setStart(node, cb);
          rc.setEnd(node, cb + 1);
          braces.push(rc);
        }
      } catch (err) { }
    }
  }

  /**
   * 「上周未完成」标题之下、下一个标题之前的所有文字。
   * 靠位置判断而不是靠行内标记——标红信息存在 Markdown 结构里，源码永远干净。
   */
  function carryRanges() {
    var out = [];
    var root = editorRoot();
    if (!root) return out;
    var inCarry = false;
    var kids = root.children;
    for (var i = 0; i < kids.length; i++) {
      var el = kids[i];
      if (/^H[1-6]$/i.test(el.tagName)) {
        inCarry = (el.textContent || '').indexOf('上周未完成') >= 0;
        continue;
      }
      if (!inCarry) continue;
      var walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
      while (walker.nextNode()) {
        var n = walker.currentNode;
        if (!n.nodeValue || !n.nodeValue.trim()) continue;
        try {
          var r = document.createRange();
          r.selectNodeContents(n);
          out.push(r);
        } catch (err) { }
      }
    }
    return out;
  }

  function applyHighlight(kind, ranges) {
    if (!window.CSS || !CSS.highlights || typeof window.Highlight !== 'function') return;
    try {
      if (!ranges.length) { CSS.highlights.delete(kind); return; }
      // 逐条 add：不同 Chromium 版本对构造参数的处理有差异，Setlike 的 add 最稳
      var h = new Highlight();
      var n = Math.min(ranges.length, 400);
      for (var i = 0; i < n; i++) h.add(ranges[i]);
      CSS.highlights.set(kind, h);
    } catch (e) { }
  }

  function clearHighlights() {
    if (!window.CSS || !CSS.highlights) return;
    ['fn-date', 'fn-time', 'fn-place', 'fn-ddl', 'fn-ddl-brace', 'fn-carry'].forEach(function (k) {
      try { CSS.highlights.delete(k); } catch (e) { }
    });
  }

  /* ---------------- 关键词链接 ---------------- */

  function collectLinkHits(nodes) {
    var targets = [];
    var seenTitle = {};
    state.notes.forEach(function (n) {
      var t = (n.title || '').trim();
      if (t.length < 2) return;
      if (n.id === state.activeId) return;
      if (seenTitle[t]) return; // 同名便签只保留一条，避免同一处弹两组提示
      seenTitle[t] = 1;
      targets.push({ id: n.id, title: t });
    });
    if (!targets.length) return [];

    // 长标题优先，避免短标题把长标题切碎
    targets.sort(function (a, b) { return b.title.length - a.title.length; });

    var hits = [];
    var serial = {};

    nodes.forEach(function (node) {
      var p = node.parentElement;
      // 已经是链接的文本不再提示（IR 模式的链接是 span[data-type=a]，不是 <a>）
      if (p && p.closest && p.closest('a, [data-type="a"]')) return;

      var text = node.nodeValue;
      var taken = [];

      targets.forEach(function (t) {
        var idx = 0;
        while (true) {
          var at = text.indexOf(t.title, idx);
          if (at < 0) break;
          idx = at + t.title.length;
          var end = at + t.title.length;
          if (overlaps(taken, at, end)) continue;
          taken.push([at, end]);

          var n0 = serial[t.id] || 0;
          serial[t.id] = n0 + 1;

          try {
            var r = document.createRange();
            r.setStart(node, at);
            r.setEnd(node, end);
            hits.push({
              id: t.id,
              title: t.title,
              range: r,
              serial: n0,
              key: state.activeId + '|' + t.id + '|' + n0
            });
          } catch (e) { }
        }
      });
    });

    return hits;
  }

  function clearPills() {
    state.linkHits = [];
    state.remindHit = null;
    var layer = $('linkLayer');
    if (layer) layer.innerHTML = '';
  }

  /** 编辑器上方的浮动提示层：便签链接确认条 + 时间提醒确认条。 */
  function placeMarks() {
    var layer = $('linkLayer');
    var mainEl = document.querySelector('.main');
    if (!layer || !mainEl) return;

    layer.innerHTML = '';
    var mr = mainEl.getBoundingClientRect();
    var used = [];
    var shown = 0;

    if (state.settings.autoLink && state.linkHits.length) {
      for (var i = 0; i < state.linkHits.length && shown < 6; i++) {
        var hit = state.linkHits[i];
        if (state.ignored[hit.key]) continue;

        var rects = null;
        try { rects = hit.range.getClientRects(); } catch (e) { rects = null; }
        if (!rects || !rects.length) continue;

        var r = rects[rects.length - 1];
        if (!r || r.width <= 0) continue;

        var top = r.top - mr.top;
        if (top < -4 || top > mr.height - 12) continue;

        var pill = document.createElement('div');
        pill.className = 'link-pill';

        var ok = document.createElement('button');
        ok.type = 'button';
        ok.className = 'ok';
        ok.textContent = '✓';
        ok.title = '链接到「' + hit.title + '」';

        var no = document.createElement('button');
        no.type = 'button';
        no.className = 'no';
        no.textContent = '✗';
        no.title = '不要链接';

        pill.appendChild(ok);
        pill.appendChild(no);

        var left = r.right - mr.left + 5;
        if (left + 38 > mr.width) left = Math.max(2, r.left - mr.left - 38);
        var ptop = Math.max(0, top + r.height / 2 - 9);
        pill.style.left = Math.round(left) + 'px';
        pill.style.top = Math.round(ptop) + 'px';

        bindPill(ok, no, hit);
        layer.appendChild(pill);
        used.push([Math.round(left), Math.round(ptop)]);
        shown++;
      }
    }

    // 提醒确认条：只针对光标附近的这一处时间
    var rh = state.remindHit;
    if (rh) {
      var rr = null;
      try { rr = rh.range.getClientRects(); } catch (e) { rr = null; }
      if (rr && rr.length) {
        var rect = rr[rr.length - 1];
        if (rect && rect.width > 0) {
          var t = rect.bottom - mr.top + 2;
          var l = rect.right - mr.left + 5;
          if (l + 54 > mr.width) l = Math.max(2, rect.left - mr.left - 54);
          if (t + 20 > mr.height) t = Math.max(0, rect.top - mr.top - 22);

          var rp = document.createElement('div');
          rp.className = 'link-pill';

          var clock = document.createElement('span');
          clock.className = 'clock';
          clock.textContent = '⏰';

          var rok = document.createElement('button');
          rok.type = 'button';
          rok.className = 'ok';
          rok.textContent = '✓';
          rok.title = '为「' + rh.text + '」添加提醒';

          var rno = document.createElement('button');
          rno.type = 'button';
          rno.className = 'no';
          rno.textContent = '✗';
          rno.title = '不用提醒';

          rp.appendChild(clock);
          rp.appendChild(rok);
          rp.appendChild(rno);

          rp.style.left = Math.round(l) + 'px';
          rp.style.top = Math.round(t) + 'px';

          (function (rh0) {
            function stop(ev) { ev.preventDefault(); ev.stopPropagation(); }
            rok.addEventListener('mousedown', stop);
            rno.addEventListener('mousedown', stop);
            rok.addEventListener('click', function (ev) { stop(ev); openReminderDialog(rh0); });
            rno.addEventListener('click', function (ev) {
              stop(ev);
              state.remindMute[rh0.key] = 1;
              state.remindHit = null;
              placeMarks();
            });
          })(rh);

          layer.appendChild(rp);
        }
      }
    }
  }

  function bindPill(ok, no, hit) {
    function stop(ev) { ev.preventDefault(); ev.stopPropagation(); }
    ok.addEventListener('mousedown', stop);
    no.addEventListener('mousedown', stop);
    ok.addEventListener('click', function (ev) { stop(ev); makeLink(hit); });
    no.addEventListener('click', function (ev) {
      stop(ev);
      state.ignored[hit.key] = 1;
      placeMarks();
    });
  }

  /** 源文本里已被链接 / 代码占用的区间，不能重复插链。 */
  function blockedRanges(src) {
    var out = [];
    var m;
    var re = /```[\s\S]*?```/g;
    while ((m = re.exec(src)) !== null) out.push([m.index, m.index + m[0].length]);
    re = /`[^`\n]*`/g;
    while ((m = re.exec(src)) !== null) out.push([m.index, m.index + m[0].length]);
    re = /!?\[[^\]\n]*\]\([^)\n]*\)/g;
    while ((m = re.exec(src)) !== null) out.push([m.index, m.index + m[0].length]);
    return out;
  }

  function freeOccurrences(src, title) {
    var blocked = blockedRanges(src);
    var out = [];
    var i = 0;
    while (true) {
      var p = src.indexOf(title, i);
      if (p < 0) break;
      i = p + title.length;
      var e = p + title.length;
      var bad = false;
      for (var k = 0; k < blocked.length; k++) {
        if (p < blocked[k][1] && e > blocked[k][0]) { bad = true; break; }
      }
      if (!bad && p > 0 && src.charAt(p - 1) === '[') bad = true;
      if (!bad) out.push(p);
    }
    return out;
  }

  function makeLink(hit) {
    if (!state.activeId) return;
    var content = state.contents.get(state.activeId) || '';
    var free = freeOccurrences(content, hit.title);
    if (!free.length) { setStatus('原文已变化，未建立链接'); decorate(); return; }

    var pos = free[Math.min(hit.serial, free.length - 1)];
    var linked = '[' + hit.title + '](floatnote://note/' + hit.id + ')';
    var next = content.slice(0, pos) + linked + content.slice(pos + hit.title.length);

    setEditorValue(next, true);
    setStatus('已链接到「' + hit.title + '」');
  }

  /* ---------------- 装饰总调度 ---------------- */

  function scheduleDecorate(delay) {
    clearTimeout(state.decoTimer);
    state.decoTimer = setTimeout(decorate, delay === undefined ? 300 : delay);
  }

  function decorate() {
    clearTimeout(state.decoTimer);
    if (!state.booted || !state.vditor || state.suppress) return;

    var nodes = collectTextNodes();

    var bucket = { 'fn-date': [], 'fn-time': [], 'fn-place': [] };
    var ddl = [], brace = [];
    if (state.settings.autoHighlight) {
      nodes.forEach(function (n) {
        if (inHeading(n)) return;
        rangesInNode(n, bucket);
        ddlRangesInNode(n, ddl, brace);
      });
    }
    applyHighlight('fn-date', bucket['fn-date']);
    applyHighlight('fn-time', bucket['fn-time']);
    applyHighlight('fn-place', bucket['fn-place']);
    applyHighlight('fn-ddl', ddl);
    // 后注册的在上层，花括号才能盖住整段红字变成透明
    applyHighlight('fn-ddl-brace', brace);
    applyHighlight('fn-carry', carryRanges());

    state.linkHits = state.settings.autoLink ? collectLinkHits(nodes) : [];
    state.remindHit = findRemindCandidate();
    placeMarks();
  }

  function onScrollLike() {
    var root = editorRoot();
    if (root && state.activeId && !state.suppress) {
      state.scroll.set(state.activeId, root.scrollTop);
    }
    if ((!state.linkHits.length && !state.remindHit) || state.pillRaf) return;
    state.pillRaf = requestAnimationFrame(function () {
      state.pillRaf = 0;
      placeMarks();
    });
  }

  /* ---------------- 按钮与快捷键 ---------------- */

  function bindUi() {
    $('btnLevel').addEventListener('click', cycleLevel);
    $('btnPin').addEventListener('click', toggleFixedTop);
    $('btnTheme').addEventListener('click', cycleTheme);
    $('btnNew').addEventListener('click', function (ev) {
      ev.stopPropagation();
      openNewMenu();
    });
    $('btnEmptyNew').addEventListener('click', function () { createNote(''); });
    $('btnMin').addEventListener('click', function () { post({ type: 'minimize' }); });
    $('btnHide').addEventListener('click', function () {
      flushSave();
      post({ type: 'hide', src: 'btn' });
    });

    $('tabNotes').addEventListener('click', function () { switchTab('notes'); });
    $('tabRemind').addEventListener('click', function () { switchTab('remind'); });

    $('remCancel').addEventListener('click', closeReminderDialog);
    $('remSave').addEventListener('click', saveReminder);
    $('remDialog').addEventListener('mousedown', function (ev) {
      if (ev.target === $('remDialog')) closeReminderDialog();
    });
    $('remWhen').addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') { ev.preventDefault(); saveReminder(); }
      ev.stopPropagation();
    });
    $('remText').addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') { ev.preventDefault(); saveReminder(); }
      ev.stopPropagation();
    });

    // 光标移动后重新判断「附近有没有时间」，从而决定要不要出现 ⏰ 提示
    document.addEventListener('selectionchange', function () {
      if (!state.booted || !state.vditor || state.suppress) return;
      if (!$('remDialog').hidden) return;
      scheduleDecorate(260);
    });

    $('newMenu').addEventListener('click', function (ev) {
      var item = ev.target.closest('.pop-item');
      if (!item) return;
      var kind = item.getAttribute('data-kind') || '';
      closePops();
      createNote(kind);
    });

    $('btnHL').addEventListener('click', function () {
      state.settings.autoHighlight = !state.settings.autoHighlight;
      updateMini();
      post({ type: 'autoFlag', name: 'highlight', on: state.settings.autoHighlight });
      decorate();
    });

    $('btnLK').addEventListener('click', function () {
      state.settings.autoLink = !state.settings.autoLink;
      updateMini();
      post({ type: 'autoFlag', name: 'link', on: state.settings.autoLink });
      if (!state.settings.autoLink) clearPills();
      decorate();
    });

    $('btnSidebar').addEventListener('click', function () {
      state.settings.sidebar = !state.settings.sidebar;
      document.querySelector('.workspace')
        .classList.toggle('no-side', !state.settings.sidebar);
      closePops();
    });

    document.addEventListener('mousedown', function (ev) {
      var pal = $('markPalette'), menu = $('newMenu');
      if (pal.hidden && menu.hidden) return;
      if (ev.target.closest('.pop')) return;
      if (ev.target.closest('#btnNew') || ev.target.closest('.note-mark')) return;
      closePops();
    }, true);

    var bar = $('titlebar');
    bar.addEventListener('mousedown', function (ev) {
      if (ev.button !== 0) return;
      if (ev.target.closest('button')) return;
      post({ type: 'drag' });
    });

    // ---------- Markdown 行内格式快捷键 ----------
    // Vditor 的 IR 模式虽然自带一部分按键，但它的实现依赖工具栏内部状态（本项目 toolbar 为空），
    // 行为不可靠。这里在捕获阶段统一接管：先自己插入标记，再阻止事件继续传播，
    // 避免和内核的按键处理叠加成双层包裹。
    var MD_WRAP = {
      b: { before: '**', after: '**', hint: '粗体', ph: '粗体' },
      i: { before: '*', after: '*', hint: '斜体', ph: '斜体' },
      u: { before: '<u>', after: '</u>', hint: '下划线', ph: '下划线' },
      e: { before: '`', after: '`', hint: '行内代码', ph: 'code' },
      x: { shift: true, before: '~~', after: '~~', hint: '删除线', ph: '删除线' },
      // 高亮是 Markdown 的 ==...== 扩展语法（渲染成 <mark>）
      h: { shift: true, before: '==', after: '==', hint: '高亮', ph: '高亮' },
      k: { shift: true, before: '[', after: '](https://)', hint: '链接', ph: '链接文字' },
      m: { shift: true, before: '$', after: '$', hint: '行内公式', ph: 'x' }
    };

    /** 用 execCommand 插入，能触发原生 input 事件，让 Vditor 正常解析标记。 */
    function wrapInline(cfg) {
      var root = editorRoot();
      if (!root || !state.vditor) return false;

      var sel = window.getSelection();
      if (!sel || sel.rangeCount === 0) return false;
      var r = sel.getRangeAt(0);
      var holder = r.commonAncestorContainer;
      // 只接管正文内的按键；搜索框、重命名输入框等一律走浏览器默认行为
      if (holder !== root && !root.contains(holder)) return false;

      var picked = r.toString();
      var body = picked || cfg.ph;

      var ok = false;
      try { ok = document.execCommand('insertText', false, cfg.before + body + cfg.after); } catch (e) { }
      if (!ok) {
        // 退路：交给 Vditor 自己的插入接口（不触发解析，但至少内容正确）
        try { state.vditor.insertValue(cfg.before + body + cfg.after); ok = true; } catch (e) { }
      }
      if (!ok) return false;

      // 没有选中内容时，把占位词选中，直接打字即可覆盖
      if (!picked) {
        try {
          var s2 = window.getSelection();
          for (var i = 0; i < cfg.after.length; i++) s2.modify('move', 'backward', 'character');
          for (var j = 0; j < body.length; j++) s2.modify('extend', 'backward', 'character');
        } catch (e) { }
      }

      setStatus(cfg.hint);
      return true;
    }

    /**
     * 子任务全部完成 → 主任务自动完成。
     * 单向：只把父项勾上，不会因为取消父项去反勾子项，避免误操作丢状态。
     * 多级嵌套靠多轮迭代收敛。
     */
    function syncTaskTree(md) {
      if (!md || md.indexOf('[ ]') < 0 && md.indexOf('[x]') < 0 && md.indexOf('[X]') < 0) return md;
      var lines = md.split('\n');
      var info = [];
      for (var i = 0; i < lines.length; i++) {
        var m = /^(\s*)([-*+])\s+\[([ xX])\]\s?/.exec(lines[i]);
        info.push(m ? { indent: m[1].length, done: m[3] !== ' ', idx: i } : null);
      }

      var changed = false;
      for (var pass = 0; pass < 6; pass++) {
        var dirty = false;
        for (var a = 0; a < info.length; a++) {
          var cur = info[a];
          if (!cur || cur.done) continue;
          var kids = [];
          for (var b = a + 1; b < info.length; b++) {
            var nx = info[b];
            if (!nx) continue;
            if (nx.indent > cur.indent) { kids.push(nx); continue; }
            break;
          }
          if (!kids.length) continue;
          var allDone = true;
          for (var c = 0; c < kids.length; c++) {
            if (!kids[c].done) { allDone = false; break; }
          }
          if (!allDone) continue;
          cur.done = true;
          lines[cur.idx] = lines[cur.idx].replace(/\[\s\]/, '[x]');
          dirty = true;
          changed = true;
        }
        if (!dirty) break;
      }
      return changed ? lines.join('\n') : md;
    }

    /** Tab 缩进 / Shift+Tab 反缩进。只接管正文里的按键。 */
    function indentTask(back) {
      var root = editorRoot();
      if (!root || !state.vditor) return false;
      var sel = window.getSelection();
      if (!sel || sel.rangeCount === 0) return false;
      var r = sel.getRangeAt(0);
      var holder = r.commonAncestorContainer;
      if (holder !== root && !root.contains(holder)) return false;

      // 用浏览器原生的 indent/outdent：它会真正改变列表层级（生成嵌套 <ul>），
      // 而不是插入空格——IR 模式下缩进是 DOM 结构，不是文本内容
      if (!back) {
        try { return document.execCommand('indent'); } catch (e) { return false; }
      }
      try { return document.execCommand('outdent'); } catch (e) { return false; }
    }

    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') {
        closePops();
        if (!$('remDialog').hidden) { closeReminderDialog(); return; }
      }
      if (!$('remDialog').hidden) return;

      // Tab / Shift+Tab：把当前任务升成上一项的子任务，或退一级
      if (ev.key === 'Tab' && !ev.ctrlKey && !ev.altKey && !ev.metaKey) {
        if (indentTask(ev.shiftKey)) {
          ev.preventDefault();
          ev.stopPropagation();
          if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
        }
        return;
      }

      var mod = ev.ctrlKey || ev.metaKey;
      if (!mod) return;
      var k = (ev.key || '').toLowerCase();

      // 行内格式：Shift 状态必须与配置一致，避免 Ctrl+B 和 Ctrl+Shift+B 互相误触
      var cfg = MD_WRAP[k];
      if (cfg && !!cfg.shift === !!ev.shiftKey) {
        if (wrapInline(cfg)) {
          ev.preventDefault();
          ev.stopPropagation();
          if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
        }
        return;
      }

      if (k === 'n') { ev.preventDefault(); createNote(''); }
      else if (k === 's') {
        ev.preventDefault();
        state.dirty = true;
        flushSave();
        setStatus('已保存 ' + timeStr(Date.now()));
      }
      else if (k === 'w') { ev.preventDefault(); flushSave(); post({ type: 'hide', src: 'key' }); }
    }, true);

    window.addEventListener('wheel', function (ev) {
      if (!(ev.ctrlKey || ev.metaKey)) return;
      ev.preventDefault();
      var next = Math.min(22, Math.max(11, state.settings.fontSize + (ev.deltaY < 0 ? 1 : -1)));
      if (next === state.settings.fontSize) return;
      state.settings.fontSize = next;
      applyFontSize();
      post({ type: 'fontSize', size: next });
      setStatus('字号 ' + next + 'px');
      scheduleDecorate(180);
    }, { passive: false, capture: true });

    // 任务清单兜底：若内核未触发 input，用值比对补齐
    // 链接点击的统一接管在 document 捕获阶段（见文件末尾），这里只管 checkbox
    $('editor').addEventListener('click', function (ev) {
      if (!ev.target.closest || !ev.target.closest('input[type="checkbox"]')) return;
      setTimeout(function () {
        if (state.suppress || !state.vditor || !state.activeId) return;
        var v = state.vditor.getValue();
        var merged = syncTaskTree(v);
        if (merged !== v) {
          setEditorValue(merged, true);
          scheduleDecorate(300);
          return;
        }
        if (v !== state.contents.get(state.activeId)) {
          state.contents.set(state.activeId, v);
          state.dirty = true;
          updateCount(v);
          flushSave();
          scheduleDecorate(300);
        }
      }, 40);
    });

    // 打完 ddl 自动补一对花括号，并把光标停在括号中间
    document.addEventListener('input', function () {
      if (!state.vditor || state.suppress || state.ddlBusy) return;
      var root = editorRoot();
      if (!root) return;
      var sel = window.getSelection();
      if (!sel || sel.rangeCount === 0) return;
      var r = sel.getRangeAt(0);
      if (!r.collapsed) return;
      var node = r.startContainer;
      if (!node || node.nodeType !== 3 || !root.contains(node)) return;
      // 注意不能直接 closest('pre')：编辑器根节点自己就是 pre.vditor-reset，
      // 那样会把所有正文都排除掉。只排代码块与行内代码。
      var pe = node.parentElement;
      if (pe && (pe.closest('code') || pe.closest('[data-type="code-block"]') ||
                 pe.closest('.vditor-ir__marker--pre'))) return;

      var before = node.nodeValue.slice(0, r.startOffset);
      if (!RE_DDL_TAIL.test(before)) return;
      var after = node.nodeValue.slice(r.startOffset);
      if (/^\s*\{/.test(after)) return;

      state.ddlBusy = true;
      setTimeout(function () {
        try {
          document.execCommand('insertText', false, '{}');
          var s2 = window.getSelection();
          if (s2 && s2.rangeCount) s2.modify('move', 'backward', 'character');
        } catch (e) { }
        state.ddlBusy = false;
      }, 0);
    }, true);

    $('editor').addEventListener('scroll', onScrollLike, true);
    window.addEventListener('resize', function () { scheduleDecorate(160); });

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) flushSave();
    });

    window.addEventListener('beforeunload', function () { flushSave(); });
  }

  /* ---------------- 与宿主通信 ---------------- */

  function normalizeIndex(list) {
    return (list || []).map(function (n) {
      return {
        id: n.id,
        title: n.title,
        updated: n.updated,
        pin: !!n.pin,
        mark: n.mark || '',
        kind: n.kind || ''
      };
    });
  }

  function onHostMessage(ev) {
    var msg = ev.data;
    if (!msg || !msg.type) return;

    switch (msg.type) {
      case 'init':
        state.notes = normalizeIndex(msg.notes);
        state.contents.clear();
        (msg.notes || []).forEach(function (n) { state.contents.set(n.id, n.content || ''); });
        state.activeId = msg.activeId || (state.notes[0] ? state.notes[0].id : null);
        if (msg.settings) {
          state.settings.theme = msg.settings.theme || 'system';
          state.settings.fontSize = msg.settings.fontSize || 14;
          state.settings.level = msg.settings.level || 'top';
          state.settings.systemDark = !!msg.settings.systemDark;
          state.settings.sidebar = msg.settings.sidebar !== false;
          state.settings.fixedTop = msg.settings.fixedTop !== false;
          state.settings.autoHighlight = msg.settings.autoHighlight !== false;
          state.settings.autoLink = msg.settings.autoLink !== false;
        }
        state.hotkeyOk = msg.hotkeyOk !== false;
        state.reminders = msg.reminders || [];
        state.booted = true;
        log('INIT notes=' + state.notes.length + ' active=' + state.activeId +
          ' fixedTop=' + state.settings.fixedTop);
        document.querySelector('.workspace')
          .classList.toggle('no-side', !state.settings.sidebar);
        applyTheme();
        applyFontSize();
        updateLevelBtn();
        updatePinBtn();
        updateMini();
        if (!state.hotkeyOk) {
          $('statusHint').textContent = '全局热键被占用，可从托盘唤出';
        }
        renderCats();
        renderReminders();
        switchTab('notes');
        loadIntoEditor();
        break;

      case 'reminders':
        state.reminders = msg.list || [];
        renderReminders();
        break;

      case 'focusNote':
        if (msg.id && findNote(msg.id)) {
          switchTab('notes');
          selectNote(msg.id);
        }
        break;

      case 'reloadContent':
        // 宿主每周结算改写了正文：只在用户没正在改这一篇时无痛替换，避免吃掉他的输入
        if (msg.id && state.contents.has(msg.id)) {
          state.contents.set(msg.id, msg.content || '');
          if (msg.id === state.activeId && !state.dirty) {
            setEditorValue(msg.content || '', false);
            state.dirty = false;
          }
        }
        break;

      case 'toast':
        if (msg.text) setStatus(msg.text);
        break;

      case 'saved':
        if (state.dirty) break;
        setStatus('已保存 ' + timeStr(msg.at));
        (function () {
          for (var i = 0; i < state.notes.length; i++) {
            if (state.notes[i].id === msg.id) {
              if (msg.title && state.notes[i].title !== msg.title) {
                state.notes[i].title = msg.title;
                renderList();
              }
              break;
            }
          }
        })();
        break;

      case 'created':
        state.notes.unshift(normalizeIndex([msg.note])[0]);
        state.contents.set(msg.note.id, msg.note.content || '');
        state.activeId = msg.note.id;
        state.filterMark = '*'; // 新便签可能还没有分类，先把筛选复位，否则会看不见
        renderCats();
        loadIntoEditor();
        if (msg.note.kind === 'weekly') setStatus('已新建「每周待办」');
        else setStatus('已新建便签');
        break;

      case 'index':
        state.notes = normalizeIndex(msg.notes);
        renderCats();
        renderList();
        break;

      case 'level':
        state.settings.level = msg.level || 'top';
        if (state.settings.level !== 'top') state.settings.fixedTop = false;
        updateLevelBtn();
        updatePinBtn();
        break;

      case 'fixedTop':
        state.settings.fixedTop = !!msg.on;
        if (msg.level) state.settings.level = msg.level;
        updatePinBtn();
        updateLevelBtn();
        break;

      case 'theme':
        state.settings.theme = msg.theme || 'system';
        applyTheme();
        break;

      case 'flush':
        flushSave();
        post({ type: 'flushed' });
        break;
    }
  }

  function boot() {
    log('boot start');
    bindUi();

    if (bridge) {
      bridge.addEventListener('message', onHostMessage);
      post({ type: 'ready' });
    } else {
      // 浏览器直开时的降级预览，便于单独调试前端
      state.booted = true;
      state.notes = [
        { id: 'demo', title: '预览模式', updated: Date.now(), pin: true, mark: 'emoji:📄', kind: '' },
        { id: 'demo2', title: '高数作业', updated: Date.now(), pin: false, mark: 'emoji:📚', kind: '' }
      ];
      state.contents.set('demo',
        '# 预览模式\n\n未连接宿主，仅用于界面调试。\n\n今天 2026-10-26 14:30 在 @图书馆 讨论高数作业。\n\n- [ ] 任务一\n\n$E=mc^2$\n');
      state.contents.set('demo2', '# 高数作业\n\n- [ ] 第三章习题\n');
      state.activeId = 'demo';
      state.reminders = [{
        id: 'r1', noteId: 'demo', noteTitle: '预览模式',
        text: '讨论高数作业', dueAt: Date.now() + 3600e3, fired: false
      }];
      applyTheme();
      applyFontSize();
      updateLevelBtn();
      updatePinBtn();
      updateMini();
      renderCats();
      renderReminders();
      switchTab('notes');
      loadIntoEditor();
    }
  }

  // ---------- 链接点击统一接管 ----------
  // floatnote:// 是本程序的自定义协议；只要漏一次给浏览器，系统就会把它交给外壳去
  // 「查找能打开此链接的应用」，弹出一个没用的对话框。所以这里在 document 捕获阶段
  // 抢先处理，覆盖两种 DOM 形态：
  //   A. 预览态 / 普通 <a href="...">
  //   B. 编辑态 span[data-type="a"] > .vditor-ir__link（URL 在相邻 marker 里）
  function linkTarget(ev) {
    var el = ev.target;
    if (!el || !el.closest) return null;

    var a = el.closest('a[href]');
    if (a) {
      var href = (a.getAttribute('href') || '').trim();
      if (href.indexOf('floatnote://note/') === 0) return { note: href };
      if (/^https?:\/\//i.test(href)) return { ext: href };
      if (href.indexOf('floatnote:') === 0) return { dead: true };
      return null;
    }

    var node = el.closest('[data-type="a"]');
    if (node) {
      var urlEl = node.querySelector('.vditor-ir__marker--link');
      var url = urlEl ? (urlEl.textContent || '').trim() : '';
      if (url.indexOf('floatnote://note/') === 0) return { note: url };
      if (/^https?:\/\//i.test(url)) return { ext: url };
      if (url.indexOf('floatnote:') === 0) return { dead: true };
    }
    return null;
  }

  function onAnyLink(ev) {
    var hit = linkTarget(ev);
    if (!hit) return;
    ev.preventDefault();
    ev.stopPropagation();
    if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();

    if (hit.dead) { setStatus('链接目标无效'); return; }
    if (hit.ext) { post({ type: 'openExternal', url: hit.ext }); return; }

    var id = hit.note.substring('floatnote://note/'.length).replace(/[?#].*$/, '').replace(/\/+$/, '');
    if (id && findNote(id)) {
      selectNote(id);
    } else {
      setStatus('目标便签已不存在');
    }
  }

  document.addEventListener('click', onAnyLink, true);
  document.addEventListener('auxclick', onAnyLink, true);

  // 仅调试用：宿主以 --dev 启动时会给页面带上 ?debug=1，正常启动不暴露任何内部句柄
  if (location.search.indexOf('debug=1') > -1) {
    window.__fn = {
      state: state,
      decorate: decorate,
      setEditorValue: setEditorValue,
      openPalette: openPalette,
      createNote: createNote,
      selectNote: selectNote
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
