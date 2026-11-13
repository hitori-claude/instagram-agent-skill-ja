#!/usr/bin/env python3
# 日本語版: Jake Schincariol 氏の MIT ライセンス原版 (Copyright (c) 2026 Jake Schincariol) を日本語向けに改変したものです。
"""
caption.py - Instagram のキャプションをチェックし、フィードで「…続きを読む」の
前に見える部分をそのまま表示します。

フィードでは、キャプションは最初の2行ほどしか見えません。残りはタップしないと
読めません。失敗するキャプションの多くはここでつまずきます。フックが3文目にある、
1行目があいさつ、ハッシュタグから始まっている、などです。このツールは見える範囲を
枠で表示し、そのあとチェック項目を並べます。

見える範囲の考え方（目安です）
  日本語のフィードは文字数ではなく「表示幅」と「行数」で切れます。全角文字は半角の
  約2倍の幅をとるので、幅は unicodedata.east_asian_width で数えます。
    F / W（全角・漢字・かな・絵文字）= 2
    A（①、※、★、… などあいまい幅）= 2  ※日本語フォントのスマホでは全角で出るため
    それ以外（半角英数・半角カナ）= 1
    結合文字・異体字セレクタ・ゼロ幅接合子 = 0
  スマホ（標準の文字サイズ）の1行を半角52（全角26字）と仮定しています。
  表示は2行で、1行目の頭にユーザー名（半角14ぶん）、2行目の末尾に
  「…続きを読む」（半角12ぶん）が入ると仮定しています。
  つまり改行なしなら、本文に使える幅は 52×2 − 14 − 12 = 78（全角で約39字）です。
  改行するとその行の残りは空白になり、次の行に進みます。
  実際の幅は機種、文字サイズ、ユーザー名の長さで変わります。ぎりぎりを狙わず、
  余裕を持たせる前提の数字です。--truncate で本文の幅（半角換算）を変えられます。

使い方
  python caption.py caption.txt
  python caption.py caption.txt --keywords "朝活,時短レシピ"
  type caption.txt | python caption.py -
  python caption.py caption.txt --json
"""

import argparse
import json
import re
import sys
import unicodedata

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

LIMIT = 2200             # Instagram のキャプション上限（文字数）。
LINE_WIDTH = 52          # スマホ1行の幅の目安（半角換算）。全角なら26字。
LINES = 2                # フィードで見える行数の目安。
HANDLE_WIDTH = 14        # 1行目の頭に出るユーザー名ぶんの幅（目安）。
MORE_LABEL = "…続きを読む"
MORE_WIDTH = 12          # 「…続きを読む」ぶんの幅（全角6字）。
TRUNCATE = LINE_WIDTH * LINES - HANDLE_WIDTH - MORE_WIDTH   # = 78
HASHTAG_LIMIT = 5        # 2025年12月18日から1投稿5個まで（以前は30個）。
                         # @Creators の告知では、汎用タグを大量に付けるより
                         # 絞った5個以内のほうが成果が出やすい、という趣旨でした。
EMOJI_PER_100 = 5        # 100文字あたりの絵文字の上限目安。日本語は英語より
                         # 1文字の情報量が多いので、原版の4より少しゆるめです。

# ハッシュタグ: 半角 # または全角 ＃ のあとに、英数字・かな・漢字・長音・々・_ が続くもの。
# 直前が英数字や / & のときは URL の一部とみなして数えません。
HASHTAG_RE = re.compile(r"(?<![A-Za-z0-9&/])([#＃][\w・]+)")
MENTION_RE = re.compile(r"(?<![A-Za-z0-9_.])(@[A-Za-z0-9_.]+)")
LINK_RE = re.compile(r"https?://\S+|\bwww\.\S+|\b[a-z0-9-]+\.(?:com|co|io|net|org|ai|app|jp|me)/\S*",
                     re.IGNORECASE)
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2190-\u21FF]")
# 具体性の目安: 数字（全角含む）、漢数字＋助数詞、金額・割合、英語の固有名詞らしき語。
CONCRETE_RE = re.compile(
    r"[0-9０-９][0-9０-９,，.．]*"
    r"|[一二三四五六七八九十百千万億]+(?:つ|個|本|日|年|か月|ヶ月|カ月|回|人|円|選|分|秒|歳|倍|週間|時間|キロ|kg)"
    r"|[¥￥$＄]|円|[%％]"
    r"|\b[A-Z][a-zA-Z]{2,}\b")

KEYWORD_CHARS = r"[A-Za-z0-9Ａ-Ｚａ-ｚ０-９ぁ-んァ-ヶー一-龥々]{1,12}"
ASKS = [
    (re.compile(r"[「『\"“]" + KEYWORD_CHARS + r"[」』\"”]\s*(?:と|って|で)\s*(?:コメント|書いて|送って)"
                r"|コメント(?:欄)?(?:に|で)\s*[「『\"“]" + KEYWORD_CHARS + r"[」』\"”]"
                r"|(?i:\bcomment (?:the word |\")?[A-Z0-9]{2,}\b)"), "キーワードをコメント"),
    # 「DMで送ります」は相手へのお願いではないので数えません。
    (re.compile(r"(?i)DM(?:ください|下さい|してね|して下さい|してください|お待ち|で(?:ご連絡|お問い合わせ|相談|聞いて|気軽に|お気軽に))"
                r"|ダイレクトメッセージ(?:ください|して)|\b(?:dm|message) me\b"),
     "DM"),
    (re.compile(r"保存(?:して|しておいて|しておく|推奨|がおすすめ|必須|お願い)|あとで見返|後で見返|(?i:\bsave (?:this|it)\b)"),
     "保存"),
    (re.compile(r"シェア(?:して|お願い|してね)|(?:友達|友だち|誰か|家族)に(?:送|シェア|教え)|(?i:\bshare (?:this|it)\b)"),
     "シェア"),
    (re.compile(r"フォロー(?:して|お願い|すると|ください|してね|で)|(?i:\bfollow (?:me|for)\b)"), "フォロー"),
    (re.compile(r"プロフ(?:ィール)?(?:欄)?の?(?:リンク|URL)|(?i:\blink in (?:my )?bio\b)"), "プロフィールのリンク"),
    (re.compile(r"スワイプ|横にめくって|(?i:\b(?:swipe|tap) (?:through|left|right|for|to)\b)"), "スワイプ"),
    (re.compile(r"教えて(?:ください|下さい|ね|ほしい)|どっち派|どれ派|何派|あなたは(?:どう|どれ|どっち)"
                r"|(?i:\btell me\b|\bwhat would you\b|\bwhich one\b)"), "質問に答えてもらう"),
]

FILLER_TAGS = {"#viral", "#fyp", "#explore", "#explorepage", "#foryou", "#foryoupage",
               "#trending", "#instagood", "#love", "#follow", "#like4like", "#reels",
               "#reelsinstagram", "#viralreels", "#instadaily", "#followme", "#f4f", "#l4l",
               "#lfl", "#fff",
               "#いいね", "#いいね返し", "#いいね返しは絶対", "#フォロー", "#フォロー返し",
               "#フォロバ", "#フォロバ100", "#相互フォロー", "#相互", "#おすすめ",
               "#おすすめにのりたい", "#おすすめ載りたい", "#バズれ", "#バズりたい",
               "#拡散希望", "#いいねした人で気になった人フォロー", "#リール", "#インスタ映え"}


def char_width(ch):
    """表示幅（半角=1、全角=2）。結合文字などは0。"""
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    if ch in "\ufe0e\ufe0f\u200d":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("F", "W", "A") else 1


def text_width(s):
    return sum(char_width(c) for c in s)


def zen(w):
    """半角換算の幅を「全角で約何字」に直す。"""
    return round(w / 2)


def norm(s):
    return unicodedata.normalize("NFKC", s).lower()


def layout(text, cut=TRUNCATE):
    """フィードの見え方を再現します。

    1行目はユーザー名ぶんずれて始まり、本文に使える幅は cut（半角換算）。
    改行するとその行の残りは使えなくなります。全文が2行に収まるなら切れません。
    戻り値: (見える行のリスト, 見えた文字列, 切れたかどうか)
    """
    end_full = LINE_WIDTH * LINES          # 2行ぶん全部（ユーザー名込み）
    end_cut = HANDLE_WIDTH + cut           # 「続きを読む」を入れる場合の本文の終わり

    def run(limit):
        # row: 何行目か（0始まり）、col: その行で使った幅
        row, col, lines, cur, shown = 0, HANDLE_WIDTH, [], "", ""
        for ch in text:
            if ch == "\n":                                  # 改行は行の残りを捨てる
                if (row + 1) * LINE_WIDTH >= limit:
                    return lines + [cur], shown, True
                lines.append(cur)
                row, col, cur = row + 1, 0, ""
                shown += ch
                continue
            w = char_width(ch)
            if col + w > LINE_WIDTH:                        # 行の端で折り返す
                lines.append(cur)
                row, col, cur = row + 1, 0, ""
            if row * LINE_WIDTH + col + w > limit:
                return lines + [cur], shown, True
            cur += ch
            shown += ch
            col += w
        return lines + [cur], shown, False

    lines, shown, cut_off = run(end_full)
    if not cut_off:
        return lines, shown, False
    lines, shown, _ = run(end_cut)
    return lines, shown, True


def pad(s, width):
    return s + " " * max(0, width - text_width(s))


def render_box(lines, truncated, out=sys.stdout):
    width = LINE_WIDTH
    print("\n  フィードで見える部分（目安）", file=out)
    print("  +" + "-" * (width + 2) + "+", file=out)
    for i, line in enumerate(lines[:LINES]):
        if i == 0:
            line = pad("@ユーザー名", HANDLE_WIDTH) + line
        if i == len(lines[:LINES]) - 1 and truncated:
            line = line + MORE_LABEL
        print(f"  | {pad(line, width)} |", file=out)
    tail = "ここで切れます" if truncated else "全文が見えています"
    print("  +" + "-" * (width + 2 - text_width(tail) - 2) + f" {tail} " + "+", file=out)


def analyse(text, cut=TRUNCATE, keywords=None):
    text = text.rstrip()
    stripped = text.strip()
    chars = len(stripped)
    width = text_width(stripped)
    lines = stripped.split("\n")
    first_line = lines[0].strip() if lines else ""
    first_w = text_width(first_line)
    no_links = LINK_RE.sub(" ", stripped)
    tags = HASHTAG_RE.findall(no_links)
    mentions = MENTION_RE.findall(no_links)
    links = LINK_RE.findall(stripped)
    emoji = EMOJI_RE.findall(stripped)
    box_lines, window, truncated = layout(stripped, cut)
    asks = [name for pattern, name in ASKS if pattern.search(stripped)]
    filler = [t for t in tags if norm(t).replace("＃", "#") in FILLER_TAGS]
    zenkaku_hash = [t for t in tags if t.startswith("＃")]
    keywords = [k.strip() for k in (keywords or []) if k.strip()]

    checks = []

    def add(name, label, status, detail):
        checks.append({"check": name, "label": label, "status": status, "detail": detail})

    add("LENGTH", "文字数", "FAIL" if chars > LIMIT else "PASS",
        f"{chars} / {LIMIT} 文字" + (f"。上限を {chars - LIMIT} 文字こえています"
                                    if chars > LIMIT else ""))

    if not first_line:
        add("FIRST LINE", "1行目", "FAIL", "空行から始まっています")
    elif first_line[0] in "#＃@":
        add("FIRST LINE", "1行目", "FAIL",
            "ハッシュタグかメンションから始まっています。一番目立つ場所なので文章を置きたいところです")
    elif first_w > cut:
        add("FIRST LINE", "1行目", "WARN",
            f"幅 {first_w}（全角で約{zen(first_w)}字）。見える幅 {cut}（約{zen(cut)}字）をこえるので途中で切れます。"
            "続きが気になる所で切れるなら問題なく、文の途中で切れるなら損です")
    else:
        add("FIRST LINE", "1行目", "PASS", f"幅 {first_w}（全角で約{zen(first_w)}字）。切れずに見えます")

    found = CONCRETE_RE.findall(window)
    add("HOOK IS CONCRETE", "具体性", "PASS" if found else "WARN",
        f"見える範囲に数字や固有名詞が {len(found)} 個"
        + ("" if found else "。タップ前に確かめられる情報がありません"))

    if len(tags) > HASHTAG_LIMIT:
        add("HASHTAGS", "ハッシュタグ", "FAIL",
            f"{len(tags)} 個。上限の {HASHTAG_LIMIT} 個をこえています。6個目以降は効かず、古い書き方に見えます")
    elif len(tags) == HASHTAG_LIMIT and filler:
        add("HASHTAGS", "ハッシュタグ", "WARN",
            f"{len(tags)} 個で上限ちょうど。そのうち {len(filler)} 個は中身のない汎用タグです。5枠は話題に使いたいところです")
    elif filler:
        add("HASHTAGS", "ハッシュタグ", "WARN",
            f"{len(tags)} 個、うち汎用タグ {len(filler)} 個（{', '.join(filler[:3])}）。内容を何も説明していません")
    else:
        add("HASHTAGS", "ハッシュタグ", "PASS",
            f"{len(tags)} 個" + (f": {' '.join(tags)}" if tags else ""))
    if zenkaku_hash:
        add("HASHTAG FORM", "タグの#", "WARN",
            f"全角の ＃ が {len(zenkaku_hash)} 個あります（{', '.join(zenkaku_hash[:3])}）。"
            "半角の # にしておくと確実にタグになります")

    if not tags:
        add("TAG PLACEMENT", "タグの位置", "PASS", "タグなし")
    elif any(t in window for t in tags):
        add("TAG PLACEMENT", "タグの位置", "WARN", "見える範囲にハッシュタグが入っています。貴重な場所をラベルに使っています")
    else:
        add("TAG PLACEMENT", "タグの位置", "PASS", "タグは「続きを読む」の後ろにあります")

    add("LINKS", "リンク", "WARN" if links else "PASS",
        f"URL が {len(links)} 個あります。キャプションのリンクは押せません。プロフィールか DM に移しましょう"
        if links else "押せないリンクはありません")

    if len(asks) == 1:
        add("ONE ASK", "お願いは1つ", "PASS", f"行動の呼びかけは1つ: {asks[0]}")
    elif not asks:
        add("ONE ASK", "お願いは1つ", "WARN", "行動の呼びかけがありません。この投稿で何をしてほしいか決めましょう")
    else:
        add("ONE ASK", "お願いは1つ", "WARN",
            f"呼びかけが {len(asks)} 個（{', '.join(asks)}）。2つあると、どちらもされにくくなります")

    density = len(emoji) * 100 / max(chars, 1)
    add("EMOJI", "絵文字", "WARN" if density > EMOJI_PER_100 else "PASS",
        f"{len(emoji)} 個、100文字あたり {density:.1f} 個"
        + ("。飾りに見えます" if density > EMOJI_PER_100 else ""))

    if keywords:
        low, win = norm(stripped), norm(window)
        found_k = [k for k in keywords if norm(k) in low]
        missing = [k for k in keywords if norm(k) not in low]
        in_window = [k for k in found_k if norm(k) in win]
        status = "PASS" if not missing else ("WARN" if found_k else "FAIL")
        add("SEARCH TERMS", "検索語", status,
            f"{len(found_k)}/{len(keywords)} 個が入っています"
            + (f"、うち見える範囲に {len(in_window)} 個" if found_k else "")
            + (f"。入っていない語: {', '.join(missing)}" if missing else ""))

    fails = sum(1 for c in checks if c["status"] == "FAIL")
    warns = sum(1 for c in checks if c["status"] == "WARN")
    verdict = "FIX" if fails else ("REVIEW" if warns else "READY")

    return {
        "characters": chars, "width": width, "limit": LIMIT, "truncate_at": cut,
        "line_width": LINE_WIDTH, "lines_shown": LINES,
        "visible": window, "visible_lines": box_lines[:LINES], "truncated": truncated,
        "first_line_chars": len(first_line), "first_line_width": first_w,
        "hashtags": tags, "mentions": mentions, "links": links,
        "emoji": len(emoji), "asks": asks,
        "checks": checks, "verdict": verdict,
    }


VERDICT_JA = {"READY": "READY（このまま出せます）", "REVIEW": "REVIEW（WARN を見て判断）",
              "FIX": "FIX（FAIL を直してください）"}


def render(a, out=sys.stdout):
    head = (f"キャプションチェック  ·  {a['characters']} / {a['limit']} 文字  ·  "
            f"タグ {len(a['hashtags'])} 個  ·  呼びかけ {len(a['asks'])} 個")
    print("\n" + head, file=out)
    print("=" * 62, file=out)
    render_box(a["visible_lines"], a["truncated"], out=out)
    print("  ※ 1行=全角26字、2行表示と仮定した目安です。機種や文字サイズで前後します。", file=out)
    print("", file=out)
    for c in a["checks"]:
        print(f"  {c['status']:<5} {pad(c['label'], 12)} {c['detail']}", file=out)
    print("-" * 62, file=out)
    print(f"  判定  {VERDICT_JA[a['verdict']]}\n", file=out)


def main():
    ap = argparse.ArgumentParser(description="Instagram のキャプションをチェックします。")
    ap.add_argument("input", nargs="?", default="-", help="キャプションのファイル。- なら標準入力")
    ap.add_argument("--truncate", type=int, default=TRUNCATE,
                    help=f"「…続きを読む」の前に見える本文の幅（半角換算。既定 {TRUNCATE}＝全角約{zen(TRUNCATE)}字）")
    ap.add_argument("--keywords", default="", help="見つけてほしい検索語をカンマ区切りで（例: 朝活,時短レシピ）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.input == "-":
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")
        raw = sys.stdin.read()
    else:
        raw = open(args.input, encoding="utf-8-sig").read()
    kw = re.split(r"[,，、]", args.keywords)
    a = analyse(raw.replace("\r\n", "\n"), cut=args.truncate, keywords=kw)
    if args.json:
        print(json.dumps(a, indent=2, ensure_ascii=False))
    else:
        render(a)
    sys.exit(0 if a["verdict"] == "READY" else 1)


if __name__ == "__main__":
    main()
