"""无侵入验收：每周待办（不分周几 / 周一 04:00 结算 / 未完成标红）、
二级子任务（Tab 与父子联动）、ddl 截止日期。

全部通过 CDP 在页面内断言，不发送真实键鼠、不移动窗口。
"""
import json
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP  # noqa: E402
from guard import guard_before_test  # noqa: E402

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _norm_exe(p):
    # 有些 shell 会把反斜杠再转义一层，路径里出现 \\\\ 会导致字符串比对失败
    return p.replace("\\\\", "\\") if p else p


EXE = _norm_exe(os.environ.get(
    "FN_EXE",
    os.path.join(_SRC, "bin", "Release", "net10.0-windows", "FloatNote.exe")))
DATA = os.path.join(os.path.dirname(EXE), "data")

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (("   -> " + str(detail)) if detail else ""))


def port_open(port=9222):
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def main():
    if not os.path.exists(EXE):
        print("找不到 exe：" + EXE)
        return 2

    # 干净起步：删掉索引与正文，让程序重新种模板
    import shutil
    for name in ("index.json", "settings.json", "reminders.json"):
        p = os.path.join(DATA, name)
        if os.path.exists(p):
            os.remove(p)
    for name in ("notes", "trash"):
        p = os.path.join(DATA, name)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)

    # 守卫：只要桌面上还有别的浮笺实例（可能是用户本人在用），整个测试立即中止
    guard_before_test()

    proc = subprocess.Popen([EXE, "--dev"], close_fds=True)
    cdp = None
    try:
        for _ in range(60):
            if port_open():
                break
            time.sleep(0.4)
        if not port_open():
            print("调试端口未就绪")
            return 2
        cdp = CDP()
        for _ in range(60):
            try:
                if cdp.eval("!!(window.__fn && window.__fn.state.vditor && window.__fn.state.activeId)"):
                    break
            except Exception:
                pass
            time.sleep(0.4)
        time.sleep(1.2)

        def body():
            return cdp.eval("window.__fn.state.vditor.getValue()")

        def render(md, wait=1.1):
            cdp.eval("window.__fn.setEditorValue(%s, true)" % json.dumps(md))
            time.sleep(wait)
            cdp.eval("window.__fn.decorate()")
            time.sleep(0.45)

        def hl_size(kind):
            return cdp.eval(
                "(function(){var h=CSS.highlights.get('%s');return h?h.size:0;})()" % kind)

        # ============ 1. 模板不按周几分段 ============
        weekly = cdp.eval("""
        (function(){
          var n = window.__fn.state.notes.find(function(x){return x.kind==='weekly';});
          return n ? window.__fn.state.contents.get(n.id) : '';
        })()
        """)
        print("  模板正文：", repr(weekly[:180]))
        import re as _re
        segs = _re.findall(r"^##\s*(周[一二三四五六日天]|星期[一二三四五六日天])", weekly or "", _re.M)
        check("模板不含「周一~周日」分段", not segs, segs)
        check("模板含「## 本周」", "## 本周" in (weekly or ""), (weekly or "")[:60])
        check("模板标注了周一 04:00 结算", "04:00" in (weekly or ""), "")

        # ============ 2. 结算：完成清除、未完成搬入「上周未完成」============
        old_style = (
            "# 每周待办\n\n"
            "> 第 44 周 · 2026-10-26 — 11-01\n\n"
            "## 周一 10-26\n\n"
            "- [x] 已经做完的事\n"
            "- [ ] 没做完的事\n\n"
            "## 周二 10-27\n\n"
            "- [x] 周二做完的\n"
            "- [ ] 周二没做的\n"
        )
        render(old_style)
        cdp.eval("chrome.webview.postMessage({type:'weeklyReset'})")
        time.sleep(1.8)
        after = body()
        print("  结算后：", repr(after))
        check("已完成任务被清除", "- [x] 已经做完的事" not in after and "周二做完的" not in after)
        check("未完成任务保留", "没做完的事" in after and "周二没做的" in after)
        check("生成「上周未完成」区块", "上周未完成" in after, after[:120])

        # ============ 3. 未完成整块标红 ============
        render(after, wait=0.9)
        carry_hl = hl_size("fn-carry")
        check("「上周未完成」区块被染色", carry_hl >= 1, "ranges=%s" % carry_hl)

        # ============ 4. 二级子任务：结构 + Tab 缩进 ============
        render("- [ ] 主任务\n- [ ] 待缩进任务\n")
        cdp.eval("""
        (function(){
          var pre = document.querySelector('.vditor-ir pre.vditor-reset');
          window.__fn.state.vditor.focus();
          var w = document.createTreeWalker(pre, NodeFilter.SHOW_TEXT, null);
          var node = null;
          while (w.nextNode()) {
            if (w.currentNode.nodeValue.indexOf('待缩进任务') >= 0) { node = w.currentNode; break; }
          }
          if (!node) return;
          var r = document.createRange();
          r.selectNodeContents(node);
          r.collapse(false);
          var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
          pre.dispatchEvent(new KeyboardEvent('keydown',
            {key:'Tab', bubbles:true, cancelable:true}));
        })()
        """)
        time.sleep(1.0)
        v = body()
        nested = cdp.eval("document.querySelectorAll('#editor li li').length")
        check("Tab 让任务变成子任务", nested >= 1 and "  - " in v.replace("\n", "|"), v[:80])

        # ============ 5. 子任务全完成 → 主任务自动完成 ============
        render("- [ ] 主任务\n  - [ ] 子一\n  - [ ] 子二\n", wait=1.2)
        boxes = cdp.eval("document.querySelectorAll('#editor input[type=checkbox]').length")
        cdp.eval("""
        (function(){
          var bs = document.querySelectorAll('#editor input[type=checkbox]');
          if (bs.length >= 3) { bs[1].click(); }
        })()
        """)
        time.sleep(0.9)
        cdp.eval("""
        (function(){
          var bs = document.querySelectorAll('#editor input[type=checkbox]');
          if (bs.length >= 3) { bs[2].click(); }
        })()
        """)
        time.sleep(1.6)
        v = body()
        print("  联动后：", repr(v))
        check("checkboxes 数量正确", boxes == 3, boxes)
        check("两条子任务都勾上后主任务自动完成",
              bool(_re.search(r"- \[x\]\s*主任务", v, _re.I)), v[:80])

        # ============ 6. ddl 渲染：整段红 + 花括号透明 ============
        render("交作业 ddl{2026-11-01 23:59} 之前交给老师\n", wait=1.3)
        check("ddl 整段被染色", hl_size("fn-ddl") == 1, hl_size("fn-ddl"))
        check("两个花括号被单独染色", hl_size("fn-ddl-brace") == 2, hl_size("fn-ddl-brace"))
        color = cdp.eval("getComputedStyle(document.documentElement).getPropertyValue('--danger').trim()")
        check("红色变量可用", bool(color), color)

        # ============ 6b. ddl 里的短日期 / 未闭合 / 重复括号 ============
        render("英语作业（ddl{11-1 11:30}）\n", wait=1.3)
        check("ddl 内短日期整段染色", hl_size("fn-ddl") == 1, hl_size("fn-ddl"))
        check("ddl 内的日期不叠日期底色", hl_size("fn-date") == 0, hl_size("fn-date"))
        check("ddl 内的时间不叠时间底色", hl_size("fn-time") == 0, hl_size("fn-time"))

        render("英语作业 ddl{11-1 11:3\n", wait=1.3)
        check("未闭合的 ddl 也染色", hl_size("fn-ddl") == 1, hl_size("fn-ddl"))

        render("英语作业 ddl{{11-1 11:30}}\n", wait=1.3)
        check("重复花括号仍整段染色", hl_size("fn-ddl") == 1, hl_size("fn-ddl"))
        check("连续括号各算一处透明", hl_size("fn-ddl-brace") == 2, hl_size("fn-ddl-brace"))

        render("# 英语作业 ddl{11-1 11:30}\n\n正文\n", wait=1.3)
        check("标题里的 ddl 也染色", hl_size("fn-ddl") == 1, hl_size("fn-ddl"))

        cand = cdp.eval("""
        (function(){
          var root = document.querySelector('.vditor-ir pre.vditor-reset');
          var h = root.querySelector('h1') || root.firstElementChild;
          var w = document.createTreeWalker(h, NodeFilter.SHOW_TEXT, null);
          var last = null; while (w.nextNode()) last = w.currentNode;
          if (!last) return null;
          var r = document.createRange();
          r.selectNodeContents(last); r.collapse(false);
          var s = getSelection(); s.removeAllRanges(); s.addRange(r);
          window.__fn.decorate();
          var hit = window.__fn.findRemindCandidate();
          return hit ? hit.text : null;
        })()
        """)
        print("  标题内提醒候选：", cand)
        check("标题里的时间能触发提醒候选", bool(cand), cand)

        # ============ 6c. 提醒时间解析：ddl 优先、短日期补年份 ============
        def parsed(line):
            return cdp.eval(
                "(function(){var d=window.__fn.parseWhen(%s,new Date());"
                "return [d.getFullYear(),d.getMonth()+1,d.getDate(),"
                "d.getHours(),d.getMinutes()].join('-');})()" % json.dumps(line))

        p1 = parsed("英语作业（ddl{11-1 11:30}）")
        print("  ddl 短日期解析：", p1)
        check("ddl{11-1 11:30} 解析出 11 月 1 日 11:30", p1.endswith("-11-1-11-30"), p1)
        check("短日期未给年份时补成未来年份", int(p1.split("-")[0]) >= 2026, p1)

        p2 = parsed("交作业 ddl{11月1日 11:30} 第 3 题")
        print("  ddl 中文日期解析：", p2)
        check("ddl{11月1日 11:30} 解析出 11 月 1 日 11:30", p2.endswith("-11-1-11-30"), p2)

        p3 = parsed("11-1 开会 11:30")
        print("  非 ddl 行解析：", p3)
        check("非 ddl 行同样认得 11-1", p3.endswith("-11-1-11-30"), p3)

        # ============ 7. 打字不篡改正文（旧版自动补 {}，已移除）============
        render("交作业 ", wait=0.9)
        cdp.eval("""
        (function(){
          var pre = document.querySelector('.vditor-ir pre.vditor-reset');
          window.__fn.state.vditor.focus();
          var w = document.createTreeWalker(pre, NodeFilter.SHOW_TEXT, null);
          var last = null;
          while (w.nextNode()) { last = w.currentNode; }
          if (!last) return;
          var r = document.createRange();
          r.selectNodeContents(last); r.collapse(false);
          var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
          document.execCommand('insertText', false, 'ddl');
        })()
        """)
        time.sleep(1.2)
        v = body()
        print("  打完 ddl 后：", repr(v))
        check("打完 ddl 不擅自插入花括号", "{" not in v, v[:60])
        check("正文保持用户原样", "交作业" in v and v.strip().endswith("ddl"), v[:60])

        # ============ 8. 代码块里的 ddl 不参与染色 ============
        render("```\nddl{11-1 11:30}\n```\n", wait=1.3)
        check("代码块里的 ddl 不染色", hl_size("fn-ddl") == 0, hl_size("fn-ddl"))

        # ============ 9. 无 JS 异常 ============
        log = os.path.join(DATA, "app.log")
        if os.path.exists(log):
            txt = open(log, encoding="utf-8", errors="replace").read()[-6000:]
            bad = [l for l in txt.splitlines() if "[ERROR]" in l or "Unhandled" in l]
            check("无 JS/宿主异常", not bad, bad[:2])

    finally:
        if cdp:
            try:
                cdp.close()
            except Exception:
                pass
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        time.sleep(0.4)

    print()
    print("=== %d/%d 通过 ===" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
