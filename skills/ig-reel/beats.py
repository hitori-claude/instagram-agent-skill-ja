#!/usr/bin/env python3
# MIT ライセンスの原作（Jake Schincariol「Instagram agent skills」）を日本語向けに作り直したものです。
"""
beats.py - 撮る前に、リールの台本を秒数つきのビート表にします。

各行を言うのにかかる時間を見積もり、タイムコードに積み上げます。
編集でリールをだめにする4つを指摘します。
3秒を過ぎるフック、見る人が離れるほど長いビート、具体的なものが何もない行の連続、
作るつもりの長さと合わない全体の尺、です。

秒数は、モーラ数（読み上げたときの拍の数）を話す速さで割った目安です。
編集の計画には十分ですが、実際に録った尺の代わりにはなりません。
モーラの数え方は hookscore.py の冒頭に書いてあります
（かな1字=1、漢字1字=1.8、数字・英字は別の目安）。

話す速さは毎秒7モーラ（毎分420モーラ）を基準にしています。
日本語のふつうの会話はおよそ毎秒6〜8モーラで、漢字かな混じりの文字数に直すと
毎秒5〜6文字くらいです。自分で台本を声に出して測ったら、--mpm で自分の速さにしてください。
ゆっくりな人は360前後、早口の人は480前後です。
この基準は経験則で、日本語のリールで検証した値ではありません。

使い方
  python beats.py script.txt
  python beats.py script.txt --target 30
  python beats.py script.txt --mpm 480 --target 45
  type script.txt | python beats.py -
  python beats.py script.txt --json

  --wpm は英語版との互換のために残しています。日本語版では「毎分のモーラ数」として読みます。
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hookscore import (mora, chars, normalize, find_numbers, find_latin,  # noqa: E402
                       find_money)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

SENT_RE = re.compile(r"[^。！？!?]+[。！？!?」』）)]*")
# 見出しになりうる語の塊。漢字2字以上、カタカナ2字以上、数字、英単語。
CONTENT_RE = re.compile(r"[一-鿿々]{2,}|[ァ-ヺー]{2,}|\d[\d,.]*|[A-Za-z]{2,}")
STOPWORDS = {"今日", "今回", "本当", "自分", "感じ", "時間", "場合", "必要", "大事", "一番",
             "ため", "こと", "もの", "ところ", "最近", "結構", "全部", "普通"}

HOOK_WINDOW = 3.0        # 秒。ここを過ぎると、指はもう決めています。
MAX_BEAT = 4.0           # 秒。画面が変わらないまま一つの話を続ける上限。
ABSTRACT_RUN = 3         # 確かめられるものがない行がこの数だけ続いたら指摘。
DEFAULT_MPM = 420        # 毎分のモーラ数（毎秒7モーラ）


def concrete_count(text):
    return len(find_numbers(text)) + len(find_latin(text)) + len(find_money(text))


def content_words(text):
    return {w for w in CONTENT_RE.findall(normalize(text)) if w not in STOPWORDS}


def tc(seconds):
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:04.1f}"


def split_beats(raw, mps):
    """1行が1ビート。ただし1ビートに収まらないほど長い行は分けます。"""
    beats = []
    for line in [l.strip().lstrip("﻿") for l in raw.splitlines()]:
        if not line:
            continue
        if mora(line) / mps <= MAX_BEAT * 1.5:
            beats.append(line)
            continue
        # 長い段落は文の終わりで分けます。秒数に意味を持たせるためです。
        parts = []
        for sent in [p.strip() for p in SENT_RE.findall(line) if p.strip()]:
            if mora(sent) / mps <= MAX_BEAT:
                parts.append(sent)
            else:
                # 日本語は1文が長くなりやすいので、読点でも分けます。
                parts.extend(p for p in re.findall(r"[^、，,]+[、，,]?", sent) if p.strip())
        buf = ""
        for part in parts:
            candidate = buf + part
            if buf and mora(candidate) / mps > MAX_BEAT:
                beats.append(buf)
                buf = part
            else:
                buf = candidate
        if buf:
            beats.append(buf)
    return beats


def analyse(raw, wpm=DEFAULT_MPM, target=None):
    """wpm は日本語版では「毎分のモーラ数」です（名前は英語版との互換のため）。"""
    mps = wpm / 60.0
    beats = split_beats(raw, mps)
    if not beats:
        return None

    rows, clock = [], 0.0
    for i, text in enumerate(beats):
        n = mora(text)
        dur = n / mps
        rows.append({
            "n": i + 1,
            "start": round(clock, 2),
            "dur": round(dur, 2),
            "words": round(n, 1),          # 日本語版ではモーラ数
            "chars": chars(text),
            "text": text,
            "concrete": concrete_count(text),
            "label": "",
            "flags": [],
        })
        clock += dur
    total = clock
    total_mora = sum(r["words"] for r in rows)
    total_chars = sum(r["chars"] for r in rows)
    cpm = total_chars / total_mora if total_mora else 0.75   # 1モーラあたりの文字数

    # リールの骨組みになる位置に印を付けます。
    for r in rows:
        if r["n"] == 1 or r["start"] + r["dur"] <= HOOK_WINDOW:
            r["label"] = "HOOK"
    rows[-1]["label"] = "CTA" if rows[-1]["label"] != "HOOK" else "HOOK/CTA"
    half = total / 2
    for r in rows:
        if not r["label"] and r["start"] <= half < r["start"] + r["dur"]:
            r["label"] = "MID"

    notes = []
    if rows[0]["dur"] > HOOK_WINDOW:
        limit = HOOK_WINDOW * mps
        rows[0]["flags"].append(f"フックが{rows[0]['dur']:.1f}秒。{HOOK_WINDOW:.0f}秒を過ぎています")
        notes.append(f"1行目は言うのに{rows[0]['dur']:.1f}秒かかります。"
                     f"{int(limit)}モーラ（約{int(limit * cpm)}文字）以内に削ってください。"
                     "でないと、見る人が決めたあとにフックが届きます。")
    if rows[0]["concrete"] == 0:
        notes.append("1行目に数字も名前もありません。確かめられるものがないフックは、"
                     "スクロールされやすいです。")

    for r in rows:
        if r["dur"] > MAX_BEAT:
            r["flags"].append(f"一つのビートに{r['dur']:.1f}秒")
    long_beats = [r["n"] for r in rows if r["dur"] > MAX_BEAT]
    if long_beats:
        notes.append(f"{', '.join(map(str, long_beats))}行目が{MAX_BEAT:.0f}秒を超えています。"
                     "行を分けるか、その途中で画面を変えてください。"
                     "動かない画面で人は離れます。")

    run, start = 0, None
    for r in rows:
        if r["concrete"] == 0:
            run += 1
            start = start if start is not None else r["n"]
            if run == ABSTRACT_RUN:
                notes.append(f"{start}〜{r['n']}行目に具体的なものがありません。"
                             "どれか一つに数字、名前、値段を入れてください。")
        else:
            run, start = 0, None

    # 最後の行が、最初の行に戻してくれるか。
    loop = sorted(content_words(rows[0]["text"]) & content_words(rows[-1]["text"]))
    if loop:
        notes.append(f"ループあり：最後の行がフックの「{'、'.join(loop[:3])}」を繰り返しています。"
                     "2回目の再生は、ただで手に入るリーチです。")
    else:
        notes.append("ループなし。最後の行がフックと同じ言葉を一つも使っていないので、"
                     "動画が平らに終わります。1行目の言葉を一つ繰り返すのが、一番安い再再生です。")

    if target:
        delta = total - target
        if abs(delta) <= target * 0.1:
            notes.append(f"尺は目標どおりです（{total:.1f}秒／目標{target:g}秒）。")
        elif delta > 0:
            cut = delta * mps
            notes.append(f"目標より{delta:.1f}秒長いです。約{int(cut)}モーラ"
                         f"（約{int(cut * cpm)}文字）削ってください。")
        else:
            add = -delta * mps
            notes.append(f"目標より{-delta:.1f}秒短いです。約{int(add)}モーラ"
                         f"（約{int(add * cpm)}文字）足すか、短いまま撮ってください。"
                         "たいていは短いほうが正解です。")

    return {
        "wpm": wpm, "target": target,
        "unit": "mora",
        "total_seconds": round(total, 2),
        "total_words": round(total_mora, 1),   # 日本語版ではモーラ数
        "total_chars": total_chars,
        "beats": rows,
        "notes": notes,
    }


def render(a, out=sys.stdout):
    head = (f"ビート表  ·  約{a['total_words']:.0f}モーラ（{a['total_chars']}文字）"
            f"  ·  約{a['total_seconds']:.1f}秒（毎分{a['wpm']:g}モーラ）"
            + (f"  ·  目標{a['target']:g}秒" if a["target"] else ""))
    print("\n" + head, file=out)
    print("=" * 72, file=out)
    for r in a["beats"]:
        label = f"{r['label']:<8}" if r["label"] else " " * 8
        print(f"  {tc(r['start'])}  {r['dur']:4.1f}s  {label}{r['text']}", file=out)
        for f in r["flags"]:
            print(f"  {'':>6}  {'':>5}  {'':<8}^ {f}", file=out)
    print("-" * 72, file=out)
    for n in a["notes"]:
        print(f"  - {n}", file=out)
    print("", file=out)


def main():
    ap = argparse.ArgumentParser(description="リールの台本を秒数つきのビート表にします（日本語版・速さの基準は目安）。")
    ap.add_argument("input", nargs="?", default="-", help="台本ファイル、または - で標準入力")
    ap.add_argument("--mpm", "--wpm", dest="wpm", type=float, default=DEFAULT_MPM,
                    help="話す速さ。毎分のモーラ数（既定420＝毎秒7）。--wpm は互換用の別名です")
    ap.add_argument("--target", type=float, help="目標の尺（秒）")
    ap.add_argument("--json", action="store_true", help="JSONで出力します")
    args = ap.parse_args()

    if args.input == "-":
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")
        raw = sys.stdin.read()
    else:
        raw = open(args.input, encoding="utf-8-sig").read()
    if args.wpm < 250:
        print(f"注意: 速さ {args.wpm:g} は英語の「毎分の単語数」に見えます。"
              "日本語版は毎分のモーラ数です（目安360〜480）。", file=sys.stderr)
    a = analyse(raw, wpm=args.wpm, target=args.target)
    if not a:
        print("台本が空です", file=sys.stderr)
        sys.exit(2)
    if args.json:
        print(json.dumps(a, indent=2, ensure_ascii=False))
    else:
        render(a)
    sys.exit(0)


if __name__ == "__main__":
    main()
