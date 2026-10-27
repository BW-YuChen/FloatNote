using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Threading;
using Microsoft.Web.WebView2.Core;

namespace FloatNote;

public partial class MainWindow : Window
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping
    };

    private const int HotKeyId = 0xB10;

    private readonly NoteStore _store = new();
    private readonly ReminderStore _reminders = new();
    private readonly AppSettings _settings;
    private readonly bool _devMode;
    private readonly DispatcherTimer _geoTimer;
    private readonly DispatcherTimer _topmostTimer;
    private readonly DispatcherTimer _reminderTimer;
    private readonly DispatcherTimer _weeklyTimer;
    private bool _weeklyJustSeeded;

    private CoreWebView2? _core;
    private IntPtr _hwnd;
    private TrayIconManager? _tray;
    private TaskCompletionSource<bool>? _flushTcs;
    private WindowLevel _level;
    private bool _uiReady;
    private bool _exiting;
    private bool _reallyClose;
    private bool _hotKeyOk;
    private bool _geoDirty;
    private bool _hideAfterReady;

    public MainWindow(bool devMode)
    {
        _devMode = devMode;
        _settings = SettingsStore.Load();
        _level = _settings.Level;

        // 注册表是自启动的唯一真相：用户可能在「任务管理器 → 启动」里手动禁用过，
        // 配置文件里记的 true 并不代表真的会自启。
        _settings.StartWithWindows = StartupRegistrar.IsEnabled();

        // 启动后隐藏到托盘：等页面完全就绪再收，避免 WebView 初始化被打断。
        // dev 模式强制显示，否则验收脚本抓不到页面。
        _hideAfterReady = _settings.StartMinimized && !devMode;

        // 固定置顶与「贴桌面/普通」互斥：配置文件里若出现矛盾组合，以固定置顶为准
        if (_settings.FixedTop) _level = WindowLevel.Top;

        AppLog.Write($"MainWindow ctor: level={_level}, geo={_settings.X},{_settings.Y} {_settings.Width}x{_settings.Height}");

        InitializeComponent();

        _geoTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(450) };
        _geoTimer.Tick += (_, _) =>
        {
            _geoTimer.Stop();
            SaveGeometry();
        };

        // 固定置顶：周期性校验 WS_EX_TOPMOST 样式位。
        // 某些程序（全屏应用、任务栏重建、DPI 变化）会顺手摘掉置顶，光设一次不可靠。
        _topmostTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
        _topmostTimer.Tick += (_, _) => EnsureFixedTop();
        _topmostTimer.Start();

        // 提醒调度：15 秒粒度足够（不需要秒级精确，避免无谓唤醒）
        _reminderTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(15) };
        _reminderTimer.Tick += (_, _) => CheckDueReminders();
        _reminderTimer.Start();

        // 每周结算：周一 04:00 是分界点。20 分钟一查，既不会错过，也不至于频繁读盘
        _weeklyTimer = new DispatcherTimer { Interval = TimeSpan.FromMinutes(20) };
        _weeklyTimer.Tick += (_, _) => MaybeWeeklyReset();
        _weeklyTimer.Start();

        Deactivated += OnWindowDeactivated;
        LocationChanged += (_, _) => MarkGeometryDirty();
        SizeChanged += (_, _) => MarkGeometryDirty();
        Loaded += OnWindowLoaded;

        StateChanged += (_, _) => AppLog.Write($"StateChanged: {WindowState} visible={IsVisible}");
        Activated += (_, _) =>
        {
            AppLog.Write("Activated");
            EnsureFixedTop();
        };
        IsVisibleChanged += (_, _) => AppLog.Write($"IsVisibleChanged: {IsVisible}");
        ContentRendered += (_, _) =>
        {
            AppLog.Write("ContentRendered: re-apply level");
            ApplyLevel(_level, persist: false);
        };
    }

    // ---------- 生命周期 ----------

    protected override void OnSourceInitialized(EventArgs e)
    {
        base.OnSourceInitialized(e);

        _hwnd = new WindowInteropHelper(this).Handle;
        Native.SetCornerPreference(_hwnd, true);
        Native.RemoveBorder(_hwnd);

        HwndSource.FromHwnd(_hwnd)?.AddHook(WndProc);

        // 必须在句柄存在后注册：放在 Loaded 时 _hwnd 仍可能为 0，
        // 那样会注册成线程级热键，WM_HOTKEY 不会进入窗口过程
        RegisterHotKey();

        RestoreGeometry();
        ApplyLevel(_level, persist: false);
        ApplyWindowBackground();

        AppLog.Write($"SourceInitialized: hwnd=0x{_hwnd.ToInt64():X} geo={Left:F0},{Top:F0} {Width:F0}x{Height:F0} visible={IsVisible}");
    }

    private void OnWindowLoaded(object? sender, RoutedEventArgs e)
    {
        AppLog.Write($"Loaded: visible={IsVisible} state={WindowState}");
        SeedWelcomeNoteIfEmpty();
        MaybeWeeklyReset();
        BuildTray();
        // 窗口真正显示后再定位一次层级，避免 Show 过程重置 z-order
        if (_hwnd != IntPtr.Zero)
        {
            ApplyLevel(_level, persist: false);
            EnsureFixedTop(force: true);
        }
        _ = InitWebViewAsync();
    }

    /// <summary>首次运行时放一条「每周待办」和一条示例便签，展示待办、Markdown 与公式能力。</summary>
    private void SeedWelcomeNoteIfEmpty()
    {
        try
        {
            if (_store.Index.Notes.Count > 0) return;

            var weekly = _store.Create("每周待办", kind: "weekly", mark: "emoji:📅", pin: true);
            _store.SetActive(weekly.Id);
            _weeklyJustSeeded = true;

            const string seed = @"# 欢迎使用浮笺

这是一款常驻桌面的便签工具。正文以 Markdown 文件保存在 `data\notes` 目录，可直接用任何编辑器打开、备份或迁移。

## 快捷键

- `Ctrl+N` 新建便签 · `Ctrl+S` 立即保存
- `Ctrl+Alt+N` 任意程序中唤出或收起 · `Ctrl+滚轮` 调整字号
- `Ctrl+B` 粗体 · `Ctrl+I` 斜体 · `Ctrl+U` 下划线
- `Ctrl+E` 行内代码 · `Ctrl+Shift+X` 删除线
- `Ctrl+Shift+H` 高亮 · `Ctrl+Shift+K` 链接 · `Ctrl+Shift+M` 公式
- `Tab` 让当前任务变成上一行的子任务，`Shift+Tab` 退一级

## 待办清单

- [ ] 点击左侧方框即可勾选
- [x] 已完成的事项会自动划线
- [ ] 换成你自己的第一条待办

## 子任务

- [ ] 主任务，下面挂两条子任务
  - [ ] 子任务一
  - [ ] 子任务二

子任务全部勾上后，主任务会自动跟着完成。

## 截止日期

在正文任意位置写 `ddl{2026-11-01 23:59}`，打完 ddl 会自动补出花括号；显示时花括号隐藏，只留红色的截止时间。

交作业 ddl{2026-11-01 23:59}

## 公式

行内公式 $E = mc^{2}$，以及块级公式：

$$
\int_{0}^{\infty} e^{-x^{2}}\,\mathrm{d}x = \frac{\sqrt{\pi}}{2}
$$

## 代码块

```csharp
var level = WindowLevel.Top; // 置顶 / 普通 / 贴桌面
```

> 标题取正文首行的 `# 标题`，所以改标题即改这里。

## 试试这些

- 左侧「＋」里可以新建**每周待办**：不分周几，一张连续清单，每周一 04:00 自动结算
- 结算时已完成的任务清除，没做完的搬进「上周未完成」并标红
- 正文里写另一条便签的标题，文字旁会出现 ✓/✗，点 ✓ 变成可跳转的蓝色链接
- 日期 2026-10-26、时间 14:30、地点 @图书馆 会自动带上不同底色
- 光标停在时间后面，会冒出 ⏰ 提示，点 ✓ 可以设提醒，到点弹 Windows 通知
- 便签可以用图标或颜色分类，也能钉在列表最上方
- 「提醒」分页里能看到所有待触发与已触发的提醒
";
            var meta = _store.Create("欢迎使用浮笺", mark: "emoji:📄");
            _store.SaveContent(meta.Id, seed);
            _store.SetActive(weekly.Id);
        }
        catch (Exception ex)
        {
            AppLog.Error("Seed", ex);
        }
    }

    private async Task InitWebViewAsync()
    {
        try
        {
            AppLog.Write("WebView: extracting assets");
            string webRoot = WebAssets.EnsureExtracted();
            AppLog.Write("WebView: assets at " + webRoot);

            // --dev 只是开发期自测开关：开调试端口并给页面带上 debug 标记，正式启动两者都不开
            var envOptions = new CoreWebView2EnvironmentOptions();
            if (_devMode) envOptions.AdditionalBrowserArguments = "--remote-debugging-port=9222";

            var env = await CoreWebView2Environment.CreateAsync(
                browserExecutableFolder: null,
                userDataFolder: AppPaths.WebViewCacheDir,
                options: envOptions);
            AppLog.Write("WebView: env ready");

            await Web.EnsureCoreWebView2Async(env);
            _core = Web.CoreWebView2;
            AppLog.Write("WebView: core ready");

            Web.DefaultBackgroundColor = IsDarkEffective()
                ? System.Drawing.Color.FromArgb(0x18, 0x18, 0x17)
                : System.Drawing.Color.FromArgb(0xFB, 0xFB, 0xFA);

            var s = _core.Settings;
            s.IsStatusBarEnabled = false;
            s.IsZoomControlEnabled = false;
            s.AreDefaultContextMenusEnabled = true;
            s.AreBrowserAcceleratorKeysEnabled = true;
            s.IsSwipeNavigationEnabled = false;
            s.AreDevToolsEnabled = _devMode;
            s.IsPasswordAutosaveEnabled = false;
            s.IsGeneralAutofillEnabled = false;

            _core.WebMessageReceived += OnWebMessage;
            _core.NavigationCompleted += (_, ev) =>
                AppLog.Write($"WebView: nav completed ok={ev.IsSuccess} status={ev.HttpStatusCode} err={ev.WebErrorStatus}");
            _core.NavigationStarting += (_, ev) =>
            {
                string uri = ev.Uri ?? "";
                // 页面自身（虚拟主机）与内联数据放行
                if (uri.StartsWith("https://floatnote.local/", StringComparison.OrdinalIgnoreCase)) return;
                if (uri.StartsWith("about:", StringComparison.OrdinalIgnoreCase)) return;
                if (uri.StartsWith("data:", StringComparison.OrdinalIgnoreCase)) return;
                if (uri.StartsWith("blob:", StringComparison.OrdinalIgnoreCase)) return;

                // 其余一律拦下，否则 floatnote:// 会被丢给系统外壳去「找应用」
                ev.Cancel = true;
                if (TryHandleInternalUri(uri)) return;
                if (uri.StartsWith("http://", StringComparison.OrdinalIgnoreCase) ||
                    uri.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                {
                    OpenExternal(uri);
                }
                else
                {
                    AppLog.Write("WebView: blocked nav " + uri);
                }
            };
            _core.DOMContentLoaded += (_, _) => AppLog.Write("WebView: DOMContentLoaded");
            _core.NewWindowRequested += (_, ev) =>
            {
                ev.Handled = true;
                if (TryHandleInternalUri(ev.Uri)) return;
                OpenExternal(ev.Uri ?? "");
            };
            _core.ProcessFailed += (_, ev) =>
            {
                AppLog.Write("WebView: process failed " + ev.ProcessFailedKind);
                if (ev.ProcessFailedKind == CoreWebView2ProcessFailedKind.BrowserProcessExited)
                    Dispatcher.Invoke(() => { try { Web.Reload(); } catch { } });
            };

            _core.SetVirtualHostNameToFolderMapping(
                "floatnote.local", webRoot, CoreWebView2HostResourceAccessKind.Allow);

            AppLog.Write("WebView: navigating");
            _core.Navigate("https://floatnote.local/index.html" + (_devMode ? "?debug=1" : ""));
        }
        catch (Exception ex)
        {
            AppLog.Error("InitWebViewAsync", ex);
            System.Windows.MessageBox.Show(
                "界面引擎初始化失败：\n" + ex.Message +
                "\n\n请确认系统已安装 Microsoft Edge WebView2 运行时。",
                "浮笺", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    // ---------- 窗口几何 ----------

    private void RestoreGeometry()
    {
        double w = Math.Max(MinWidth, _settings.Width);
        double h = Math.Max(MinHeight, _settings.Height);
        Width = w;
        Height = h;

        bool hasSaved = !double.IsNaN(_settings.X) && !double.IsNaN(_settings.Y);
        if (hasSaved && IsPositionVisible(_settings.X, _settings.Y, w, h))
        {
            Left = _settings.X;
            Top = _settings.Y;
        }
        else
        {
            var wa = SystemParameters.WorkArea;
            Left = Math.Max(wa.Left + 24, wa.Right - w - 56);
            Top = wa.Top + 56;
        }
    }

    private static bool IsPositionVisible(double x, double y, double w, double h)
    {
        double vl = SystemParameters.VirtualScreenLeft;
        double vt = SystemParameters.VirtualScreenTop;
        double vr = vl + SystemParameters.VirtualScreenWidth;
        double vb = vt + SystemParameters.VirtualScreenHeight;

        // 要求标题栏区域至少有 60x30 落在虚拟屏幕内
        double minVisibleW = Math.Min(60, w);
        double minVisibleH = Math.Min(30, h);
        return x + minVisibleW > vl && x < vr && y + minVisibleH > vt && y < vb;
    }

    private void MarkGeometryDirty()
    {
        _geoDirty = true;
        _geoTimer.Stop();
        _geoTimer.Start();
    }

    private void SaveGeometry()
    {
        if (_exiting && !_geoDirty) return;
        if (WindowState == WindowState.Minimized) return;
        if (WindowState == WindowState.Normal)
        {
            _settings.X = Left;
            _settings.Y = Top;
            _settings.Width = Width;
            _settings.Height = Height;
        }
        _settings.Level = _level;
        SettingsStore.Save(_settings);
        _geoDirty = false;
    }

    // ---------- 层级 ----------

    private void ApplyLevel(WindowLevel level, bool persist)
    {
        _level = level;

        // 先同步 WPF 自身的 Topmost，否则后续 WPF 内部操作会把 z-order 改回去
        Topmost = level == WindowLevel.Top;

        IntPtr after = level switch
        {
            WindowLevel.Top => Native.HWND_TOPMOST,
            WindowLevel.Desk => Native.HWND_BOTTOM,
            _ => Native.HWND_NOTOPMOST
        };

        if (_hwnd != IntPtr.Zero)
        {
            Native.SetWindowPos(_hwnd, after, 0, 0, 0, 0,
                Native.SWP_NOMOVE | Native.SWP_NOSIZE | Native.SWP_NOACTIVATE);
        }

        AppLog.Write($"ApplyLevel: {level} topmost={Topmost} hwnd=0x{_hwnd.ToInt64():X}");

        // 切到非置顶档位时，固定置顶自然失效（用户手动降层，说明不想再压在最上面）
        if (persist && level != WindowLevel.Top && _settings.FixedTop)
        {
            _settings.FixedTop = false;
            SettingsStore.Save(_settings);
            PostFixedTop();
            _tray?.UpdateFixedTop(false);
        }

        if (persist)
        {
            _settings.Level = level;
            SettingsStore.Save(_settings);
        }

        if (_uiReady)
        {
            Post(new { type = "level", level = LevelToken(level) });
            PostSettings();
        }

        _tray?.UpdateLevel(level);
    }

    /// <summary>固定置顶：把层级锁在置顶，并周期性补回被系统或其它程序摘掉的样式位。
    /// 关闭时回落到普通窗口，交由层级按钮自由选择。</summary>
    private void SetFixedTop(bool on)
    {
        _settings.FixedTop = on;

        if (on)
        {
            ApplyLevel(WindowLevel.Top, persist: false);
            EnsureFixedTop(force: true);
        }
        else
        {
            ApplyLevel(WindowLevel.Normal, persist: false);
        }

        _settings.Level = _level;
        _settings.FixedTop = on;
        SettingsStore.Save(_settings);

        AppLog.Write($"SetFixedTop: {on} level={_level}");
        PostFixedTop();
        PostSettings();
        _tray?.UpdateFixedTop(on);
    }

    private void PostFixedTop()
    {
        if (_uiReady) Post(new { type = "fixedTop", on = _settings.FixedTop, level = LevelToken(_level) });
    }

    private void EnsureFixedTop(bool force = false)
    {
        if (!_settings.FixedTop || _exiting) return;
        if (!IsVisible || WindowState == WindowState.Minimized) return;
        if (_hwnd == IntPtr.Zero) return;
        if (!force && Native.IsTopmost(_hwnd)) return;

        Topmost = true;
        Native.SetWindowPos(_hwnd, Native.HWND_TOPMOST, 0, 0, 0, 0,
            Native.SWP_NOMOVE | Native.SWP_NOSIZE | Native.SWP_NOACTIVATE);
        AppLog.Write("EnsureFixedTop: re-asserted");
    }

    private static string LevelToken(WindowLevel l) => l switch
    {
        WindowLevel.Top => "top",
        WindowLevel.Desk => "desk",
        _ => "normal"
    };

    private void OnWindowDeactivated(object? sender, EventArgs e)
    {
        AppLog.Write($"Deactivated: level={_level}");
        // 贴桌面档位：失焦后自动回落到所有窗口之下，避免挡住正常工作
        if (_level == WindowLevel.Desk && !_exiting && _hwnd != IntPtr.Zero)
        {
            Native.SetWindowPos(_hwnd, Native.HWND_BOTTOM, 0, 0, 0, 0,
                Native.SWP_NOMOVE | Native.SWP_NOSIZE | Native.SWP_NOACTIVATE);
        }
    }

    // ---------- 窗口背景（与页面主题保持一致，避免 6px 缩放边露出异色） ----------

    private void ApplyWindowBackground(bool? darkOverride = null)
    {
        bool dark = darkOverride ?? IsDarkEffective();
        var color = dark
            ? System.Windows.Media.Color.FromRgb(0x18, 0x18, 0x17)
            : System.Windows.Media.Color.FromRgb(0xFB, 0xFB, 0xFA);
        var brush = new System.Windows.Media.SolidColorBrush(color);
        Background = brush;
        RootGrid.Background = brush;

        // WebView 的兜底底色与窗口保持一致，避免加载瞬间或滚动边界出现异色
        try
        {
            Web.DefaultBackgroundColor = dark
                ? System.Drawing.Color.FromArgb(0x18, 0x18, 0x17)
                : System.Drawing.Color.FromArgb(0xFB, 0xFB, 0xFA);
        }
        catch { }
    }

    private bool IsDarkEffective()
    {
        if (_settings.Theme == "dark") return true;
        if (_settings.Theme == "light") return false;
        return IsSystemDark();
    }

    private static bool IsSystemDark()
    {
        try
        {
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(
                @"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
            var v = key?.GetValue("AppsUseLightTheme");
            if (v is int i) return i == 0;
        }
        catch { }
        return false;
    }

    // ---------- 托盘与热键 ----------

    private void BuildTray()
    {
        _tray = new TrayIconManager(
            level: _level,
            theme: _settings.Theme,
            startWithWindows: _settings.StartWithWindows,
            fixedTop: _settings.FixedTop,
            onToggleVisibility: ToggleVisibility,
            onLevel: l => ApplyLevel(l, persist: true),
            onFixedTop: SetFixedTop,
            onTheme: SetTheme,
            onToggleStartup: ToggleStartup,
            onOpenData: () => OpenExternal(AppPaths.DataDir),
            onExit: ExitApp,
            onReminderClick: FocusNote,
            onWeeklyReset: () => MaybeWeeklyReset(force: true));
    }

    /// <summary>把窗口带到前台并跳到指定便签。</summary>
    private void FocusNote(string noteId)
    {
        try
        {
            if (!IsVisible || WindowState == WindowState.Minimized)
            {
                if (WindowState == WindowState.Minimized) WindowState = WindowState.Normal;
                Show();
                Activate();
                Native.SetForegroundWindow(_hwnd);
                ApplyLevel(_level, persist: false);
                EnsureFixedTop(force: true);
            }

            if (!string.IsNullOrEmpty(noteId) && _store.Index.Notes.Any(n => n.Id == noteId))
            {
                _store.SetActive(noteId);
                if (_uiReady) Post(new { type = "focusNote", id = noteId });
            }
        }
        catch (Exception ex)
        {
            AppLog.Error("FocusNote", ex);
        }
    }

    /// <summary>
    /// 消化 floatnote:// 内部协议。返回 true 表示已在本程序内处理完毕。
    /// 关键：这类 URI 绝不能落到 ShellExecute —— 系统没有注册处理器，
    /// 一交给外壳就会弹「获取打开此 floatnote 链接的应用」。
    /// </summary>
    private bool TryHandleInternalUri(string? uri)
    {
        if (string.IsNullOrWhiteSpace(uri)) return false;
        if (!uri.StartsWith("floatnote:", StringComparison.OrdinalIgnoreCase)) return false;

        const string prefix = "floatnote://note/";
        try
        {
            if (uri.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            {
                string id = Uri.UnescapeDataString(uri.Substring(prefix.Length)).Trim();
                int cut = id.IndexOfAny(new[] { '?', '#', '/' });
                if (cut >= 0) id = id.Substring(0, cut);
                if (id.Length > 0)
                {
                    FocusNote(id);
                    return true;
                }
            }
            AppLog.Write("InternalUri: ignored " + uri);
        }
        catch (Exception ex)
        {
            AppLog.Error("TryHandleInternalUri", ex);
        }
        return true;   // 只要前缀是 floatnote:，一律内部吞掉，绝不外抛
    }

    // ---------- 提醒调度 ----------

    private void CheckDueReminders()
    {
        try
        {
            long now = DateTimeOffset.Now.ToUnixTimeMilliseconds();
            var due = _reminders.TakeDue(now);
            if (due.Count == 0) return;

            foreach (var r in due)
            {
                string note = _store.Index.Notes.FirstOrDefault(n => n.Id == r.NoteId)?.Title ?? "";
                AppLog.Write($"提醒触发: {r.Text} note={note}");
                _tray?.NotifyReminder(r, note);
            }

            PostReminders();
            _tray?.RefreshTip(_store.Index, _reminders.PendingCount);
        }
        catch (Exception ex)
        {
            AppLog.Error("CheckDueReminders", ex);
        }
    }

    private object BuildReminderPayload()
    {
        var titles = _store.Index.Notes.ToDictionary(n => n.Id, n => n.Title);
        return _reminders.Snapshot().Select(r => new
        {
            id = r.Id,
            noteId = r.NoteId,
            noteTitle = titles.TryGetValue(r.NoteId, out var t) ? t : "",
            text = r.Text,
            dueAt = r.DueAt,
            fired = r.Fired
        });
    }

    private void PostReminders()
    {
        if (_uiReady) Post(new { type = "reminders", list = BuildReminderPayload(), now = DateTimeOffset.Now.ToUnixTimeMilliseconds() });
    }

    // ---------- 每周结算 ----------

    /// <summary>
    /// 每周一 04:00 之后的首次检查执行结算：已完成任务删掉，未完成的搬进「上周未完成」区块。
    /// 全新安装（本次运行刚种下模板）只登记周期、不动内容——刚建出来的清单不该被搬走。
    /// </summary>
    private void MaybeWeeklyReset(bool force = false)
    {
        try
        {
            // 自动结算可在设置页关闭；手动触发（托盘 / 设置页按钮）不受开关限制
            if (!force && !_settings.WeeklyAutoReset) return;

            var now = DateTime.Now;
            var cycle = WeeklyTemplate.CycleStart(now);
            string key = cycle.ToString("yyyy-MM-dd");

            if (!force && _settings.LastWeeklyReset == key) return;

            bool firstEver = string.IsNullOrEmpty(_settings.LastWeeklyReset);
            _settings.LastWeeklyReset = key;
            SettingsStore.Save(_settings);

            if (firstEver && _weeklyJustSeeded && !force)
            {
                AppLog.Write("weekly reset: 首次安装，仅登记周期 " + key);
                return;
            }

            string quote = WeeklyTemplate.Quote(cycle);
            int touched = 0;
            foreach (var meta in _store.Index.Notes.Where(n => n.Kind == "weekly").ToList())
            {
                string md = _store.ReadRaw(meta.Id);
                if (string.IsNullOrEmpty(md)) continue;
                var (text, changed) = WeeklyRollover.Apply(md, quote);
                if (!changed) continue;
                _store.WriteRaw(meta.Id, text);
                touched++;
                if (meta.Id == _store.Index.ActiveId && _uiReady)
                    Post(new { type = "reloadContent", id = meta.Id, content = text });
            }
            AppLog.Write($"weekly reset: 周期 {key}，处理 {touched} 篇");
            if (touched > 0 && _uiReady) Post(new { type = "toast", text = "已结算本周待办" });
        }
        catch (Exception ex)
        {
            AppLog.Error("MaybeWeeklyReset", ex);
        }
    }

    private void RegisterHotKey()
    {
        _hwnd = new WindowInteropHelper(this).Handle;
        if (_hwnd == IntPtr.Zero)
        {
            AppLog.Write("RegisterHotKey skipped: hwnd=0");
            _hotKeyOk = false;
            return;
        }

        try { Native.UnregisterHotKey(_hwnd, HotKeyId); } catch { }

        _hotKeyOk = Native.RegisterHotKey(_hwnd, HotKeyId,
            Native.MOD_CONTROL | Native.MOD_ALT | Native.MOD_NOREPEAT, 0x4E /* N */);
        AppLog.Write($"RegisterHotKey ok={_hotKeyOk} hwnd=0x{_hwnd.ToInt64():X} err={Marshal.GetLastWin32Error()}");
    }

    private IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (msg == Native.WM_HOTKEY && wParam.ToInt32() == HotKeyId)
        {
            AppLog.Write("WM_HOTKEY received");
            ToggleVisibility();
            handled = true;
        }
        return IntPtr.Zero;
    }

    private void ToggleVisibility()
    {
        AppLog.Write($"ToggleVisibility: visible={IsVisible} state={WindowState}");
        if (IsVisible && WindowState != WindowState.Minimized)
        {
            Hide();
        }
        else
        {
            if (WindowState == WindowState.Minimized)
                WindowState = WindowState.Normal;
            Show();
            Activate();
            Native.SetForegroundWindow(_hwnd);
            ApplyLevel(_level, persist: false);
            EnsureFixedTop(force: true);
        }
    }

    private void SetTheme(string theme)
    {
        _settings.Theme = theme;
        SettingsStore.Save(_settings);
        ApplyWindowBackground();
        if (_uiReady) Post(new { type = "theme", theme });
        _tray?.UpdateTheme(theme);
        PostSettings();
    }

    private void ToggleStartup() => SetStartup(!_settings.StartWithWindows);

    private void SetStartup(bool enable)
    {
        _settings.StartWithWindows = enable;
        SettingsStore.Save(_settings);
        StartupRegistrar.Apply(enable);
        _tray?.UpdateStartup(enable);
        PostSettings();
    }

    private void OpenExternal(string target)
    {
        // 内部协议先自行消化，绝不外抛给系统外壳
        if (TryHandleInternalUri(target)) return;

        try
        {
            if (!IsSafeExternal(target))
            {
                AppLog.Write("OpenExternal: rejected " + target);
                return;
            }
            Process.Start(new ProcessStartInfo
            {
                FileName = target,
                UseShellExecute = true
            });
        }
        catch (Exception ex)
        {
            AppLog.Error("OpenExternal", ex);
        }
    }

    /// <summary>外链白名单：只放行 http/https/mailto 与确实存在的本地路径。</summary>
    private static bool IsSafeExternal(string target)
    {
        if (string.IsNullOrWhiteSpace(target)) return false;
        if (Uri.TryCreate(target, UriKind.Absolute, out var u) && !u.IsFile)
        {
            return u.Scheme == Uri.UriSchemeHttp
                || u.Scheme == Uri.UriSchemeHttps
                || u.Scheme == Uri.UriSchemeMailto;
        }
        return File.Exists(target) || Directory.Exists(target);
    }

    // ---------- 前端消息 ----------

    private void Post(object payload)
    {
        try
        {
            _core?.PostWebMessageAsJson(JsonSerializer.Serialize(payload, JsonOpts));
        }
        catch { }
    }

    private void OnWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        try
        {
            using var doc = JsonDocument.Parse(e.WebMessageAsJson);
            var root = doc.RootElement;

            string? type = root.TryGetProperty("type", out var tEl) ? tEl.GetString() : null;
            if (string.IsNullOrEmpty(type)) return;

            if (type != "log" && type != "save")
                AppLog.Write("msg in: " + type);

            switch (type)
            {
                case "ready":
                    _uiReady = true;
                    Post(BuildInitPayload());
                    if (_hideAfterReady)
                    {
                        _hideAfterReady = false;
                        AppLog.Write("start minimized: hide after ready");
                        Hide();
                    }
                    break;

                case "save":
                {
                    string id = GetString(root, "id");
                    string content = GetString(root, "content");
                    if (id.Length == 0) return;
                    var meta = _store.SaveContent(id, content);
                    Post(new
                    {
                        type = "saved",
                        id,
                        at = meta.Updated,
                        title = meta.Title
                    });
                    _tray?.RefreshTip(_store.Index, _reminders.PendingCount);
                    break;
                }

                case "flushed":
                    _flushTcs?.TrySetResult(true);
                    break;

                case "create":
                {
                    string kind = GetString(root, "kind");
                    var meta = _store.Create(kind: kind);
                    var payload = new NotePayload
                    {
                        Id = meta.Id,
                        Title = meta.Title,
                        Content = _store.ReadRaw(meta.Id),
                        Updated = meta.Updated,
                        Pin = meta.Pin,
                        Mark = meta.Mark,
                        Kind = meta.Kind
                    };
                    Post(new { type = "created", note = payload, index = BuildIndexPayload() });
                    _tray?.RefreshTip(_store.Index, _reminders.PendingCount);
                    break;
                }

                case "pin":
                {
                    string id = GetString(root, "id");
                    bool on = root.TryGetProperty("on", out var onEl) && onEl.ValueKind == JsonValueKind.True;
                    if (id.Length == 0) return;
                    _store.SetPin(id, on);
                    Post(new { type = "index", notes = BuildIndexPayload(), activeId = _store.Index.ActiveId });
                    break;
                }

                case "mark":
                {
                    string id = GetString(root, "id");
                    string mark = GetString(root, "mark");
                    if (id.Length == 0) return;
                    _store.SetMark(id, mark);
                    Post(new { type = "index", notes = BuildIndexPayload(), activeId = _store.Index.ActiveId });
                    break;
                }

                case "fixedTop":
                {
                    bool on = root.TryGetProperty("on", out var ftEl) && ftEl.ValueKind == JsonValueKind.True;
                    SetFixedTop(on);
                    break;
                }

                case "autoFlag":
                {
                    string name = GetString(root, "name");
                    bool on = root.TryGetProperty("on", out var afEl) && afEl.ValueKind == JsonValueKind.True;
                    if (name == "highlight") _settings.AutoHighlight = on;
                    else if (name == "link") _settings.AutoLink = on;
                    SettingsStore.Save(_settings);
                    PostSettings();
                    break;
                }

                case "startup":
                {
                    bool on = root.TryGetProperty("on", out var suEl) && suEl.ValueKind == JsonValueKind.True;
                    SetStartup(on);
                    break;
                }

                case "startMinimized":
                {
                    bool on = root.TryGetProperty("on", out var smEl) && smEl.ValueKind == JsonValueKind.True;
                    _settings.StartMinimized = on;
                    SettingsStore.Save(_settings);
                    PostSettings();
                    break;
                }

                case "weeklyAutoReset":
                {
                    bool on = root.TryGetProperty("on", out var wrEl) && wrEl.ValueKind == JsonValueKind.True;
                    _settings.WeeklyAutoReset = on;
                    SettingsStore.Save(_settings);
                    PostSettings();
                    break;
                }

                case "sidebar":
                {
                    bool on = root.TryGetProperty("on", out var sbEl) && sbEl.ValueKind == JsonValueKind.True;
                    _settings.SidebarVisible = on;
                    SettingsStore.Save(_settings);
                    break;
                }

                case "delete":
                {
                    string id = GetString(root, "id");
                    if (id.Length == 0) return;
                    _store.Delete(id);
                    _reminders.RemoveByNote(id);   // 便签没了，挂在上面的提醒一并清掉
                    Post(new { type = "index", notes = BuildIndexPayload(), activeId = _store.Index.ActiveId });
                    PostReminders();
                    _tray?.RefreshTip(_store.Index, _reminders.PendingCount);
                    break;
                }

                case "rename":
                {
                    string id = GetString(root, "id");
                    string title = GetString(root, "title");
                    _store.Rename(id, title);
                    Post(new { type = "index", notes = BuildIndexPayload(), activeId = _store.Index.ActiveId });
                    _tray?.RefreshTip(_store.Index, _reminders.PendingCount);
                    break;
                }

                case "active":
                {
                    string id = GetString(root, "id");
                    if (id.Length > 0) _store.SetActive(id);
                    break;
                }

                case "level":
                {
                    string token = GetString(root, "level");
                    var lv = token switch
                    {
                        "desk" => WindowLevel.Desk,
                        "normal" => WindowLevel.Normal,
                        _ => WindowLevel.Top
                    };
                    ApplyLevel(lv, persist: true);
                    break;
                }

                case "theme":
                    SetTheme(GetString(root, "theme"));
                    break;

                case "fontSize":
                {
                    int size = root.TryGetProperty("size", out var sz) && sz.TryGetInt32(out int v) ? v : 14;
                    _settings.FontSize = Math.Clamp(size, 11, 22);
                    SettingsStore.Save(_settings);
                    PostSettings();
                    break;
                }

                case "drag":
                    StartDrag();
                    break;

                case "minimize":
                    WindowState = WindowState.Minimized;
                    break;

                case "hide":
                    AppLog.Write("msg: hide src=" + GetString(root, "src"));
                    Hide();
                    break;

                case "addReminder":
                {
                    string noteId = GetString(root, "noteId");
                    string text = GetString(root, "text");
                    long dueAt = root.TryGetProperty("dueAt", out var dEl) && dEl.TryGetInt64(out long dv)
                        ? dv : 0;
                    if (dueAt <= 0) return;
                    _reminders.Add(noteId, text, dueAt);
                    PostReminders();
                    break;
                }

                case "removeReminder":
                {
                    string id = GetString(root, "id");
                    if (id.Length == 0) return;
                    _reminders.Remove(id);
                    PostReminders();
                    break;
                }

                case "syncReminders":
                    PostReminders();
                    break;

                case "weeklyReset":
                    // 托盘菜单与调试都用这一条路径，行为完全一致
                    MaybeWeeklyReset(force: true);
                    break;

                case "exit":
                    AppLog.Write("msg: exit from web");
                    ExitApp();
                    break;

                case "openData":
                    OpenExternal(AppPaths.DataDir);
                    break;

                case "openExternal":
                    OpenExternal(GetString(root, "url"));
                    break;

                case "log":
                    AppLog.Write("[web] " + GetString(root, "message"));
                    break;
            }
        }
        catch (Exception ex)
        {
            AppLog.Error("OnWebMessage", ex);
        }
    }

    private void StartDrag()
    {
        if (_hwnd == IntPtr.Zero) return;
        Native.ReleaseCapture();
        Native.SendMessage(_hwnd, Native.WM_NCLBUTTONDOWN, (IntPtr)Native.HTCAPTION, IntPtr.Zero);
    }

    private static string GetString(JsonElement root, string name) =>
        root.TryGetProperty(name, out var el) && el.ValueKind == JsonValueKind.String
            ? el.GetString() ?? ""
            : "";

    private object BuildIndexPayload() =>
        _store.Index.Notes.Select(n => new
        {
            id = n.Id,
            title = n.Title,
            updated = n.Updated,
            pin = n.Pin,
            mark = n.Mark,
            kind = n.Kind
        });

    private object BuildInitPayload()
    {
        var idx = _store.Index;
        var notes = idx.Notes.Select(n => new NotePayload
        {
            Id = n.Id,
            Title = n.Title,
            Content = _store.ReadRaw(n.Id),
            Updated = n.Updated,
            Pin = n.Pin,
            Mark = n.Mark,
            Kind = n.Kind
        }).ToList();

        return new
        {
            type = "init",
            notes,
            activeId = idx.ActiveId ?? notes.FirstOrDefault()?.Id,
            settings = BuildSettingsPayload(),
            hotkeyOk = _hotKeyOk,
            reminders = BuildReminderPayload(),
            now = DateTimeOffset.Now.ToUnixTimeMilliseconds()
        };
    }

    /// <summary>
    /// 全部可调项的统一快照。初始化和「设置页里改了任意一项」都走这里，
    /// 避免前端各处各自维护副本导致托盘与页面状态打架。
    /// </summary>
    private object BuildSettingsPayload() => new
    {
        theme = _settings.Theme,
        fontSize = _settings.FontSize,
        level = LevelToken(_level),
        systemDark = IsSystemDark(),
        sidebar = _settings.SidebarVisible,
        fixedTop = _settings.FixedTop,
        autoHighlight = _settings.AutoHighlight,
        autoLink = _settings.AutoLink,
        startWithWindows = StartupRegistrar.IsEnabled(),
        startMinimized = _settings.StartMinimized,
        weeklyAutoReset = _settings.WeeklyAutoReset,
        version = AppVersion(),
        dataDir = AppPaths.DataDir
    };

    private void PostSettings()
    {
        if (_uiReady) Post(new { type = "settings", settings = BuildSettingsPayload() });
    }

    private static string AppVersion()
    {
        var asm = typeof(MainWindow).Assembly;
        var attr = Attribute.GetCustomAttribute(
            asm, typeof(System.Reflection.AssemblyInformationalVersionAttribute))
            as System.Reflection.AssemblyInformationalVersionAttribute;
        string v = attr?.InformationalVersion ?? asm.GetName().Version?.ToString() ?? "1.0.0";
        // 带 SourceLink 时会带 +commit，界面上只显示版本号
        int cut = v.IndexOf('+');
        return cut > 0 ? v[..cut] : v;
    }

    // ---------- 关闭 / 退出 ----------

    protected override void OnClosing(System.ComponentModel.CancelEventArgs e)
    {
        AppLog.Write($"OnClosing: reallyClose={_reallyClose} visible={IsVisible}");
        if (!_reallyClose)
        {
            e.Cancel = true;
            Hide();
            return;
        }
        base.OnClosing(e);
    }

    private async void ExitApp()
    {
        AppLog.Write("ExitApp requested at:\n" + Environment.StackTrace);
        if (_exiting) return;
        _exiting = true;

        await FlushAndWaitAsync(700);

        SaveGeometry();
        SettingsStore.Save(_settings);
        _store.Persist();

        if (_hwnd != IntPtr.Zero && _hotKeyOk)
            Native.UnregisterHotKey(_hwnd, HotKeyId);

        _tray?.Dispose();
        _tray = null;

        try { Web.Dispose(); } catch { }

        _reallyClose = true;
        Close();
        Application.Current.Shutdown();
    }

    private async Task FlushAndWaitAsync(int timeoutMs)
    {
        if (_core is null) return;
        _flushTcs = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        Post(new { type = "flush" });
        await Task.WhenAny(_flushTcs.Task, Task.Delay(timeoutMs));
        _flushTcs = null;
    }
}
