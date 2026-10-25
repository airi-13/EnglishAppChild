"""動作確認用のサンプル問題を作る（20級〜1級、初段〜20段 × 各40問 = 1,600問）。
文はテンプレートから機械的に作った練習用です。本番では、自分の教材のCSVを取り込んでください。"""
import csv
import random

# 名詞: (英, 日, 数えられるか)
FOOD = [("apple", "りんご", 1), ("cake", "ケーキ", 1), ("egg", "卵", 1), ("bread", "パン", 0), ("banana", "バナナ", 1), ("orange", "オレンジ", 1),
        ("sandwich", "サンドイッチ", 1), ("cookie", "クッキー", 1), ("pizza", "ピザ", 1), ("salad", "サラダ", 0), ("rice", "ご飯", 0), ("soup", "スープ", 0)]
DRINK = [("milk", "牛乳", 0), ("tea", "お茶", 0), ("water", "水", 0), ("juice", "ジュース", 0), ("coffee", "コーヒー", 0)]
READ = [("book", "本", 1), ("letter", "手紙", 1), ("magazine", "雑誌", 1), ("newspaper", "新聞", 1), ("comic", "漫画", 1), ("story", "物語", 1)]
WRITE = [("letter", "手紙", 1), ("diary", "日記", 1), ("story", "物語", 1), ("message", "メッセージ", 1), ("report", "レポート", 1)]
USE = [("pen", "ペン", 1), ("computer", "パソコン", 1), ("phone", "電話", 1), ("dictionary", "辞書", 1), ("map", "地図", 1), ("camera", "カメラ", 1)]
PLAY = [("tennis", "テニス", 0), ("soccer", "サッカー", 0), ("baseball", "野球", 0), ("basketball", "バスケットボール", 0), ("golf", "ゴルフ", 0)]
WATCH = [("movie", "映画", 1), ("video", "ビデオ", 1), ("game", "試合", 1), ("show", "番組", 1)]
WASH = [("car", "車", 1), ("dish", "お皿", 1), ("shirt", "シャツ", 1), ("window", "窓", 1)]
OPEN = [("door", "ドア", 1), ("window", "窓", 1), ("box", "箱", 1), ("bag", "かばん", 1)]
BUY = [("bag", "かばん", 1), ("hat", "帽子", 1), ("pen", "ペン", 1), ("book", "本", 1), ("shirt", "シャツ", 1), ("watch", "時計", 1)]
THINGS = [("pen", "ペン", 1), ("book", "本", 1), ("apple", "りんご", 1), ("dog", "犬", 1), ("cat", "ねこ", 1), ("bag", "かばん", 1), ("desk", "机", 1),
          ("chair", "いす", 1), ("car", "車", 1), ("ball", "ボール", 1), ("cake", "ケーキ", 1), ("hat", "帽子", 1), ("key", "かぎ", 1), ("map", "地図", 1),
          ("clock", "時計", 1), ("box", "箱", 1), ("bike", "自転車", 1), ("umbrella", "かさ", 1), ("kite", "たこ", 1), ("table", "テーブル", 1)]
ADJ = [("big", "大きい"), ("small", "小さい"), ("new", "新しい"), ("old", "古い"), ("red", "赤い"), ("blue", "青い"), ("long", "長い"), ("heavy", "重い"), ("cheap", "安い"), ("nice", "すてきな")]

# 動詞: (原形, 三単現, 過去, ing, ます形, ました形, ません形, ています形, 目的語)
VERBS = [
    ("eat", "eats", "ate", "eating", "食べます", "食べました", "食べません", "食べています", FOOD),
    ("drink", "drinks", "drank", "drinking", "飲みます", "飲みました", "飲みません", "飲んでいます", DRINK),
    ("read", "reads", "read", "reading", "読みます", "読みました", "読みません", "読んでいます", READ),
    ("write", "writes", "wrote", "writing", "書きます", "書きました", "書きません", "書いています", WRITE),
    ("use", "uses", "used", "using", "使います", "使いました", "使いません", "使っています", USE),
    ("play", "plays", "played", "playing", "します", "しました", "しません", "しています", PLAY),
    ("watch", "watches", "watched", "watching", "見ます", "見ました", "見ません", "見ています", WATCH),
    ("wash", "washes", "washed", "washing", "洗います", "洗いました", "洗いません", "洗っています", WASH),
    ("open", "opens", "opened", "opening", "開けます", "開けました", "開けません", "開けています", OPEN),
    ("buy", "buys", "bought", "buying", "買います", "買いました", "買いません", "買っています", BUY),
    ("make", "makes", "made", "making", "作ります", "作りました", "作りません", "作っています", FOOD),
]
SUBJ = [("I", "私は", "I"), ("You", "あなたは", "you"), ("He", "彼は", "3s"), ("She", "彼女は", "3s"), ("We", "私たちは", "pl"), ("They", "彼らは", "pl"),
        ("Ken", "ケンは", "3s"), ("Mika", "ミカは", "3s"), ("My brother", "私の兄は", "3s"), ("My mother", "私の母は", "3s"), ("The boy", "その男の子は", "3s")]
HABIT = [("every day", "毎日"), ("every morning", "毎朝"), ("on Sundays", "日曜日に"), ("after school", "放課後に")]
PAST = [("yesterday", "昨日"), ("last night", "昨夜"), ("last week", "先週")]
FUT = [("tomorrow", "明日"), ("next week", "来週"), ("next Sunday", "次の日曜日に")]

NOTES = [
    "this is ~ / I have ~ のあとの数えられる名詞には a / an をつける（母音の音の前は an）",
    "主語が I / you / we / they のとき、動詞はそのままの形（原形）を使う",
    "主語が he / she / 名前など1人のとき、動詞の最後に s / es をつける",
    "過去のことは動詞を過去形にする（ate, drank, wrote など不規則な形に注意）",
    "「〜しているところ」は be動詞(am/is/are) + 動詞のing 形",
    "否定文は do not / does not のあとに動詞の原形を置く",
    "疑問文は Do / Does を文の最初に置き、動詞は原形にする",
    "未来は will + 動詞の原形、過去の否定は did not + 原形",
]


def art(noun, cnt, who="a"):
    if not cnt:
        return noun
    return ("an " if noun[0] in "aiueo" else "a ") + noun


def be(kind):
    return {"I": "am", "3s": "is"}.get(kind, "are")


def gen_tier(tier, rnd, need):
    seen, out = set(), []
    guard = 0
    while len(out) < need and guard < 20000:
        guard += 1
        s_en, s_ja, kind = rnd.choice(SUBJ)
        v = rnd.choice(VERBS)
        base, s3, past, ing, jp, jpast, jneg, jprog, objs = v
        n_en, n_ja, cnt = rnd.choice(objs)
        o_en = art(n_en, cnt)
        noun_target = len(out) % 2 == 0
        if tier == 0:
            kindt = rnd.choice(("this", "have", "adj"))
            n2 = rnd.choice(THINGS)
            if kindt == "this":
                en, ja = f"This is {art(n2[0], 1)}.", f"これは{n2[1]}です。"
            elif kindt == "have":
                en, ja = f"I have {art(n2[0], 1)}.", f"私は{n2[1]}を持っています。"
            else:
                a_en, a_ja = rnd.choice(ADJ)
                en, ja = f"It is {art(a_en, 1)} {n2[0]}.", f"それは{a_ja}{n2[1]}です。"
            if kindt == "adj" and not noun_target:
                t_en, t_ja = a_en, a_ja
            else:
                t_en, t_ja = n2[0], n2[1]
        else:
            if tier == 1:
                s_en, s_ja, kind = rnd.choice([x for x in SUBJ if x[2] != "3s"])
                en, ja, ven, vja = f"{s_en} {base} {o_en}.", f"{s_ja}{n_ja}を{jp}。", base, jp
            elif tier == 2:
                t = rnd.choice(HABIT); vv = s3 if kind == "3s" else base
                en, ja, ven, vja = f"{s_en} {vv} {o_en} {t[0]}.", f"{s_ja}{t[1]}{n_ja}を{jp}。", vv, jp
            elif tier == 3:
                t = rnd.choice(PAST)
                en, ja, ven, vja = f"{s_en} {past} {o_en} {t[0]}.", f"{s_ja}{t[1]}{n_ja}を{jpast}。", past, jpast
            elif tier == 4:
                t = ("now", "今")
                en, ja, ven, vja = f"{s_en} {be(kind)} {ing} {o_en} now.", f"{s_ja}今{n_ja}を{jprog}。", ing, jprog
            elif tier == 5:
                aux = "does not" if kind == "3s" else "do not"; t = rnd.choice(HABIT)
                en, ja, ven, vja = f"{s_en} {aux} {base} {o_en} {t[0]}.", f"{s_ja}{t[1]}{n_ja}を{jneg}。", base, jneg
            elif tier == 6:
                aux = "Does" if kind == "3s" else "Do"; subj_q = {"I": "you", "You": "I"}.get(s_en, s_en[0].lower() + s_en[1:])
                sq_ja = {"I": "あなたは", "You": "私は"}.get(s_en, s_ja)
                en, ja, ven, vja = f"{aux} {subj_q} {base} {o_en}?", f"{sq_ja}{n_ja}を{jp}か。", base, jp
            else:
                if rnd.random() < .5:
                    t = rnd.choice(FUT)
                    en, ja, ven, vja = f"{s_en} will {base} {o_en} {t[0]}.", f"{s_ja}{t[1]}{n_ja}を{jp}。", base, jp
                else:
                    t = rnd.choice(PAST); aux = "did not"
                    en, ja, ven, vja = f"{s_en} did not {base} {o_en} {t[0]}.", f"{s_ja}{t[1]}{n_ja}を{jneg.replace('ません', 'ませんでした')}。", base, jneg.replace("ません", "ませんでした")
            t_en, t_ja = (n_en, n_ja) if noun_target else (ven, vja)
        if en in seen:
            continue
        seen.add(en)
        out.append((en, ja, t_en, t_ja, NOTES[tier]))
    if len(out) < need:
        raise RuntimeError("sample generation ran out of combinations")
    return out


def level_names():
    return [f"{n}級" for n in range(20, 0, -1)] + ["初段"] + [f"{n}段" for n in range(2, 21)]


def write_csv(path, seed=7):
    rnd = random.Random(seed)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["級・段", "番号", "英文", "和文", "英単語", "日本語の単語", "文法メモ"])
        for tier in range(8):  # 5つの級・段ごとに、同じ文型の問題を40問ずつ
            pool = gen_tier(tier, rnd, 200)
            for k in range(5):
                name = level_names()[tier * 5 + k]
                for i, r in enumerate(pool[k * 40:(k + 1) * 40], 1):
                    w.writerow([name, i, *r])
