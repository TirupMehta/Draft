"""Regression tests for Draft's pure helpers (stdlib only + numpy).

Run with zero new dependencies:
    py -3.13 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import draft


class CleanTextTests(unittest.TestCase):
    def test_basic_cleanup(self):
        self.assertEqual(draft.clean_text("hello,world"), "Hello, world ")

    def test_space_before_punct(self):
        self.assertEqual(draft.clean_text("hi , there ."), "Hi, there. ")

    def test_empty(self):
        self.assertEqual(draft.clean_text(""), "")
        self.assertEqual(draft.clean_text("   "), "")

    def test_no_trailing_space(self):
        self.assertEqual(draft.clean_text("hello", trailing_space=False), "Hello")

    def test_capitalizes_first_letter(self):
        self.assertTrue(draft.clean_text("hello").startswith("H"))

    def test_sentence_capital_after_period(self):
        out = draft.clean_text("hello. world")
        self.assertIn("World", out)

    def test_preserves_paragraph_break(self):
        out = draft.clean_text("hello\n\nworld")
        self.assertIn("\n\n", out)
        self.assertTrue(out.startswith("Hello"))
        self.assertIn("World", out)

    def test_preserves_single_line_break(self):
        out = draft.clean_text("hello\nworld")
        self.assertIn("\n", out)
        self.assertNotIn("helloworld", out.replace("\n", ""))

    def test_voice_command_breaks_survive_cleanup(self):
        kind, payload = draft.apply_voice_commands("hello new paragraph world")
        self.assertEqual(kind, "text")
        cleaned = draft.clean_text(payload)
        self.assertIn("\n\n", cleaned)

    def test_full_pipeline_keeps_paragraph(self):
        # voice commands -> corrections -> fillers -> cleanup, as workers run it
        kind, payload = draft.apply_voice_commands("hello new paragraph world")
        t = draft.apply_corrections(payload, [])
        t = draft.remove_fillers(t, "en")
        self.assertEqual(draft.clean_text(t), "Hello\n\nWorld ")

    def test_no_trailing_space_with_break(self):
        out = draft.clean_text("hello\n\nworld", trailing_space=False)
        self.assertIn("\n\n", out)
        self.assertFalse(out.endswith(" "))

    def test_standalone_i_capitalized(self):
        self.assertEqual(draft.clean_text("he said i am here"),
                         "He said I am here ")

    def test_i_contraction(self):
        out = draft.clean_text("he said i'm here")
        self.assertIn("I'm", out)

    def test_i_not_english_untouched(self):
        # "i" is a real word in e.g. Italian — mid-sentence it stays lower
        out = draft.clean_text("e i cani", lang="it")
        self.assertIn(" i ", out)

    def test_does_not_touch_real_words(self):
        self.assertIn("mber", draft.clean_text("amber alert"))


class VoiceCommandTests(unittest.TestCase):
    def test_scratch(self):
        kind, payload = draft.apply_voice_commands("scratch that")
        self.assertEqual((kind, payload), ("scratch", ""))

    def test_paragraph(self):
        kind, payload = draft.apply_voice_commands("new paragraph")
        self.assertEqual((kind, payload), ("paste", "\n\n"))

    def test_newline(self):
        kind, payload = draft.apply_voice_commands("  New Line. ")
        self.assertEqual((kind, payload), ("paste", "\n"))

    def test_newline_one_word(self):
        kind, payload = draft.apply_voice_commands("newline")
        self.assertEqual((kind, payload), ("paste", "\n"))

    def test_scratch_synonyms(self):
        for phrase in ("scratch it", "delete that", "forget that",
                       "scratch that?"):
            kind, _p = draft.apply_voice_commands(phrase)
            self.assertEqual(kind, "scratch", phrase)

    def test_paragraph_question_mark(self):
        kind, payload = draft.apply_voice_commands("new paragraph?")
        self.assertEqual((kind, payload), ("paste", "\n\n"))

    def test_inline_commands(self):
        kind, out = draft.apply_voice_commands("hello new paragraph world")
        self.assertEqual(kind, "text")
        self.assertIn("\n\n", out)

    def test_plain_text_passthrough(self):
        kind, out = draft.apply_voice_commands("hello world")
        self.assertEqual(kind, "text")
        self.assertIn("hello", out)


class CorrectionTests(unittest.TestCase):
    def test_taught_word_replaced(self):
        out = draft.apply_corrections("tiroop mehta", [["tiroop mehta", "Tirup Mehta"]])
        self.assertEqual(out, "Tirup Mehta")

    def test_longest_match_first(self):
        pairs = [["tirup", "Tirup"], ["tirup mehta", "Tirup Mehta"]]
        out = draft.apply_corrections("tirup mehta", pairs)
        self.assertEqual(out, "Tirup Mehta")

    def test_case_insensitive(self):
        out = draft.apply_corrections("TIROOP", [["tiroop", "Tirup"]])
        self.assertEqual(out, "Tirup")

    def test_word_boundaries(self):
        # "tirup" inside another word must not be replaced
        out = draft.apply_corrections("atirupx", [["tirup", "Tirup"]])
        self.assertEqual(out, "atirupx")

    def test_smart_dots(self):
        out = draft.apply_corrections("tirup dot in", [], smart_dots=True)
        self.assertEqual(out, "tirup.in")

    def test_smart_dots_off(self):
        out = draft.apply_corrections("tirup dot in", [], smart_dots=False)
        self.assertEqual(out, "tirup dot in")

    def test_smart_dots_domain_and_version(self):
        self.assertEqual(
            draft.apply_corrections("gmail dot com", [], True), "gmail.com")
        self.assertEqual(
            draft.apply_corrections("version 2 dot 0", [], True), "version 2.0")
        self.assertEqual(
            draft.apply_corrections("a dot b dot c", [], True), "a.b.c")

    def test_smart_dots_ignores_plain_phrase(self):
        # "dot" as a noun must not glue ordinary words together
        self.assertEqual(
            draft.apply_corrections("the dot product", [], True),
            "the dot product")


class FillerTests(unittest.TestCase):
    def test_removes_hesitations(self):
        out = draft.remove_fillers("um hello uh world")
        self.assertNotIn("um", out.split())
        self.assertIn("hello", out)

    def test_keeps_real_a(self):
        # "ah+" needs the h; a lone "a" must survive
        self.assertEqual(draft.remove_fillers("a dog"), "a dog")

    def test_keeps_words_containing_filler_letters(self):
        self.assertIn("amber", draft.remove_fillers("amber alert"))

    def test_cleans_orphan_commas(self):
        self.assertEqual(draft.remove_fillers("um, uh, hello"), "hello")

    def test_mid_sentence_fillers(self):
        self.assertEqual(draft.remove_fillers("hello, um, world"),
                         "hello, world")

    def test_elongated_fillers(self):
        self.assertEqual(draft.remove_fillers("ummm hello"), "hello")

    def test_german_er_survives(self):
        # "er" is the German pronoun "he" — never a filler there
        self.assertEqual(draft.remove_fillers("er kommt", lang="de"),
                         "er kommt")
        self.assertEqual(draft.remove_fillers("um den see", lang="de"),
                         "um den see")

    def test_german_fillers_removed(self):
        self.assertEqual(draft.remove_fillers("ähm hallo", lang="de"), "hallo")

    def test_filler_lang_resolution(self):
        self.assertEqual(draft.filler_lang("de", "auto"), "de")
        self.assertEqual(draft.filler_lang("", "fr"), "fr")
        self.assertEqual(draft.filler_lang("", "auto"), "en")


class SegmentFilterTests(unittest.TestCase):
    class _Seg:
        def __init__(self, text, no_speech_prob=0.0):
            self.text = text
            self.no_speech_prob = no_speech_prob

    def test_drops_high_no_speech(self):
        segs = [self._Seg("hello", 0.1), self._Seg("Thank you", 0.95)]
        self.assertEqual(draft._join_pro_segments(segs), "hello")

    def test_keeps_confident(self):
        segs = [self._Seg("hello", 0.0), self._Seg("world", 0.2)]
        self.assertEqual(draft._join_pro_segments(segs), "hello world")

    def test_all_dropped_gives_empty(self):
        self.assertEqual(draft._join_pro_segments([self._Seg("x", 0.99)]), "")


class HotwordTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(draft.parse_hotwords("a, b  c"), "a b c")
        self.assertIsNone(draft.parse_hotwords(""))
        self.assertIsNone(draft.parse_hotwords("   ,  "))

    def test_list(self):
        self.assertEqual(draft.hotword_list("a, b c"), ["a", "b", "c"])
        self.assertEqual(draft.hotword_list(""), [])

    def test_guidance_merges_hotwords_and_taught(self):
        guide = draft.EngineManager.guidance("alice",
                                             [["heard bob", "Bob Jones"]])
        self.assertIn("alice", guide.split())
        self.assertIn("Bob", guide.split())
        self.assertIn("Jones", guide.split())

    def test_guidance_dedups(self):
        guide = draft.EngineManager.guidance("alice alice",
                                             [["x", "alice"]])
        self.assertEqual(guide.split().count("alice"), 1)

    def test_guidance_none_when_empty(self):
        self.assertIsNone(draft.EngineManager.guidance("", []))


class LangTests(unittest.TestCase):
    def test_auto_is_none(self):
        self.assertIsNone(draft.lang_or_none("auto"))
        self.assertIsNone(draft.lang_or_none(""))

    def test_explicit(self):
        self.assertEqual(draft.lang_or_none("en"), "en")
        self.assertEqual(draft.lang_or_none("de", pro_engine=True), "de")


class UptimeTests(unittest.TestCase):
    def test_mm_ss(self):
        self.assertEqual(draft.fmt_uptime(65), "01:05")

    def test_hh_mm_ss(self):
        self.assertEqual(draft.fmt_uptime(3665), "01:01:05")


class HotkeyParseTests(unittest.TestCase):
    def test_generic_ctrl_covers_both_sides(self):
        names = draft._names_for("ctrl")
        self.assertIn("left ctrl", names)
        self.assertIn("right ctrl", names)

    def test_side_specific(self):
        self.assertEqual(draft._names_for("right ctrl"), {"right ctrl"})

    def test_chord(self):
        spec = draft._parse_key("ctrl+alt+d")
        self.assertEqual(spec["combo"], "ctrl+alt+d")
        self.assertIn("d", spec["finals"])

    def test_single(self):
        spec = draft._parse_key("f9")
        self.assertIsNone(spec["combo"])
        self.assertIn("f9", spec["finals"])


@unittest.skipUnless(draft._np() is not None, "numpy required")
class AudioTests(unittest.TestCase):
    def test_remove_dc(self):
        import numpy as np

        x = np.ones(100, dtype=np.float32) * 0.5
        out = draft.remove_dc(x)
        self.assertAlmostEqual(float(abs(out).max()), 0.0, places=5)

    def test_auto_gain_boosts_quiet(self):
        import numpy as np

        x = (np.ones(1000, dtype=np.float32) * 0.05)
        out = draft.auto_gain(x)
        self.assertGreater(float(abs(out).max()), 0.2)

    def test_auto_gain_leaves_loud_alone(self):
        import numpy as np

        x = (np.ones(1000, dtype=np.float32) * 0.9)
        out = draft.auto_gain(x)
        self.assertLessEqual(float(abs(out).max()), 1.0)

    def test_trim_silence_keeps_speech(self):
        import numpy as np

        rng = np.random.RandomState(0)
        silence = (rng.randn(8000).astype(np.float32) * 1e-5)
        speech = (rng.randn(8000).astype(np.float32) * 0.2)
        x = np.concatenate([silence, speech, silence])
        out, had = draft.trim_silence(x)
        self.assertTrue(had)
        self.assertLess(len(out), len(x))

    def test_trim_silence_all_quiet(self):
        import numpy as np

        x = np.zeros(16000, dtype=np.float32)
        _out, had = draft.trim_silence(x)
        self.assertFalse(had)

    def test_preprocess_empty(self):
        import numpy as np

        out, had = draft.preprocess(np.zeros(0, dtype=np.float32))
        self.assertFalse(had)
        self.assertEqual(out.size, 0)

    def test_resample_passthrough(self):
        import numpy as np

        x = np.ones(160, dtype=np.float32)
        out = draft.resample_to_16k(x, 16000)
        self.assertEqual(out.size, 160)


class ConfigTests(unittest.TestCase):
    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            old = draft.CONFIG_PATH
            draft.CONFIG_PATH = path
            try:
                cfg = draft.load_config()
                cfg["theme"] = "midnight"
                cfg["hotwords"] = "alice, bob"
                draft.save_config(cfg)
                with open(path, encoding="utf-8") as fh:
                    raw = json.load(fh)
                self.assertEqual(raw["theme"], "midnight")
                cfg2 = draft.load_config()
                self.assertEqual(cfg2["theme"], "midnight")
                self.assertEqual(cfg2["hotwords"], "alice, bob")
            finally:
                draft.CONFIG_PATH = old

    def test_invalid_values_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"language": "xx", "engine_mode": "nope",
                           "theme": "nope"}, fh)
            old = draft.CONFIG_PATH
            draft.CONFIG_PATH = path
            try:
                cfg = draft.load_config()
                self.assertEqual(cfg["language"], "auto")
                self.assertEqual(cfg["engine_mode"], "pro")
                self.assertEqual(cfg["theme"], "golden")
            finally:
                draft.CONFIG_PATH = old


if __name__ == "__main__":
    unittest.main()
