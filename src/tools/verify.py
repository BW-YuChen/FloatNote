"""一次性静默验收：启动一次应用，通过 CDP 在页面内断言，全程不注入鼠标键盘。"""
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _norm_exe(p):
    # 有些 shell 会把反斜杠再转义一层，路径里出现 \\\\ 会导致字符串比对失败
    return p.replace("\\\\", "\\") if p else p


APP = _norm_exe(os.environ.get(
    "FN_EXE",
    os.path.join(_SRC, "bin", "Release", "net10.0-windows", "FloatNote.exe")))
WORK = os.path.dirname(APP)
DATA = os.path.join(WORK, "data")

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (("  | " + str(detail)) if detail else ""))


def find_window():
    u = ctypes.windll.user32
    box = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        n = u.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, b, n + 1)
            if b.value == "浮笺":
                box.append(h)
        return True

    u.EnumWindows(cb, 0)
    return box[0] if box else 0


def ex_topmost(h):
    u = ctypes.windll.user32
    fn = u.GetWindowLongPtrW
    fn.restype = ctypes.c_longlong
    fn.argtypes = [wintypes.HWND, ctypes.c_int]
    return bool(fn(h, -20) & 0x8)


def main():
    # 必须从空库起步：「首次运行会种一张每周待办」这类断言只有在干净数据目录下才有意义
    import shutil
    subprocess.run(["taskkill", "/F", "/IM", "FloatNote.exe"], capture_output=True)
    time.sleep(0.8)
    for name in ("index.json", "settings.json", "reminders.json"):
        p = os.path.join(DATA, name)
        if os.path.exists(p):
            os.remove(p)
    for name in ("notes", "trash"):
        p = os.path.join(DATA, name)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)

    p = subprocess.Popen([APP, "--dev"], cwd=WORK,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        cdp = None
        for _ in range(40):
            time.sleep(0.5)
            try:
                cdp = CDP()
                break
            except Exception:
                continue
        if cdp is None:
            print("FAIL 无法连接调试端口")
            return 1

        time.sleep(2.5)

        # --- 1. 基础结构 ---
        info = json.loads(cdp.eval("""JSON.stringify({
          items: [].map.call(document.querySelectorAll('.note-item'), function (e) {
            var m = e.querySelector('.note-mark');
            return {
              t: e.querySelector('.note-title').textContent,
              mark: m ? (m.textContent || 'dot') : '',
              pin: e.classList.contains('is-pinned'),
              del: !!e.querySelector('.note-del'),
              pinBtn: !!e.querySelector('.note-pin')
            };
          }),
          cats: [].map.call(document.querySelectorAll('.cat-chip'), function (e) { return e.textContent; }),
          hlApi: !!(window.CSS && CSS.highlights && window.Highlight),
          pinBtn: document.getElementById('btnPin').textContent,
          level: document.getElementById('levelText').textContent,
          paletteEmoji: document.querySelectorAll('#palEmoji button').length,
          paletteColor: document.querySelectorAll('#palColor button').length
        })"""))
        print(json.dumps(info, ensure_ascii=False, indent=1))

        titles = [x["t"] for x in info["items"]]
        check("首次运行生成「每周待办」", "每周待办" in titles, titles)
        check("每周待办被置顶", any(x["pin"] and x["t"] == "每周待办" for x in info["items"]))
        check("便签项带标记与删除按钮", all(x["mark"] and x["del"] and x["pinBtn"] for x in info["items"]))
        check("分类筛选条出现", len(info["cats"]) >= 2, info["cats"])
        check("Custom Highlight API 可用", info["hlApi"])
        check("固定置顶按钮显示为开启", "固定" in info["pinBtn"], info["pinBtn"])

        # --- 2. 窗口样式位 ---
        h = find_window()
        check("窗口存在", h != 0)
        if h:
            # 固定置顶每 2 秒校验一次，首次启动要等窗口激活后才补齐样式位，
            # 因此给最多 5 秒的收敛窗口，避免把「刚起来还没轮到」误判成缺陷
            top = False
            for _ in range(10):
                if ex_topmost(h):
                    top = True
                    break
                time.sleep(0.5)
            check("窗口带 WS_EX_TOPMOST", top)

        # --- 3. 每周待办正文 ---
        content = cdp.eval("window.__fn.state.contents.get(window.__fn.state.activeId)")
        import re as _re
        _segs = _re.findall(r"^##\s*(周[一二三四五六日天]|星期[一二三四五六日天])", content or "", _re.M)
        check("每周待办不按周几分段", (not _segs) and "## 本周" in (content or ""), _segs)
        check("每周待办标注周一 04:00 结算", "04:00" in (content or ""))
        check("每周待办写入 ISO 周次", "周 ·" in content, content.splitlines()[2][:40] if content else "")

        # --- 4. 切到欢迎便签，注入含日期/时间/地点/关键词的文本 ---
        cdp.eval("""(function () {
          var items = document.querySelectorAll('.note-item');
          for (var i = 0; i < items.length; i++) {
            if (!items[i].classList.contains('is-pinned')) { items[i].click(); break; }
          }
          return 'switched';
        })()""")
        time.sleep(0.8)

        cdp.eval("""(function () {
          var fn = window.__fn;
          fn.state.settings.autoLink = true;
          fn.state.settings.autoHighlight = true;
          fn.setEditorValue('# 测试便签\\n\\n交 每周待办 里的任务，今天 2026-10-26 14:30 在 @图书馆，明天下午3点前完成。\\n', false);
          return 'set';
        })()""")
        time.sleep(1.0)

        deco = json.loads(cdp.eval("""JSON.stringify({
          date: CSS.highlights.get('fn-date') ? CSS.highlights.get('fn-date').size : -1,
          time: CSS.highlights.get('fn-time') ? CSS.highlights.get('fn-time').size : -1,
          place: CSS.highlights.get('fn-place') ? CSS.highlights.get('fn-place').size : -1,
          pills: document.querySelectorAll('.link-pill').length,
          hint: [].map.call(document.querySelectorAll('.link-pill .ok'), function (e) { return e.title; })
        })"""))
        print(json.dumps(deco, ensure_ascii=False, indent=1))
        check("日期高亮命中", deco["date"] >= 2, deco["date"])
        check("时间高亮命中", deco["time"] >= 2, deco["time"])
        check("地点高亮命中", deco["place"] >= 1, deco["place"])
        check("关键词出现 ✓/✗ 确认条", deco["pills"] >= 1, deco["hint"])

        # --- 5. 点 ✓ 建立链接 ---
        cdp.eval("document.querySelector('.link-pill .ok').click()")
        time.sleep(1.2)
        link = json.loads(cdp.eval("""(function () {
          var lk = document.querySelector('.vditor-ir__link');
          var node = lk ? lk.closest('[data-type="a"]') : null;
          var urlEl = node ? node.querySelector('.vditor-ir__marker--link') : null;
          return JSON.stringify({
            count: document.querySelectorAll('.vditor-ir__link').length,
            href: urlEl ? urlEl.textContent.trim() : '',
            color: lk ? getComputedStyle(lk).color : '',
            text: lk ? lk.textContent : '',
            deco: lk ? getComputedStyle(lk).textDecorationLine : '',
            src: (window.__fn.state.contents.get(window.__fn.state.activeId) || '').indexOf('floatnote://') >= 0
          });
        })()"""))
        print(json.dumps(link, ensure_ascii=False))
        check("正文里生成链接", link["count"] >= 1, link["text"])
        check("链接指向目标便签", link["href"].startswith("floatnote://note/"), link["href"])
        check("链接为蓝色超链接样式",
              link["color"] in ("rgb(47, 111, 208)", "rgb(116, 169, 234)") and "underline" in link["deco"],
              link["color"] + " / " + link["deco"])
        check("链接已写回 Markdown 源", link["src"])

        # 点链接可跳到目标便签
        target_id = link["href"].split("/")[-1]
        cdp.eval("document.querySelector('.vditor-ir__link').click()")
        time.sleep(0.8)
        moved = cdp.eval("(function(){var fn=window.__fn;return JSON.stringify({active:fn.state.activeId,target:'%s'});})()" % target_id)
        mv = json.loads(moved)
        check("点击链接跳到目标便签", mv["active"] == mv["target"], mv)

        # 回到测试便签继续后面的断言
        cdp.eval("""(function () {
          var items = document.querySelectorAll('.note-item');
          for (var i = 0; i < items.length; i++) {
            if (items[i].querySelector('.note-title').textContent === '测试便签') { items[i].click(); break; }
          }
        })()""")
        time.sleep(0.8)

        # --- 6. 已链接的文字不再提示；点 ✗ 后也不提示 ---
        cdp.eval("""(function () {
          var fn = window.__fn;
          fn.setEditorValue('# 测试便签\\n\\n每周待办 里还有一条没有链接。\\n', false);
          return 'ok';
        })()""")
        time.sleep(1.0)
        before = cdp.eval("document.querySelectorAll('.link-pill').length")
        cdp.eval("document.querySelector('.link-pill .no').click()")
        time.sleep(0.4)
        after = cdp.eval("document.querySelectorAll('.link-pill').length")
        check("点 ✗ 后不再提示", before >= 1 and after == 0, "%s -> %s" % (before, after))

        # --- 7. 标记面板与分类筛选 ---
        cdp.eval("""(function () {
          var fn = window.__fn;
          var item = document.querySelector('.note-item.is-active');
          fn.openPalette(fn.state.activeId, item.querySelector('.note-mark'));
          return 'open';
        })()""")
        time.sleep(0.4)
        pal = json.loads(cdp.eval("""(function () {
          var p = document.getElementById('markPalette');
          return JSON.stringify({
            open: !p.hidden,
            inView: p.getBoundingClientRect().right <= window.innerWidth + 1,
            style: getComputedStyle(p).display,
            emoji: document.querySelectorAll('#palEmoji button').length,
            color: document.querySelectorAll('#palColor button').length
          });
        })()"""))
        check("标记面板可打开且在视口内", pal["open"] and pal["inView"], pal["style"])
        check("标记面板预置 emoji 数量", pal["emoji"] >= 20, pal["emoji"])
        check("标记面板预置颜色数量", pal["color"] >= 8, pal["color"])

        cdp.eval("document.querySelectorAll('#palEmoji button')[6].click()")
        time.sleep(0.8)
        mark = json.loads(cdp.eval("""(function () {
          var fn = window.__fn;
          var n = null;
          fn.state.notes.forEach(function (x) { if (x.id === fn.state.activeId) n = x; });
          return JSON.stringify({
            mark: n ? n.mark : '',
            paletteClosed: document.getElementById('markPalette').hidden,
            icon: (document.querySelector('.note-item.is-active .note-mark') || {}).textContent
          });
        })()"""))
        print(json.dumps(mark, ensure_ascii=False))
        check("选中 emoji 即写入分类", mark["mark"].startswith("emoji:"), mark["mark"])
        check("分类图标反映到列表", bool(mark["icon"]))

        cdp.eval("document.querySelectorAll('.cat-chip')[2].click()")
        time.sleep(0.4)
        filt = cdp.eval("document.querySelectorAll('.note-item').length")
        check("按分类筛选生效", filt >= 1, filt)

        # --- 8. 新建菜单 ---
        cdp.eval("document.getElementById('btnNew').click()")
        time.sleep(0.4)
        menu = json.loads(cdp.eval("""(function () {
          var m = document.getElementById('newMenu');
          return JSON.stringify({
            open: !m.hidden,
            kinds: [].map.call(m.querySelectorAll('.pop-item'), function (e) { return e.getAttribute('data-kind'); })
          });
        })()"""))
        check("新建菜单含普通 / 每周待办", menu["open"] and "" in menu["kinds"] and "weekly" in menu["kinds"], menu)

        cdp.eval("""(function () {
          var items = document.querySelectorAll('#newMenu .pop-item');
          for (var i = 0; i < items.length; i++) {
            if (items[i].getAttribute('data-kind') === 'weekly') { items[i].click(); return 'w'; }
          }
        })()""")
        time.sleep(1.2)
        weekly2 = json.loads(cdp.eval("""(function () {
          var fn = window.__fn;
          var n = null;
          fn.state.notes.forEach(function (x) { if (x.id === fn.state.activeId) n = x; });
          return JSON.stringify({
            title: n ? n.title : '',
            kind: n ? n.kind : '',
            pin: n ? n.pin : false,
            hasWeek: (fn.state.contents.get(fn.state.activeId) || '').indexOf('## 本周') >= 0
          });
        })()"""))
        print(json.dumps(weekly2, ensure_ascii=False))
        check("新建每周待办带类型与本周分区",
              weekly2["kind"] == "weekly" and weekly2["hasWeek"] and weekly2["pin"], weekly2)

        # --- 9. 提醒：光标旁的时间提示 → 设置框 → 落盘 → 到点触发 ---
        cdp.eval("""(function () {
          var fn = window.__fn;
          fn.setEditorValue('# 提醒测试\\n\\n明天下午3点前交高数作业\\n', false);
          return 'ok';
        })()""")
        time.sleep(1.0)

        # 把光标放到「明天」后面，模拟刚敲完时间
        placed = cdp.eval("""(function () {
          var root = document.querySelector('.vditor-ir pre.vditor-reset');
          var ps = root.querySelectorAll('p');
          var target = null;
          for (var i = 0; i < ps.length; i++) {
            if ((ps[i].textContent || '').indexOf('明天下午') >= 0) { target = ps[i]; break; }
          }
          if (!target) return 'no-p';
          var node = null;
          for (var j = 0; j < target.childNodes.length; j++) {
            if (target.childNodes[j].nodeType === 3) { node = target.childNodes[j]; break; }
          }
          if (!node) return 'no-text';
          var rg = document.createRange();
          rg.setStart(node, 2);
          rg.collapse(true);
          var sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(rg);
          return 'caret@2';
        })()""")
        time.sleep(0.9)
        pill = json.loads(cdp.eval("""JSON.stringify({
          all: document.querySelectorAll('.link-pill').length,
          clock: document.querySelectorAll('.link-pill .clock').length
        })"""))
        check("光标停在时间后出现提醒提示条", pill["clock"] >= 1, pill)

        cdp.eval("document.querySelector('.link-pill .clock').parentElement.querySelector('.ok').click()")
        time.sleep(0.6)
        dlg = json.loads(cdp.eval("""JSON.stringify({
          open: !document.getElementById('remDialog').hidden,
          when: document.getElementById('remWhen').value,
          text: document.getElementById('remText').value
        })"""))
        print(json.dumps(dlg, ensure_ascii=False))
        check("弹出提醒设置框", dlg["open"])
        check("按自然语言预填时间", dlg["when"].endswith("15:00"), dlg["when"])
        check("预填提醒内容", len(dlg["text"]) > 0, dlg["text"])

        cdp.eval("document.getElementById('remSave').click()")
        time.sleep(1.2)

        rf = os.path.join(DATA, "reminders.json")
        stored = json.load(open(rf, encoding="utf-8")) if os.path.exists(rf) else []
        check("提醒写入 reminders.json", len(stored) == 1, stored)
        check("提醒记录关联到便签", bool(stored) and bool(stored[0].get("noteId")), stored[0] if stored else "")

        cdp.eval("document.getElementById('tabRemind').click()")
        time.sleep(0.5)
        tab = json.loads(cdp.eval("""JSON.stringify({
          remindVisible: !document.getElementById('remindList').hidden,
          notesHidden: document.getElementById('noteList').hidden,
          items: document.querySelectorAll('.rem-item').length,
          badge: document.getElementById('remindCount').textContent,
          when: (document.querySelector('.rem-when') || {}).textContent || ''
        })"""))
        print(json.dumps(tab, ensure_ascii=False))
        check("侧栏切到提醒分页", tab["remindVisible"] and tab["notesHidden"])
        check("提醒列表显示 1 条", tab["items"] == 1, tab["when"])
        check("提醒角标显示待触发数", tab["badge"] == "1", tab["badge"])

        # 再插一条 4 秒后到点的，验证调度与系统通知
        cdp.eval("""(function () {
          var fn = window.__fn;
          window.chrome.webview.postMessage({
            type: 'addReminder', noteId: fn.state.activeId,
            text: '到点测试', dueAt: Date.now() + 4000
          });
          return 'posted';
        })()""")
        time.sleep(1.0)
        n2 = len(json.load(open(rf, encoding="utf-8"))) if os.path.exists(rf) else 0
        check("第二条提醒写入", n2 == 2, n2)

        time.sleep(21)
        stored = json.load(open(rf, encoding="utf-8")) if os.path.exists(rf) else []
        fired = [r for r in stored if r.get("fired")]
        check("到点后标记为已触发", len(fired) >= 1, [(r["text"], r["fired"]) for r in stored])

        logpath0 = os.path.join(DATA, "app.log")
        log0 = open(logpath0, encoding="utf-8", errors="replace").read() if os.path.exists(logpath0) else ""
        check("到点写出触发日志（说明已发系统通知）", "提醒触发" in log0,
              [l for l in log0.splitlines() if "提醒触发" in l][:1])

        # 提醒分页里已触发的项应带划线状态
        cdp.eval("document.getElementById('tabRemind').click()")
        time.sleep(0.5)
        fired_ui = cdp.eval("document.querySelectorAll('.rem-item.is-fired').length")
        check("已触发提醒在列表中有已办状态", fired_ui >= 1, fired_ui)

        # --- 10. 日志无 JS 错误 ---
        logpath = os.path.join(DATA, "app.log")
        logtxt = open(logpath, encoding="utf-8", errors="replace").read() if os.path.exists(logpath) else ""
        errs = [l for l in logtxt.splitlines() if "JS错误" in l or "ERROR" in l or "Promise拒绝" in l]
        check("运行期间无 JS 错误", not errs, errs[:3])

        idx = os.path.join(DATA, "index.json")
        if os.path.exists(idx):
            j = json.load(open(idx, encoding="utf-8"))
            print("index.json:", json.dumps(j, ensure_ascii=False)[:600])

        cdp.close()
    finally:
        try:
            p.terminate()
            time.sleep(0.6)
            subprocess.run(["taskkill", "/F", "/PID", str(p.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    bad = [r for r in results if not r[1]]
    print("\n=== %d/%d 通过 ===" % (len(results) - len(bad), len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
