using System.IO;
using System.Text;

namespace FloatNote;

/// <summary>
/// 启动时对用户数据做快照：index / settings / reminders + notes 全部纯文本，
/// 直接目录复制到 data\backup\snap-<日期>\。每天最多一份，滚动保留最近 KeepDays 天。
/// 目的：更新、误删、手滑任何一种意外，都能从快照里整份找回。
/// 快照放在 data 内部 —— 用户把整个 data 文件夹复制走，备份也一并带走。
/// </summary>
public static class DataBackup
{
    private const int KeepDays = 5;

    public static void RunOnStartup()
    {
        try
        {
            if (!File.Exists(AppPaths.IndexFile)) return;          // 首次运行，无数据可备
            if (!Directory.Exists(AppPaths.NotesDir)) return;
            if (!Directory.EnumerateFiles(AppPaths.NotesDir).Any()) return;

            string root = Path.Combine(AppPaths.DataDir, "backup");
            Directory.CreateDirectory(root);

            // 同一天多次启动只留一份快照
            string target = Path.Combine(root, "snap-" + DateTime.Now.ToString("yyyy-MM-dd"));
            if (Directory.Exists(target)) return;

            Directory.CreateDirectory(target);

            foreach (var f in new[] { AppPaths.IndexFile, AppPaths.SettingsFile, AppPaths.RemindersFile })
            {
                if (File.Exists(f))
                    File.Copy(f, Path.Combine(target, Path.GetFileName(f)), true);
            }
            foreach (var f in Directory.EnumerateFiles(AppPaths.NotesDir))
                File.Copy(f, Path.Combine(target, Path.GetFileName(f)), true);

            // 写个说明，防止将来翻到快照时认不出这是什么东西
            File.WriteAllText(Path.Combine(target, "README.txt"),
                "浮笺自动快照（每天启动时生成一份，保留最近 " + KeepDays + " 天）。\n" +
                "恢复方法：把这里面的 .md 与 .json 复制回上一级 data 目录即可。\n",
                new UTF8Encoding(false));

            PruneOld(root);
        }
        catch (Exception ex)
        {
            AppLog.Error("backup", ex);   // 备份失败绝不能影响正常启动
        }
    }

    private static void PruneOld(string root)
    {
        var dirs = Directory.GetDirectories(root, "snap-*")
            .OrderBy(d => d, StringComparer.Ordinal)
            .ToList();
        while (dirs.Count > KeepDays)
        {
            try { Directory.Delete(dirs[0], true); } catch { }
            dirs.RemoveAt(0);
        }
    }
}
