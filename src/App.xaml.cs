using System.Threading;
using System.Windows;

namespace FloatNote;

public partial class App : Application
{
    private static Mutex? _instanceMutex;

    protected override void OnStartup(StartupEventArgs e)
    {
        const string mutexName = @"Local\FloatNote.SingleInstance.7C1B4A36";
        _instanceMutex = new Mutex(true, mutexName, out bool isFirst);

        if (!isFirst)
        {
            // 已有实例在运行：唤起它的窗口，自身退出
            try
            {
                var hwnd = Native.FindWindow(null, "浮笺");
                if (hwnd != IntPtr.Zero)
                {
                    Native.ShowWindow(hwnd, Native.SW_RESTORE);
                    Native.SetForegroundWindow(hwnd);
                }
            }
            catch { }
            Shutdown();
            return;
        }

        base.OnStartup(e);

        AppPaths.EnsureDataDirs();

        bool devMode = e.Args.Any(a =>
            string.Equals(a, "--dev", StringComparison.OrdinalIgnoreCase));

        // 正式运行不写过程日志，避免 data/app.log 无谓增长；错误始终记录
        AppLog.Enabled = devMode;
        AppLog.Write("app start, dev=" + devMode);

        // 先快照再开窗：任何后续意外（含更新替换 exe）都有当天之前的存档可回
        DataBackup.RunOnStartup();

        var window = new MainWindow(devMode);
        MainWindow = window;
        window.Show();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _instanceMutex?.Dispose();
        base.OnExit(e);
    }
}
