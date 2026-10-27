"""测试守卫：绝不触碰用户正在运行的浮笺实例。

背景（2026-10-27 教训）：验收脚本曾用 `taskkill /F /IM FloatNote.exe` 无差别强杀，
把用户自己正打开着的浮笺一起杀掉了。从现在起：

1. 每个脚本启动测试实例之前必须先调 `guard_before_test()`——
   只要桌面上还有别的浮笺实例（很可能就是用户本人在用），立即中止整个测试，
   一个进程都不碰。
2. 结束时只 terminate 本脚本自己 Popen 出来的那个 PID，严禁按映像名全局杀。

守卫本身只读进程列表，无副作用。
"""
import ctypes
import sys
from ctypes import wintypes


def floatnote_pids():
    """返回当前所有 FloatNote.exe 进程的 [(pid, exe路径)]，路径拿不到则为 ''。"""
    k32 = ctypes.windll.kernel32
    TH32CS_SNAPPROCESS = 0x2

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    out = []
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = k32.Process32First(snap, ctypes.byref(entry))
        while ok:
            name = entry.szExeFile.decode("utf-8", "replace")
            if name.lower() == "floatnote.exe":
                pid = entry.th32ProcessID
                out.append((pid, _exe_path(pid)))
            ok = k32.Process32Next(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def _exe_path(pid):
    """QueryFullProcessImageNameW 拿进程可执行文件路径；失败返回 ''。"""
    k32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        k32.CloseHandle(h)


def guard_before_test(keep_pids=()):
    """发现不属于本次测试的浮笺实例就立即中止——宁可不测，绝不干扰。"""
    mine = set(keep_pids)
    others = [(p, path) for (p, path) in floatnote_pids() if p not in mine]
    if others:
        detail = "; ".join("pid=%d %s" % (p, path or "?") for p, path in others)
        print("中止测试：检测到正在运行的浮笺实例（%s）。" % detail)
        print("它很可能是你自己打开的；测试脚本不会碰它，本次验收跳过。")
        sys.exit(3)
