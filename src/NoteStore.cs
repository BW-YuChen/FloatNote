using System.Globalization;
using System.IO;
using System.Text;
using System.Text.Json;

namespace FloatNote;

/// <summary>便签持久化：索引存 index.json，正文每条一个 .md 文件。删除进回收目录而非直接销毁。</summary>
public sealed class NoteStore
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping
    };

    private static readonly UTF8Encoding Utf8NoBom = new(false);
    private readonly object _gate = new();
    private NoteIndex _index;

    public NoteStore()
    {
        _index = Load();
    }

    public NoteIndex Index
    {
        get { lock (_gate) return _index; }
    }

    private NoteIndex Load()
    {
        try
        {
            if (File.Exists(AppPaths.IndexFile))
            {
                var json = File.ReadAllText(AppPaths.IndexFile, Utf8NoBom);
                var idx = JsonSerializer.Deserialize<NoteIndex>(json, JsonOpts);
                if (idx is not null && idx.Notes.Count > 0) return idx;
            }
        }
        catch { /* 损坏则重建 */ }

        var fresh = new NoteIndex();
        foreach (var f in Directory.EnumerateFiles(AppPaths.NotesDir, "*.md"))
        {
            var id = Path.GetFileNameWithoutExtension(f);
            var content = ReadRaw(id);
            fresh.Notes.Add(new NoteMeta
            {
                Id = id,
                Title = DeriveTitle(content, id),
                Created = File.GetCreationTimeUtc(f).ToUnixMs(),
                Updated = File.GetLastWriteTimeUtc(f).ToUnixMs()
            });
        }

        if (fresh.Notes.Count == 0) return fresh;

        fresh.Notes = fresh.Notes.OrderByDescending(n => n.Updated).ToList();
        fresh.ActiveId = fresh.Notes[0].Id;
        _index = fresh;
        Persist();
        return fresh;
    }

    public string ReadRaw(string id)
    {
        try
        {
            var path = NotePath(id);
            return File.Exists(path) ? File.ReadAllText(path, Utf8NoBom) : "";
        }
        catch { return ""; }
    }

    public void WriteRaw(string id, string content)
    {
        var path = NotePath(id);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        // 先写临时文件再替换，避免写入过程中断电导致正文损坏
        var tmp = path + ".tmp";
        File.WriteAllText(tmp, content, Utf8NoBom);
        File.Move(tmp, path, true);
    }

    /// <summary>保存正文并同步标题，返回该便签的最新元数据。</summary>
    public NoteMeta SaveContent(string id, string content)
    {
        lock (_gate)
        {
            WriteRaw(id, content);
            var meta = _index.Notes.FirstOrDefault(n => n.Id == id);
            if (meta is null)
            {
                meta = new NoteMeta { Id = id, Created = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() };
                _index.Notes.Insert(0, meta);
            }
            var derived = DeriveTitle(content, meta.Title);
            if (!string.IsNullOrWhiteSpace(derived)) meta.Title = derived;
            meta.Updated = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            Persist();
            return meta;
        }
    }

    /// <summary>新建便签。kind = "weekly" 时按 ISO 周生成「每周待办」骨架。</summary>
    public NoteMeta Create(string? title = null, string kind = "", string mark = "", bool pin = false, string? content = null)
    {
        lock (_gate)
        {
            var id = NewId();

            bool weekly = string.Equals(kind, "weekly", StringComparison.OrdinalIgnoreCase);
            title ??= weekly ? "每周待办" : "未命名便签";
            if (string.IsNullOrWhiteSpace(mark))
                mark = weekly ? "emoji:📅" : "";

            var body = content ?? (weekly
                ? WeeklyTemplate.Build(DateTime.Now, title)
                : $"# {title}\n\n");

            WriteRaw(id, body);
            var now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            var meta = new NoteMeta
            {
                Id = id,
                Title = title,
                Kind = weekly ? "weekly" : "",
                Mark = mark,
                Pin = pin || weekly,
                Created = now,
                Updated = now
            };
            _index.Notes.Insert(0, meta);
            _index.ActiveId = id;
            Persist();
            return meta;
        }
    }

    public void Rename(string id, string title)
    {
        lock (_gate)
        {
            var meta = _index.Notes.FirstOrDefault(n => n.Id == id);
            if (meta is null) return;
            meta.Title = string.IsNullOrWhiteSpace(title) ? "未命名便签" : title.Trim();
            meta.Updated = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            Persist();
        }
    }

    /// <summary>侧栏置顶开关。</summary>
    public void SetPin(string id, bool pin)
    {
        lock (_gate)
        {
            var meta = _index.Notes.FirstOrDefault(n => n.Id == id);
            if (meta is null) return;
            meta.Pin = pin;
            Persist();
        }
    }

    /// <summary>设置分类标记（"emoji:📚" / "color:#e0685c" / 空串）。</summary>
    public void SetMark(string id, string mark)
    {
        lock (_gate)
        {
            var meta = _index.Notes.FirstOrDefault(n => n.Id == id);
            if (meta is null) return;
            meta.Mark = mark ?? "";
            Persist();
        }
    }

    /// <summary>删除便签：正文移入 data/trash，索引移除。</summary>
    public void Delete(string id)
    {
        lock (_gate)
        {
            var src = NotePath(id);
            if (File.Exists(src))
            {
                Directory.CreateDirectory(AppPaths.TrashDir);
                var dest = Path.Combine(AppPaths.TrashDir,
                    $"{id}_{DateTime.Now:yyyyMMdd_HHmmss}.md");
                try { File.Move(src, dest, true); } catch { }
            }

            _index.Notes.RemoveAll(n => n.Id == id);
            if (_index.ActiveId == id)
                _index.ActiveId = _index.Notes.FirstOrDefault()?.Id;
            Persist();
        }
    }

    public void SetActive(string id)
    {
        lock (_gate)
        {
            _index.ActiveId = id;
            Persist();
        }
    }

    public void Persist()
    {
        try
        {
            var json = JsonSerializer.Serialize(_index, JsonOpts);
            var tmp = AppPaths.IndexFile + ".tmp";
            File.WriteAllText(tmp, json, Utf8NoBom);
            File.Move(tmp, AppPaths.IndexFile, true);
        }
        catch { }
    }

    private string NotePath(string id) => Path.Combine(AppPaths.NotesDir, id + ".md");

    private static string NewId()
    {
        var t = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds().ToString("x");
        var r = Random.Shared.Next(0, 0xFFFF).ToString("x4");
        return t + r;
    }

    /// <summary>标题优先取正文首行的 # 标题，否则沿用原标题。</summary>
    private static string DeriveTitle(string content, string fallback)
    {
        foreach (var raw in content.Split('\n').Take(12))
        {
            var line = raw.Trim();
            if (line.Length == 0) continue;
            if (line.StartsWith("# "))
            {
                var t = line.Substring(2).Trim();
                if (t.Length > 0) return Truncate(t, 40);
            }
            return fallback;
        }
        return fallback;
    }

    private static string Truncate(string s, int max) => s.Length <= max ? s : s.Substring(0, max);
}

/// <summary>「每周待办」正文骨架：按 ISO 周次与本周一~周日生成日期分组。</summary>
internal static class WeeklyTemplate
{
    // Lute 只把「[ ] 后面跟了实际文字」的行认作任务，空的 `- [ ] ` 会退化成普通列表项，
    // 因此用「·」作占位符：一个字符，删掉即可换成自己的事项。
    private const string Slot = "- [ ] ·";

    /// <summary>结算点：每周一 04:00。早于该时刻仍属上一周。</summary>
    public const int ResetHour = 4;

    /// <summary>返回 now 所属周期的起始时刻（本周一 04:00；若还没到点则回退一周）。</summary>
    public static DateTime CycleStart(DateTime now)
    {
        var monday = now.Date.AddDays(-(((int)now.DayOfWeek + 6) % 7));
        var anchor = monday.AddHours(ResetHour);
        return now < anchor ? anchor.AddDays(-7) : anchor;
    }

    /// <summary>周期说明行，结算时也会用它刷新旧文档。</summary>
    public static string Quote(DateTime cycleStart)
    {
        var ci = CultureInfo.InvariantCulture;
        int week = ISOWeek.GetWeekOfYear(cycleStart);
        return $"> 第 {week} 周 · {cycleStart.ToString("yyyy-MM-dd", ci)} — " +
               $"{cycleStart.AddDays(6).ToString("MM-dd", ci)} · 每周一 04:00 自动结算";
    }

    /// <summary>
    /// 模板不按周几分段——待办是一张连续的清单，周一的结算负责清理，
    /// 而不是让用户每天去不同的标题下面找地方写。
    /// </summary>
    public static string Build(DateTime today, string title)
    {
        var start = CycleStart(today);
        var sb = new StringBuilder();
        sb.Append("# ").Append(title).Append('\n').Append('\n');
        sb.Append(Quote(start)).Append('\n').Append('\n');
        sb.Append("## 本周").Append('\n').Append('\n');
        sb.Append(Slot).Append('\n');
        return sb.ToString();
    }
}

internal static class TimeExt
{
    public static long ToUnixMs(this DateTime dt) =>
        new DateTimeOffset(DateTime.SpecifyKind(dt, DateTimeKind.Utc)).ToUnixTimeMilliseconds();
}
