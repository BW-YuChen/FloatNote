# 验收脚本

全部为**无侵入**验收：启动一次应用，通过 Chrome DevTools Protocol 在页面内注入断言，
不发送真实键鼠事件、不移动窗口、不抢前台焦点。

## 前置条件

1. 已编译或已发布可执行文件
2. Python 3.8+（只用标准库，无需 pip 安装任何东西）
3. 应用必须带 `--dev` 启动才会打开调试端口 9222（正式双击启动是关闭的，这是刻意设计）

脚本自己会用 `--dev` 拉起进程，不需要你手动开。

## 用法

默认查找 `src/bin/Release/net10.0-windows/FloatNote.exe`。
测已发布的单文件版本时，用 `FN_EXE` 指定路径：

```bat
set FN_EXE=D:\Desktop\浮笺 FloatNote\FloatNote.exe
python verify.py
```

也可以直接对着 `bin\Debug` 或 `bin\Release` 的产物测。

## 脚本清单

| 脚本 | 断言数 | 覆盖内容 |
|---|---|---|
| `verify.py` | 43 | 全功能回归：便签增删改、每周待办、置顶、分类、互链、三类高亮、提醒全链路、窗口层级、无 JS 异常 |
| `verify_keys.py` | 19 | Markdown 行内快捷键（B/I/U/X/E/H/K/M）、`==高亮==` 渲染与底色、非正文区域不抢按键 |
| `verify_link.py` | 10 | `floatnote://` 内部协议拦截：裸 `<a>`、IR 编辑态、`window.open`、绕过前端强改 location 仍存活 |
| `verify_weekly.py` | 29 | 每周待办不分周几、周一 04:00 结算、未完成搬入并标红、Tab 二级子任务、父子联动；ddl 染色（含标题内、未闭合、重复括号）、短日期 `11-1` 解析、ddl 优先取时间 |
| `verify_settings.py` | 37 | 设置页：入口与五组分区、七个开关回显、开机自启真实写注册表并回滚、层级/配色/字号/侧栏生效与落盘、设置页打开时快捷键让位、重启后持久化 |

合计 **138 项**。

## 探针（排查用，不计入验收）

| 脚本 | 用途 |
|---|---|
| `probe_ddl.py` | 用人造文本灌进编辑器，dump 文本节点拆分与已注册的高亮区间 |
| `probe_ddl_typing.py` | 用 `Input.insertText` 逐字模拟手打，复现输入法与 IR 重排后的真实 DOM 形态 |

## 其他

- `cdp.py`：纯标准库实现的 WebSocket + CDP 客户端（本机 `pip` 装不上 `websocket-client` 时写的替代品）
- `shoot.py`：用 `Page.captureScreenshot` 截页面，不经桌面 DC，不打扰用户
- `make-icon.ps1`：生成 `Assets/app.ico`

## 已知注意点

- 每套脚本启动前会清空 `data\` 下的索引与提醒，**请不要指向含有真实便签的目录**
- 脚本之间不要并发跑，都会占用 9222 端口
- `verify_settings.py` 会临时读写 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 验证开机自启，
  结束时按原值还原。用的是 Python 内置 `winreg`，不调用 `reg.exe`
