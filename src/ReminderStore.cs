using System.IO;
using System.Text;
using System.Text.Json;

namespace FloatNote;

/// <summary>一条提醒。noteId 指向来源便签，正文时间点用 Unix 毫秒表示。</summary>
public sealed class Reminder
{
    public string Id { get; set; } = "";
    public string NoteId { get; set; } = "";
    public string Text { get; set; } = "";
    public long DueAt { get; set; }
    public bool Fired { get; set; }
    public long Created { get; set; }
}

/// <summary>提醒持久化到 data/reminders.json，纯 JSON，便于手工查看与备份。</summary>
public sealed class ReminderStore
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
    private readonly List<Reminder> _items;

    public ReminderStore()
    {
        _items = Load();
        Prune();
    }

    public List<Reminder> Snapshot()
    {
        lock (_gate) return _items
            .OrderBy(r => r.Fired)
            .ThenBy(r => r.DueAt)
            .Select(Clone)
            .ToList();
    }

    public int PendingCount
    {
        get { lock (_gate) return _items.Count(r => !r.Fired); }
    }

    private static Reminder Clone(Reminder r) => new()
    {
        Id = r.Id,
        NoteId = r.NoteId,
        Text = r.Text,
        DueAt = r.DueAt,
        Fired = r.Fired,
        Created = r.Created
    };

    private List<Reminder> Load()
    {
        try
        {
            if (File.Exists(AppPaths.RemindersFile))
            {
                var json = File.ReadAllText(AppPaths.RemindersFile, Utf8NoBom);
                var list = JsonSerializer.Deserialize<List<Reminder>>(json, JsonOpts);
                if (list is not null) return list;
            }
        }
        catch { }
        return new List<Reminder>();
    }

    public Reminder Add(string noteId, string text, long dueAt)
    {
        lock (_gate)
        {
            var r = new Reminder
            {
                Id = Guid.NewGuid().ToString("n")[..10],
                NoteId = noteId,
                Text = string.IsNullOrWhiteSpace(text) ? "提醒" : text.Trim(),
                DueAt = dueAt,
                Fired = false,
                Created = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
            };
            _items.Add(r);
            Persist();
            return r;
        }
    }

    public void Remove(string id)
    {
        lock (_gate)
        {
            _items.RemoveAll(r => r.Id == id);
            Persist();
        }
    }

    public void RemoveByNote(string noteId)
    {
        lock (_gate)
        {
            _items.RemoveAll(r => r.NoteId == noteId);
            Persist();
        }
    }

    /// <summary>取出所有已到点且未触发过的提醒，并标记为已触发。</summary>
    public List<Reminder> TakeDue(long nowMs)
    {
        lock (_gate)
        {
            var due = _items.Where(r => !r.Fired && r.DueAt <= nowMs).ToList();
            if (due.Count == 0) return due;

            foreach (var r in due) r.Fired = true;
            Persist();
            return due.Select(Clone).ToList();
        }
    }

    /// <summary>清理 30 天前的已触发记录，避免文件无限增长。</summary>
    public void Prune()
    {
        lock (_gate)
        {
            long cutoff = DateTimeOffset.UtcNow.AddDays(-30).ToUnixTimeMilliseconds();
            int removed = _items.RemoveAll(r => r.Fired && r.DueAt < cutoff);
            if (removed > 0) Persist();
        }
    }

    private void Persist()
    {
        try
        {
            Directory.CreateDirectory(AppPaths.DataDir);
            var json = JsonSerializer.Serialize(_items, JsonOpts);
            var tmp = AppPaths.RemindersFile + ".tmp";
            File.WriteAllText(tmp, json, Utf8NoBom);
            File.Move(tmp, AppPaths.RemindersFile, true);
        }
        catch { }
    }
}
