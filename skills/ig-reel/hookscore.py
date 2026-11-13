#!/usr/bin/env python3
# MIT ライセンスの原作（Jake Schincariol「Instagram agent skills」）を日本語向けに作り直したものです。
"""
hookscore.py - リールの最初の一言を、強いフックに共通する5つの性質で採点し、
複数のフックを並べて順位を付けます。

これは何か:  文字だけを見て手元で計算する、5つの目安です。
3秒以内に言える長さか、具体的な目印（数字や名前）があるか、何かが懸かっているか、
大事な言葉が前にあるか、見る人に向けて話しているか、を見ます。

これは何でないか:  再生数の予測ではありません。
英語版の原作は、英語のフック74本で検証されています（作り込んだダメなフックとの
見分けは AUC 0.83、同じクリエイターの当たりと外れの見分けは AUC 0.56）。
この日本語版は、日本語の文に合わせて測り方も基準も作り直しています。
しきい値は経験則で決めたもので、日本語のフックではまだ検証していません。
英語版の数字は、この日本語版には当てはまりません。

使い道は、あいさつ、前置き、具体的なものが何もないフック、言うのに5秒かかる
フックを見つけることです。まともなフック2本のどちらが伸びるかは分かりません。
それは顔、編集、音、インスタが誰に見せるかで決まります。
このスクリプトより、視聴維持のグラフを信じてください。

長さの測り方（モーラの目安）
  ひらがな・カタカナ 1字 = 1モーラ（ゃゅょ等の小さい字は前の字とまとめて0、ー・っ・ん は1）
  漢字 1字 = 1.8モーラ（音読みの多くは1〜2モーラ。例: 円=2, 時間=3, 失敗=4）
  数字 = 1 + 0でない桁の数 × 2（例: 5=3, 20=3, 18000=5）
  英字 = 大文字の略語は1字2.5、それ以外は1字1.2（例: AI=5, ChatGPT=8.4）
  話す速さは毎秒7モーラを基準にしています（ふつうの会話で6〜8モーラ/秒）。

各項目は0〜100点です。高いほど良いです。

使い方
  python hookscore.py hooks.txt              # 1行1フック、順位を付ける
  python hookscore.py --hook "契約書の1行を読み飛ばして、18万円失いました。"
  type hooks.txt | python hookscore.py -
  python hookscore.py hooks.txt --json
"""

import argparse
import json
import re
import statistics
import sys
import unicodedata

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

MORA_PER_SEC = 7.0          # 標準の話す速さ（モーラ/秒）
KANJI_MORA = 1.8

SMALL_KANA = set("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ")
KANA_RE = re.compile(r"[ぁ-ゖァ-ヺー]")
KANJI_RE = re.compile(r"[一-鿿々〆ヶ]")   # 々 〆 ヶ を含む
TOKEN_RE = re.compile(r"\d[\d,.]*|[A-Za-z]+|.", re.S)

UNITS = (r"万円|千円|億円|円|ドル|%|パーセント|万|億|千|人|日間|日|時間|分|秒|年|ヶ月|か月|カ月|"
         r"週間|週|本|回|倍|割|歳|才|件|社|冊|枚|キロ|kg|点|位|代|部|曲|文字|字|個|つ|選|"
         r"再生|フォロワー|いいね|保存")
NUMBER_RE = re.compile(r"\d[\d,.]*\s?(?:" + UNITS + r")?")
KANJI_NUM_RE = re.compile(r"(?<![\d〇一二三四五六七八九十百千万億兆半両])[〇一二三四五六七八九十百千万億兆半両]+(?:" + UNITS + r")")
KANJI_NUM_FILLER = {"一つ", "一回", "十分", "一人", "一本", "一日", "万", "千", "百"}
LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9.+\-]+")
LATIN_IGNORE = {"vs", "ng", "ok", "or", "and", "the", "kg"}
HASHTAG_RE = re.compile(r"(?:^|\s)#\S+")
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")

# お金・量の言葉。数字がなくても具体性の目印になります。
MONEY_WORDS = ["年収", "月収", "売上", "給料", "家賃", "貯金", "時給", "利益", "赤字",
               "黒字", "借金", "半分", "倍"]

# 何かが懸かっている目印。（ラベル, 正規表現）。ラベルの種類で数えます。
STAKES = [
    ("損", r"損"),
    ("失敗", r"失敗|しくじ|ミス"),
    ("やめた", r"やめ|辞め|止め[たてるろな]"),
    ("後悔", r"後悔"),
    ("無駄", r"無駄|ムダ|もったいな"),
    ("間違い", r"間違|勘違い|逆効果"),
    ("危険", r"危険|危な|リスク|注意|警告|要注意"),
    ("禁止", r"禁止|NG|ダメ|だめ"),
    ("嘘", r"嘘|ウソ|騙"),
    ("秘密", r"秘密|裏側|裏技|本音|本当は|実は"),
    ("知らない", r"知らな|知らず|気づいてな|気づかな|気付かな"),
    ("誰も", r"誰も|だれも|誰一人"),
    ("絶対", r"絶対"),
    ("失う", r"失[いうっえ]|消え|消し|捨て|削除|溶か"),
    ("炎上", r"炎上|破産|クビ|解雇|退職|赤字|借金"),
    ("無料", r"無料|タダ|(?<!\d)0円"),
    ("最悪", r"最悪|最低|ヤバ|やば|怖"),
    ("逆接", r"なのに|のに[、,]|じゃなくて|ではなく|代わりに|でも[、,]|けど|しかし|逆に"),
    ("限定", r"だけ|しか|たった|まだ|もう"),
    ("対比", r"前は|昔は|以前は|今は|今では|最初の|→"),
    ("悪化", r"減っ|落ち|切れ|隠れ|伸びな|バレ|止ま[っる]|下が"),
    ("期限", r"前に|までに|うちに"),
    ("比較", r"\bvs\b|VS|対決|比較|より"),
    ("得", r"得する|節約|浮い|稼げ|稼い"),
    ("否定", r"ない|ません|なかった|なく|ず[にとも、。]"),
]
STAKES_C = [(label, re.compile(p, re.I)) for label, p in STAKES]

# 最初の1秒を何も言わずに使う出だし。
WEAK_OPENERS = [
    "こんにちは", "こんばんは", "おはよう", "どうも", "はいどうも", "はい", "えー", "えっと",
    "あの", "さて", "今日は", "今回は", "本日は", "今日の", "皆さん", "みなさん", "皆様", "みんな",
    "というわけで", "ということで", "突然ですが", "いきなりですが", "早速", "改めて", "正直",
    "ねえ", "ねぇ", "ちょっと", "まず", "今から", "これから", "これは", "それは", "ここでは",
    "実は今日", "知ってる", "知ってます",
]

DEALBREAKERS = [
    (re.compile(r"^\s*(?:スクロール(?:を)?(?:止め|やめ|しないで)|ちょっと待って|待って[!！]?)"),
     "「スクロール止めて」で始まっています。注意をお願いするのは、まだ注意を得ていない証拠です。"),
    (re.compile(r"(?:この動画|今回の動画|今日の動画|このリール)(?:では|で|は)"
                r"|(?:今日|今回|本日)は[^。！？!?]{0,25}について"
                r"|(?:紹介|解説|お話し|説明)(?:して)?(?:いき)?ます"),
     "動画の前置きです。消して、答えから始めてください。"),
    (re.compile(r"^\s*(?:こんにちは|こんばんは|おはよう|どうも|はいどうも|皆さん|みなさん|皆様"
                r"|みんな[、!！]|やっほー|ハロー)"),
     "あいさつです。あいさつされるためにフィードを見ている人はいません。"),
    (HASHTAG_RE,
     "フックにハッシュタグがあります。ハッシュタグは、使うならキャプションの最後です。"),
    (EMOJI_RE,
     "フックに絵文字があります。大きな画面文字には、言葉か絵文字のどちらかしか入りません。"),
]


def clamp(n):
    return max(0.0, min(100.0, n))


def normalize(text):
    """全角数字・全角英字・半角カナをそろえます。"""
    return unicodedata.normalize("NFKC", text)


def number_mora(token):
    digits = re.sub(r"[^\d]", "", token.split(".")[0])
    nonzero = sum(1 for d in digits if d != "0")
    return 1 + max(1, nonzero) * 2


def mora(text):
    """読み上げたときのモーラ数の目安。docstring 冒頭の規則で数えます。"""
    t = normalize(text)
    m = 0.0
    for tok in TOKEN_RE.findall(t):
        if tok[0].isdigit():
            m += number_mora(tok)
        elif tok[0].isascii() and tok[0].isalpha():
            if tok.isupper() and len(tok) <= 5:
                m += 2.5 * len(tok)
            else:
                m += 1.2 * len(tok)
        elif tok in SMALL_KANA:
            continue
        elif KANA_RE.match(tok):
            m += 1
        elif KANJI_RE.match(tok):
            m += KANJI_MORA
        elif tok == "%":
            m += 4
        elif tok == "$":
            m += 2
    return m


def chars(text):
    """画面に出る文字数（空白と記号を除く）。"""
    t = normalize(text)
    return len([c for c in t if not c.isspace() and unicodedata.category(c)[0] in "LN"])


def find_numbers(text):
    t = normalize(text)
    found = [m.group().strip() for m in NUMBER_RE.finditer(t)]
    for m in KANJI_NUM_RE.finditer(t):
        if m.group() not in KANJI_NUM_FILLER:
            found.append(m.group())
    return found


def find_latin(text):
    return [w for w in LATIN_RE.findall(normalize(text)) if w.lower() not in LATIN_IGNORE]


def find_money(text):
    t = normalize(text)
    return [w for w in MONEY_WORDS if w in t]


def find_stakes(text):
    t = normalize(text)
    hits = []
    for label, pat in STAKES_C:
        m = pat.search(t)
        if m:
            hits.append((label, m.start()))
    return hits


def check_length(text):
    """指が動く前に届く長さ。目安は2〜3秒。"""
    n = mora(text)
    c = chars(text)
    secs = n / MORA_PER_SEC
    if 8 <= n <= 28:
        score = 100.0
    elif n < 8:
        score = clamp(100 - (8 - n) * 8)
    else:
        score = clamp(100 - (n - 28) * 4)
    if c > 30:                            # 大きな画面文字で2行を超える
        score -= 12
    return clamp(score), f"約{n:.0f}モーラ・{c}文字・話して約{secs:.1f}秒（目安 8〜28モーラ、約1〜4秒）"


def check_specificity(text):
    """抽象的な3つより、具体的な1つ。"""
    nums = find_numbers(text)
    latin = find_latin(text)
    money = [w for w in find_money(text) if not any(w in n for n in nums)]
    hits = len(nums) + len(latin) + len(money)
    score = 15.0 if hits == 0 else clamp(45 + hits * 30)
    found = "、".join(nums[:2] + latin[:2] + money[:2])
    return score, (f"具体的な目印 {hits}個" + (f"：{found}" if found else
                   "。数字も名前もなく、確かめられるものがありません"))


def check_stakes(text):
    """緊張、損、否定。見る人が失うかもしれないもの。"""
    markers = [label for label, _ in find_stakes(text)]
    if re.search(r"\d[\d,.]*\s?(?:万|千|億)?円|\$\s?\d", normalize(text)):
        markers.append("値段")
    n = len(markers)
    score = {0: 20.0, 1: 70.0}.get(n, 100.0)
    detail = f"懸かっているものの目印 {n}個" + (f"：{'、'.join(markers[:4])}" if markers else
                                            "。この一言では何も懸かっていません")
    return clamp(score), detail


def _payload_positions(text):
    t = normalize(text)
    pos = []
    for m in NUMBER_RE.finditer(t):
        pos.append(m.start())
    for m in KANJI_NUM_RE.finditer(t):
        if m.group() not in KANJI_NUM_FILLER:
            pos.append(m.start())
    for m in LATIN_RE.finditer(t):
        if m.group().lower() not in LATIN_IGNORE:
            pos.append(m.start())
    for w in MONEY_WORDS:
        i = t.find(w)
        if i >= 0:
            pos.append(i)
    for label, i in find_stakes(t):
        if label != "否定":                 # 文末の「ない」は日本語の語順上どうしても後ろに来る
            pos.append(i)
    m = re.search(r"あなた|君|きみ", t)
    if m:
        pos.append(m.start())
    return t, sorted(pos)


def check_frontload(text):
    """大事な言葉を後ろに置かない。"""
    t, pos = _payload_positions(text)
    if not t.strip():
        return 0.0, "空です"
    stripped = t.strip()
    penalty = 0
    hit_opener = None
    for weak in WEAK_OPENERS:
        if stripped.startswith(weak):
            penalty, hit_opener = 30, weak
            break
    if not pos:
        base = 30.0
        where = "目玉になる言葉がどこにもありません"
    else:
        at = mora(t[:pos[0]])
        if at <= 6:
            base = 100.0
            where = f"目玉の言葉が{at:.0f}モーラ目に来ています"
        elif at <= 12:
            base = 70.0
            where = f"目玉の言葉が{at:.0f}モーラ目。もう少し前に出せます"
        else:
            base = 40.0
            where = f"目玉の言葉が{at:.0f}モーラ目。遅すぎます"
    detail = where + (f"。出だしが弱い（「{hit_opener}」）" if hit_opener else "")
    return clamp(base - penalty), detail


IMPERATIVE_RE = re.compile(
    r"(?:ください|してみて|しないで|やめて|やめろ|見て(?!た|る|い)|試して|保存して|パクって|"
    r"使って|捨てて|しよう|やろう|するな|しろ|して[。！!]?\s*$)")
TARGET_RE = re.compile(
    r"(?:な|てる|でる|ている|でいる|たい|ない|した|だった|の)(?:人|方)"
    r"(?:へ|に|は|が|も|、|。|！|!|？|\?|向け|必見|$)")
QUESTION_RE = re.compile(r"[？?]\s*$|(?:ますか|ですか|ませんか|てない|てる)[？?]?\s*$")


def check_address(text):
    """一人の見る人に向けているか、宙に浮いているか。"""
    t = normalize(text)
    if re.search(r"あなた|アナタ|君|きみ|お前", t):
        return 100.0, "見る人に直接話しかけています"
    if TARGET_RE.search(t):
        return 95.0, "「〜な人」で見る人を名指ししています"
    if IMPERATIVE_RE.search(t):
        return 90.0, "見る人への呼びかけ・お願いの形です"
    if QUESTION_RE.search(t):
        return 85.0, "見る人への問いかけです"
    if re.search(r"私|わたし|僕|ぼく|俺|おれ|自分|うちの|我が家", t):
        return 70.0, "一人称。見る人の名指しはありません"
    if re.search(r"彼女|彼(?!ら)|あの人|[一-龯ァ-ヴ]さん[がは]", t):
        return 40.0, "三人称。この場に誰もいません"
    return 55.0, "主語なし（日本語では普通です。見る人を名指しすると強くなります）"


CHECKS = ["LENGTH", "SPECIFICITY", "STAKES", "FRONTLOAD", "ADDRESS"]
LABELS = {"LENGTH": "長さ", "SPECIFICITY": "具体性", "STAKES": "懸かるもの",
          "FRONTLOAD": "前のめり", "ADDRESS": "呼びかけ"}
VERDICT_JA = {"STRONG": "強い", "OK": "まあまあ", "WEAK": "弱い"}


def run(text):
    results = {
        "LENGTH": check_length(text),
        "SPECIFICITY": check_specificity(text),
        "STAKES": check_stakes(text),
        "FRONTLOAD": check_frontload(text),
        "ADDRESS": check_address(text),
    }
    t = normalize(text)
    flags = [msg for pattern, msg in DEALBREAKERS if pattern.search(t)]
    scores = [results[c][0] for c in CHECKS]
    # 一番弱い項目がフック全体の上限になります。一つ悪いだけで指は止まりません。
    overall = statistics.mean(scores) * 0.6 + min(scores) * 0.4 - len(flags) * 15
    overall = clamp(overall)
    verdict = "STRONG" if overall >= 70 and min(scores) >= 55 and not flags else (
        "OK" if overall >= 50 else "WEAK")
    return results, overall, verdict, flags


def bar(score, width=24):
    filled = round(score / 100 * width)
    return "#" * filled + "." * (width - filled)


def pad(s, width):
    """全角文字を2桁として左寄せします。"""
    w = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)
    return s + " " * max(0, width - w)


def render_one(text, results, overall, verdict, flags, out=sys.stdout):
    print("\nフック採点", file=out)
    print("=" * 62, file=out)
    print(f"  「{text.strip()}」\n", file=out)
    for name in CHECKS:
        score, detail = results[name]
        print(f"  {pad(LABELS[name], 12)} {bar(score)} {score:5.1f}", file=out)
        print(f"  {pad('', 12)} {detail}", file=out)
    print("-" * 62, file=out)
    print(f"  {pad('総合', 12)} {bar(overall)} {overall:5.1f}   {verdict}（{VERDICT_JA[verdict]}）",
          file=out)
    for f in flags:
        print(f"\n  致命的  {f}", file=out)
    if verdict != "STRONG":
        weakest = min(CHECKS, key=lambda c: results[c][0])
        print(f"\n  一番弱い項目: {LABELS[weakest]}。ここを直して、もう一度測ってください。", file=out)
    print("", file=out)


def render_table(rows, out=sys.stdout):
    print("\nフックの順位\n" + "=" * 78, file=out)
    for i, r in enumerate(rows, 1):
        mark = "->" if i == 1 else "  "
        hook = r["hook"] if len(r["hook"]) <= 34 else r["hook"][:32] + "…"
        print(f"{mark} {r['score']:5.1f} {r['verdict']:<7} {hook}", file=out)
        print(f"        一番弱い項目: {LABELS[r['weakest']]}"
              f"（{r['checks'][r['weakest']]['score']:.0f}）", file=out)
        for f in r["flags"]:
            print(f"        致命的: {f}", file=out)
    print("\n1位を撮ってください。1位が50点未満なら、この中にフックはまだありません。\n",
          file=out)


def main():
    ap = argparse.ArgumentParser(description="リールのフックを5つの性質で採点します（日本語版・基準は未検証の目安）。")
    ap.add_argument("input", nargs="?", default="-", help="1行1フックのファイル、または - で標準入力")
    ap.add_argument("--hook", help="コマンドラインで渡した1本だけを採点します")
    ap.add_argument("--json", action="store_true", help="JSONで出力します")
    args = ap.parse_args()

    if args.hook:
        lines = [args.hook]
    else:
        if args.input == "-":
            if hasattr(sys.stdin, "reconfigure"):
                sys.stdin.reconfigure(encoding="utf-8")
            raw = sys.stdin.read()
        else:
            raw = open(args.input, encoding="utf-8-sig").read()
        lines = [l.strip().lstrip("\ufeff") for l in raw.splitlines() if l.strip().lstrip("\ufeff")]
    if not lines:
        print("採点するものがありません", file=sys.stderr)
        sys.exit(2)

    payload = []
    for line in lines:
        results, overall, verdict, flags = run(line)
        payload.append({
            "hook": line,
            "checks": {k: {"score": round(v[0], 1), "detail": v[1]} for k, v in results.items()},
            "weakest": min(CHECKS, key=lambda c: results[c][0]),
            "flags": flags,
            "score": round(overall, 1),
            "verdict": verdict,
        })

    if args.json:
        print(json.dumps(payload if len(payload) > 1 else payload[0], indent=2, ensure_ascii=False))
        return

    if len(payload) == 1:
        results, overall, verdict, flags = run(lines[0])
        render_one(lines[0], results, overall, verdict, flags)
    else:
        render_table(sorted(payload, key=lambda r: -r["score"]))

    sys.exit(0 if max(p["score"] for p in payload) >= 70 else 1)


if __name__ == "__main__":
    main()
