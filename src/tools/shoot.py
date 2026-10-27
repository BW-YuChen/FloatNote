import base64
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(_SRC, "bin", "Debug", "net10.0-windows")
def _norm_exe(p):
    # 有些 shell 会把反斜杠再转义一层，路径里出现 \\\\ 会导致字符串比对失败
    return p.replace("\\\\", "\\") if p else p


APP = _norm_exe(os.environ.get("FN_EXE", os.path.join(WORK, "FloatNote.exe")))
OUT = os.path.join(os.path.dirname(_SRC), "_shots")

os.makedirs(OUT, exist_ok=True)


def shot(cdp, name):
    r = cdp.call("Page.captureScreenshot", {"format": "png"})
    path = os.path.join(OUT, name + ".png")
    with open(path, "wb") as f:
        f.write(base64.b64decode(r["data"]))
    print("saved", path, os.path.getsize(path))


def theme(cdp, want):
    cdp.eval("""(function () {
      var b = document.getElementById('btnTheme');
      for (var i = 0; i < 4; i++) {
        if (document.documentElement.getAttribute('data-theme') === '%s') break;
        b.click();
      }
      return document.documentElement.getAttribute('data-theme');
    })()""" % want)
    time.sleep(0.7)


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
            pass
    if cdp is None:
        print("no cdp")
        sys.exit(1)
    time.sleep(3.0)

    theme(cdp, "light")
    shot(cdp, "01-weekly")

    # 建一条提醒并打开提醒分页
    cdp.eval("""(function () {
      var fn = window.__fn;
      window.chrome.webview.postMessage({
        type: 'addReminder', noteId: fn.state.activeId,
        text: '交高数第三章作业', dueAt: Date.now() + 5400e3
      });
      return 'ok';
    })()""")
    time.sleep(0.8)
    cdp.eval("document.getElementById('tabRemind').click()")
    time.sleep(0.5)
    shot(cdp, "02-reminders")
    cdp.eval("document.getElementById('tabNotes').click()")
    time.sleep(0.4)

    # 写一句话并停在时间后，触发 ⏰ 提示，再打开设置框
    cdp.eval("""(function () {
      var fn = window.__fn;
      fn.setEditorValue('# 高数作业\\n\\n明天下午3点前交第三章作业，地点 @图书馆。\\n', false);
      return 'ok';
    })()""")
    time.sleep(1.2)
    cdp.eval("""(function () {
      var root = document.querySelector('.vditor-ir pre.vditor-reset');
      var ps = root.querySelectorAll('p');
      var t = null;
      for (var i = 0; i < ps.length; i++) {
        if ((ps[i].textContent || '').indexOf('明天下午') >= 0) { t = ps[i]; break; }
      }
      var node = null;
      for (var j = 0; j < t.childNodes.length; j++) {
        if (t.childNodes[j].nodeType === 3) { node = t.childNodes[j]; break; }
      }
      var rg = document.createRange();
      rg.setStart(node, 2);
      rg.collapse(true);
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(rg);
      return 'ok';
    })()""")
    time.sleep(1.0)
    shot(cdp, "03-remind-pill")

    cdp.eval("document.querySelector('.link-pill .clock').parentElement.querySelector('.ok').click()")
    time.sleep(0.7)
    shot(cdp, "04-remind-dialog")
    cdp.eval("document.getElementById('remCancel').click()")
    time.sleep(0.4)

    theme(cdp, "dark")
    cdp.eval("document.getElementById('tabRemind').click()")
    time.sleep(0.5)
    shot(cdp, "05-dark-reminders")

    cdp.close()
finally:
    p.terminate()
    time.sleep(0.5)
    subprocess.run(["taskkill", "/F", "/PID", str(p.pid)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
