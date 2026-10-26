using System.Drawing;
using System.IO;
using System.Windows.Forms;

namespace FloatNote;

/// <summary>系统托盘：显示/隐藏、层级切换、主题切换、开机自启、退出。</summary>
public sealed class TrayIconManager : IDisposable
{
    private readonly NotifyIcon _icon;
    private readonly ContextMenuStrip _menu;

    private readonly ToolStripMenuItem _showItem;
    private readonly ToolStripMenuItem _lvDesk;
    private readonly ToolStripMenuItem _lvNormal;
    private readonly ToolStripMenuItem _lvTop;
    private readonly ToolStripMenuItem _thLight;
    private readonly ToolStripMenuItem _thDark;
    private readonly ToolStripMenuItem _thSystem;
    private readonly ToolStripMenuItem _startup;
    private readonly ToolStripMenuItem _fixedTop;
    private readonly ToolStripMenuItem _visibleMenuItem;
    private readonly Action<string>? _onReminderClick;

    private string _lastReminderNote = "";
    private bool _disposed;

    public TrayIconManager(
        WindowLevel level,
        string theme,
        bool startWithWindows,
        bool fixedTop,
        Action onToggleVisibility,
        Action<WindowLevel> onLevel,
        Action<bool> onFixedTop,
        Action<string> onTheme,
        Action onToggleStartup,
        Action onOpenData,
        Action onExit,
        Action<string>? onReminderClick = null,
        Action? onWeeklyReset = null)
    {
        _onReminderClick = onReminderClick;
        _menu = new ContextMenuStrip
        {
            ShowImageMargin = false,
            Font = new Font("Microsoft YaHei UI", 9f)
        };

        _showItem = new ToolStripMenuItem("显示 / 隐藏", null, (_, _) => onToggleVisibility())
        {
            ShortcutKeyDisplayString = "Ctrl+Alt+N"
        };
        _menu.Items.Add(_showItem);
        _menu.Items.Add(new ToolStripSeparator());

        // 用局部变量规避「初始化过程中引用自身字段」的空引用告警
        ToolStripMenuItem? fixedItem = null;
        fixedItem = new ToolStripMenuItem("固定置顶", null,
            (_, _) => onFixedTop(!(fixedItem?.Checked ?? false)))
        {
            CheckOnClick = false
        };
        _fixedTop = fixedItem;
        _menu.Items.Add(_fixedTop);

        var levelMenu = new ToolStripMenuItem("窗口层级");
        _lvDesk = new ToolStripMenuItem("贴桌面（最底层）", null, (_, _) => onLevel(WindowLevel.Desk));
        _lvNormal = new ToolStripMenuItem("普通窗口", null, (_, _) => onLevel(WindowLevel.Normal));
        _lvTop = new ToolStripMenuItem("置顶显示", null, (_, _) => onLevel(WindowLevel.Top));
        levelMenu.DropDownItems.AddRange(new ToolStripItem[] { _lvDesk, _lvNormal, _lvTop });
        _menu.Items.Add(levelMenu);

        var themeMenu = new ToolStripMenuItem("配色");
        _thLight = new ToolStripMenuItem("浅色", null, (_, _) => onTheme("light"));
        _thDark = new ToolStripMenuItem("深色", null, (_, _) => onTheme("dark"));
        _thSystem = new ToolStripMenuItem("跟随系统", null, (_, _) => onTheme("system"));
        themeMenu.DropDownItems.AddRange(new ToolStripItem[] { _thLight, _thDark, _thSystem });
        _menu.Items.Add(themeMenu);

        _startup = new ToolStripMenuItem("开机自启", null, (_, _) => onToggleStartup())
        {
            CheckOnClick = false
        };
        _menu.Items.Add(_startup);

        _menu.Items.Add(new ToolStripSeparator());

        _visibleMenuItem = new ToolStripMenuItem("打开数据文件夹", null, (_, _) => onOpenData());
        _menu.Items.Add(_visibleMenuItem);

        if (onWeeklyReset is not null)
        {
            _menu.Items.Add(new ToolStripMenuItem("立即结算本周待办", null,
                (_, _) => onWeeklyReset()));
        }

        _menu.Items.Add(new ToolStripMenuItem("退出浮笺", null, (_, _) => onExit()));

        _icon = new NotifyIcon
        {
            Icon = LoadIcon(),
            Text = "浮笺",
            Visible = true,
            ContextMenuStrip = _menu
        };
        _icon.DoubleClick += (_, _) => onToggleVisibility();
        // 点气泡正文即跳回来源便签
        _icon.BalloonTipClicked += (_, _) =>
        {
            try { _onReminderClick?.Invoke(_lastReminderNote); } catch { }
        };

        UpdateLevel(level);
        UpdateTheme(theme);
        UpdateStartup(startWithWindows);
        UpdateFixedTop(fixedTop);
    }

    private static Icon LoadIcon()
    {
        try
        {
            var res = System.Windows.Application.GetResourceStream(
                new Uri("pack://application:,,,/Assets/app.ico"));
            if (res?.Stream is not null)
                return new Icon(res.Stream, new Size(32, 32));
        }
        catch { }

        try
        {
            string? exe = Environment.ProcessPath;
            if (exe is not null) return Icon.ExtractAssociatedIcon(exe) ?? SystemIcons.Application;
        }
        catch { }

        return SystemIcons.Application;
    }

    public void UpdateLevel(WindowLevel level)
    {
        _lvDesk.Checked = level == WindowLevel.Desk;
        _lvNormal.Checked = level == WindowLevel.Normal;
        _lvTop.Checked = level == WindowLevel.Top;
    }

    public void UpdateTheme(string theme)
    {
        _thLight.Checked = theme == "light";
        _thDark.Checked = theme == "dark";
        _thSystem.Checked = theme == "system";
    }

    public void UpdateStartup(bool enabled) => _startup.Checked = enabled;

    public void UpdateFixedTop(bool enabled) => _fixedTop.Checked = enabled;

    /// <summary>托盘悬浮提示：便签数、未完成待办数与待触发提醒数。</summary>
    public void RefreshTip(NoteIndex index, int pendingReminders = 0)
    {
        try
        {
            int pending = 0;
            foreach (var n in index.Notes)
            {
                string path = Path.Combine(AppPaths.NotesDir, n.Id + ".md");
                if (!File.Exists(path)) continue;
                foreach (var line in File.ReadLines(path))
                {
                    if (line.TrimStart().StartsWith("- [ ]", StringComparison.Ordinal)) pending++;
                }
            }

            string tip = $"浮笺 · {index.Notes.Count} 条便签";
            if (pending > 0) tip += $" · {pending} 项待办";
            if (pendingReminders > 0) tip += $" · {pendingReminders} 个提醒";

            _icon.Text = tip.Length > 62 ? tip.Substring(0, 62) : tip;
        }
        catch { }
    }

    /// <summary>到点提醒：走系统通知气泡（Win10/11 会归入操作中心）。</summary>
    public void NotifyReminder(Reminder r, string noteTitle)
    {
        try
        {
            _lastReminderNote = r.NoteId;
            string body = string.IsNullOrWhiteSpace(noteTitle)
                ? r.Text
                : r.Text + "\n（来自便签： " + noteTitle + "）";
            _icon.ShowBalloonTip(12000, "浮笺提醒", body, ToolTipIcon.None);
        }
        catch { }
    }

    public static void Toast(string text)
    {
        // 轻量提示：托盘气泡由调用方决定，这里仅用于无窗口场景的兜底
        System.Diagnostics.Debug.WriteLine(text);
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        try
        {
            _icon.Visible = false;
            _icon.ContextMenuStrip?.Dispose();
            _icon.Dispose();
            _menu.Dispose();
        }
        catch { }
    }
}

/// <summary>开机自启（HKCU Run 键，不写系统目录、不需要管理员权限）。</summary>
public static class StartupRegistrar
{
    private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string ValueName = "FloatNote";

    public static void Apply(bool enable)
    {
        try
        {
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(RunKey, writable: true);
            if (key is null) return;

            if (enable)
            {
                string? exe = Environment.ProcessPath;
                if (string.IsNullOrEmpty(exe)) return;
                key.SetValue(ValueName, "\"" + exe + "\"");
            }
            else if (key.GetValue(ValueName) is not null)
            {
                key.DeleteValue(ValueName, throwOnMissingValue: false);
            }
        }
        catch { }
    }

    public static bool IsEnabled()
    {
        try
        {
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(RunKey);
            return key?.GetValue(ValueName) is not null;
        }
        catch { return false; }
    }
}
