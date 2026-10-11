"""FR-15 的「提问纪律」：brief 一次写全（必填声明 / **提问纪律** / 结果契约）。

**这条需求原来是漏做的，而且要害比「少个字段」深**：需求原文要求 `brief` 写全三样东西，
结果契约与必填声明都在，唯独提问纪律没有。更关键的是——`collect` 的结果契约原本是

    "status_in": ["ok", "failed"]

也就是说 **worker 根本没有「我有问题要问」这个合法通道**。在这种情况下加一个
`question_policy` 字段只是**不可执行的装饰**：它要求 worker 提问，而 worker 报不出来，
于是唯一的出路还是自己拍板。

所以最小可信的做法是三件一起：

1. `question_policy` 进 brief（三层解析：节点 `context.question_policy` > 项目配置 > 内建默认）
2. 结果契约里的 `status_in` 加 `blocked` —— 让「我要问」成为一个**报得出来**的状态
3. `collect` 对 `blocked` 照旧**不写 node-done**（提问不等于完成）

本文件的重点在第 4 条：「status 里没有『我猜了一下』这个选项」。
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from base import DisflowTestCase

from disflow import config, dispatch, vocab
from disflow.cli import main
from disflow.config import DEFAULTS
from disflow.engine import Engine


def _task(node_id: str, **extra) -> dict:
    body = {"id": node_id, "goal": node_id, "acceptance": {"cmd": "true"}}
    body.update(extra)
    return body


def _config_from(body: dict) -> config.Config:
    """把一个配置对象写到临时 `disflow.json` 再走**真实入口** `config.load` 读回来。

    为何不直接构造 `Config(**body)`：那样就绕过了校验——而「拼错要响亮拒」正是本文件的
    被测对象之一，绕过校验等于把要测的东西换掉。
    """
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "disflow.json"), "w", encoding="utf-8") as handle:
            json.dump(body, handle, ensure_ascii=False)
        return config.load(tmp)


class _CliRunMixin:
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def _seed(self) -> None:
        self._run(["init", "t", "--goal", "demo"])
        self._run(["expand", "t", "root", "--children", json.dumps([_task("a")])])


class ResolveQuestionPolicyTest(DisflowTestCase):
    """三层解析：节点声明 > 项目配置 > 内建默认。"""

    def test_default_has_all_three_subkeys(self) -> None:
        policy = dispatch.resolve_question_policy({})
        self.assertEqual(sorted(policy), ["ask_host", "decide_alone", "how"])
        self.assertTrue(policy["ask_host"] and policy["decide_alone"] and policy["how"])

    def test_default_is_a_copy_not_the_shared_constant(self) -> None:
        """返回值被改不能污染出厂默认（否则一次调用会改掉所有后续调用）。"""
        policy = dispatch.resolve_question_policy({})
        policy["how"] = "被改了"
        self.assertNotEqual(vocab.QUESTION_POLICY_DEFAULT["how"], "被改了")
        self.assertNotEqual(dispatch.resolve_question_policy({})["how"], "被改了")

    def test_config_overrides_default(self) -> None:
        cfg = _config_from({"question_policy": {"how": "项目级：先问再干"}})
        self.assertEqual(dispatch.resolve_question_policy({}, cfg)["how"], "项目级：先问再干")

    def test_node_context_overrides_config(self) -> None:
        cfg = _config_from({"question_policy": {"how": "项目级"}})
        node = {"context": {"question_policy": {"how": "节点级"}}}
        self.assertEqual(dispatch.resolve_question_policy(node, cfg)["how"], "节点级")

    def test_partial_override_merges_instead_of_replacing(self) -> None:
        """**这条是重点**：节点只声明 `how` 时不能把 `ask_host` 弄丢。

        整体替换的话，纪律会**静默变窄**，而 brief 看上去仍然「有纪律」——
        那比没有更坏：读的人会以为「该问的都列出来了」。
        """
        node = {"context": {"question_policy": {"how": "节点级"}}}
        policy = dispatch.resolve_question_policy(node)
        self.assertEqual(policy["how"], "节点级")
        self.assertEqual(policy["ask_host"], list(vocab.QUESTION_POLICY_DEFAULT["ask_host"]))

    def test_garbage_node_context_is_ignored_not_merged_into_result(self) -> None:
        for junk in ("字符串", 42, ["列表"], None):
            with self.subTest(junk=junk):
                policy = dispatch.resolve_question_policy({"context": {"question_policy": junk}})
                self.assertEqual(sorted(policy), ["ask_host", "decide_alone", "how"])


class ConfigPolicyValidationTest(DisflowTestCase):
    """配置里的拼错必须**响亮**，不能静默丢掉——否则纪律没生效而不自知。"""

    def _load(self, body: dict) -> config.Config:
        return _config_from(body)

    def test_unknown_subkey_is_rejected_with_the_allowed_list(self) -> None:
        with self.assertRaises(config.ConfigError) as ctx:
            self._load({"question_policy": {"ask_hsot": ["x"]}})
        self.assertIn("ask_hsot", str(ctx.exception))
        self.assertIn("ask_host", str(ctx.exception))

    def test_non_dict_is_rejected(self) -> None:
        with self.assertRaises(config.ConfigError):
            self._load({"question_policy": "先问再干"})

    def test_comma_string_is_accepted_for_lists(self) -> None:
        cfg = self._load({"question_policy": {"ask_host": "范围, 接口"}})
        self.assertEqual(list(cfg.question_policy["ask_host"]), ["范围", "接口"])

    def test_non_string_how_is_rejected(self) -> None:
        with self.assertRaises(config.ConfigError):
            self._load({"question_policy": {"how": 42}})

    def test_question_policy_is_in_the_config_key_surface(self) -> None:
        """键必须在可发现面上（`doctor` 要能报出实际取值）。"""
        self.assertIn("question_policy", config.CONFIG_KEYS)
        described = {key: value for key, value, _src in config.describe(DEFAULTS, self.tmp)}
        self.assertIn("question_policy", described)


class BriefWritesAllThreeTest(DisflowTestCase):
    """FR-15 原文：一次写全「必填声明 / 提问纪律 / 结果契约」。三样都要在。"""

    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)
        self.engine.init_tree("t", "demo")
        self.engine.expand("t", "root", [_task("a")])

    def test_brief_contains_question_policy_and_result_contract(self) -> None:
        payload = self.engine.brief("t", "a")
        self.assertIn("question_policy", payload, "提问纪律（FR-15 三样之一）缺失")
        self.assertIn("result_contract", payload, "结果契约缺失")
        self.assertTrue(payload["question_policy"]["ask_host"])
        self.assertEqual(payload["result_contract"]["required"],
                         ["status", "summary", "changed_files"])

    def test_result_contract_exposes_a_question_channel(self) -> None:
        """通道要**报得出来**：光有纪律字段、没有合法状态 = 不可执行的装饰。"""
        contract = self.engine.brief("t", "a")["result_contract"]
        self.assertIn("blocked", contract["status_in"])
        self.assertIn("blocked", contract["question_channel"])

    def test_brief_policy_follows_node_context_override(self) -> None:
        self.engine.annotate("t", "a", {"question_policy": {"how": "这个节点必须先问"}})
        self.assertEqual(self.engine.brief("t", "a")["question_policy"]["how"], "这个节点必须先问")


class CollectAcceptsBlockedTest(_CliRunMixin, DisflowTestCase):
    """`blocked` 是一个**合法**结果，但它不是完成。"""

    def setUp(self) -> None:
        super().setUp()
        self._seed()
        self._run(["start", "t", "a"])

    def test_blocked_result_is_accepted(self) -> None:
        code, out, err = self._run([
            "collect", "t", "a", "--result",
            json.dumps({"status": "blocked", "summary": "需要把 touch_plan 放宽到 b.py",
                        "changed_files": []}, ensure_ascii=False)])
        self.assertEqual(code, 0, err)
        self.assertIn("已校验落盘", out)

    def test_blocked_does_not_write_done(self) -> None:
        """**提问不等于完成** —— 若它顺手写了 done，纪律就成了绕门的后门。"""
        self._run(["collect", "t", "a", "--result",
                   json.dumps({"status": "blocked", "summary": "问题", "changed_files": []},
                              ensure_ascii=False)])
        node = Engine(cwd=self.tmp).tree("t")["nodes"]["a"]
        self.assertNotEqual(node["status"], "done")
        self.assertEqual(node["status"], "running")

    def test_made_up_status_is_rejected_and_names_the_allowed_set(self) -> None:
        """「status 里没有『我猜了一下』这个选项」。"""
        code, _out, err = self._run([
            "collect", "t", "a", "--result",
            json.dumps({"status": "我猜了一下", "summary": "x", "changed_files": []},
                       ensure_ascii=False)])
        self.assertEqual(code, 1)
        self.assertIn("blocked", err, "拒收报文必须列出允许集，否则报错帮不上忙")

    def test_blocked_result_survives_replay(self) -> None:
        from disflow import store
        from disflow.state import project
        self._run(["collect", "t", "a", "--result",
                   json.dumps({"status": "blocked", "summary": "问题", "changed_files": []},
                              ensure_ascii=False)])
        replayed = project(store.load_events(vocab.STORAGE))
        self.assertEqual(replayed["trees"]["t"]["nodes"]["a"]["collected"]["status"], "blocked")
        matched, diffs = store.projection_matches(vocab.STORAGE, project=project)
        self.assertTrue(matched, diffs)


if __name__ == "__main__":
    unittest.main()
