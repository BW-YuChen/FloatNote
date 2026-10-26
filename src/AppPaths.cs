using System.IO;
using System.Reflection;
using System.Text;

namespace FloatNote;

/// <summary>程序目录与数据目录的集中定义。所有用户数据都在 exe 同级 data 文件夹内。</summary>
public static class AppPaths
{
    public static string BaseDir =>
        Path.GetDirectoryName(Environment.ProcessPath) ?? AppContext.BaseDirectory;

    public static string DataDir => Path.Combine(BaseDir, "data");
    public static string NotesDir => Path.Combine(DataDir, "notes");
    public static string TrashDir => Path.Combine(DataDir, "trash");
    public static string WebDir => Path.Combine(DataDir, "web");
    public static string WebViewCacheDir => Path.Combine(DataDir, "cache");
    public static string IndexFile => Path.Combine(DataDir, "index.json");
    public static string SettingsFile => Path.Combine(DataDir, "settings.json");
    public static string RemindersFile => Path.Combine(DataDir, "reminders.json");

    public static void EnsureDataDirs()
    {
        Directory.CreateDirectory(DataDir);
        Directory.CreateDirectory(NotesDir);
        Directory.CreateDirectory(TrashDir);
        Directory.CreateDirectory(WebViewCacheDir);
    }
}

/// <summary>把嵌入资源中的前端静态文件解压到 data/web，内容变化时自动重解压。</summary>
public static class WebAssets
{
    private const string Prefix = "web/";

    public static string EnsureExtracted()
    {
        string target = AppPaths.WebDir;
        var asm = Assembly.GetExecutingAssembly();

        // 用「资源名 + 长度」的指纹判断是否需要重解压：
        // 比程序集版本更灵敏，改一行前端代码也能触发更新
        // 注意：LogicalName 中的 RecursiveDir 带反斜杠，读取流必须用原始名
        var resources = new List<(string Raw, string Norm, long Length)>();
        foreach (var raw in asm.GetManifestResourceNames())
        {
            string norm = raw.Replace('\\', '/');
            if (!norm.StartsWith(Prefix, StringComparison.Ordinal)) continue;
            using var probe = asm.GetManifestResourceStream(raw);
            resources.Add((raw, norm, probe?.Length ?? 0));
        }

        if (resources.Count == 0)
            throw new InvalidOperationException("前端资源缺失：程序集中未找到嵌入资源。");

        var sb = new StringBuilder();
        foreach (var r in resources.OrderBy(r => r.Norm, StringComparer.Ordinal))
            sb.Append(r.Norm).Append(':').Append(r.Length).Append(';');
        string fingerprint = Fingerprint(sb.ToString());

        string marker = Path.Combine(target, ".version");
        if (File.Exists(marker))
        {
            try
            {
                if (File.ReadAllText(marker, Encoding.UTF8).Trim() == fingerprint &&
                    File.Exists(Path.Combine(target, "index.html")))
                    return target;
            }
            catch { }
        }

        Directory.CreateDirectory(target);
        foreach (var file in Directory.EnumerateFiles(target, "*", SearchOption.AllDirectories))
        {
            try { File.Delete(file); } catch { }
        }

        int written = 0;
        var failed = new List<string>();
        foreach (var r in resources)
        {
            string rel = r.Norm.Substring(Prefix.Length);
            if (string.IsNullOrWhiteSpace(rel)) continue;

            using var src = asm.GetManifestResourceStream(r.Raw);
            if (src is null)
            {
                failed.Add(r.Raw);
                continue;
            }

            string dest = Path.Combine(target, rel.Replace('/', Path.DirectorySeparatorChar));
            Directory.CreateDirectory(Path.GetDirectoryName(dest)!);

            using var dst = File.Create(dest);
            src.CopyTo(dst);
            written++;
        }

        if (failed.Count > 0)
            AppLog.Write($"WebAssets: {failed.Count} 个资源读取失败，首个：{failed[0]}");
        if (written == 0)
            throw new InvalidOperationException("前端资源解压失败：未能读出任何文件。");

        File.WriteAllText(marker, fingerprint, new UTF8Encoding(false));
        AppLog.Write($"WebAssets: 解压 {written} 个文件到 {target}");
        return target;
    }

    private static string Fingerprint(string input)
    {
        var bytes = System.Security.Cryptography.SHA256.HashData(Encoding.UTF8.GetBytes(input));
        return Convert.ToHexString(bytes, 0, 8).ToLowerInvariant();
    }
}
