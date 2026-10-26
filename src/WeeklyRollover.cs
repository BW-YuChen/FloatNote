using System.Text;
using System.Text.RegularExpressions;

namespace FloatNote;

/// <summary>
/// 每周结算（周一 04:00）：删除已完成的任务，把未完成的搬进「上周未完成」区块。
///
/// 为什么用「搬区块」而不是在行里塞标记：Lute 会丢弃零宽字符、把内联 HTML 转成
/// 可见的标记文本，两种不可见标记都活不下来。而「是否在上周未完成区」这个位置信息
/// 是纯 Markdown 结构，天然持久、且前端能据此把整块染红。
/// </summary>
internal static class WeeklyRollover
{
    public const string CarryHeading = "上周未完成";

    /// <summary>`- [ ] 文本` / `* [x] 文本`，允许缩进与不同项目符号。</summary>
    private static readonly Regex TaskLine =
        new(@"^(\s*)([-*+])\s+\[([ xX])\]\s?(.*)$", RegexOptions.Compiled);

    private static readonly Regex Heading2 =
        new(@"^##\s+(.+?)\s*$", RegexOptions.Compiled);

    private static readonly Regex QuoteLine =
        new(@"^>\s*第\s*\d+\s*周", RegexOptions.Compiled);

    private sealed class Block
    {
        public string? Heading;          // null 表示首个 ## 之前的内容
        public List<string> Lines = new();
        public bool IsCarry =>
            Heading != null && Heading.Replace(" ", "").Contains(CarryHeading);
        public bool IsCurrent => Heading != null && IsTaskContainer(Heading);
    }

    /// <summary>
    /// 判断一节是不是「待办容器」。
    /// 新模板只有「本周」，但老模板是「本周重点 / 周一 10-26 / 周日 / 下周预留」这一套，
    /// 结算时也要一并收拢，否则里面的任务会被当成自定义内容留在原地、永远不清。
    /// </summary>
    private static bool IsTaskContainer(string heading)
    {
        var s = heading.Replace(" ", "").Replace("　", "");
        if (s is "本周" or "本周待办" or "本周重点" or "下周预留" or "本周计划") return true;
        // 「周一」「周一10-26」「周日11-01」这类老模板分段，允许后面跟日期后缀
        if (Regex.IsMatch(s, @"^周[一二三四五六日天]")) return true;
        if (Regex.IsMatch(s, @"^星期[一二三四五六日天]")) return true;
        if (Regex.IsMatch(s, @"^\d{1,2}[-/.月]\d{1,2}[日号]?$")) return true;
        return false;
    }

    /// <summary>执行一次结算。返回新正文；第二项表示是否真的有改动。</summary>
    public static (string Text, bool Changed) Apply(string markdown, string quoteLine)
    {
        if (string.IsNullOrEmpty(markdown)) return (markdown, false);

        var raw = markdown.Replace("\r\n", "\n").Replace('\r', '\n');
        var blocks = SplitBlocks(raw);

        // 整篇没有任何容器标题时（纯标题 + 任务），把首个无标题块当成本周容器，
        // 否则任务会一直留在原地、永远不清算
        bool hasContainer = blocks.Any(b => b.IsCurrent || b.IsCarry);

        // 未完成的任务统一收集到这里（保持 original order）
        var carryOut = new List<string>();
        foreach (var b in blocks)
        {
            if (!b.IsCarry) continue;
            var kept = ExtractTasks(b.Lines, keepDone: false).Carry;
            carryOut.AddRange(kept);
            b.Lines = new List<string>();   // 稍后整体重写
        }

        foreach (var b in blocks)
        {
            if (b.IsCarry) continue;
            var r = ExtractTasks(b.Lines, keepDone: false);
            bool container = b.IsCurrent || (!hasContainer && b.Heading == null);
            if (container)
            {
                b.Lines = r.Rest;
                carryOut.AddRange(r.Carry);
            }
            else
            {
                // 自定义区块：只清掉已完成任务，未完成的留在原地，不搬动
                b.Lines = Merge(r.Rest, r.Kept);
            }
        }
        // 老的七段模板会留下成堆的空标题，顺手清掉（"本周" 与 "上周未完成" 除外）
        blocks.RemoveAll(b => b.Heading != null && !b.IsCurrent &&
                              b.Lines.All(l => string.IsNullOrWhiteSpace(l)) &&
                              !b.Heading.Replace(" ", "").Contains("长期"));

        var sb = new StringBuilder();
        foreach (var b in blocks)
        {
            if (b.Heading != null)
            {
                sb.Append("## ").Append(b.Heading).Append('\n');
                if (b.Lines.Count > 0) sb.Append('\n');
            }
            foreach (var l in b.Lines) sb.Append(l).Append('\n');
            if (b.Heading != null) sb.Append('\n');
        }

        if (carryOut.Count > 0)
        {
            sb.Append("## ").Append(CarryHeading).Append('\n').Append('\n');
            foreach (var l in carryOut) sb.Append(l).Append('\n');
        }

        var text = Normalize(sb.ToString(), quoteLine);
        return (text, text != raw);
    }

    /// <summary>把文档切成「头部 + 每个 ## 一节」。</summary>
    private static List<Block> SplitBlocks(string text)
    {
        var list = new List<Block>();
        var cur = new Block();
        foreach (var line in text.Split('\n'))
        {
            var m = Heading2.Match(line);
            if (m.Success)
            {
                list.Add(cur);
                cur = new Block { Heading = m.Groups[1].Value.Trim() };
                continue;
            }
            cur.Lines.Add(line);
        }
        list.Add(cur);
        return list;
    }

    private sealed class TaskSplit
    {
        public List<string> Rest = new();    // 非任务行
        public List<string> Carry = new();   // 未完成的任务（含其缩进子树）
        public List<string> Kept = new();    // 保留在原地的任务（已完成，仅自定义区块用）
    }

    /// <summary>
    /// 拆出任务块。已完成整块丢弃；未完成整块进 Carry。
    /// 任务块 = 任务行本身 + 其后所有缩进更深的行（子任务、续行）。
    /// </summary>
    private static TaskSplit ExtractTasks(List<string> lines, bool keepDone)
    {
        var r = new TaskSplit();
        int i = 0;
        while (i < lines.Count)
        {
            var line = lines[i];
            var m = TaskLine.Match(line);
            if (!m.Success)
            {
                r.Rest.Add(line);
                i++;
                continue;
            }

            int indent = m.Groups[1].Value.Length;
            bool done = m.Groups[3].Value is "x" or "X";

            var group = new List<string> { line };
            int j = i + 1;
            while (j < lines.Count)
            {
                var nxt = lines[j];
                if (string.IsNullOrWhiteSpace(nxt)) { group.Add(nxt); j++; continue; }
                int nind = nxt.Length - nxt.TrimStart().Length;
                if (nind > indent) { group.Add(nxt); j++; continue; }
                break;
            }
            // 去掉尾部多余空行，保持紧凑
            while (group.Count > 1 && string.IsNullOrWhiteSpace(group[^1])) group.RemoveAt(group.Count - 1);

            if (done)
            {
                if (keepDone) r.Kept.AddRange(group);
            }
            else
            {
                r.Carry.AddRange(group);
                r.Carry.Add("");
            }
            i = j;
        }
        return r;
    }

    /// <summary>把保留的任务按原始出现顺序还原（自定义区块用）。</summary>
    private static List<string> Merge(List<string> rest, List<string> kept)
    {
        if (kept.Count == 0) return rest;
        var outLines = new List<string>(rest);
        outLines.AddRange(kept);
        return outLines;
    }

    /// <summary>重写引用行里的周次，并收拾多余空行。</summary>
    private static string Normalize(string text, string quoteLine)
    {
        var lines = text.Replace("\r\n", "\n").Split('\n').ToList();

        int qi = lines.FindIndex(l => QuoteLine.IsMatch(l));
        if (qi >= 0 && !string.IsNullOrWhiteSpace(quoteLine)) lines[qi] = quoteLine;
        else if (qi < 0 && !string.IsNullOrWhiteSpace(quoteLine))
        {
            int hi = lines.FindIndex(l => l.StartsWith("# "));
            lines.Insert(hi >= 0 ? hi + 1 : 0, "");
            lines.Insert(hi >= 0 ? hi + 2 : 1, quoteLine);
        }

        // 连续空行压成一行
        var outp = new List<string>();
        foreach (var l in lines)
        {
            if (string.IsNullOrWhiteSpace(l) && outp.Count > 0 && string.IsNullOrWhiteSpace(outp[^1])) continue;
            outp.Add(l);
        }
        while (outp.Count > 0 && string.IsNullOrWhiteSpace(outp[^1])) outp.RemoveAt(outp.Count - 1);
        return string.Join("\n", outp) + "\n";
    }
}
