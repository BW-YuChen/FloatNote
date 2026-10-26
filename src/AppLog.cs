using System.IO;
using System.Text;

namespace FloatNote;

/// <summary>运行日志：写入 data/app.log，便于排查启动与渲染问题。</summary>
public static class AppLog
{
    private static readonly object Gate = new();
    private const long MaxBytes = 512 * 1024;

    /// <summary>过程日志开关：仅在 --dev 下开启，正式运行只记录 Error。</summary>
    public static bool Enabled { get; set; }

    public static void Write(string message)
    {
        if (!Enabled) return;
        try
        {
            lock (Gate)
            {
                Directory.CreateDirectory(AppPaths.DataDir);
                string path = Path.Combine(AppPaths.DataDir, "app.log");

                var fi = new FileInfo(path);
                if (fi.Exists && fi.Length > MaxBytes)
                {
                    string old = Path.Combine(AppPaths.DataDir, "app.old.log");
                    File.Move(path, old, true);
                }

                File.AppendAllText(path,
                    $"{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff} {message}{Environment.NewLine}",
                    new UTF8Encoding(false));
            }
        }
        catch { }
    }

    public static void Error(string where, Exception ex)
    {
        bool prev = Enabled;
        Enabled = true;   // 错误无论是否 dev 都要落盘
        try { Write($"ERROR [{where}] {ex.GetType().Name}: {ex.Message}\n{ex.StackTrace}"); }
        finally { Enabled = prev; }
    }
}
