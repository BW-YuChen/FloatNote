"""静默探针：把样例文本灌进编辑器，dump 出 DOM 拆点与已注册的高亮区间。

用途：定位「ddl{...} 有时不高亮」「短日期 11-1 不被识别」这类问题。
全程只通过 CDP 在页面内求值，不注入鼠标键盘，跑完即退出。
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP
from guard import guard_before_test

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(_SRC, "bin", "Release", "net10.0-windows", "FloatNote.exe")
WORK = os.path.dirname(APP)

SAMPLES = [
    "英语作业（ddl{11-1 11:30}）",
    "# 英语作业 ddl{11-1 11:30}",
    "- [ ] 英语作业 ddl{11-1 11:30}",
    "英语作业 ddl{{11-1 11:30}）}",
]

DUMP = r"""
(function () {
  var root = document.querySelector('.vditor-ir pre.vditor-reset');
  if (!root) return { err: 'no root' };
  var out = { html: root.innerHTML, nodes: [], hl: {} };
  var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  var i = 0, n;
  while ((n = w.nextNode())) {
    out.nodes.push({ i: i++, text: n.nodeValue, parent: n.parentElement ? n.parentElement.className || n.parentElement.tagName : '' });
  }
  ['fn-ddl', 'fn-ddl-brace', 'fn-date', 'fn-time'].forEach(function (k) {
    var h = CSS.highlights.get(k);
    if (!h) { out.hl[k] = null; return; }
    var arr = [];
    for (var r of h) arr.push(r.toString());
    out.hl[k] = arr;
  });
  return out;
})()
"""


def main():
    # 守卫：桌面上还有别的浮笺实例（可能是用户本人在用）时，探针不启动
    guard_before_test()
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
        for _ in range(30):
            if cdp.eval("!!(window.__fn && window.__fn.state.booted && window.__fn.state.vditor)"):
                break
            time.sleep(0.3)

        cdp.eval("window.__fn.createNote && window.__fn.createNote()")
        time.sleep(0.6)

        for s in SAMPLES:
            cdp.eval("window.__fn.setEditorValue(%s)" % json.dumps(s))
            time.sleep(0.7)
            d = cdp.eval(DUMP)
            print("=" * 70)
            print("IN : " + s)
            print("OUT: " + json.dumps(d.get("hl"), ensure_ascii=False))
            if not d.get("nodes"):
                print("HTML: " + str(d.get("html"))[:600])
            else:
                for nd in d["nodes"]:
                    if nd["text"].strip():
                        print("  node[%d] %-18s %r" % (nd["i"], nd["parent"][:18], nd["text"]))
                print("  HTML: " + str(d.get("html"))[:400])
        cdp.close()
    finally:
        try:
            p.terminate()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
