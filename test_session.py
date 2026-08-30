"""One DeepSeek chat thread: no per-goal session, no parent=None regenerations."""
from __future__ import annotations

import unittest

from deepseek_agent.providers.deepseek import extract_message_id, turn_prompt


class TurnPrompt(unittest.TestCase):
    def test_first_turn_includes_user_and_tools(self):
        tools = [{"function": {"name": "write_file", "description": "w",
                               "parameters": {"properties": {"path": {"type": "string"}},
                                              "required": ["path"]}}}]
        p = turn_prompt(False, "You are the agent.", tools,
                        [{"role": "system", "content": "You are the agent."},
                         {"role": "user", "content": "make a portfolio"}])
        self.assertIn("make a portfolio", p)
        self.assertIn("write_file", p)
        self.assertIn("You are the agent.", p)

    def test_followup_is_only_new_user_text(self):
        msgs = [
            {"role": "system", "content": "You are the agent."},
            {"role": "user", "content": "first goal"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "second goal host locally"},
        ]
        p = turn_prompt(True, "You are the agent.", None, msgs)
        self.assertEqual(p.strip(), "second goal host locally")
        self.assertNotIn("You are the agent.", p)
        self.assertNotIn("first goal", p)

    def test_tool_results_only_after_last_assistant(self):
        msgs = [
            {"role": "user", "content": "build it"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "write_file"}}]},
            {"role": "tool", "name": "write_file", "content": "wrote index.html"},
        ]
        p = turn_prompt(True, "sys", None, msgs)
        self.assertIn("TOOL RESULT (write_file): wrote index.html", p)
        self.assertNotIn("build it", p)
        self.assertNotIn("sys", p)


class MessageId(unittest.TestCase):
    def test_sse_message_id(self):
        sse = (
            'data: {"v":{"response":{"message_id":7,"fragments":[]}}}\n'
            'data: {"p":"response/fragments/-1/content","o":"APPEND","v":"hi"}\n'
            "data: [DONE]\n"
        )
        self.assertEqual(extract_message_id(sse), 7)

    def test_json_patch_path(self):
        sse = 'data: {"p":"response/message_id","o":"SET","v":3}\n'
        self.assertEqual(extract_message_id(sse), 3)

    def test_empty(self):
        self.assertIsNone(extract_message_id("data: {\"v\":\"hello\"}\n"))


class ProviderInit(unittest.TestCase):
    def test_primed_exists_without_reset_session(self):
        """Live: /resume then 'hyy' crashed — _primed was only set in reset_session."""
        from tempfile import TemporaryDirectory
        from deepseek_agent.providers.deepseek import DeepSeekProvider
        with TemporaryDirectory() as d:
            p = DeepSeekProvider({"keys_dir": d, "data_dir": d}, None)
            self.assertFalse(getattr(p, "_primed", True))


if __name__ == "__main__":
    unittest.main()
