#!/usr/bin/env python3
# 日本語版: Jake Schincariol 氏の MIT ライセンス原版 (Copyright (c) 2026 Jake Schincariol) を日本語向けに改変したものです。
"""
swipe.py - 集めたリールを「そのアカウントのいつもの再生数の何倍か」で並べ、
それぞれのフックの型に名前を付けて、スワイプファイル（ネタ帳）を書き出します。

このスクリプトの目的は1つの思い込みを正すことです。再生数そのものは証拠に
なりません。フォロワー200万人のアカウントで40万再生なら、静かな1日です。
フォロワー4,000人のアカウントで40万再生なら、何かを見つけています。
だから「そのアカウント自身の基準の何倍か」で並べます。これが、まねできる
「バズった」を見分ける唯一の物差しです。

入力は、見て回りながら埋めるタブ区切り（TSV）か CSV のファイルです。
1行に1本、先頭の行に列名を書きます。英語でも日本語でも使えます。

    アカウント   フォロワー   中央値   再生数   フック
    @someone     4.8万        1.1万    41.2万   最初の30本は伸びなくて当たり前です

  列名の対応（どれでも可）
    account   = アカウント / 垢 / ハンドル
    followers = フォロワー / フォロワー数
    median    = 中央値 / いつもの再生数 / 平均再生数
    views     = 再生数 / 再生 / 再生回数
    hook      = フック / 冒頭 / 最初の一言 / 一言目

  数字は次の書き方を受け付けます
    3400 / 3,400 / ３，４００ / 1.2万 / 12万3千 / 1億 / 12K / 1.5M / 約2万回

`median` はそのアカウントの最近のいつもの再生数で、基準としてはこちらが確かです。
`followers` しかない場合は median を空にすれば、その旨を表示します。
`hook` はリールの最初の一言（話した言葉か画面の文字）を、本人の言葉のまま書きます。

使い方
  python swipe.py captured.tsv
  python swipe.py captured.tsv --out ~/.claude/instagram/swipe.md
  python swipe.py captured.csv --json
"""

import argparse
import csv
import io
import json
import os
import re
import statistics
import sys
import unicodedata

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, "..", "ig-reel", "hooks.json")
WORD_RE = re.compile(r"[A-Za-z0-9$%'’-]+")
CJK_RE = re.compile(r"[ぁ-んァ-ヶー一-龥々]")

try:                                              # 任意: フックの点数も付ける
    sys.path.insert(0, os.path.join(HERE, "..", "ig-reel"))
    from hookscore import run as score_hook       # noqa: E402
except Exception:                                 # ig-viral だけコピーした場合
    score_hook = None

COLUMN_ALIASES = {
    "account": ["account", "handle", "アカウント", "アカウント名", "垢", "ハンドル", "ユーザー名"],
    "followers": ["followers", "フォロワー", "フォロワー数"],
    "median": ["median", "中央値", "いつもの再生数", "平均再生数", "平均再生", "普段の再生数"],
    "views": ["views", "再生数", "再生", "再生回数", "視聴回数"],
    "hook": ["hook", "フック", "冒頭", "最初の一言", "一言目", "冒頭の一言"],
}
ALIAS = {unicodedata.normalize("NFKC", a).lower(): k for k, v in COLUMN_ALIASES.items() for a in v}

UNITS = {"億": 100_000_000, "万": 10_000, "千": 1_000, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(億|万|千|k|m|b)?")


def parse_count(s):
    """日本語・英語の数字表記を整数にします。読めなければ None。

    例: "1.2万"→12000, "12万3千"→123000, "3,400"→3400, "１２K"→12000, "約2万回"→20000
    """
    if s is None:
        return None
    t = unicodedata.normalize("NFKC", str(s)).lower()   # 全角数字・全角カンマを半角に
    t = re.sub(r"[,\s]", "", t)
    t = re.sub(r"^(約|およそ|~|〜)", "", t)
    t = re.sub(r"(回|人|再生|views?|followers?|以上|くらい|程度|件)$", "", t)
    if not t:
        return None
    total, matched = 0.0, ""
    for m in NUM_RE.finditer(t):
        num, unit = float(m.group(1)), m.group(2)
        total += num * (UNITS[unit] if unit else 1)
        matched += m.group(0)
    if matched != t:                 # 数字と単位以外の文字が残っていたら読まない
        return None
    return int(round(total))


def load_formulas(path):
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
    by_id = {h["id"]: h for h in d["hooks"]}
    order = d.get("classify_order") or sorted(by_id)
    out = []
    for i in order:
        if i not in by_id or not by_id[i].get("match"):
            continue
        try:
            out.append((by_id[i]["id"], by_id[i]["name"], re.compile(by_id[i]["match"], re.IGNORECASE)))
        except re.error:
            continue
    return out


def classify(hook, formulas):
    if not formulas:
        return None, "未分類"
    for fid, name, pattern in formulas:
        if pattern.search(hook):
            return fid, name
    return None, "未分類"


def hook_length(hook):
    """日本語のフックは文字数、英語のフックは単語数で数えます。"""
    if CJK_RE.search(hook):
        return len(re.sub(r"[\s、。！？!?「」『』…・,.]", "", hook)), "字"
    return len(WORD_RE.findall(hook)), "語"


def read_rows(path):
    if path == "-":
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")
        raw = sys.stdin.read()
    else:
        raw = open(os.path.expanduser(path), encoding="utf-8-sig").read()
    lines = [l for l in raw.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    if not lines:
        return []
    delim = "\t" if "\t" in lines[0] else ","
    table = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))
    head = [ALIAS.get(unicodedata.normalize("NFKC", c).strip().lower(), c.strip().lower())
            for c in table[0]]
    if "views" in head and "hook" in head:
        cols, body = head, table[1:]
    else:
        cols, body = ["account", "followers", "views", "hook"], table
    rows = []
    for cells in body:
        if len(cells) < len(cols):
            cells += [""] * (len(cols) - len(cells))
        r = dict(zip(cols, [c.strip() for c in cells]))
        r["views"] = parse_count(r.get("views", "")) or 0
        for k in ("followers", "median"):
            r[k] = parse_count(r.get(k, "")) or None
        if r["views"] and r.get("hook"):
            rows.append(r)
        elif any(c.strip() for c in cells):
            print(f"読めない行を飛ばしました（再生数かフックが空、または数字が読めない）: {', '.join(cells)[:60]}",
                  file=sys.stderr)
    return rows


def analyse(rows, formulas):
    used_median = any(r.get("median") for r in rows)
    for r in rows:
        base = r.get("median") or r.get("followers") or 0
        r["baseline"] = base
        r["outlier"] = round(r["views"] / base, 2) if base else None
        r["formula_id"], r["formula"] = classify(r["hook"], formulas)
        r["words"], r["length_unit"] = hook_length(r["hook"])
        r["hook_score"], r["hook_verdict"] = None, None
        if score_hook:
            try:
                _, overall, verdict, _ = score_hook(r["hook"])
                r["hook_score"], r["hook_verdict"] = round(overall, 1), verdict
            except Exception:
                pass
    ranked = sorted(rows, key=lambda r: -(r["outlier"] or 0))
    third = max(1, len(ranked) // 3)
    top, bottom = ranked[:third], ranked[-third:]

    def med(items, key):
        vals = [i[key] for i in items if i.get(key) is not None]
        return round(statistics.median(vals), 1) if vals else None

    counts = {}
    for r in top:
        counts[r["formula"]] = counts.get(r["formula"], 0) + 1
    units = [r["length_unit"] for r in ranked]
    return {
        "baseline": "アカウントの中央値" if used_median else "フォロワー数",
        "n": len(ranked),
        "accounts": len({r.get("account", "") for r in ranked}),
        "reels": ranked,
        "top_formulas": sorted(counts.items(), key=lambda kv: -kv[1]),
        "top_hook_score": med(top, "hook_score"),
        "bottom_hook_score": med(bottom, "hook_score"),
        "top_words": med(top, "words"),
        "bottom_words": med(bottom, "words"),
        "length_unit": max(set(units), key=units.count) if units else "字",
        "mixed_languages": len(set(units)) > 1,
        "unclassified": sum(1 for r in ranked if r["formula"] == "未分類"),
    }


def width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "FWA" else 1 for c in s)


def fit(s, w):
    """表示幅 w に収まるよう切って、右を空白で埋めます。"""
    out = ""
    for c in s:
        if width(out + c) > w:
            break
        out += c
    return out + " " * (w - width(out))


def render(a, out=sys.stdout):
    head = (f"スワイプファイル  ·  リール {a['n']} 本  ·  {a['accounts']} アカウント  ·  "
            f"基準: {a['baseline']}")
    print("\n" + head, file=out)
    print("=" * 78, file=out)
    for r in a["reels"]:
        mult = f"{r['outlier']:.1f}倍" if r["outlier"] else "    ?"
        score = f"{r['hook_score']:.0f}" if r["hook_score"] is not None else " -"
        fid = f"#{r['formula_id']:<2}" if r["formula_id"] else "-  "
        print(f"  {mult:>7}  フック {score:>3}  {fid} {fit(r['formula'], 22)} "
              f"{fit(r.get('account', ''), 16)} {r['views']:>11,}", file=out)
        print(f"           「{r['hook'][:60]}」", file=out)
    print("-" * 78, file=out)
    print("この束で効いているもの", file=out)
    if a["top_formulas"]:
        print("  倍率の上位3分の1:   "
              + ", ".join(f"{n} ×{c}" for n, c in a["top_formulas"][:4]), file=out)
    if a["top_hook_score"] is not None and a["bottom_hook_score"] is not None:
        print(f"  フック点数の中央値: 上位 {a['top_hook_score']:.0f}  "
              f"下位 {a['bottom_hook_score']:.0f}", file=out)
    u = a["length_unit"]
    print(f"  フックの長さ中央値: 上位 {a['top_words']}{u}  下位 {a['bottom_words']}{u}"
          + ("（日本語は字、英語は語で数えているので混在時は参考程度）" if a["mixed_languages"] else ""),
          file=out)
    print(f"  未分類:             {a['n']} 本中 {a['unclassified']} 本。手で読んでください。"
          "まだ持っていない型が隠れているのはここです。", file=out)
    print("\n  手で集めた束は「証拠」であって「証明」ではありません。12本では何も言えません。\n"
          "  6アカウントで40本あれば、何かが言えます。信じる前にもっと集めましょう。\n", file=out)
    if a["baseline"] == "フォロワー数":
        print("  ※ 中央値がないのでフォロワー数で割っています。倍率は小さく出ます。"
              "できれば中央値を入れてください。\n", file=out)


def to_markdown(a):
    lines = ["# スワイプファイル", "",
             f"{a['accounts']} アカウントのリール {a['n']} 本。"
             f"{a['baseline']}に対する倍率の順です。", ""]
    for r in a["reels"]:
        mult = f"{r['outlier']:.1f}倍" if r["outlier"] else "?"
        lines += [f"## {mult}  {r['formula']}  ({r.get('account', '')})",
                  f"- 再生数: {r['views']:,}  基準: {r['baseline']:,}",
                  f"- フック点数: {r['hook_score']}  長さ: {r['words']}{r['length_unit']}",
                  f"- フック: 「{r['hook']}」", ""]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="集めたリールを、いつもの再生数の何倍かで並べます。")
    ap.add_argument("input", nargs="?", default="-", help="TSV か CSV のファイル。- なら標準入力")
    ap.add_argument("--hooks", default=HOOKS, help="ig-reel/hooks.json の場所")
    ap.add_argument("--out", help="スワイプファイルを Markdown でここにも書き出す")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = read_rows(args.input)
    if not rows:
        print("使える行がありません。少なくとも「再生数」と「フック」の列がある"
              "タブ区切りか CSV のファイルが必要です。", file=sys.stderr)
        sys.exit(2)
    formulas = load_formulas(args.hooks)
    a = analyse(rows, formulas)
    if not formulas:
        print("注意: hooks.json が見つからないので型の名前は付けていません。--hooks で指定できます。",
              file=sys.stderr)
    if score_hook is None:
        print("注意: hookscore.py を読み込めないので、フックの点数は省略しました。", file=sys.stderr)

    if args.json:
        print(json.dumps(a, indent=2, ensure_ascii=False))
    else:
        render(a)
    if args.out:
        path = os.path.expanduser(args.out)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        open(path, "w", encoding="utf-8").write(to_markdown(a))
        print(f"書き出しました: {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
