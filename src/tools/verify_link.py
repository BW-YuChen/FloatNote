"""无侵入验收：floatnote:// 内部协议是否被彻底拦住。

背景：`floatnote://` 是本程序自定义的伪协议，系统没有注册处理器。
只要有一次导航漏到 WebView2 的默认流程，Windows 就会弹出
「获取打开此 floatnote 链接的应用 / 浏览 Microsoft Store」对话框。

本脚本在页面内用 CDP 注入断言，全程不移动窗口、不抢焦点、不发送真实鼠标键盘事件。
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
LOG = os.path.join(DATA, "app.log")

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


def log_size():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return 0


def log_since(offset):
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            return f.read()
    except OSError:
        return ""


def wait_ready(cdp, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if cdp.eval("!!(window.__fn && window.__fn.state && window.__fn.state.activeId)") is True:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def main():
    if not os.path.exists(EXE):
        print("找不到 dev 版 exe：" + EXE)
        return 2

    import shutil
    for name in ("index.json", "reminders.json", "settings.json"):
        p = os.path.join(DATA, name)
        if os.path.exists(p):
            os.remove(p)
    for name in ("notes", "trash"):
        p = os.path.join(DATA, name)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)

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
        time.sleep(0.8)

        state = cdp.eval(
            "JSON.stringify({k:Object.keys(window.__fn.state),"
            "notes:(window.__fn.state.notes||[]).map(n=>({id:n.id,title:n.title}))})"
        )
        info = json.loads(state)
        notes = info["notes"]
        print("  state keys:", ",".join(info["k"]))
        print("  notes:", [(n["title"], n["id"]) for n in notes])

        for _ in range(5):
            if len(notes) >= 2:
                break
            cdp.eval("window.__fn.createNote('测试目标')")
            time.sleep(0.9)
            notes = json.loads(cdp.eval(
                "JSON.stringify((window.__fn.state.notes||[]).map(n=>({id:n.id,title:n.title})))"
            ))
        print("  notes:", [(n["title"], n["id"]) for n in notes])

        src, dst = notes[0]["id"], notes[1]["id"]

        # ---- 1. 渲染出 floatnote:// 链接 ----
        cdp.eval("window.__fn.selectNote(%s)" % json.dumps(src))
        time.sleep(0.5)
        md = "[去目标](floatnote://note/%s)\n" % dst
        cdp.eval("window.__fn.setEditorValue(%s,true)" % json.dumps(md))
        time.sleep(0.5)
        cdp.eval("window.__fn.decorate()")
        time.sleep(0.9)

        dom = json.loads(cdp.eval(
            "JSON.stringify({"
            "anchors:document.querySelectorAll('#editor a[href^=\"floatnote://\"]').length,"
            "irnodes:document.querySelectorAll('#editor [data-type=\"a\"]').length,"
            "irspans:document.querySelectorAll('#editor .vditor-ir__link').length"
            "})"
        ))
        print("  DOM:", dom)
        check("编辑器里出现 floatnote:// 链接结构",
              dom["anchors"] + dom["irnodes"] > 0, dom)

        # ---- 2. 裸 <a> 分支：注入一个真实 anchor，验证统一接管逻辑 ----
        before = log_size()
        r1 = json.loads(cdp.eval("""
        (function(){
          var a = document.createElement('a');
          a.setAttribute('href', 'floatnote://note/DSTID');
          a.textContent = 'probe';
          a.style.cssText = 'position:fixed;left:-9999px;top:0';
          document.body.appendChild(a);
          var e = new MouseEvent('click', {bubbles:true, cancelable:true, view:window});
          a.dispatchEvent(e);
          a.remove();
          return JSON.stringify({defaultPrevented:e.defaultPrevented});
        })()
        """.replace('DSTID', dst)))
        time.sleep(0.7)
        active1 = cdp.eval("window.__fn.state.activeId")
        check("裸 <a> 点击被 preventDefault", r1.get("defaultPrevented") is True, r1)
        check("裸 <a> 点击跳到目标便签", active1 == dst, "%s -> %s" % (src, active1))

        # ---- 3. 点编辑态的 span[data-type=a] ----
        cdp.eval("window.__fn.selectNote(%s)" % json.dumps(src))
        time.sleep(0.5)
        cdp.eval("window.__fn.setEditorValue(%s,true)" % json.dumps(md))
        time.sleep(0.4)
        cdp.eval("window.__fn.decorate()")
        time.sleep(0.8)
        r2 = cdp.eval("""
        (function(){
          var n = document.querySelector('#editor [data-type="a"]');
          if (!n) return {no:'node'};
          var el = n.querySelector('.vditor-ir__link') || n;
          var e = new MouseEvent('click', {bubbles:true, cancelable:true, view:window});
          el.dispatchEvent(e);
          return {defaultPrevented:e.defaultPrevented};
        })()
        """)
        time.sleep(0.7)
        active2 = cdp.eval("window.__fn.state.activeId")
        check("点击编辑态链接被 preventDefault", bool(r2 and r2.get("defaultPrevented")), r2)
        check("编辑态点击同样能跳转", active2 == dst, active2)

        # ---- 4. window.open 走 NewWindowRequested ----
        cdp.eval("window.__fn.selectNote(%s)" % json.dumps(src))
        time.sleep(0.5)
        cdp.eval("window.open('floatnote://note/%s')" % dst)
        time.sleep(1.0)
        check("window.open 内部跳转", cdp.eval("window.__fn.state.activeId") == dst,
              cdp.eval("window.__fn.state.activeId"))

        seg = log_since(before)
        check("以上操作未触发任何页面导航", "nav starting" not in seg and "blocked nav" not in seg,
              seg.strip().replace("\n", " | ")[:200] or "(无新增导航日志)")

        # ---- 5. 绕过前端直接改 location：宿主 NavigationStarting 必须 cancel ----
        before2 = log_size()
        cdp.eval("setTimeout(function(){ location.href='floatnote://ping'; },10); 'ok'")
        time.sleep(1.5)
        alive = cdp.eval("document.readyState")
        seg2 = log_since(before2)
        check("绕过前端导航后页面仍存活（说明被宿主拦下）",
              alive in ("complete", "interactive"), alive)
        check("宿主记录了内部协议的拦截", "InternalUri: ignored floatnote://ping" in seg2,
              seg2.strip().replace("\n", " | ")[:240] or "(无相关日志)")

        # ---- 6. 全流程无 JS 错误 ----
        seg3 = log_since(before)
        check("过程中无 JS 错误", "[web][error]" not in seg3.lower() and "JS ERROR" not in seg3, "")

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
