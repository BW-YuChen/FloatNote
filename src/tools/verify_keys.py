"""无侵入验收：Markdown 行内格式快捷键 + ==高亮== 语法。

全部通过 CDP 在页面内注入按键事件完成，不发送真实键盘事件、不移动窗口。
"""
import json
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP  # noqa: E402

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


def wait_ready(cdp, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if cdp.eval("!!(window.__fn && window.__fn.state.activeId && window.__fn.state.vditor)") is True:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


# 在正文里选中某段文字，再把按键派发到编辑器节点
SELECT_AND_KEY = """
(function(){
  var pre = document.querySelector('.vditor-ir pre.vditor-reset');
  if (!pre) return JSON.stringify({err:'no pre'});
  window.__fn.state.vditor.focus();
  var walker = document.createTreeWalker(pre, NodeFilter.SHOW_TEXT, null);
  var node = null, target = NEEDLE;
  while (walker.nextNode()) {
    if (walker.currentNode.nodeValue.indexOf(target) >= 0) { node = walker.currentNode; break; }
  }
  if (!node) return JSON.stringify({err:'text not found'});
  var i = node.nodeValue.indexOf(target);
  var r = document.createRange();
  r.setStart(node, i);
  r.setEnd(node, i + target.length);
  var sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(r);

  var ev = new KeyboardEvent('keydown', {
    key: KEY, ctrlKey: true, shiftKey: SHIFT,
    bubbles: true, cancelable: true
  });
  pre.dispatchEvent(ev);
  return JSON.stringify({ok:true, prevented: ev.defaultPrevented});
})()
"""


def press(cdp, needle, key, shift=False):
    js = (SELECT_AND_KEY
          .replace("NEEDLE", json.dumps(needle))
          .replace("KEY", json.dumps(key))
          .replace("SHIFT", "true" if shift else "false"))
    out = cdp.eval(js)
    return json.loads(out) if out else {}


def set_body(cdp, md):
    cdp.eval("window.__fn.setEditorValue(%s, true)" % json.dumps(md))
    time.sleep(0.55)
    cdp.eval("window.__fn.decorate()")
    time.sleep(0.35)


def value(cdp):
    return cdp.eval("window.__fn.state.vditor.getValue()")


def main():
    if not os.path.exists(EXE):
        print("找不到 exe：" + EXE)
        return 2
    proc = subprocess.Popen([EXE, "--dev"], close_fds=True)
    cdp = None
    try:
        end = time.time() + 30
        while time.time() < end and not port_open():
            time.sleep(0.4)
        if not port_open():
            print("调试端口未就绪")
            return 2
        cdp = CDP()
        if not wait_ready(cdp):
            print("页面未就绪")
            return 2
        time.sleep(1.0)

        # ================= 1. 高亮语法是否被解析 =================
        set_body(cdp, "这里有 ==重点内容== 请留意")
        marks = cdp.eval("document.querySelectorAll('#editor mark').length")
        mtext = cdp.eval("(document.querySelector('#editor mark')||{}).textContent || ''")
        check("==text== 渲染成 <mark>", marks == 1, "mark=%s text=%r" % (marks, mtext))
        check("<mark> 内容正确", mtext == "重点内容", mtext)
        mbg = cdp.eval("""
        (function(){
          var m = document.querySelector('#editor mark');
          if (!m) return '';
          return getComputedStyle(m).backgroundColor;
        })()
        """)
        check("<mark> 有底色", bool(mbg) and mbg not in ("rgba(0, 0, 0, 0)", "transparent"), mbg)

        # 反例：代码里的双等号不应被误判
        set_body(cdp, "判断 `a == b` 是否相等")
        check("行内代码里的 == 不被误判",
              cdp.eval("document.querySelectorAll('#editor mark').length") == 0,
              cdp.eval("document.querySelectorAll('#editor mark').length"))

        # ================= 2. Ctrl+B 粗体 =================
        set_body(cdp, "hello world")
        r = press(cdp, "hello", "b")
        v = value(cdp)
        check("Ctrl+B 事件被接管", r.get("ok") is True and r.get("prevented") is True, r)
        check("Ctrl+B 产出 **hello**", "**hello**" in v.replace("\n", " "), v)

        # ================= 3. Ctrl+I 斜体 =================
        set_body(cdp, "hello world")
        press(cdp, "hello", "i")
        v = value(cdp)
        check("Ctrl+I 产出 *hello*", "*hello*" in v and "**hello**" not in v, v)

        # ================= 4. Ctrl+Shift+X 删除线 =================
        set_body(cdp, "hello world")
        press(cdp, "hello", "x", shift=True)
        v = value(cdp)
        check("Ctrl+Shift+X 产出 ~~hello~~", "~~hello~~" in v, v)

        # ================= 5. Ctrl+Shift+H 高亮 =================
        set_body(cdp, "hello world")
        press(cdp, "hello", "h", shift=True)
        v = value(cdp)
        check("Ctrl+Shift+H 产出 ==hello==", "==hello==" in v, v)
        time.sleep(0.5)
        check("快捷键产生的高亮能渲染",
              cdp.eval("document.querySelectorAll('#editor mark').length") == 1,
              cdp.eval("document.querySelectorAll('#editor mark').length"))

        # ================= 6. Ctrl+E 行内代码 =================
        set_body(cdp, "hello world")
        press(cdp, "hello", "e")
        v = value(cdp)
        check("Ctrl+E 产出 `hello`", "`hello`" in v, v)

        # ================= 7. Ctrl+Shift+K 链接 =================
        set_body(cdp, "hello world")
        press(cdp, "hello", "k", shift=True)
        v = value(cdp)
        check("Ctrl+Shift+K 产出链接骨架", "[hello](" in v, v)

        # ================= 8. Ctrl+Shift+M 行内公式 =================
        set_body(cdp, "hello world")
        press(cdp, "hello", "m", shift=True)
        v = value(cdp)
        check("Ctrl+Shift+M 产出 $hello$", "$hello$" in v, v)
        time.sleep(0.6)
        check("行内公式被 KaTeX 渲染",
              cdp.eval("document.querySelectorAll('#editor .katex').length") >= 1,
              cdp.eval("document.querySelectorAll('#editor .katex').length"))

        # ================= 9. 无选区时插入占位并选中 =================
        set_body(cdp, "abc")
        cdp.eval("""
        (function(){
          var pre = document.querySelector('.vditor-ir pre.vditor-reset');
          window.__fn.state.vditor.focus();
          var w = document.createTreeWalker(pre, NodeFilter.SHOW_TEXT, null);
          var last = null;
          while (w.nextNode()) { last = w.currentNode; }
          var r = document.createRange();
          r.selectNodeContents(last);
          r.collapse(false);
          var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
          pre.dispatchEvent(new KeyboardEvent('keydown',
            {key:'b', ctrlKey:true, bubbles:true, cancelable:true}));
        })()
        """)
        time.sleep(0.5)
        v = value(cdp)
        check("无选区时插入占位粗体", "**粗体**" in v, v)
        sel_text = cdp.eval("(window.getSelection()||{}).toString()")
        check("占位文字被自动选中", sel_text == "粗体", repr(sel_text))

        # ================= 10. 非正文区域不抢按键 =================
        r2 = cdp.eval("""
        (function(){
          var el = document.createElement('input');
          el.type = 'text';
          el.style.cssText = 'position:fixed;left:-9999px';
          document.body.appendChild(el);
          el.focus();
          var ev = new KeyboardEvent('keydown',
            {key:'b', ctrlKey:true, bubbles:true, cancelable:true});
          el.dispatchEvent(ev);
          var prevented = ev.defaultPrevented;
          el.remove();
          return JSON.stringify({prevented: prevented});
        })()
        """)
        r2 = json.loads(r2) if r2 else {}
        check("普通输入框里的 Ctrl+B 不被接管", r2.get("prevented") is False, r2)

        # ================= 11. 原有 Ctrl+S 未被破坏 =================
        set_body(cdp, "save check")
        cdp.eval("""
        document.querySelector('.vditor-ir pre.vditor-reset').dispatchEvent(
          new KeyboardEvent('keydown', {key:'s', ctrlKey:true, bubbles:true, cancelable:true}))
        """)
        time.sleep(0.4)
        check("Ctrl+S 仍然只触发保存（不产生 ~~）", "~~" not in value(cdp), value(cdp))

        # ================= 12. 无 JS 异常 =================
        log = os.path.join(DATA, "app.log")
        if os.path.exists(log):
            txt = open(log, encoding="utf-8", errors="replace").read()
            tail = txt[-4000:]
            bad = [l for l in tail.splitlines() if "[ERROR]" in l or "Unhandled" in l]
            check("过程中无 JS/宿主异常", not bad, bad[:2])

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

    print()
    print("=== %d/%d 通过 ===" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
