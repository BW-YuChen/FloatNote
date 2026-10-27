"""静默探针 2：模拟真实手打，复现 ddl 自动补括号与高亮的实际 DOM 形态。

用 CDP Input.insertText 逐字送入（不注入鼠标、不抢焦点），
分别在若干节点上 dump 文本节点拆分情况与已注册高亮区间。
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

DUMP = r"""
(function () {
  var root = document.querySelector('.vditor-ir pre.vditor-reset');
  if (!root) return { err: 'no root' };
  var out = { html: root.innerHTML, nodes: [], hl: {}, raw: null };
  var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  var i = 0, n;
  while ((n = w.nextNode())) {
    out.nodes.push({ i: i++, text: n.nodeValue, parent: n.parentElement ? (n.parentElement.className || n.parentElement.tagName) : '' });
  }
  ['fn-ddl', 'fn-ddl-brace', 'fn-date', 'fn-time'].forEach(function (k) {
    var h = CSS.highlights.get(k);
    if (!h) { out.hl[k] = null; return; }
    var arr = [];
    for (var r of h) arr.push(r.toString());
    out.hl[k] = arr;
  });
  try { out.raw = window.__fn.state.vditor.getValue(); } catch (e) { out.raw = String(e); }
  return out;
})()
"""

FOCUS = r"""
(function () {
  var root = document.querySelector('.vditor-ir pre.vditor-reset');
  if (!root) return 'no root';
  root.focus();
  var rg = document.createRange();
  rg.selectNodeContents(root.lastElementChild || root);
  rg.collapse(false);
  var s = getSelection();
  s.removeAllRanges();
  s.addRange(rg);
  return 'ok';
})()
"""


def show(tag, d):
    print("-" * 70)
    print(tag)
    print("  hl   : " + json.dumps(d.get("hl"), ensure_ascii=False))
    print("  raw  : " + repr(d.get("raw"))[:200])
    for nd in d.get("nodes") or []:
        if nd["text"].strip():
            print("  node[%d] %-22s %r" % (nd["i"], nd["parent"][:22], nd["text"]))
    print("  html : " + str(d.get("html"))[:700])


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
        cdp.eval("window.__fn.setEditorValue('')")
        time.sleep(0.5)

        text = "英语作业（ddl{11-1 11:30}）"
        print("focus: " + str(cdp.eval(FOCUS)))
        for ch in text:
            cdp.call("Input.insertText", {"text": ch})
            time.sleep(0.05)
        time.sleep(1.2)
        show("手打后 · 完整一行", cdp.eval(DUMP))

        # 追加第二行，验证多行场景
        cdp.call("Input.insertText", {"text": "\n"})
        time.sleep(0.3)
        cdp.eval(FOCUS)
        for ch in "数学作业 ddl{11-5 8:00}":
            cdp.call("Input.insertText", {"text": ch})
            time.sleep(0.05)
        time.sleep(1.2)
        show("手打后 · 两行", cdp.eval(DUMP))
        cdp.close()
    finally:
        try:
            p.terminate()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
