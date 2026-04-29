"""Tests for MeCab-based hiragana conversion in text_processor."""

import pytest

from app.services.text_processor import _katakana_to_hiragana, _mecab_to_hiragana


def _mecab_available() -> bool:
    try:
        import MeCab  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _mecab_available(), reason="MeCab not installed"
)


class TestKatakanaToHiragana:
    def test_basic(self):
        assert _katakana_to_hiragana("アイウエオ") == "あいうえお"

    def test_mixed(self):
        assert _katakana_to_hiragana("コンニチハ") == "こんにちは"

    def test_non_katakana_preserved(self):
        assert _katakana_to_hiragana("ABC123あ") == "ABC123あ"


class TestMecabToHiragana:
    def test_simple_kanji(self):
        """漢字→ひらがな変換の基本動作。"""
        result = _mecab_to_hiragana("今日")
        assert result == "きょう"

    def test_particle_wa(self):
        """助詞「は」→「わ」に変換（非助詞の「は」は残る）。"""
        result = _mecab_to_hiragana("彼女は走った")
        assert "かのじょわ" in result

    def test_particle_e(self):
        """助詞「へ」→「え」に変換。"""
        result = _mecab_to_hiragana("学校へ行く")
        assert "がっこうえ" in result

    def test_particle_wo_preserved(self):
        """助詞「を」はそのまま保持。"""
        result = _mecab_to_hiragana("本を読む")
        assert "を" in result

    def test_tokorodokoro_reading(self):
        """所々 → ところどころ（しょどころにならない）。"""
        result = _mecab_to_hiragana("所々に手形があった")
        assert "ところどころ" in result
        assert "しょどころ" not in result

    def test_jouhanshin_reading(self):
        """上半身 → じょうはんしん（じょうわんしんにならない）。"""
        result = _mecab_to_hiragana("上半身が見えた")
        assert "じょうはんしん" in result
        assert "じょうわんしん" not in result

    def test_no_kanji_remaining(self):
        """変換後に漢字が残らない。"""
        text = "彼女は暗い穴を覗き込んだ。所々に上半身の影が見えた。"
        result = _mecab_to_hiragana(text)
        import re
        remaining = re.findall(r"[一-龯]", result)
        assert remaining == [], f"未変換の漢字: {remaining}"

    def test_non_particle_ha_preserved(self):
        """非助詞の「は」は変えない（例: 「はなし」の「は」）。"""
        result = _mecab_to_hiragana("はなし")
        assert result == "はなし"

    def test_katakana_preserved(self):
        """カタカナはそのまま。"""
        result = _mecab_to_hiragana("カーテンを開けた")
        assert "カーテン" in result

    def test_punctuation_preserved(self):
        """句読点・記号は保持。"""
        result = _mecab_to_hiragana("彼は、走った。")
        assert "、" in result
        assert "。" in result

    def test_reading_override_watashi(self):
        """読み上書き辞書: 私 → わたし（MeCabデフォルトの「わたくし」を上書き）。"""
        result = _mecab_to_hiragana("私は走った")
        assert "わたし" in result
        assert "わたくし" not in result

    def test_compound_otousan(self):
        """複合語置換: お父さん → おとうさん（MeCab分割で 父→ちち になるのを防ぐ）。"""
        result = _mecab_to_hiragana("お父さんは走った")
        assert "おとうさん" in result
        assert "おちち" not in result

    def test_compound_okaasan(self):
        """複合語置換: お母さん → おかあさん（MeCab分割で 母→はは になるのを防ぐ）。"""
        result = _mecab_to_hiragana("お母さんは優しい")
        assert "おかあさん" in result
        assert "おはは" not in result

    def test_keep_haha_as_kanji(self):
        """母は漢字のまま保持（VOICEVOXが "はは" を "ワワ" と誤読するため）。"""
        result = _mecab_to_hiragana("3人で母を見た")
        assert "母" in result
        assert "はは" not in result

    def test_keep_hanashi_as_kanji(self):
        """話は漢字のまま保持（「こわいはなし」等で VOICEVOX が はなし→ワナシ と誤読）。"""
        result = _mecab_to_hiragana("怖い話")
        assert "話" in result
        assert "はなし" not in result

    def test_keep_hana_kanji(self):
        """花/鼻は漢字のまま保持（VOICEVOX が はな→ワナ と誤読）。"""
        assert "花" in _mecab_to_hiragana("きれいな花")
        assert "鼻" in _mecab_to_hiragana("赤い鼻")

    def test_keep_body_parts_kanji(self):
        """肌/羽など、VOICEVOX が は→ワ と誤読する1-2モーラ名詞を漢字保持。"""
        assert "肌" in _mecab_to_hiragana("きれいな肌")
        assert "羽" in _mecab_to_hiragana("長い羽")
        assert "箱" in _mecab_to_hiragana("この箱")

    def test_keep_haka_kanji(self):
        """墓 は漢字のまま保持（、はかわ… で VOICEVOX が ワカ と誤読するため）。"""
        result = _mecab_to_hiragana("結局、墓は移してもらった")
        assert "墓" in result
        assert "はか" not in result

    def test_hanasu_verb_form_kept_kanji(self):
        """話す/話し (動詞活用形) も漢字保持 (particle+話し で ワナシ 誤読対策)。"""
        assert "話す" in _mecab_to_hiragana("彼は話す")
        assert "話し" in _mecab_to_hiragana("彼が話した")
        assert "話し" in _mecab_to_hiragana("小声で話し始めた")
        # 複合語名詞も保持
        assert "話し声" in _mecab_to_hiragana("話し声が聞こえる")

    def test_compound_kanji_still_converted(self):
        """会話/昔話/花火 などの複合語も1トークンで処理されひらがな変換される。"""
        assert "かいわ" in _mecab_to_hiragana("静かな会話")
        assert "むかしばなし" in _mecab_to_hiragana("昔話を聞く")
        assert "はなび" in _mecab_to_hiragana("夏の花火")

    def test_noun_nioi_after_no(self):
        """の臭い は名詞(におい)として読む。MeCab が後続文脈で形容詞に倒れるのを防ぐ。"""
        result = _mecab_to_hiragana("石油ヒーターの臭いと、機械音")
        assert "におい" in result
        assert "くさい" not in result

    def test_counter_span_preserved_kanji(self):
        """数字+カウンター漢字は漢字のまま保持（VOICEVOXが正しい促音/連濁で読むため）。"""
        # 一泊二日: MeCab分解だと いち+はく+ふた+か になり誤読
        result = _mecab_to_hiragana("一泊二日の旅")
        assert "一泊二日" in result, f"expected 一泊二日 preserved, got: {result}"

    def test_counter_span_with_arabic_digits(self):
        """アラビア数字+カウンター漢字も保持される。"""
        result = _mecab_to_hiragana("3人は1泊2日で")
        assert "3人" in result
        assert "1泊2日" in result

    def test_counter_nichikan_preserved(self):
        """日間 (duration counter) も 数詞+日間 として保持される。"""
        result = _mecab_to_hiragana("3日間ずっと")
        assert "3日間" in result

    def test_counter_nensei_preserved(self):
        """年生 (学年) も 数詞+年生 として保持され MeCab で生→なま と誤変換されない。"""
        result = _mecab_to_hiragana("6年生の子供")
        assert "6年生" in result
        assert "なま" not in result

    def test_non_counter_sei_still_converted(self):
        """カウンター文脈にない 生 は従来通り変換される（なま/い）。"""
        assert "なま" in _mecab_to_hiragana("生卵")
        assert "うまれる" in _mecab_to_hiragana("生まれる")

    def test_non_counter_kanji_still_converted(self):
        """カウンター文脈にない同じ漢字は従来通り変換される。"""
        # その日 の 日 は名詞(単独)なので ひ に変換
        result = _mecab_to_hiragana("その日のこと")
        assert "ひ" in result
        assert "日" not in result
        # 月が綺麗 の 月 も名詞
        result = _mecab_to_hiragana("月が綺麗")
        assert "つき" in result
        assert "月" not in result

    def test_keep_nani_as_kanji(self):
        """何は漢字のまま保持（VOICEVOXが文脈で ナニを/ナンラ/ナンニン 等を使い分ける）。"""
        # 何+を → ナニ読み (MeCab は常に ナン を返すので hiragana だと誤読)
        result = _mecab_to_hiragana("何を言う")
        assert "何" in result
        assert "なん" not in result

    def test_keep_ato_as_kanji(self):
        """後は漢字のまま保持（文境界直後で MeCab が 接尾辞/ゴ と誤解析する対策）。"""
        # 文境界直後の 後から を MeCab は ご と読むが VOICEVOX は漢字なら アト
        result = _mecab_to_hiragana("なぜだろう？後から聞いた")
        assert "後" in result
        assert "ごから" not in result
        # 複合語(午後/最後)は1トークンで処理されひらがな化される（副作用なし）
        assert "ごご" in _mecab_to_hiragana("午後になる")
        assert "さいご" in _mecab_to_hiragana("最後の夜")

    def test_nani_counter_span(self):
        """何+カウンター漢字 (何人/何年/何回) は counter span として保護される。"""
        result = _mecab_to_hiragana("何人いるのか")
        assert "何人" in result
        result = _mecab_to_hiragana("何年経った")
        assert "何年" in result

    def test_itsunomanika_idiom(self):
        """「いつの間にか」は慣用句で 間→ま 固定（MeCab は間→あいだと誤読）。"""
        result = _mecab_to_hiragana("いつの間にか眠っていた")
        assert "いつのまにか" in result
        assert "いつのあいだ" not in result

    def test_shiranu_maani_idiom(self):
        """「知らない間に」「知らぬ間に」も 間→ま 固定。"""
        assert "しらないまに" in _mecab_to_hiragana("知らない間に消えた")
        assert "しらぬまに" in _mecab_to_hiragana("知らぬ間に")

    def test_atto_iu_ma_idiom(self):
        """「あっという間」は 間→ま 固定。"""
        result = _mecab_to_hiragana("あっという間に終わった")
        assert "あっというま" in result

    def test_counter_kurai_approximately(self):
        """カウンター+位 は「〜くらい(約)」。4０代位→4０代くらい で VOICEVOX が
        ヨンジュウダイクライ と読めるようにする（代位→ダイイ 誤読を回避）。"""
        result = _mecab_to_hiragana("4０代位の女の人")
        assert "くらい" in result
        assert "だいい" not in result

    def test_furigana_annotation(self):
        """原文の「漢字（ふりがな）」注記でふりがなが採用される。"""
        # 優曇華（うどんげ）: MeCab は ウドンカ と誤解析するが、ふりがな優先
        result = _mecab_to_hiragana("優曇華（うどんげ）の花")
        assert "うどんげ" in result
        assert "うどんか" not in result

    def test_furigana_okurigana_dedup(self):
        """ふりがな末尾の文字が続きの送り仮名と重複する場合は除去。
        例: 掴（つかみ）み取って → つかみ取って (not つかみみ取って)"""
        result = _mecab_to_hiragana("花を掴（つかみ）み取って")
        assert "つかみみ" not in result
        assert "つかみ" in result

    def test_furigana_standalone_kanji(self):
        """ふりがなが複合語内の複数漢字に対応するケース。"""
        assert "いんごう" in _mecab_to_hiragana("因業（いんごう）なヤツ")
        assert "てんまつ" in _mecab_to_hiragana("顛末（てんまつ）を話す")

    def test_furigana_propagates_to_later_occurrences(self):
        """1回目にふりがなが付いた漢字は、同一テキスト内の後続の漢字にも同じ読みが適用される。"""
        text = "優曇華（うどんげ）の花というものがある。優曇華の花は伝説上の植物だ。"
        result = _mecab_to_hiragana(text)
        # 両方の 優曇華 がふりがな読みに揃う
        assert result.count("うどんげ") >= 2
        assert "うどんか" not in result

    def test_counter_kurai_various(self):
        """代/年/時/分 等、各種カウンター+位 すべて処理される。"""
        assert "くらい" in _mecab_to_hiragana("5時位に帰る")
        assert "くらい" in _mecab_to_hiragana("20年位前")
        assert "くらい" in _mecab_to_hiragana("10人位集まった")

    def test_adjective_kusai_preserved(self):
        """形容詞用法の 臭い はくさい として読まれる（MeCab が正しく判別する）。"""
        result = _mecab_to_hiragana("魚が臭い")
        assert "くさい" in result

    def test_user_config_merges_with_defaults(self, tmp_path, monkeypatch):
        """UIから追加した辞書エントリがデフォルトとマージされて適用される。"""
        import app.config as config_module
        config_path = tmp_path / "config.toml"
        monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)
        config_module.save_config({
            "reading_overrides": {"某": "ぼう"},
            "compound_replacements": {"お爺さん": "おじいさん"},
            "keep_as_kanji": ["葉"],
        })
        # User-added reading override applied
        assert "ぼう" in _mecab_to_hiragana("某氏が来た")
        # User-added compound replacement applied
        assert "おじいさん" in _mecab_to_hiragana("お爺さんは優しい")
        # User-added keep-as-kanji applied
        result = _mecab_to_hiragana("葉が落ちた")
        assert "葉" in result
        # Default 母→漢字保持 still works
        assert "母" in _mecab_to_hiragana("母を見た")
        # Default 私→わたし still works
        assert "わたし" in _mecab_to_hiragana("私は行く")

    def test_omote_reading_override(self):
        """「表」単独は おもて 読み（MeCab デフォルトの ひょう を上書き）。"""
        result = _mecab_to_hiragana("表に装飾はなく")
        assert "おもてに" in result
        assert "ひょう" not in result

    def test_omote_compound_unaffected(self):
        """表 が含まれる複合語(代表/表彰/表面/表現)は1トークンなので override 影響なし。"""
        assert "だいひょう" in _mecab_to_hiragana("代表者")
        assert "ひょうしょう" in _mecab_to_hiragana("表彰式")
        assert "ひょうめん" in _mecab_to_hiragana("表面が滑らか")
        assert "ひょうげん" in _mecab_to_hiragana("自己表現")

    def test_nisokusanmon_idiom(self):
        """四字熟語「二束三文」は にそくさんもん（MeCab分割の にたばさんぶん を回避）。"""
        result = _mecab_to_hiragana("二束三文にしかならず")
        assert "にそくさんもん" in result
        assert "にたばさんぶん" not in result

    def test_miyou_mimane_idiom(self):
        """「見様見真似」は みようみまね（MeCab分割の みさまみまね を回避）。"""
        result = _mecab_to_hiragana("見様見真似で発音した")
        assert "みようみまね" in result
        assert "みさまみまね" not in result

    def test_kokoro_nashi_idiom(self):
        """「心做しか」は こころなしか（做が漢字のまま残るのを回避）。"""
        result = _mecab_to_hiragana("心做しか違って見えた")
        assert "こころなしか" in result
        assert "做" not in result

    def test_ninin_sankyaku_idiom(self):
        """「二人三脚」は ににんさんきゃく（フタリ+サンキャク を回避）。"""
        result = _mecab_to_hiragana("二人三脚で歩く")
        assert "ににんさんきゃく" in result
        assert "ふたりさんきゃく" not in result

    def test_futari_gumi_rendaku(self):
        """「二人組」は ふたりぐみ（counter span 保護で漢字保持され ニニングミ
        と読まれるのを回避）。"""
        result = _mecab_to_hiragana("二人組の男性")
        assert "ふたりぐみ" in result
        assert "二人組" not in result

    def test_gyuugyuu_zume_rendaku(self):
        """「ぎゅうぎゅう詰め」は連濁で づめ（MeCab は 詰め→つめ で連濁を起こさない）。"""
        result = _mecab_to_hiragana("ぎゅうぎゅう詰めの満員電車")
        assert "ぎゅうぎゅうづめ" in result
        assert "ぎゅうぎゅうつめ" not in result

    def test_nihongo_compound(self):
        """「日本語」は にほんご（MeCab デフォルトの にっぽんご を回避）。"""
        result = _mecab_to_hiragana("日本語を知らない")
        assert "にほんご" in result
        assert "にっぽんご" not in result

    def test_nihon_reading_override(self):
        """「日本」は にほん（MeCab デフォルト ニッポン を回避）。日本酒/海/国/一 等で
        日本 が独立トークン化される複合に効く。"""
        # 日本酒/海/国 はMeCabで 日本+suffix なので surface 「日本」override で にほん 化
        assert "にほんしゅ" in _mecab_to_hiragana("日本酒を飲む")
        assert "にほんかい" in _mecab_to_hiragana("日本海の波")
        assert "にほんいち" in _mecab_to_hiragana("日本一の山")
        assert "にほんにんぎょう" in _mecab_to_hiragana("日本人形を飾る")

    def test_nihon_jin_compound(self):
        """国名+人 は じん（にん にならない）。"""
        assert "にほんじん" in _mecab_to_hiragana("日本人の友人")
        assert "がいこくじん" in _mecab_to_hiragana("外国人と話す")
        assert "ちゅうごくじん" in _mecab_to_hiragana("中国人観光客")
        assert "かんこくじん" in _mecab_to_hiragana("韓国人留学生")

    def test_nihon_juu_compound(self):
        """「日本中」は にほんじゅう。"""
        result = _mecab_to_hiragana("日本中で話題になった")
        assert "にほんじゅう" in result
        assert "にっぽんちゅう" not in result

    def test_nandemo_compound(self):
        """「何でも」は なんでも 展開（VOICEVOX が ナニデモ と誤読するのを回避）。"""
        result = _mecab_to_hiragana("何でもいいから早く")
        assert "なんでも" in result
        # 他の「何+助詞」(何か/何が/何と) は VOICEVOX で正しく読めるので
        # 漢字保持のまま残す
        assert "何か" in _mecab_to_hiragana("何かが動いた")

    def test_toko_ni_tsuku_hiragana(self):
        """ひらがな「床につき/床につく」も就寝慣用句として とこ 読み。"""
        assert "とこにつき" in _mecab_to_hiragana("床につき眠りかけた")
        assert "とこにつく" in _mecab_to_hiragana("早めに床につく")
        assert "とこについて" in _mecab_to_hiragana("床について寝た")

    def test_heyajuu_compound(self):
        """「部屋中」は へやじゅう (中=throughout)。MeCab の へやちゅう は誤読、
        さらに VOICEVOX が へ を助詞 え と誤解析して 部屋→夜 に化けるのを回避。"""
        result = _mecab_to_hiragana("吠えながら部屋中駆けずり回り")
        assert "へやじゅう" in result
        assert "へやちゅう" not in result

    def test_shijuukunichi_idiom(self):
        """「四十九日」は仏教の慣用読みで しじゅうくにち。VOICEVOX が漢字で
        ヨンジュウクニチ と読んでしまうのを回避。"""
        result = _mecab_to_hiragana("四十九日にまにあう")
        assert "しじゅうくにち" in result
        assert "四十九日" not in result

    def test_keep_kanagawa_kanji(self):
        """神奈川 は漢字保持。「かながわけん」が VOICEVOX で カナガ+ワケン と
        誤分割されるのを回避。"""
        result = _mecab_to_hiragana("そうしきは神奈川県でおこなわれた")
        assert "神奈川" in result
        assert "かながわ" not in result

    def test_keep_shiken_kanji(self):
        """試験 は漢字保持。動物試験場 が ドオブツ+シ+ケンジョオ と妙に分解
        されるのを回避。"""
        result = _mecab_to_hiragana("動物試験場をおとずれた")
        assert "試験" in result
        assert "しけん" not in result

    def test_keep_iru_verb_kanji(self):
        """入る は漢字保持。「ドアをあけて入ると」で VOICEVOX が アケテ+ワ+イル と
        は→ワ誤解析するのを回避。"""
        result = _mecab_to_hiragana("ドアをあけて入る")
        assert "入る" in result
        assert "はいる" not in result

    def test_keep_kami_kanji(self):
        """髪 は漢字保持。「見ると髪の長い」で VOICEVOX が ミルトカ+ミ と
        と+か(列挙助詞)+み に誤分割するのを回避。"""
        result = _mecab_to_hiragana("見ると髪の長い女性")
        assert "髪" in result
        assert "かみ" not in result

    def test_counter_tai_preserved(self):
        """数+体 (霊体助数詞) は漢字保持。MeCab分割の したい(=死体同音) を回避。"""
        result = _mecab_to_hiragana("一体の時もあれば四体いた時もあった")
        assert "四体" in result
        assert "したい" not in result

    def test_keep_hakike_kanji(self):
        """「吐き気」は漢字保持。「と吐き気」「に吐き気」が VOICEVOX で
        ト・ワキケ と読まれる(は→ワ誤解析)のを回避。"""
        result = _mecab_to_hiragana("頭痛と吐き気がする")
        assert "吐き気" in result
        assert "はきけ" not in result

    def test_hanashi_dai_separator(self):
        """「話 + 大〜」(話大好き/話大事) は VOICEVOX が 話題(ワダイ) と
        誤合成するため読点を挿入。"""
        result = _mecab_to_hiragana("怖い話大好きなんだよね")
        # 話 と だいすき の境界に読点が入っている
        assert "話、" in result

    def test_keep_hanare_kanji(self):
        """「離れ/離れる」は漢字保持。ひらがな「はなれ」を助詞「に/で」の後に置くと
        VOICEVOX が は→ワ と助詞誤解析する (ニワ・ナレ)。"""
        # 連用形 (離れて/離れた)
        assert "離れ" in _mecab_to_hiragana("遠くに離れて")
        assert "離れ" in _mecab_to_hiragana("そこから離れた")
        # 終止形/連体形
        assert "離れる" in _mecab_to_hiragana("ふと離れる")

    def test_itten_bari_idiom(self):
        """「一点張り」は いってんばり (MeCab分割の いちてんばり を回避)。"""
        result = _mecab_to_hiragana("「何でもいい」の一点張りだった")
        assert "いってんばり" in result
        assert "いちてんばり" not in result

    def test_butsuma_context(self):
        """「仏間」は文脈に関わらず ぶつま。「仏間で」では MeCab が 仏(フツ)+間(カン)
        に分解して ふつかん になるのを回避。"""
        # 単独 / 「に」前置 / 「で」前置 / 「の」後続 すべて ぶつま
        for ctx in ["仏間", "仏間で", "仏間に", "奥の仏間で", "仏間の仏壇"]:
            result = _mecab_to_hiragana(ctx)
            assert "ぶつま" in result, f"failed for {ctx!r}: got {result!r}"
            assert "ふつかん" not in result

    def test_newline_preserved(self):
        """改行は保持される（MeCab は \\n を落とすので明示的に分割処理）。"""
        text = "今日は晴れ。\n明日は雨。"
        result = _mecab_to_hiragana(text)
        assert "\n" in result
        # 改行位置が保持される (各行が独立に変換)
        lines = result.split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("きょうわはれ")
        assert lines[1].endswith("あめ。")

    def test_multiline_paragraph(self):
        """複数段落のテキストの段落構造が壊れない。"""
        text = "一段落目です。\n\n二段落目です。\n\n三段落目です。"
        result = _mecab_to_hiragana(text)
        assert result.count("\n") == text.count("\n")

    def test_process_text_ai_proofread_off_uses_mecab(self, monkeypatch):
        """use_ai_proofread=False の場合、AI校正関数は呼ばれない。"""
        from app.services import text_processor as tp
        called = {"ai": False}

        def fake_ai(processed, raw):
            called["ai"] = True
            return "MODIFIED"

        monkeypatch.setattr(tp, "_ai_proofread", fake_ai)
        result = tp.process_text("今日は晴れ。", use_ai_proofread=False)
        assert called["ai"] is False
        assert "きょうわはれ" in result

    def test_process_text_ai_proofread_on_invokes_proofreader(self, monkeypatch):
        """use_ai_proofread=True の場合、_ai_proofread が呼ばれて結果が採用される。"""
        from app.services import text_processor as tp
        called = {"ai": False, "args": None}

        def fake_ai(processed, raw):
            called["ai"] = True
            called["args"] = (processed, raw)
            return "AICORRECTED"

        monkeypatch.setattr(tp, "_ai_proofread", fake_ai)
        result = tp.process_text("今日は晴れ。", use_ai_proofread=True)
        assert called["ai"] is True
        assert result == "AICORRECTED"
        # AI校正には MeCab結果と原文の両方が渡される
        assert called["args"][1] == "今日は晴れ。"

    def test_ai_proofread_nfc_normalizes_decomposed_dakuten(self, tmp_path, monkeypatch):
        """NFD分解された ば (は+結合用濁点) は NFC で ば に再合成される。
        これは Gemini の出力で起きやすい破損パターンの正攻法な修復。"""
        from app.services import text_processor as tp

        import app.config as config_module
        monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "c.toml")
        config_module.save_config({
            "text_model": "gemini-2.5-flash",
            "ai_proofread_prompt": "{raw}|{processed}",
        })

        # は + 結合用濁点 (U+3099) — 視覚的には ば
        decomposed = "きけ" + "は" + "゙" + "それわ"
        class FakeResp:
            text = decomposed
        class FakeModels:
            def generate_content(self, model, contents):
                return FakeResp()
        class FakeClient:
            models = FakeModels()
        monkeypatch.setattr(tp, "get_gemini_text", lambda: FakeClient())

        result = tp._ai_proofread("きけばそれわ", "聞けばそれは")
        # NFC で ば に再合成され、AI出力が採用される
        assert result == "きけばそれわ"
        assert "゙" not in result

    def test_gemini_retry_on_transient_error(self, monkeypatch):
        """Gemini が 500 INTERNAL を返したら指数バックオフでリトライする。"""
        from app.services import text_processor as tp
        # sleep をスタブ化(テスト高速化)
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)

        attempts = {"count": 0}

        class FakeResp:
            text = "OK"

        class FakeModels:
            def generate_content(self, model, contents):
                attempts["count"] += 1
                if attempts["count"] < 3:
                    raise RuntimeError("500 INTERNAL. server error")
                return FakeResp()

        class FakeClient:
            models = FakeModels()

        result = tp._gemini_generate_with_retry(FakeClient(), "gemini-2.5-flash", "p")
        assert attempts["count"] == 3
        assert result.text == "OK"

    def test_gemini_retry_gives_up_after_max(self, monkeypatch):
        """一過性エラーが最大試行数まで続いたら最後の例外を送出。"""
        from app.services import text_processor as tp
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)

        class FakeModels:
            def generate_content(self, model, contents):
                raise RuntimeError("503 UNAVAILABLE")

        class FakeClient:
            models = FakeModels()

        import pytest as _pytest
        with _pytest.raises(RuntimeError, match="503"):
            tp._gemini_generate_with_retry(FakeClient(), "gemini-2.5-flash", "p", max_attempts=2)

    def test_gemini_retry_does_not_retry_permanent_error(self, monkeypatch):
        """永続的エラー(認証失敗等)はリトライしない。"""
        from app.services import text_processor as tp
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)
        attempts = {"count": 0}

        class FakeModels:
            def generate_content(self, model, contents):
                attempts["count"] += 1
                raise RuntimeError("401 UNAUTHENTICATED")

        class FakeClient:
            models = FakeModels()

        import pytest as _pytest
        with _pytest.raises(RuntimeError):
            tp._gemini_generate_with_retry(FakeClient(), "gemini-2.5-flash", "p", max_attempts=4)
        assert attempts["count"] == 1  # 1回でやめている

    def test_ai_proofread_rejects_orphan_dakuten(self, tmp_path, monkeypatch):
        """NFC化しても孤立した結合用濁点が残る場合(前文字が結合不能)は破損とみなす。"""
        from app.services import text_processor as tp

        import app.config as config_module
        monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "c.toml")
        config_module.save_config({
            "text_model": "gemini-2.5-flash",
            "ai_proofread_prompt": "{raw}|{processed}",
        })

        # わ + 結合用濁点 — わ゙ は規定の合成済み文字がないので NFC でも残る
        orphan = "きけ" + "わ" + "゙" + "それわ"
        class FakeResp:
            text = orphan
        class FakeModels:
            def generate_content(self, model, contents):
                return FakeResp()
        class FakeClient:
            models = FakeModels()
        monkeypatch.setattr(tp, "get_gemini_text", lambda: FakeClient())

        result = tp._ai_proofread("きけばそれわ", "聞けばそれは")
        # 孤立濁点ありなのでフォールバック (元入力を返す)
        assert result == "きけばそれわ"

    def test_ai_proofread_rejects_size_explosion(self, tmp_path, monkeypatch):
        """AI出力が大幅に長くなった場合(>30%)もフォールバック。"""
        from app.services import text_processor as tp
        import app.config as config_module
        monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "c.toml")
        config_module.save_config({
            "text_model": "gemini-2.5-flash",
            "ai_proofread_prompt": "{raw}|{processed}",
        })
        class FakeResp:
            text = "あ" * 100  # 元の入力 "ab" の50倍
        class FakeModels:
            def generate_content(self, model, contents):
                return FakeResp()
        class FakeClient:
            models = FakeModels()
        monkeypatch.setattr(tp, "get_gemini_text", lambda: FakeClient())

        result = tp._ai_proofread("ab", "あいうえお")
        assert result == "ab"

    def test_ai_proofread_uses_configured_prompt(self, tmp_path, monkeypatch):
        """設定の ai_proofread_prompt がそのまま Gemini に渡される。"""
        from app.services import text_processor as tp
        import app.config as config_module
        monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "c.toml")
        config_module.save_config({
            "text_model": "gemini-2.5-flash",
            "ai_proofread_prompt": "CUSTOM:{raw}/{processed}",
        })
        captured = {}
        class FakeResp:
            text = "きょうわはれ。"
        class FakeModels:
            def generate_content(self, model, contents):
                captured["prompt"] = contents
                return FakeResp()
        class FakeClient:
            models = FakeModels()
        monkeypatch.setattr(tp, "get_gemini_text", lambda: FakeClient())

        tp._ai_proofread("きょうわはれ。", "今日は晴れ。")
        assert captured["prompt"] == "CUSTOM:今日は晴れ。/きょうわはれ。"

    def test_process_text_ai_proofread_failure_falls_back(self, monkeypatch):
        """AI校正が例外を投げた場合、MeCab結果にフォールバック。"""
        from app.services import text_processor as tp

        def fake_ai_raises(processed, raw):
            raise RuntimeError("API down")

        monkeypatch.setattr(tp, "_ai_proofread", fake_ai_raises)
        result = tp.process_text("今日は晴れ。", use_ai_proofread=True)
        # MeCab結果が返る
        assert "きょうわはれ" in result

    def test_inflected_verb_reading(self):
        """活用形の動詞はsurface形の読みを使う（レンマ形ではない）。"""
        # 覚ました → さました (not さまする)
        assert _mecab_to_hiragana("覚ました") == "さました"
        # 閉ざされる → とざされる (not とざすれる)
        assert _mecab_to_hiragana("閉ざされる") == "とざされる"
        # 積もった → つもった (not つもるた)
        assert _mecab_to_hiragana("積もった") == "つもった"
        # 寝ていた → ねていた (not ねるているた)
        assert _mecab_to_hiragana("寝ていた") == "ねていた"
        # 珍しくない → めずらしくない (not めずらしいない)
        assert _mecab_to_hiragana("珍しくない") == "めずらしくない"
