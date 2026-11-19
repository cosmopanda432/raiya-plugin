import unittest

from raiya_plugin import report

FULL = """Verdict: ok

範囲外: 本番 DB での確認はしていない
省略: 無し
残した仮のもの: 無し
"""


class ParseFieldsTest(unittest.TestCase):
    def test_all_three_fields(self):
        self.assertEqual(report.missing_fields(FULL), [])
        fields = report.parse_fields(FULL)
        self.assertEqual(fields["範囲外"], "本番 DB での確認はしていない")
        self.assertEqual(fields["省略"], "無し")

    def test_missing_fields_in_order(self):
        self.assertEqual(report.missing_fields("done."), ["範囲外", "省略", "残した仮のもの"])
        self.assertEqual(report.missing_fields("省略: 無し"), ["範囲外", "残した仮のもの"])
        self.assertEqual(report.missing_fields(""), ["範囲外", "省略", "残した仮のもの"])
        self.assertEqual(report.missing_fields(None), ["範囲外", "省略", "残した仮のもの"])

    def test_decorated_and_long_labels(self):
        text = (
            "## 範囲外 / できなかったこと:\n- a\n- b\n"
            "- **省略したもの**: 無し\n"
            "**残した仮のもの:** tmp/x.py\n"
        )
        fields = report.parse_fields(text)
        self.assertEqual(fields["範囲外"], "- a\n- b")
        self.assertEqual(fields["省略"], "無し")
        self.assertEqual(fields["残した仮のもの"], "tmp/x.py")

    def test_fullwidth_colon(self):
        self.assertEqual(report.parse_fields("範囲外：無し")["範囲外"], "無し")

    def test_label_must_start_the_line(self):
        self.assertEqual(
            report.missing_fields("今回は範囲外: の話はしない\n文中の省略: も欄ではない"),
            ["範囲外", "省略", "残した仮のもの"],
        )

    def test_label_without_colon_is_not_a_field(self):
        self.assertIn("省略", report.missing_fields("省略したものは特にありません"))

    def test_trailing_text_after_a_blank_line_is_not_content(self):
        text = "残した仮のもの: 無し\n\n以上です。"
        self.assertEqual(report.parse_fields(text)["残した仮のもの"], "無し")

    def test_code_fence_ends_a_field_and_fenced_fields_count(self):
        text = "```\n範囲外: 無し\n省略: 無し\n残した仮のもの: 無し\n```\n"
        self.assertEqual(report.missing_fields(text), [])
        self.assertEqual(report.parse_fields(text)["残した仮のもの"], "無し")

    def test_bullet_styles(self):
        text = "1. 範囲外: 無し\n・省略: 無し\n+ 残した仮のもの: 無し\n2) 範囲外: x"
        fields = report.parse_fields(text)
        self.assertEqual(report.missing_fields(text), [])
        self.assertEqual(fields["範囲外"], "x")

    def test_prose_starting_with_a_label_is_not_a_field(self):
        text = "範囲外のファイルには触れていない: 確認済み\n省略記法を使った: x\n残した仮のものは無い: 本当"
        self.assertEqual(report.missing_fields(text), ["範囲外", "省略", "残した仮のもの"])

    def test_two_labels_on_one_line_count_once(self):
        self.assertEqual(report.missing_fields("範囲外: 無し 省略: 無し"), ["省略", "残した仮のもの"])

    def test_heading_ends_a_field(self):
        text = "残した仮のもの: 無し\n## 次の節\nstub を残した"
        self.assertEqual(report.parse_fields(text)["残した仮のもの"], "無し")

    def test_last_occurrence_wins(self):
        text = "残した仮のもの: a.py\n残した仮のもの: 無し"
        self.assertEqual(report.parse_fields(text)["残した仮のもの"], "無し")


class IsNoneTest(unittest.TestCase):
    def test_none_words(self):
        for s in ("無し", "なし", " 無し。", "**無し**", "None", "", "- 無し", "特に無し", "ない", "N/A"):
            self.assertTrue(report.is_none(s), s)

    def test_first_line_decides(self):
        self.assertTrue(report.is_none("無し\n以上です。"))
        self.assertFalse(report.is_none("tmp/x.py\n無し"))

    def test_content_is_not_none(self):
        for s in ("tmp/x.py", "無し (ただし tmp/x.py)", "stub を 1 つ", "無しではない"):
            self.assertFalse(report.is_none(s), s)


if __name__ == "__main__":
    unittest.main()
