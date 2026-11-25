import unittest

from translation.segmentation import segment_text, validate_segmentation


class SegmentationTests(unittest.TestCase):
    def test_short_article_returns_one_exact_segment(self):
        source = "第一段。\n\n第二段。\n"
        result = segment_text(source, min_chars=20, max_chars=100)
        self.assertEqual(len(result.segments), 1)
        self.assertEqual(result.segments[0].start, 0)
        self.assertEqual(result.segments[0].end, len(source))
        self.assertEqual(result.segments[0].text, source)

    def test_long_paragraphs_are_packed_below_maximum(self):
        source = "\n\n".join(
            f"第 {index} 段。" + ("内容" * 50)
            for index in range(12)
        )
        result = segment_text(source, min_chars=120, max_chars=220)
        self.assertGreater(len(result.segments), 1)
        self.assertTrue(
            all(segment.char_count <= 220 for segment in result.segments)
        )
        self.assertEqual(
            "".join(segment.text for segment in result.segments),
            source,
        )
        validate_segmentation(result)

    def test_sentence_boundaries_are_preferred(self):
        sentences = "".join(
            f"第{index}句事实完整且包含数字 {index}。" for index in range(40)
        )
        result = segment_text(sentences, min_chars=80, max_chars=120)
        for segment in result.segments[:-1]:
            self.assertTrue(segment.text.rstrip().endswith(("。", "！", "？")))

    def test_unpunctuated_text_is_hard_split_without_loss(self):
        source = "甲" * 350
        result = segment_text(source, min_chars=80, max_chars=100)
        self.assertEqual(
            [segment.char_count for segment in result.segments],
            [100, 100, 100, 50],
        )
        self.assertEqual(
            "".join(segment.text for segment in result.segments),
            source,
        )

    def test_positions_are_contiguous_and_metadata_is_complete(self):
        source = "\n\n".join(["甲" * 90, "乙" * 90, "丙" * 90])
        result = segment_text(source, min_chars=100, max_chars=180)
        cursor = 0
        for index, segment in enumerate(result.segments):
            self.assertEqual(segment.index, index)
            self.assertEqual(segment.total, len(result.segments))
            self.assertEqual(segment.start, cursor)
            cursor = segment.end
            payload = segment.as_dict()
            self.assertEqual(payload["ordinal"], index + 1)
            self.assertEqual(payload["charCount"], segment.char_count)
        self.assertEqual(cursor, len(source))

    def test_empty_text_is_rejected(self):
        for value in ("", " \n\t "):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "不能为空"):
                    segment_text(value)

    def test_invalid_limits_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "正整数"):
            segment_text("正文", min_chars=0)
        with self.assertRaisesRegex(ValueError, "不能大于"):
            segment_text("正文", min_chars=200, max_chars=100)


if __name__ == "__main__":
    unittest.main()
