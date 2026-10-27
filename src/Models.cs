using System.Text.Json.Serialization;

namespace FloatNote;

/// <summary>层级档位。desk = 贴桌面（最底），normal = 普通，top = 置顶。</summary>
public enum WindowLevel
{
    Desk,
    Normal,
    Top
}

/// <summary>一条便签的元数据；正文单独存为 data/notes/&lt;id&gt;.md。</summary>
public sealed class NoteMeta
{
    public string Id { get; set; } = "";
    public string Title { get; set; } = "";

    /// <summary>侧栏置顶（钉在列表最上方）。</summary>
    public bool Pin { get; set; }

    /// <summary>分类标记，两种取值形态："emoji:📚" 或 "color:#e0685c"；空串表示未分类。</summary>
    public string Mark { get; set; } = "";

    /// <summary>便签类型：空 = 普通，weekly = 每周待办（含周次与日分组）。</summary>
    public string Kind { get; set; } = "";

    public long Created { get; set; }
    public long Updated { get; set; }
}

/// <summary>便签索引，持久化到 data/index.json。</summary>
public sealed class NoteIndex
{
    public List<NoteMeta> Notes { get; set; } = new();
    public string? ActiveId { get; set; }
}

/// <summary>用户设置，持久化到 data/settings.json。</summary>
public sealed class AppSettings
{
    public double X { get; set; } = double.NaN;
    public double Y { get; set; } = double.NaN;
    public double Width { get; set; } = 720;
    public double Height { get; set; } = 500;
    public WindowLevel Level { get; set; } = WindowLevel.Top;

    /// <summary>固定置顶：层级锁定在置顶，并周期性校验，避免被其它窗口压下去。</summary>
    public bool FixedTop { get; set; } = true;

    public string Theme { get; set; } = "system"; // light | dark | system
    public int FontSize { get; set; } = 14;
    public bool StartWithWindows { get; set; }

    /// <summary>启动后不显示窗口，直接驻留托盘（通常与开机自启配合使用）。</summary>
    public bool StartMinimized { get; set; }

    public bool SidebarVisible { get; set; } = true;

    /// <summary>编辑器自动高亮开关（日期 / 时间 / 地点）。</summary>
    public bool AutoHighlight { get; set; } = true;

    /// <summary>关键词自动链接提示开关。</summary>
    public bool AutoLink { get; set; } = true;

    /// <summary>每周一 04:00 自动结算的开关。关闭后只能从托盘或设置页手动结算。</summary>
    public bool WeeklyAutoReset { get; set; } = true;

    /// <summary>
    /// 上一次每周结算对应的「周期起始日」（yyyy-MM-dd）。
    /// 结算点是每周一 04:00，早于该时刻仍算上一周。空串表示从未结算过。
    /// </summary>
    public string LastWeeklyReset { get; set; } = "";
}

/// <summary>发给前端的便签负载（含正文）。</summary>
public sealed class NotePayload
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("title")] public string Title { get; set; } = "";
    [JsonPropertyName("content")] public string Content { get; set; } = "";
    [JsonPropertyName("updated")] public long Updated { get; set; }
    [JsonPropertyName("pin")] public bool Pin { get; set; }
    [JsonPropertyName("mark")] public string Mark { get; set; } = "";
    [JsonPropertyName("kind")] public string Kind { get; set; } = "";
}
