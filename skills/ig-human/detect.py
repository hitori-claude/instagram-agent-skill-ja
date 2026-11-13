#!/usr/bin/env python3
# 日本語版: Jake Schincariol による MIT ライセンスの原作 (ig-human/detect.py) を日本語の文章向けに書き直したものです。
"""
detect.py - 下書きがどれくらい「AIが書いた感じ」に見えるかを5項目で採点します。

これは何か:  公開されているAI判定ツールが見ているとされる特徴を、日本語向けに
置き換えた5つの簡易チェックです。採点はすべて手元のPCで文章だけから計算します。
どこにもアップロードしません。

これは何ではないか:  GPTZero などの判定サービスではありません。APIも呼びません。
しきい値は経験則で決めた仮の値で、日本語の文章データで検証したものではありません。
点数は「直すところの目印」として使ってください。

各項目は 0〜100 の「人間らしさ」スコアです。高いほど良い。

使い方
  python detect.py draft.txt
  type draft.txt | python detect.py -
  python detect.py draft.txt --json
  python detect.py before.txt after.txt      # 2つを並べて比べる
"""

import argparse
import json
import os
import re
import statistics
import sys
import unicodedata

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
LEX = os.path.join(HERE, "slop.json")

KANJI = re.compile(r"[一-鿿㐀-䶿々〆]")
HIRA = re.compile(r"[ぁ-ゟ]")
KATA = re.compile(r"[゠-ヿｦ-ﾟ]")
JA_CHAR = re.compile(r"[ぁ-ヿ一-鿿㐀-䶿ｦ-ﾟ々〆]")
DASHES = "—―–"
# 文末から取り除く飾り（記号・絵文字・笑い）
TAIL_JUNK = re.compile(r"[\s。．.！!？?、，,…‥・〜~ー\-♪☆★♡♥（）()「」『』【】\"'”’]+$|(?:笑|w+|ｗ+)+$")
CASUAL_END = re.compile(r"(?:ね|よ|な|わ|ぞ|さ|かな|よね|じゃん|けど|けどね|し|かも|でしょ|だよ|だね|んだ|よな|っす|笑|w|ｗ)$")
STIFF_END = re.compile(r"(?:です|ます|ました|でした|ません|ください|ましょう|でしょう|でしたか|ですか|ますか|でしょうか|である)$")
URL_RE = re.compile(r"https?://\S+|www\.\S+")
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def clamp(n):
    return max(0.0, min(100.0, n))


def scale(value, human, machine):
    """value を 0〜100 に写す。human の値で100、machine の値で0。"""
    if human == machine:
        return 50.0
    return clamp((value - machine) / (human - machine) * 100)


def sentences(text):
    """。！？!? と改行で区切る。区切り記号は文の側に残す。"""
    parts = re.split(r"(?<=[。！？!?])|\n", text)
    out = []
    for p in parts:
        p = p.strip()
        if len(JA_CHAR.findall(p)) >= 4:
            out.append(p)
    return out


def body_len(s):
    return len(re.sub(r"\s", "", s))


def lexicon_hits(text, lex):
    """辞書の語を長い順に数える。数えた所は伏せて、二重に数えない。"""
    entries = sorted(lex["words"] + lex["phrases"], key=lambda e: len(e["find"]), reverse=True)
    hits, found = 0, []
    for e in entries:
        pat = re.compile(e["find"] if e.get("regex") else re.escape(e["find"]))
        n = len(pat.findall(text))
        if n:
            hits += n
            found.append(e["find"])
            text = pat.sub(lambda m: "\x00" * len(m.group(0)), text)
    return hits, found


def structure_hits(text, lex):
    tells, names = 0, []
    for s in lex["structures"]:
        try:
            n = len(re.compile(s["regex"], re.MULTILINE).findall(text))
        except re.error:
            continue
        if n:
            tells += n
            names.append(s["id"])
    return tells, names


def check_burstiness(text):
    """人は文の長さがばらつく。AIはそろう。"""
    lens = [body_len(s) for s in sentences(text)]
    if len(lens) < 4:
        return 50.0, "文が少なくて判定できません"
    mean = statistics.mean(lens)
    cv = statistics.pstdev(lens) / mean if mean else 0
    score = scale(cv, human=0.45, machine=0.15)
    return score, f"{len(lens)}文の長さのばらつき {cv:.2f}（目安 0.4以上）"


def ending_key(s):
    t = s
    for _ in range(3):
        t2 = TAIL_JUNK.sub("", t)
        t2 = EMOJI.sub("", t2).rstrip()
        if t2 == t:
            break
        t = t2
    return t[-2:] if len(t) >= 2 else t


def check_endings(text):
    """同じ語尾の連続（です。です。です。）と、語尾の変化の少なさ。"""
    sents = sentences(text)
    if len(sents) < 4:
        return 50.0, "文が少なくて判定できません"
    keys = [ending_key(s) for s in sents]
    run = best = 1
    for a, b in zip(keys, keys[1:]):
        run = run + 1 if a == b else 1
        best = max(best, run)
    stiff, lively = 0, 0
    for s in sents:
        core = EMOJI.sub("", TAIL_JUNK.sub("", s)).rstrip()
        core = TAIL_JUNK.sub("", core)
        if STIFF_END.search(core):
            stiff += 1
        elif (CASUAL_END.search(core) or re.search(r"笑|[wｗ]$", s) or EMOJI.search(s[-3:])
              or re.search(r"[！!？?♪]", s[-2:])):
            lively += 1
    stiff_share = stiff / len(sents)
    lively_share = lively / len(sents)
    score = (scale(stiff_share, human=0.40, machine=0.90) * 0.35
             + scale(best, human=2, machine=5) * 0.35
             + scale(lively_share, human=0.30, machine=0.0) * 0.30)
    detail = (f"です・ます等の言い切りだけで終わる文 {stiff_share:.0%}、同じ語尾の最長連続 {best}文、"
              f"話し言葉っぽい文末 {lively_share:.0%}")
    return clamp(score), detail


def check_slop(text, lex):
    """定型句の密度（1000字あたり）と、AIっぽい文の型。"""
    n = body_len(text)
    if not n:
        return 50.0, "空です"
    hits, found = lexicon_hits(text, lex)
    tells, names = structure_hits(text, lex)
    density = hits * 1000 / n
    score = scale(density, human=0.0, machine=12.0) - tells * 10
    detail = f"定型句 {hits}個（1000字あたり {density:.1f}）、文の型 {tells}個"
    shown = found[:4] + names[:3]
    if shown:
        detail += "（" + "、".join(shown) + ("、…" if len(found) > 4 or len(names) > 3 else "") + "）"
    return clamp(score), detail


def check_fingerprint(text):
    """手打ちではまず出ない文字と、Markdownの名残。URLの中は数えない。"""
    text = URL_RE.sub(" ", text)
    invisible = sum(1 for c in text if unicodedata.category(c) == "Cf")
    dash = len(re.findall("[" + DASHES + "]+", text))
    md = (text.count("**") // 2 + text.count("__") // 2
          + len(re.findall(r"(?m)^\s{0,3}#{1,6}\s+\S", text))
          + len(re.findall(r"(?m)^\s*[-*+]\s+\S", text))
          + len(re.findall(r"`[^`\n]+`", text))
          + len(re.findall(r"\[[^\]\n]+\]\([^)\s]+\)", text))
          + len(re.findall(r"(?m)^\s*(?:-{3,}|\*{3,})\s*$", text)))
    curly = sum(text.count(c) for c in "“”‘’")
    nbsp = sum(text.count(c) for c in "   ")
    mix = 0
    pairs = [(r"[0-9]", r"[０-９]"), (r"[A-Za-z]", r"[Ａ-Ｚａ-ｚ]"), (r"!", r"！"), (r"\?", r"？"),
             (r"[()]", r"[（）]")]
    mixed = []
    for half, full in pairs:
        if re.search(half, text) and re.search(full, text):
            mix += 1
            mixed.append(full.strip("[]\\"))
    total = invisible * 4 + dash * 2 + md * 2 + curly + nbsp + mix
    per1k = total * 1000 / max(body_len(text), 1)
    score = scale(per1k, human=0.0, machine=12.0)
    detail = (f"見えない文字 {invisible}、ダッシュ {dash}、Markdown記号 {md}、"
              f"曲がった引用符 {curly}、特殊スペース {nbsp}、全角半角の混在 {mix}種")
    return score, detail


def check_stiffness(text):
    """漢字の多さ、一文あたりの読点、体言止めの連続。"""
    k = len(KANJI.findall(text))
    h = len(HIRA.findall(text))
    ka = len(KATA.findall(text))
    letters = k + h + ka
    sents = sentences(text)
    if letters < 60 or not sents:
        return 50.0, "短くて判定できません"
    kr = k / letters
    touten = (text.count("、") + text.count("，")) / len(sents)
    # 句点のない行が漢字・カタカナで終わる（体言止め）のが3行以上続くか
    chain = best = 0
    for line in text.splitlines():
        t = line.strip()
        if not t:
            continue
        if re.search(r"[一-龯ァ-ヴー]$", EMOJI.sub("", t).rstrip()) and not re.search(r"[。！？!?]$", t):
            chain += 1
            best = max(best, chain)
        else:
            chain = 0
    penalty = 15 if best >= 3 else 0
    score = (scale(kr, human=0.25, machine=0.45) * 0.5
             + scale(touten, human=0.7, machine=2.0) * 0.5 - penalty)
    detail = f"漢字の割合 {kr:.0%}（目安 30%前後まで）、一文あたりの読点 {touten:.1f}個"
    if penalty:
        detail += f"、体言止めの行が{best}行連続"
    return clamp(score), detail


CHECKS = ["BURSTINESS", "ENDINGS", "SLOP DENSITY", "FINGERPRINT", "STIFFNESS"]
LABELS = {
    "BURSTINESS": "文の長さ",
    "ENDINGS": "語尾",
    "SLOP DENSITY": "定型句",
    "FINGERPRINT": "記号の痕跡",
    "STIFFNESS": "硬さ",
}
VERDICT_JA = {"PASS": "このまま出せそう", "REVIEW": "手直し推奨", "FLAGGED": "かなりAIっぽい"}


def run(text, lex):
    results = {
        "BURSTINESS": check_burstiness(text),
        "ENDINGS": check_endings(text),
        "SLOP DENSITY": check_slop(text, lex),
        "FINGERPRINT": check_fingerprint(text),
        "STIFFNESS": check_stiffness(text),
    }
    scores = [results[c][0] for c in CHECKS]
    # いちばん弱い項目が全体を引っぱる。判定ツールは1つの特徴で十分に疑うため。
    overall = statistics.mean(scores) * 0.6 + min(scores) * 0.4
    verdict = "PASS" if overall >= 70 and min(scores) >= 55 else (
        "REVIEW" if overall >= 50 else "FLAGGED")
    return results, overall, verdict


def bar(score, width=24):
    filled = round(score / 100 * width)
    return "#" * filled + "." * (width - filled)


def pad(s, width):
    w = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)
    return s + " " * max(0, width - w)


def render(results, overall, verdict, label=None, out=sys.stdout):
    title = "AIっぽさ判定パネル" + (f"  -  {label}" if label else "")
    print("\n" + title, file=out)
    print("=" * 62, file=out)
    for name in CHECKS:
        score, detail = results[name]
        print(f"  {pad(LABELS[name], 12)} {bar(score)} {score:5.1f}", file=out)
        print(f"  {'':<12} {detail}", file=out)
    print("-" * 62, file=out)
    print(f"  {pad('人間らしさ', 12)} {bar(overall)} {overall:5.1f}   {verdict}（{VERDICT_JA[verdict]}）",
          file=out)
    if verdict != "PASS":
        weakest = min(CHECKS, key=lambda c: results[c][0])
        print(f"\n  いちばん弱いのは「{LABELS[weakest]}」です。ここから直すと早いです。", file=out)
    print("", file=out)


def read_text(p):
    if p == "-":
        try:
            sys.stdin.reconfigure(encoding="utf-8")
        except Exception:
            pass
        return sys.stdin.read()
    with open(p, encoding="utf-8-sig") as fh:
        return fh.read()


def main():
    ap = argparse.ArgumentParser(description="下書きのAIっぽさを5項目で採点します。")
    ap.add_argument("input", nargs="?", default="-", help="ファイル。- なら標準入力")
    ap.add_argument("compare", nargs="?", help="比べたい2つ目のファイル（直す前と後など）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--lexicon", default=LEX)
    args = ap.parse_args()

    with open(args.lexicon, encoding="utf-8") as fh:
        lex = json.load(fh)

    targets = [(args.input, read_text(args.input))]
    if args.compare:
        targets.append((args.compare, read_text(args.compare)))

    payload, runs = [], []
    for name, text in targets:
        results, overall, verdict = run(text, lex)
        runs.append((results, overall, verdict))
        payload.append({
            "source": name,
            "checks": {k: {"score": round(v[0], 1), "detail": v[1]} for k, v in results.items()},
            "human_score": round(overall, 1),
            "verdict": verdict,
        })

    if args.json:
        print(json.dumps(payload if args.compare else payload[0], indent=2, ensure_ascii=False))
        return

    for (name, _), (results, overall, verdict) in zip(targets, runs):
        render(results, overall, verdict, label=os.path.basename(name) if args.compare else None)
    if args.compare:
        a, b = payload
        delta = b["human_score"] - a["human_score"]
        print(f"  {a['human_score']:.1f} {a['verdict']}  ->  "
              f"{b['human_score']:.1f} {b['verdict']}   ({delta:+.1f})\n")

    sys.exit(0 if payload[-1]["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
