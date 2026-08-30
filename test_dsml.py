"""Unit tests: DSML parse/strip + TOOL_CALL fallback + fabrication guard."""
from __future__ import annotations

import json
import unittest

from deepseek_agent.providers.dsml import (
    extract_dsml_calls, format_dsml_calls, looks_like_dsml, strip_dsml,
)
from deepseek_agent.providers.deepseek import _extract_tool_calls, _strip_tool_calls


FW = "\uFF5C"  # fullwidth pipe ｜


def _names(calls):
    return [c["function"]["name"] for c in calls]


def _args(call):
    raw = call["function"]["arguments"]
    return json.loads(raw) if isinstance(raw, str) else raw


class DsmlParse(unittest.TestCase):
    def test_ascii_cdata(self):
        text = (
            "<|DSML|tool_calls>\n"
            '  <|DSML|invoke name="write_file">\n'
            '    <|DSML|parameter name="path"><![CDATA[index.html]]></|DSML|parameter>\n'
            '    <|DSML|parameter name="content"><![CDATA[<h1>Hi</h1>]]></|DSML|parameter>\n'
            "  </|DSML|invoke>\n"
            "</|DSML|tool_calls>\n"
        )
        calls = extract_dsml_calls(text)
        self.assertEqual(_names(calls), ["write_file"])
        self.assertEqual(_args(calls[0])["path"], "index.html")
        self.assertEqual(_args(calls[0])["content"], "<h1>Hi</h1>")

    def test_fullwidth_pipe(self):
        text = (
            f"<{FW}DSML{FW}tool_calls>\n"
            f'  <{FW}DSML{FW}invoke name="list_dir">\n'
            f'    <{FW}DSML{FW}parameter name="path"><![CDATA[.]]></{FW}DSML{FW}parameter>\n'
            f"  </{FW}DSML{FW}invoke>\n"
            f"</{FW}DSML{FW}tool_calls>\n"
        )
        self.assertTrue(looks_like_dsml(text))
        calls = extract_dsml_calls(text)
        self.assertEqual(_names(calls), ["list_dir"])
        self.assertEqual(_args(calls[0])["path"], ".")

    def test_unclosed_attribute_form(self):
        text = (
            '<|DSML|invoke name="start_server">\n'
            '<|DSML|parameter name="port" string="false">8080\n'
            '<|DSML|parameter name="directory" string="true">projects/site\n'
            "</|DSML|invoke>"
        )
        calls = extract_dsml_calls(text)
        self.assertEqual(_names(calls), ["start_server"])
        args = _args(calls[0])
        self.assertEqual(args["port"], 8080)
        self.assertEqual(args["directory"], "projects/site")

    def test_provider_extracts_dsml_from_thinking_blob(self):
        blob = (
            "I will write the file now.\n"
            "<|DSML|tool_calls>"
            '<|DSML|invoke name="write_file">'
            '<|DSML|parameter name="path"><![CDATA[a.txt]]></|DSML|parameter>'
            '<|DSML|parameter name="content"><![CDATA[hello]]></|DSML|parameter>'
            "</|DSML|invoke></|DSML|tool_calls>"
        )
        calls = _extract_tool_calls(blob)
        self.assertEqual(_names(calls), ["write_file"])
        self.assertEqual(_args(calls[0])["content"], "hello")
        leftover = _strip_tool_calls(blob)
        self.assertNotIn("DSML", leftover)
        self.assertNotIn("write_file", leftover)

    def test_tool_call_json_still_works(self):
        text = 'TOOL_CALL: {"name":"list_dir","arguments":{"path":"."}}'
        calls = _extract_tool_calls(text)
        self.assertEqual(_names(calls), ["list_dir"])

    def test_xml_invoke_still_works(self):
        text = (
            "<tool_calls><invoke name=\"run_python\">"
            "<parameter name=\"code\">print(1)</parameter>"
            "</invoke></tool_calls>"
        )
        calls = _extract_tool_calls(text)
        self.assertEqual(_names(calls), ["run_python"])
        self.assertIn("print(1)", _args(calls[0])["code"])

    def test_roundtrip_format(self):
        original = (
            '<|DSML|invoke name="write_file">'
            '<|DSML|parameter name="path"><![CDATA[x.py]]></|DSML|parameter>'
            '<|DSML|parameter name="content"><![CDATA[print(2)]]></|DSML|parameter>'
            "</|DSML|invoke>"
        )
        calls = extract_dsml_calls(original)
        replay = format_dsml_calls(calls)
        again = extract_dsml_calls(replay)
        self.assertEqual(_args(again[0])["path"], "x.py")
        self.assertEqual(_args(again[0])["content"], "print(2)")

    def test_prose_without_tools(self):
        self.assertEqual(_extract_tool_calls("Hello, I am DeepSeek-Agent."), [])
        self.assertFalse(looks_like_dsml("just thinking about files"))


    def test_mixed_dsml_path_claude_content(self):
        """Live: path was DSML, HTML body was Claude <parameter> — content dropped."""
        html = "<!DOCTYPE html><html><body><h1>Portfolio</h1></body></html>"
        text = (
            '<|DSML|invoke name="write_file">\n'
            '<|DSML|parameter name="path"><![CDATA[portfolio/index.html]]>'
            '</|DSML|parameter>\n'
            f'<parameter name="content">{html}</parameter>\n'
            '</|DSML|invoke>'
        )
        calls = extract_dsml_calls(text)
        self.assertEqual(_names(calls), ["write_file"])
        self.assertEqual(_args(calls[0])["path"], "portfolio/index.html")
        self.assertIn("<h1>Portfolio</h1>", _args(calls[0])["content"])

    def test_salvage_unclosed_html_body(self):
        html = "<!DOCTYPE html>\n<html><head><title>X</title></head><body>hi</body></html>"
        text = (
            '<|DSML|invoke name="write_file">\n'
            '<|DSML|parameter name="path"><![CDATA[portfolio/index.html]]>'
            '</|DSML|parameter>\n'
            + html
        )
        calls = extract_dsml_calls(text)
        self.assertEqual(_args(calls[0])["path"], "portfolio/index.html")
        self.assertIn("<title>X</title>", _args(calls[0])["content"])

    def test_live_dropped_opening_pipe(self):
        """V4 live: <DSML|invoke without the leading |, empty close tags mixed."""
        text = (
            "<DSML|s>\n"
            '  <DSML|invoke name="write_file">\n'
            '    <DSML|parameter name="path"><![CDATA[projects/e2e-arena/index.html]]>'
            "</DSML|parameter>\n"
            '    <DSML|parameter name="content"><![CDATA[<h1>Hi</h1>]]></DSML|parameter>\n'
            "  </|DSML|invoke>\n"
            "</|DSML|s>\n"
        )
        self.assertTrue(looks_like_dsml(text))
        calls = extract_dsml_calls(text)
        self.assertEqual(_names(calls), ["write_file"])
        self.assertEqual(_args(calls[0])["path"], "projects/e2e-arena/index.html")


class MuteDetect(unittest.TestCase):
    def test_muted_json(self):
        from deepseek_agent.providers.deepseek import _mute_error
        raw = '{"code":0,"data":{"biz_code":5,"biz_msg":"user is muted","biz_data":{"mute_until":1788185755}}}'
        msg = _mute_error(raw)
        self.assertIn("muted", msg.lower())
        self.assertIn("2026", msg)
        self.assertEqual(_mute_error("data: hello"), "")


class FabricationGuard(unittest.TestCase):
    def test_action_regex_hits_build_host(self):
        from deepseek_agent.agents.base import _ACTION_TASK, _FAKE_CLAIM, _HOST_TASK, _WIP_FINAL
        self.assertTrue(_ACTION_TASK.search("banao ek portfolio website"))
        self.assertTrue(_HOST_TASK.search("host it locally on localhost"))
        self.assertTrue(_FAKE_CLAIM.search("I have built and hosted it. HTTP 200 at localhost:8080"))
        self.assertFalse(_FAKE_CLAIM.search("I will start by listing the workspace."))
        self.assertTrue(_WIP_FINAL.search(
            "The HTML file was created successfully. Let me create the CSS now."))

    def test_hinglish_host_kr_dena_is_hosting_intent(self):
        from deepseek_agent.orchestrator.engine import _is_hosting_intent
        g = ("make a best portfolio website for yourself  and host kr dena "
             "locally best ui ke sath bnana")
        self.assertTrue(_is_hosting_intent(g))
        self.assertFalse(_is_hosting_intent("what is a web host"))


if __name__ == "__main__":
    unittest.main()
