#!/usr/bin/env python3
# 日本語版: Jake Schincariol による MIT ライセンスの原作 (ig-human/humanize.py) を日本語の文章向けに書き直したものです。
"""
humanize.py - 下書きから「機械が書いた跡」を取り除きます。

この順に4回なぞります。

  1. 見えない文字   ゼロ幅スペース、ソフトハイフン、BOM、タグ文字などを消す。
                    特殊なスペースは普通のスペースにする（全角スペースは残す）。
  2. Markdown      **太字**、# 見出し、- 箇条書き、`コード`、[文字](URL)、> 引用、
                    --- 区切り線の記号を外す。#ハッシュタグ はそのまま。
  3. 記号          ダッシュ（— ― –）を文脈に合わせて「、」「。」（ ）に変える。
                    “ ” は「 」に。全角と半角が混ざっていたら、多いほうにそろえる。
  4. 定型句        slop.json の置き換えを当てる。意味が変わりやすいもの（replace が null）は
                    数えるだけで触らない。URL の中は触らない。

文の型（「AだけでなくBも」「ポイントは3つ」など）は報告するだけで、書き換えません。
文の形を変えるには判断がいるので、そこは人かモデルの仕事です。

使い方
  python humanize.py draft.txt
  python humanize.py draft.txt --report
  type draft.txt | python humanize.py - --report
  python humanize.py draft.txt --json
  python humanize.py draft.txt -o clean.txt
"""

import argparse
import json
import os
import re
import sys
import unicodedata

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
LEX = os.path.join(HERE, "slop.json")

URL_RE = re.compile(r"https?://[^\s「」『』（）()<>、。]+|www\.[^\s「」『』（）()<>、。]+|[\w.+-]+@[\w-]+\.[\w.]+")
JA = re.compile(r"[ぁ-ヿ一-鿿々]")


def load_lexicon(path=LEX):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _cp(spec):
    if "-" in spec:
        a, b = spec.split("-")
        return (int(a[2:], 16), int(b[2:], 16))
    return int(spec[2:], 16)


def protect_urls(text):
    found = []

    def stash(m):
        found.append(m.group(0))
        return f"\x00URL{len(found) - 1}\x00"

    return URL_RE.sub(stash, text), found


def restore_urls(text, found):
    for i, url in enumerate(found):
        text = text.replace(f"\x00URL{i}\x00", url)
    return text


def pass_invisible(text, lex):
    hits = []
    for entry in lex["invisible"]:
        cp = _cp(entry["cp"])
        if isinstance(cp, tuple):
            pattern = "[" + re.escape(chr(cp[0])) + "-" + re.escape(chr(cp[1])) + "]"
        else:
            pattern = re.escape(chr(cp))
        n = len(re.findall(pattern, text))
        if n:
            hits.append({"name": entry["cp"] + " " + entry["name"], "count": n,
                         "action": entry["action"]})
            text = re.sub(pattern, "" if entry["action"] == "delete" else " ", text)
    stray = [c for c in text if unicodedata.category(c) == "Cf" and c != "\x00"]
    if stray:
        hits.append({"name": "その他の見えない書式文字", "count": len(stray), "action": "delete"})
        text = "".join(c for c in text if unicodedata.category(c) != "Cf" or c == "\x00")
    return text, hits


def pass_markdown(text):
    hits = []

    def sub(pattern, repl, name, flags=0):
        nonlocal text
        new, n = re.subn(pattern, repl, text, flags=flags)
        if n:
            hits.append({"name": name, "count": n})
            text = new

    sub(r"\[([^\]\n]+)\]\((\x00URL\d+\x00|[^)\s]+)\)", r"\1 \2", "リンク [文字](URL)")
    sub(r"\*\*(.+?)\*\*", r"\1", "太字 **", re.S)
    sub(r"__(.+?)__", r"\1", "太字 __", re.S)
    sub(r"\*\*|__", "", "残った ** / __")
    sub(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", r"\1", "見出し #")
    sub(r"(?m)^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$", "", "区切り線 ---")
    sub(r"(?m)^[ \t]*[-*+][ \t]+", "・", "箇条書き - / *")
    sub(r"(?m)^[ \t]*>[ \t]?", "", "引用 >")
    sub(r"`([^`\n]+)`", r"\1", "コード `")
    return text, hits


SENT_END = r"(?:です|ます|でした|ました|ません|でしょう|だ|よね|よ|ね)"


def pass_typographic(text, lex):
    hits = []
    dash_chars = "—―–"
    n_dash = len(re.findall("[" + dash_chars + "]", text))
    if n_dash:
        D = "[" + dash_chars + "]+"
        # 数字の範囲 5–10 → 5〜10
        text = re.sub(r"(\d)\s*" + D + r"\s*(?=\d)", r"\1〜", text)
        # 行頭・行末のダッシュは消す
        text = re.sub(r"(?m)^[ \t　]*" + D + r"[ \t　]*", "", text)
        text = re.sub(r"(?m)[ \t　]*" + D + r"[ \t　]*$", "", text)
        # 句読点の隣のダッシュは消す
        text = re.sub(r"([。、！？!?」』）])[ \t　]*" + D + r"[ \t　]*", r"\1", text)
        text = re.sub(r"[ \t　]*" + D + r"[ \t　]*(?=[。、！？!?])", "", text)
        # 同じ行の A—B—C（短い挟み込み）は A（B）C
        text = re.sub(r"[ \t　]*" + D + r"[ \t　]*([^\n。！？" + dash_chars + r"]{1,25}?)[ \t　]*" + D
                      + r"[ \t　]*", r"（\1）", text)
        # 言い切りのあとなら「。」、それ以外は「、」
        text = re.sub(r"(" + SENT_END + r")[ \t　]*" + D + r"[ \t　]*", r"\1。", text)
        text = re.sub(r"[ \t　]*" + D + r"[ \t　]*", "、", text)
        hits.append({"name": "ダッシュ（— ― –）", "count": n_dash, "to": "、 / 。 / （ ） / 〜"})

    # “ ” ‘ ’
    def quote(m):
        inner = m.group(2)
        return f"「{inner}」" if JA.search(inner) else f"\"{inner}\""
    n_q = sum(text.count(c) for c in "“”")
    if n_q:
        text = re.sub(r"(“)([^“”\n]*)(”)", quote, text)
        text = text.replace("“", "「").replace("”", "」")
        hits.append({"name": "曲がった二重引用符 “ ”", "count": n_q, "to": "「 」"})
    n_s = sum(text.count(c) for c in "‘’")
    if n_s:
        text = re.sub(r"(?<=[A-Za-z])’(?=[A-Za-z])", "'", text)
        text = re.sub(r"(‘)([^‘’\n]*)(’)", lambda m: f"「{m.group(2)}」" if JA.search(m.group(2))
                      else f"'{m.group(2)}'", text)
        text = text.replace("‘", "'").replace("’", "'")
        hits.append({"name": "曲がった一重引用符 ‘ ’", "count": n_s, "to": "' か「 」"})
    for entry in lex["typographic"]:
        ch = entry["from"]
        if ch in dash_chars or ch in "“”‘’":
            continue
        n = text.count(ch)
        if n:
            text = text.replace(ch, entry["to"])
            hits.append({"name": f"{ch} {entry['name']}", "count": n, "to": entry["to"]})

    # 全角・半角が混ざっていたら多いほうにそろえる
    def unify(half, full, label):
        nonlocal text
        h, f = len(re.findall(half, text)), len(re.findall(full, text))
        if h and f:
            if f >= h:
                text = re.sub(half, lambda m: chr(ord(m.group(0)) + 0xFEE0), text)
                hits.append({"name": f"全角半角の混在（{label}）", "count": h, "to": "全角にそろえた"})
            else:
                text = re.sub(full, lambda m: chr(ord(m.group(0)) - 0xFEE0), text)
                hits.append({"name": f"全角半角の混在（{label}）", "count": f, "to": "半角にそろえた"})
    unify(r"[0-9]", r"[０-９]", "数字")
    unify(r"(?<![!?])!(?![!?])", r"！", "！")
    unify(r"(?<![!?])\?(?![!?])", r"？", "？")
    return text, hits


def _lex_entries(lex):
    return sorted(lex["phrases"] + lex["words"], key=lambda e: len(e["find"]), reverse=True)


def pass_lexical(text, lex):
    """置き換えてよいものだけ置き換える。長い順に当てる。"""
    hits, flagged = [], []
    for entry in _lex_entries(lex):
        pat = re.compile(entry["find"] if entry.get("regex") else re.escape(entry["find"]))
        found = pat.findall(text)
        if not found:
            continue
        if entry["replace"] is None:
            flagged.append({"find": entry["find"], "count": len(found), "family": entry["family"],
                            "note": entry.get("note", "")})
            continue
        hits.append({"find": entry["find"], "replace": entry["replace"] or "（削除）",
                     "count": len(found), "family": entry["family"]})
        text = pat.sub(lambda m: entry["replace"], text)
    # 削除のあとに残る句読点の後始末
    text = re.sub(r"、{2,}", "、", text)
    text = re.sub(r"([。！？!?])、", r"\1", text)
    text = re.sub(r"、([。！？!?])", r"\1", text)
    text = re.sub(r"(?m)^[ \t　]*[、，,]+", "", text)
    text = re.sub(r"(?m)^[ \t　]*[。！？]+[ \t　]*$", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"(?m)^[ \t]+$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text, hits, flagged


def scan_structures(text, lex):
    flags = []
    for s in lex["structures"]:
        try:
            pattern = re.compile(s["regex"], re.MULTILINE)
        except re.error:
            continue
        found = pattern.findall(text)
        if found:
            flags.append({"name": s["name"], "count": len(found), "fix": s["fix"]})
    # 文の長さがそろいすぎていないか
    sents = [p.strip() for p in re.split(r"(?<=[。！？!?])|\n", text) if len(JA.findall(p)) >= 4]
    lens = [len(re.sub(r"\s", "", p)) for p in sents]
    if len(lens) >= 4:
        mean = sum(lens) / len(lens)
        cv = (sum((n - mean) ** 2 for n in lens) / len(lens)) ** 0.5 / mean if mean else 0
        if cv < 0.35:
            flags.append({"name": f"文の長さがそろいすぎ（ばらつき {cv:.2f}）", "count": len(lens),
                          "fix": "1文を半分に切る。別の1文は長いままにする。AIは長さをそろえがち。"})
    return flags


def humanize(text, lex):
    text, urls = protect_urls(text)
    text, inv = pass_invisible(text, lex)
    text, md = pass_markdown(text)
    text, typo = pass_typographic(text, lex)
    text, lexi, flagged = pass_lexical(text, lex)
    text = restore_urls(text, urls)
    return text.strip() + "\n", {
        "invisible": inv,
        "markdown": md,
        "typographic": typo,
        "lexical": lexi,
        "flagged_terms": flagged,
        "structures": scan_structures(text, lex),
    }


def render_report(report, out=sys.stderr):
    def head(title):
        print(f"\n{title}\n" + "-" * 40, file=out)

    total = sum(h["count"] for k in ("invisible", "markdown", "typographic", "lexical")
                for h in report[k])
    head("AIっぽさ消し レポート")
    print(f"自動で直した箇所 {total}、手で直す候補 "
          f"{len(report['flagged_terms']) + len(report['structures'])}", file=out)

    if report["invisible"]:
        head("1. 見えない文字")
        for h in report["invisible"]:
            print(f"  {h['count']:>3}x  {h['name']}  -> {h['action']}", file=out)
    if report["markdown"]:
        head("2. Markdown記号")
        for h in report["markdown"]:
            print(f"  {h['count']:>3}x  {h['name']}  -> 外した", file=out)
    if report["typographic"]:
        head("3. 記号")
        for h in report["typographic"]:
            print(f"  {h['count']:>3}x  {h['name']}  -> {h['to']}", file=out)
    if report["lexical"]:
        head("4. 定型句（置き換えた）")
        for h in report["lexical"]:
            print(f"  {h['count']:>3}x  {h['find']}  -> {h['replace']}   [{h['family']}]", file=out)
    if report["flagged_terms"]:
        head("5. 定型句（自動では直していない。読んで判断）")
        for h in report["flagged_terms"]:
            note = f"  … {h['note']}" if h["note"] else ""
            print(f"  {h['count']:>3}x  {h['find']}   [{h['family']}]{note}", file=out)
    if report["structures"]:
        head("6. 文の型（自動では直していない。自分で書き直す）")
        for h in report["structures"]:
            print(f"  {h['count']:>3}x  {h['name']}\n        {h['fix']}", file=out)
    if not any(report.values()):
        head("きれいです")
        print("  消すものはありませんでした。", file=out)
    print("", file=out)


def main():
    ap = argparse.ArgumentParser(description="下書きから機械が書いた跡を取り除きます。")
    ap.add_argument("input", nargs="?", default="-", help="ファイル。- なら標準入力")
    ap.add_argument("-o", "--out", help="直した文章をこのファイルに書く")
    ap.add_argument("--report", action="store_true", help="何を変えたかを stderr に出す")
    ap.add_argument("--json", action="store_true", help="{text, report} を JSON で出す")
    ap.add_argument("--lexicon", default=LEX, help="slop.json の場所")
    args = ap.parse_args()

    if args.input == "-":
        try:
            sys.stdin.reconfigure(encoding="utf-8")
        except Exception:
            pass
        raw = sys.stdin.read()
    else:
        with open(args.input, encoding="utf-8-sig") as fh:
            raw = fh.read()
    lex = load_lexicon(args.lexicon)
    clean, report = humanize(raw, lex)

    if args.json:
        print(json.dumps({"text": clean, "report": report}, indent=2, ensure_ascii=False))
        return
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(clean)
        print(f"書き出しました: {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(clean)
    if args.report:
        render_report(report)


if __name__ == "__main__":
    main()
