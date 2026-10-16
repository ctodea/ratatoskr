"""Run with: .venv/bin/python -m unittest -v"""

import json
import unittest

import ratatoskr as r


def asst(text, stop):
    return json.dumps({"type": "assistant", "message": {
        "stop_reason": stop, "content": [{"type": "text", "text": text}]}})


def user(content, **flags):
    return json.dumps({"type": "user", "message": {"content": content}, **flags})


DONE = json.dumps({"type": "system", "subtype": "turn_duration"})


class Turns(unittest.TestCase):
    def roles(self, lines):
        return [(k, t) for k, t in r.turns_from_lines(lines) if k in ("user", "assistant")]

    def test_only_the_final_answer_is_posted(self):
        lines = [user("hola"), asst("Miro el repo.", "tool_use"),
                 asst("Ahora los tests.", "tool_use"), asst("Hecho.", "end_turn"), DONE]
        self.assertEqual(self.roles(lines), [("user", "hola"), ("assistant", "Hecho.")])

    def test_compaction_summary_and_meta_are_dropped(self):
        lines = [user("This session is being continued...", isCompactSummary=True),
                 user("caveat", isMeta=True), user("<system-reminder>x</system-reminder>")]
        self.assertEqual(self.roles(lines), [])

    def test_pasted_content_is_unwrapped(self):
        lines = [user('\n\n<pasted_content id="e82e">\nlinea 1\nlinea 2\n</pasted_content id="e82e">')]
        self.assertEqual(self.roles(lines), [("user", "linea 1\nlinea 2")])

    def test_injected_prompt_is_recognised_despite_whitespace(self):
        link = r.AgentLink("x")
        link.note_injected("linea 1\nlinea 2  ")
        self.assertTrue(link.take_echo("linea 1\n\nlinea 2"))
        self.assertFalse(link.take_echo("otra cosa"))


class Format(unittest.TestCase):
    def test_table_becomes_list(self):
        md = "Intro\n\n| | **A** | **B** |\n|---|---|---|\n| CPU | 24 | 16 |\n| RAM | 61 | 30 |\n\nFin"
        out = r.discordify(md)
        self.assertNotIn("|", out)
        self.assertIn("- **CPU**: **A**: 24 · **B**: 16", out)

    def test_two_column_table(self):
        out = r.discordify("| Clave | Valor |\n|---|---|\n| a | 1 |")
        self.assertEqual(out, "- **a**: 1")

    def test_code_blocks_untouched(self):
        md = "```\n| a | b |\n|---|---|\n```"
        self.assertEqual(r.discordify(md), md)

    def test_chunks_respect_limit_and_balance_fences(self):
        md = "texto\n\n```sh\n" + "\n".join(f"echo {i}" * 5 for i in range(200)) + "\n```\nfin"
        parts = r.chunks(md, limit=500)
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertLessEqual(len(p), 500)
            self.assertEqual(p.count("```") % 2, 0, p)
        self.assertTrue(parts[1].startswith("```sh"))

    def test_chunks_do_not_split_words(self):
        md = "\n\n".join("palabra " * 30 for _ in range(10))
        for p in r.chunks(md, limit=400):
            self.assertFalse(p.endswith("pala"))
            self.assertTrue(p.strip().endswith("palabra"))

    def test_mirror_is_quoted(self):
        self.assertEqual(r.mirror("a\n\nb"), "-# desde el PC\n> a\n>\n> b")


if __name__ == "__main__":
    unittest.main()
