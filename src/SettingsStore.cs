using System.IO;
using System.Text;
using System.Text.Json;

namespace FloatNote;

/// <summary>设置读写。写入失败不抛异常，避免拖垮界面。</summary>
public static class SettingsStore
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true
    };

    private static readonly UTF8Encoding Utf8NoBom = new(false);

    public static AppSettings Load()
    {
        try
        {
            if (File.Exists(AppPaths.SettingsFile))
            {
                var json = File.ReadAllText(AppPaths.SettingsFile, Utf8NoBom);
                var s = JsonSerializer.Deserialize<AppSettings>(json, JsonOpts);
                if (s is not null) return Sanitize(s);
            }
        }
        catch { }
        return new AppSettings();
    }

    public static void Save(AppSettings s)
    {
        try
        {
            Directory.CreateDirectory(AppPaths.DataDir);
            var json = JsonSerializer.Serialize(s, JsonOpts);
            var tmp = AppPaths.SettingsFile + ".tmp";
            File.WriteAllText(tmp, json, Utf8NoBom);
            File.Move(tmp, AppPaths.SettingsFile, true);
        }
        catch { }
    }

    private static AppSettings Sanitize(AppSettings s)
    {
        if (s.Width < 320) s.Width = 720;
        if (s.Height < 240) s.Height = 500;
        if (s.FontSize < 11) s.FontSize = 14;
        if (s.FontSize > 22) s.FontSize = 22;
        if (s.Theme is not ("light" or "dark" or "system")) s.Theme = "system";
        return s;
    }
}
