import unittest

from raiya_plugin import marks


class HasMarkTest(unittest.TestCase):
    def test_marks(self):
        for s in ("# TODO: x", "FIXME", "x = 1  # XXX", "a Stub here", "stubs", "ここは仮置き",
                  "stubを置いた", "TODO残し", "仮のstub関数"):
            self.assertTrue(marks.has_mark(s), s)

    def test_not_marks(self):
        for s in ("todo list", "mastodon", "stubborn", "make_stub()", "TODOS", "XXXL", "return 1", ""):
            self.assertFalse(marks.has_mark(s), s)


class ParseGrepTest(unittest.TestCase):
    def test_records(self):
        out = "src/a.py\x004\x00    # TODO: 後で直す\n日本語 名.py\x0012\x00x = None  # stub\n"
        self.assertEqual(
            marks.parse_grep(out),
            [("src/a.py", 4, "# TODO: 後で直す"), ("日本語 名.py", 12, "x = None  # stub")],
        )

    def test_coarse_hits_are_filtered(self):
        # git grep -i は todo・stubborn も返す。has_mark で落とす。
        out = "a.py\x001\x00todo list\na.py\x002\x00stubborn\na.py\x003\x00# FIXME\n"
        self.assertEqual(marks.parse_grep(out), [("a.py", 3, "# FIXME")])

    def test_text_may_contain_nul_separator_free_colons(self):
        out = "a.py\x007\x00d = {'k': 1}  # TODO: a: b\n"
        self.assertEqual(marks.parse_grep(out), [("a.py", 7, "d = {'k': 1}  # TODO: a: b")])

    def test_newline_in_path(self):
        out = "a.py\x001\x00# TODO a\nwe\nird.py\x001\x00# TODO nl\n"
        self.assertEqual(
            marks.parse_grep(out), [("a.py", 1, "# TODO a"), ("we\nird.py", 1, "# TODO nl")]
        )

    def test_colored_output_would_not_parse(self):
        # 色コードが混ざると行番号が数字でなくなる。hooks 側が色を切る (test_hooks)。
        self.assertEqual(marks.parse_grep("a.py\x00\x1b[32m1\x1b[m\x00# TODO\n"), [])

    def test_malformed_and_empty(self):
        self.assertEqual(marks.parse_grep(""), [])
        self.assertEqual(marks.parse_grep("no separators TODO\nBinary file x matches\n"), [])


class NewMarksTest(unittest.TestCase):
    def test_added_line_is_new(self):
        current = [("a.py", 9, "# TODO old"), ("a.py", 20, "# TODO new")]
        self.assertEqual(marks.new_marks(["# TODO old"], current), [("a.py", 20, "# TODO new")])

    def test_duplicate_lines_are_counted(self):
        current = [("a.py", 1, "# TODO"), ("a.py", 5, "# TODO")]
        self.assertEqual(marks.new_marks(["# TODO"], current), [("a.py", 5, "# TODO")])

    def test_moved_line_is_not_new(self):
        self.assertEqual(marks.new_marks(["# TODO"], [("b.py", 1, "# TODO")]), [])

    def test_edited_old_mark_line_is_not_new(self):
        self.assertEqual(marks.new_marks(["x = 1  # TODO old"], [("a.py", 1, "x = 2  # TODO old")]), [])

    def test_removed_marks_are_not_new(self):
        self.assertEqual(marks.new_marks(["# TODO a", "# TODO b"], [("a.py", 1, "# TODO a")]), [])

    def test_report_is_capped_at_the_growth(self):
        # 元の 1 行を直し、1 行足した: 増えたのは 1 行なので 1 件だけ返す。
        current = [("a.py", 1, "x = 2  # TODO old"), ("a.py", 2, "# FIXME new")]
        self.assertEqual(len(marks.new_marks(["x = 1  # TODO old"], current)), 1)


if __name__ == "__main__":
    unittest.main()
