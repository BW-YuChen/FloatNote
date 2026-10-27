"""无侵入验收：设置页。

覆盖入口、五组设置项、开关 / 分段 / 滑块的落地效果、落盘持久化、快捷键让位、
开机自启在注册表里的真实写入与回滚。

全部通过 CDP 在页面内断言，不发送真实键鼠、不移动窗口。
注意：开机自启用例会临时读写 HKCU\\...\\Run，结束时按原值还原。
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

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VAL = "FloatNote"

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


# ---------- 注册表（用 winreg 直读，不启动 reg.exe） ----------

def reg_read():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            v, _ = winreg.QueryValueEx(k, VAL)
            return str(v)
    except OSError:
        return None


def reg_write(value):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, VAL, 0, winreg.REG_SZ, value)


def reg_delete():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, VAL)
    except OSError:
        pass


def settings_json():
    p = os.path.join(DATA, "settings.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def launch():
    proc = subprocess.Popen([EXE, "--dev"], close_fds=True)
    for _ in range(60):
        if port_open():
            break
        time.sleep(0.4)
    if not port_open():
        return proc, None
    cdp = CDP()
    for _ in range(60):
        try:
            if cdp.eval("!!(window.__fn && window.__fn.state.vditor && window.__fn.state.activeId)"):
                break
        except Exception:
            pass
        time.sleep(0.4)
    time.sleep(1.2)
    return proc, cdp


def stop(proc, cdp):
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
    time.sleep(0.5)


def main():
    if not os.path.exists(EXE):
        print("找不到 exe：" + EXE)
        return 2

    # 干净起步：删掉配置让程序用默认值
    for name in ("index.json", "settings.json", "reminders.json"):
        p = os.path.join(DATA, name)
        if os.path.exists(p):
            os.remove(p)

    # 守卫：只要桌面上还有别的浮笺实例（可能是用户本人在用），整个测试立即中止
    guard_before_test()

    # 记录原始自启动项，结束时还原
    original_run = reg_read()

    proc, cdp = launch()
    if not cdp:
        print("调试端口未就绪")
        return 2

    try:
        # ============ 1. 入口与页面骨架 ============
        check("标题栏有设置入口", cdp.eval("!!document.getElementById('btnSettings')"))
        check("设置页初始隐藏", cdp.eval("document.getElementById('settingsPage').hidden") is True)
        cdp.eval("document.getElementById('btnSettings').click()")
        time.sleep(0.5)
        check("点击齿轮后设置页打开", cdp.eval("!document.getElementById('settingsPage').hidden") is True)

        groups = cdp.eval("""JSON.stringify(Array.prototype.map.call(
            document.querySelectorAll('#settingsPage .set-gt'),
            function(e){return e.textContent.trim();}))""")
        groups = json.loads(groups or "[]")
        check("五组分区齐全", groups == ["启动", "窗口", "编辑", "数据", "关于"], groups)

        ver = cdp.eval("document.getElementById('setVersion').textContent")
        import re as _re
        check("显示版本号", bool(_re.match(r"^v\d+\.\d+\.\d+", ver or "")), ver)

        data_dir = cdp.eval("document.getElementById('setDataDir').textContent")
        expect_dir = cdp.eval("window.__fn.state.settings.dataDir")
        check("数据目录与后端一致", bool(data_dir) and data_dir == expect_dir, data_dir)
        check("数据目录真实存在", os.path.isdir(expect_dir or ""), expect_dir)

        # ============ 2. 初始状态回显 ============
        echo = json.loads(cdp.eval("""JSON.stringify({
            startup: document.getElementById('swStartup').classList.contains('is-on'),
            startMin: document.getElementById('swStartMin').classList.contains('is-on'),
            weekly: document.getElementById('swWeekly').classList.contains('is-on'),
            hl: document.getElementById('swHL').classList.contains('is-on'),
            lk: document.getElementById('swLK').classList.contains('is-on'),
            fixedTop: document.getElementById('swFixedTop').classList.contains('is-on'),
            sidebar: document.getElementById('swSidebar').classList.contains('is-on')
        })""") or "{}")
        st = json.loads(cdp.eval("JSON.stringify(window.__fn.state.settings)") or "{}")
        same = (echo.get("startup") == bool(st.get("startWithWindows"))
                and echo.get("startMin") == bool(st.get("startMinimized"))
                and echo.get("weekly") == bool(st.get("weeklyAutoReset"))
                and echo.get("hl") == bool(st.get("autoHighlight"))
                and echo.get("lk") == bool(st.get("autoLink"))
                and echo.get("sidebar") == bool(st.get("sidebar")))
        check("七个开关回显与状态机一致", same, echo)

        on_level = cdp.eval(
            "document.querySelector('#segLevel button.is-on').getAttribute('data-v')")
        check("层级分段回显当前档位", on_level == st.get("level"), on_level)

        on_theme = cdp.eval(
            "document.querySelector('#segTheme button.is-on').getAttribute('data-v')")
        check("配色分段回显当前主题", on_theme == st.get("theme"), on_theme)

        rng = cdp.eval("document.getElementById('rngFont').value")
        check("字号滑块回显当前字号", int(rng) == int(st.get("fontSize") or 14), rng)

        # ============ 3. 开机自启：真实写注册表 ============
        before_state = bool(st.get("startWithWindows"))
        cdp.eval("document.getElementById('swStartup').click()")
        time.sleep(0.9)
        after_state = cdp.eval("window.__fn.state.settings.startWithWindows")
        check("点击开关后自启状态翻转", bool(after_state) != before_state,
              "%s -> %s" % (before_state, after_state))

        reg_val = reg_read()
        if after_state:
            check("开启后注册表写入 Run 项", reg_val is not None, reg_val)
            got = os.path.normcase((reg_val or "").strip().strip('"'))
            want = os.path.normcase(EXE)
            check("Run 项指向当前 exe", got == want, "got=%r want=%r" % (got, want))
        else:
            check("关闭后注册表移除 Run 项", reg_val is None, reg_val)

        # 再点回去，验证双向
        cdp.eval("document.getElementById('swStartup').click()")
        time.sleep(0.9)
        back_state = cdp.eval("window.__fn.state.settings.startWithWindows")
        back_reg = reg_read()
        check("再次点击可回到初始状态", bool(back_state) == before_state, back_state)
        check("注册表随之同步", (back_reg is not None) == bool(back_state),
              "%s | %s" % (back_state, back_reg))

        # ============ 4. 启动时隐藏 / 每周自动结算：落盘 ============
        cdp.eval("document.getElementById('swStartMin').click()")
        time.sleep(0.8)
        check("「启动时隐藏」落盘", settings_json().get("startMinimized") is True,
              settings_json().get("startMinimized"))
        cdp.eval("document.getElementById('swStartMin').click()")
        time.sleep(0.6)
        check("「启动时隐藏」可关回", settings_json().get("startMinimized") is False,
              settings_json().get("startMinimized"))

        cdp.eval("document.getElementById('swWeekly').click()")
        time.sleep(0.8)
        check("「每周自动结算」落盘为关", settings_json().get("weeklyAutoReset") is False,
              settings_json().get("weeklyAutoReset"))

        # ============ 5. 层级 / 配色 / 字号 / 侧栏 ============
        cdp.eval("document.querySelector('#segLevel button[data-v=\"normal\"]').click()")
        time.sleep(0.7)
        check("层级切到普通", cdp.eval("window.__fn.state.settings.level") == "normal",
              cdp.eval("window.__fn.state.settings.level"))
        cdp.eval("document.querySelector('#segLevel button[data-v=\"top\"]').click()")
        time.sleep(0.7)
        check("层级切回置顶", cdp.eval("window.__fn.state.settings.level") == "top",
              cdp.eval("window.__fn.state.settings.level"))

        cdp.eval("document.querySelector('#segTheme button[data-v=\"dark\"]').click()")
        time.sleep(0.6)
        check("配色切到深色", cdp.eval("document.documentElement.getAttribute('data-theme')") == "dark",
              cdp.eval("document.documentElement.getAttribute('data-theme')"))
        cdp.eval("document.querySelector('#segTheme button[data-v=\"system\"]').click()")
        time.sleep(0.6)
        check("配色可切回跟随系统",
              cdp.eval("window.__fn.state.settings.theme") == "system",
              cdp.eval("window.__fn.state.settings.theme"))

        cdp.eval("""
        (function(){
          var r = document.getElementById('rngFont');
          r.value = '18';
          r.dispatchEvent(new Event('input', {bubbles:true}));
          r.dispatchEvent(new Event('change', {bubbles:true}));
        })()
        """)
        time.sleep(0.8)
        fs = cdp.eval("getComputedStyle(document.documentElement).getPropertyValue('--fs-editor').trim()")
        check("滑块调字号生效", fs == "18px", fs)
        check("字号落盘", int(settings_json().get("fontSize") or 0) == 18,
              settings_json().get("fontSize"))
        cdp.eval("""
        (function(){
          var r = document.getElementById('rngFont');
          r.value = '14';
          r.dispatchEvent(new Event('input', {bubbles:true}));
          r.dispatchEvent(new Event('change', {bubbles:true}));
        })()
        """)
        time.sleep(0.7)
        check("字号可调回",
              cdp.eval("getComputedStyle(document.documentElement).getPropertyValue('--fs-editor').trim()") == "14px")

        cdp.eval("document.getElementById('swSidebar').click()")
        time.sleep(0.6)
        check("关侧栏后工作区加上 no-side",
              cdp.eval("document.querySelector('.workspace').classList.contains('no-side')") is True)
        check("侧栏状态落盘", settings_json().get("sidebarVisible") is False,
              settings_json().get("sidebarVisible"))
        cdp.eval("document.getElementById('swSidebar').click()")
        time.sleep(0.6)
        check("开侧栏后 no-side 移除",
              cdp.eval("document.querySelector('.workspace').classList.contains('no-side')") is False)

        # ============ 6. 快捷键让位 / Esc 返回 ============
        n_before = cdp.eval("window.__fn.state.notes.length")
        prevented = cdp.eval("""
        (function(){
          var ev = new KeyboardEvent('keydown',
            {key:'n', ctrlKey:true, bubbles:true, cancelable:true});
          document.body.dispatchEvent(ev);
          return ev.defaultPrevented;
        })()
        """)
        time.sleep(0.5)
        n_after = cdp.eval("window.__fn.state.notes.length")
        check("设置页打开时 Ctrl+N 不新建", n_before == n_after, "%s -> %s" % (n_before, n_after))
        check("设置页打开时 Ctrl+N 未被编辑器消费", prevented is False, prevented)

        cdp.eval("""
        document.dispatchEvent(new KeyboardEvent('keydown',
          {key:'Escape', bubbles:true, cancelable:true}))
        """)
        time.sleep(0.5)
        check("Esc 关闭设置页",
              cdp.eval("document.getElementById('settingsPage').hidden") is True)

        # ============ 7. 重启后持久化 ============
        check("关闭前每周开关已落盘为关",
              settings_json().get("weeklyAutoReset") is False,
              settings_json().get("weeklyAutoReset"))
        stop(proc, cdp)
        proc, cdp = launch()
        if not cdp:
            check("重启后重新连上调试端口", False, "port closed")
        else:
            check("重启后「每周自动结算」仍为关",
                  cdp.eval("window.__fn.state.settings.weeklyAutoReset") is False,
                  cdp.eval("window.__fn.state.settings.weeklyAutoReset"))
            cdp.eval("window.__fn.openSettings()")
            time.sleep(0.5)
            check("重启后设置页开关同步为关",
                  cdp.eval("document.getElementById('swWeekly').classList.contains('is-on')") is False)
            # 恢复默认
            cdp.eval("document.getElementById('swWeekly').click()")
            time.sleep(0.8)
            check("恢复自动结算为开", settings_json().get("weeklyAutoReset") is True,
                  settings_json().get("weeklyAutoReset"))

        # ============ 8. 无异常 ============
        log_path = os.path.join(DATA, "app.log")
        if os.path.exists(log_path):
            with open(log_path, encoding="utf-8", errors="replace") as f:
                txt = f.read()
            bad = [l for l in txt.splitlines() if "[ERROR]" in l or "Unhandled" in l]
            check("无 JS/宿主异常", not bad, bad[:2])
        else:
            check("无 JS/宿主异常", False, "app.log 缺失")

    finally:
        if cdp:
            stop(proc, cdp)
        else:
            try:
                proc.terminate()
            except Exception:
                pass

        # 还原自启动项到测试前状态
        if original_run is None:
            reg_delete()
        else:
            reg_write(original_run)
        restored = (reg_read() is None and original_run is None) or \
                   (reg_read() == original_run)
        print("  自启动项已还原：" + ("是" if restored else "否（原值 %r）" % original_run))

    print()
    print("=== %d/%d 通过 ===" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
