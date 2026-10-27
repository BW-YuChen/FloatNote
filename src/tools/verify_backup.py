"""无侵入验收：启动自动备份（DataBackup.RunOnStartup）。

流程：干净启动种模板 → 关闭 → 再启动触发快照 → 校验快照内容 →
第三次启动验证「同一天只备一份」。全程 CDP，不注入键鼠。
"""
import glob
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP  # noqa: E402
from guard import guard_before_test  # noqa: E402

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (("   -> " + str(detail)) if detail else ""))


def launch(exe, wait_ready=True):
    proc = subprocess.Popen([exe, "--dev"], close_fds=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cdp = None
    if wait_ready:
        for _ in range(60):
            time.sleep(0.5)
            try:
                cdp = CDP()
                break
            except Exception:
                continue
        if cdp:
            for _ in range(40):
                try:
                    if cdp.eval("!!(window.__fn && window.__fn.state.booted && window.__fn.state.vditor)"):
                        break
                except Exception:
                    pass
                time.sleep(0.4)
    return proc, cdp


def kill(proc):
    try:
        proc.terminate()
        proc.wait(timeout=8)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    time.sleep(1.0)


def main():
    exe = os.environ.get("FN_EXE", os.path.join(_SRC, "bin", "Release", "net10.0-windows", "FloatNote.exe"))
    exe = exe.replace("\\\\", "\\")
    data = os.path.join(os.path.dirname(exe), "data")

    # 守卫：只要桌面上还有别的浮笺实例（可能是用户本人在用），整个测试立即中止
    guard_before_test()

    import shutil
    for name in ("index.json", "settings.json", "reminders.json", "app.log"):
        p = os.path.join(data, name)
        if os.path.exists(p):
            os.remove(p)
    for name in ("notes", "trash", "backup"):
        p = os.path.join(data, name)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)

    # ---- 第 1 次启动：首次运行只种模板，不应产生快照 ----
    proc, cdp = launch(exe)
    check("首次启动连接成功", cdp is not None)
    time.sleep(2.0)
    kill(proc)
    check("首次运行不产生快照", not glob.glob(os.path.join(data, "backup", "snap-*")),
          glob.glob(os.path.join(data, "backup", "snap-*")))

    # ---- 第 2 次启动：写一条新便签并保存（此刻还没快照，快照发生在启动时）----
    proc, cdp = launch(exe)
    check("第二次启动连接成功", cdp is not None)
    if cdp:
        cdp.eval("window.__fn.setEditorValue('# 备份测试\\n\\n这条便签要在快照里找回。\\n', true)")
        time.sleep(1.5)   # 等 600ms 自动保存落盘
    kill(proc)

    # 快照每天只备一份，为了继续测「含新便签的快照」，删掉现有快照模拟跨天
    shutil.rmtree(os.path.join(data, "backup"), ignore_errors=True)

    # ---- 第 3 次启动：启动时应把「备份测试」一并快照 ----
    proc, cdp = launch(exe)
    check("第三次启动连接成功", cdp is not None)
    time.sleep(1.5)
    kill(proc)

    snaps = sorted(glob.glob(os.path.join(data, "backup", "snap-*")))
    check("生成快照目录", len(snaps) == 1, [os.path.basename(s) for s in snaps])
    if snaps:
        snap = snaps[0]
        idx = os.path.join(snap, "index.json")
        check("快照含 index.json", os.path.exists(idx))
        md = glob.glob(os.path.join(snap, "*.md"))
        check("快照含便签 Markdown", len(md) >= 2, len(md))
        content = ""
        for f in md:
            content += open(f, encoding="utf-8", errors="replace").read()
        check("新写的便签进了快照", "备份测试" in content)
        check("快照含说明文件", os.path.exists(os.path.join(snap, "README.txt")))

    # ---- 第 4 次启动：同一天不应重复备份 ----
    proc, cdp = launch(exe)
    check("第四次启动连接成功", cdp is not None)
    time.sleep(2.0)
    kill(proc)
    snaps2 = sorted(glob.glob(os.path.join(data, "backup", "snap-*")))
    check("同一天只保留一份快照", len(snaps2) == 1, [os.path.basename(s) for s in snaps2])

    print()
    print("=== %d/%d 通过 ===" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
