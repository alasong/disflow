"""项目级配置：**独立配置文件** + 优先级 + 响亮报错。

**设计要点**（本文件就是它的可执行规格）：

1. **配置是独立文件**（`<cwd>/disflow.json`，`$DISFLOW_CONFIG` 可指别处），
   **不在状态目录里、不在事件流里**。理由：
   - 配置是**策略**（人写、要版本化、要 review）；状态是**派生**（可从事件重建）。
   - 混在一起的话，「删掉 `.disflow/` 重置」这个合法动作会**静默带走策略**；
     而 `rebuild`（从事件重建投影）的语义也会被搅乱——配置不是从事件推出来的。
2. **优先级**：内建默认 < `disflow.json` < 显式参数/节点声明。
3. **响亮报错**：未知键、类型不对都报错并列允许的键——不静默忽略
   （与节点字段闭集同一个纪律）。
4. **`roles` 可配，但只在写时校验**：reducer 不能依赖配置，
   否则换台机器（或改了配置）重放旧事件会失败——事件流就不可重放了。
5. **看板定义在另一个独立文件 `boards.json`**（2026-10-11 用户裁决）：
   板会**长大**（要做动态看板），且变更节奏与引擎参数不同 ⇒ 单独一个文件。
   `Config.stages` 仍在，但已是**从 `boards` 派生的属性**；
   `disflow.json` 的 `stages` 键**已退役**，写了**响亮报错**并给迁移指引（不静默回落＝不造双源）。
"""
from __future__ import annotations

import json
import os
import shutil
import unittest

from base import DisflowTestCase, acceptance, git_repo, touch

from disflow import boards as boards_mod
from disflow import config, gates, store, views
from disflow.boards import DEFAULT_BOARDS, Board, BoardsError
from disflow.config import Config, ConfigError
from disflow.engine import Engine, EngineError, GateReject
from disflow.vocab import ROLES, SIZE_BUDGET_DEFAULT, STAGES
from disflow.state import project


def write_config(test: DisflowTestCase, payload: object) -> str:
    path = os.path.join(test.tmp, config.CONFIG_BASENAME)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


def write_boards(test: DisflowTestCase, payload: object) -> str:
    path = os.path.join(test.tmp, boards_mod.BOARDS_BASENAME)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


class ConfigFileTest(DisflowTestCase):
    def test_missing_file_means_pure_builtin_defaults(self):
        """零配置可用是前提：没有文件 = 全用内建默认，**不是错误**。"""
        self.assertFalse(os.path.isfile(config.config_path(self.tmp)))
        self.assertEqual(config.load(self.tmp), config.DEFAULTS)

    def test_config_location_is_independent_of_the_state_dir(self):
        """**配置必须是独立文件**：它的位置不随状态目录变。

        为何用“位置无关”而不是“不在状态目录里”做判据：测试夹具把
        `DISFLOW_STATE_DIR` 指到项目根，两者会重合——真正的独立性是**函数关系上无关**：
        改状态目录，配置路径不动。（生产默认：状态目录 `<cwd>/.disflow`，配置 `<cwd>/disflow.json`）
        """
        before = config.config_path(self.tmp)
        from disflow.vocab import STORAGE
        original = os.environ["DISFLOW_STATE_DIR"]
        try:
            for other in (os.path.join(self.tmp, ".disflow"),
                          os.path.join(self.tmp, "完全另一处")):
                os.environ["DISFLOW_STATE_DIR"] = other
                self.assertEqual(store.state_dir(STORAGE), other)
                self.assertEqual(config.config_path(self.tmp), before,
                                 "换状态目录不能改变配置路径")
        finally:
            os.environ["DISFLOW_STATE_DIR"] = original

    def test_config_is_not_a_state_file(self):
        """生产默认布局下，配置不在状态目录之内（是兄弟，不是子文件）。"""
        from disflow.vocab import STORAGE
        self.assertEqual(STORAGE["state"]["dir"], ".disflow")
        self.assertEqual(config.CONFIG_BASENAME, "disflow.json")
        self.assertFalse(config.CONFIG_BASENAME.startswith(".disflow"))
        self.assertNotIn("disflow.json", {STORAGE["state"]["events"], STORAGE["state"]["projection"]})

    def test_state_dir_reset_does_not_lose_policy(self):
        """删状态目录后策略仍在——这就是“独立文件”要换来的东西。

        状态是**派生**的（可从事件重建），删掉它重置是一个合法动作；
        策略是**人写**的，不该被这个动作静默带走。
        """
        write_config(self, {"size_budget": 7})
        state = os.path.join(self.tmp, ".disflow")
        os.environ["DISFLOW_STATE_DIR"] = state
        engine = Engine(cwd=self.tmp)
        engine.init_tree("t", "g")
        self.assertTrue(os.path.isdir(state))
        shutil.rmtree(state)                       # 「删状态重置」
        self.assertEqual(config.load(self.tmp).size_budget, 7, "重置状态不该带走配置")

    def test_env_var_redirects_the_config_path(self):
        other = os.path.join(self.tmp, "别处.json")
        with open(other, "w", encoding="utf-8") as handle:
            json.dump({"size_budget": 11}, handle)
        os.environ[config.ENV_CONFIG_PATH] = other
        try:
            self.assertEqual(config.config_path(self.tmp), os.path.abspath(other))
            self.assertEqual(config.load(self.tmp).size_budget, 11)
        finally:
            os.environ.pop(config.ENV_CONFIG_PATH, None)

    def test_file_overrides_builtin_defaults(self):
        write_config(self, {"acceptance_timeout": 300, "size_budget": 42,
                            "lease_ttl": 60, "output_tail": 99,
                            "walk_ignore": ["node_modules"],
                            "roles": ["r1"], "git_timeout": 5})
        write_boards(self, {"boards": [{"id": "a", "label": "甲"}, "b"]})
        loaded = config.load(self.tmp)
        self.assertEqual(loaded.acceptance_timeout, 300)
        self.assertEqual(loaded.size_budget, 42)
        self.assertEqual(loaded.lease_ttl, 60)
        self.assertEqual(loaded.output_tail, 99)
        self.assertEqual(loaded.walk_ignore, ("node_modules",))
        self.assertEqual(loaded.stages, ("a", "b"), "stages 由 boards 派生")
        self.assertEqual(loaded.boards, (Board("a", "甲"), Board("b", "b")),
                         "label 缺省回落成 id")
        self.assertEqual(loaded.labels, {"a": "甲", "b": "b"})
        self.assertEqual(loaded.roles, ("r1",))
        self.assertEqual(loaded.git_timeout, 5)

    def test_partial_file_keeps_defaults_for_untouched_keys(self):
        write_config(self, {"size_budget": 5})
        loaded = config.load(self.tmp)
        self.assertEqual(loaded.size_budget, 5)
        self.assertEqual(loaded.stages, STAGES)
        self.assertEqual(loaded.lease_ttl, config.DEFAULTS.lease_ttl)

    def test_unknown_key_is_rejected_with_the_allowed_list(self):
        """未知键要**报错**（写错一个字母不能静默无效果）。"""
        write_config(self, {"size_budgte": 5})
        with self.assertRaises(ConfigError) as ctx:
            config.load(self.tmp)
        message = str(ctx.exception)
        self.assertIn("size_budgte", message)
        self.assertIn("size_budget", message)

    def test_wrong_type_is_rejected(self):
        for payload, expect in (({"size_budget": "大"}, "应为数字"),
                                ({"size_budget": True}, "应为数字"),
                                ({"size_budget": 0}, "应为正数"),
                                ({"walk_ignore": [1, 2]}, "字符串数组")):
            with self.subTest(payload=payload):
                write_config(self, payload)
                with self.assertRaises(ConfigError) as ctx:
                    config.load(self.tmp)
                self.assertIn(expect, str(ctx.exception))

    def test_retired_stages_key_is_rejected_with_a_migration_hint(self):
        """`stages` 已迁到 `boards.json`：写了必须**响亮报错**，不静默回落。

        为何不回落（判据不是洁癖）：用户在 disflow.json 里改 `stages` 却以为生效了，
        而板实际由 boards.json 决定 —— **静默失效**比报错贵得多，且它正是双源的入口。
        """
        write_config(self, {"stages": ["a", "b"]})
        with self.assertRaises(ConfigError) as ctx:
            config.load(self.tmp)
        message = str(ctx.exception)
        self.assertIn("已退役", message)
        self.assertIn("boards.json", message, "必须给出迁移到哪")
        self.assertNotIn("未知配置键", message, "退役键要报迁移指引，不是笼统的『拼错了』")

    def test_malformed_json_is_rejected_not_ignored(self):
        with open(os.path.join(self.tmp, config.CONFIG_BASENAME), "w", encoding="utf-8") as handle:
            handle.write("{不是 JSON")
        with self.assertRaises(ConfigError) as ctx:
            config.load(self.tmp)
        self.assertIn("不是合法 JSON", str(ctx.exception))

    def test_non_object_top_level_is_rejected(self):
        write_config(self, [1, 2])
        with self.assertRaises(ConfigError) as ctx:
            config.load(self.tmp)
        self.assertIn("顶层应为对象", str(ctx.exception))

    def test_engine_reads_the_file_at_construction(self):
        write_config(self, {"size_budget": 3})
        self.assertEqual(Engine(cwd=self.tmp).cfg.size_budget, 3)

    def test_explicit_cfg_argument_wins_over_the_file(self):
        write_config(self, {"size_budget": 3})
        engine = Engine(cwd=self.tmp, cfg=Config(size_budget=9))
        self.assertEqual(engine.cfg.size_budget, 9)


class ConfigAffectsBehaviourTest(DisflowTestCase):
    def test_node_declared_timeout_beats_the_project_default(self):
        """①最尖锐那条的正面验证：节点能声明自己的验收超时。

        今天这条不存在的话，跑 >2 分钟的验收**这道门永远过不去**，
        唯一出路是 `waiver`——等于把门关掉。
        """
        engine = Engine(cwd=self.tmp, cfg=Config(acceptance_timeout=999))
        plan = gates._resolve_timeout({"cmd": "x", "timeout": 1}, engine.cfg)
        self.assertEqual(plan, 1, "节点声明必须压过项目默认")
        self.assertEqual(gates._resolve_timeout({"cmd": "x"}, engine.cfg), 999)
        self.assertEqual(gates._resolve_timeout(None, config.DEFAULTS),
                         config.DEFAULTS.acceptance_timeout)

    def test_output_tail_is_configurable(self):
        engine = Engine(cwd=self.tmp, cfg=Config(output_tail=10))
        cmd = 'echo 0123456789abcdefghij'
        ok, _why, meta = gates.run_acceptance({"cmd": cmd, "expect": "exit0"},
                                             self.tmp, engine.cfg)
        self.assertTrue(ok)
        self.assertLessEqual(len(meta["output_tail"]), 11)

    def test_walk_ignore_keeps_dependency_dirs_out_of_the_fingerprint(self):
        """`fingerprint` 递归目录时跳过忽略集。

        不跳的话：`touch_plan` 写了一个含 `node_modules` 的目录 ⇒ 哈希整棵依赖树
        （慢，且毫无信息量——依赖一变指纹就变，与本次改动无关）。
        """
        os.makedirs(os.path.join(self.tmp, "pkg", "node_modules"))
        target = os.path.join(self.tmp, "pkg", "node_modules", "dep.js")
        with open(os.path.join(self.tmp, "pkg", "real.py"), "w", encoding="utf-8") as handle:
            handle.write("x = 1\n")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("dep v1\n")

        before = gates.fingerprint(self.tmp, "pkg")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("dep v2\n")
        self.assertEqual(gates.fingerprint(self.tmp, "pkg"), before,
                         "忽略集内的改动不该影响指纹")

        # 反过来：真的改动必须被看见（否则这条测试只是在证明“指纹总是不变”）
        with open(os.path.join(self.tmp, "pkg", "real.py"), "w", encoding="utf-8") as handle:
            handle.write("x = 2\n")
        self.assertNotEqual(gates.fingerprint(self.tmp, "pkg"), before)

    def test_project_size_budget_default_with_node_override(self):
        cfg = Config(size_budget=7)
        self.assertEqual(gates.resolve_budget({}, cfg), 7)
        self.assertEqual(gates.resolve_budget({"context": {"size_budget": 2}}, cfg), 2)
        self.assertEqual(gates.resolve_budget({}, config.DEFAULTS), SIZE_BUDGET_DEFAULT)

    def test_lease_ttl_default_from_config_and_explicit_ttl_wins(self):
        from datetime import datetime

        from disflow.ready import lease_expired

        engine = Engine(cwd=self.tmp, cfg=Config(lease_ttl=1))
        engine.init_tree("t", "g")
        engine.expand("t", "root", [{"id": "n1", "goal": "g", "acceptance": acceptance()}])
        engine.start("t", "n1", holder="h")
        lease = engine.load()["trees"]["t"]["nodes"]["n1"]["lease"]
        # `lease_expired(lease, now_ts)` 比的是 `expires_at < now_ts`——所以用租约自己的
        # 到期时刻做参照，测试就不依赖墙钟（不会因为跑得慢/快而飘）。
        expires = datetime.fromisoformat(lease["expires_at"]).timestamp()
        self.assertFalse(lease_expired(lease, expires - 1), "到期前一秒不该判过期")
        self.assertTrue(lease_expired(lease, expires + 1), "到期后一秒必须判过期")

        engine.done("t", "n1", declarations={"approver": "me"})
        engine.expand("t", "root", [{"id": "n2", "goal": "g", "acceptance": acceptance()}])
        engine.start("t", "n2", holder="h", ttl=99999)
        lease2 = engine.load()["trees"]["t"]["nodes"]["n2"]["lease"]
        expires2 = datetime.fromisoformat(lease2["expires_at"]).timestamp()
        self.assertGreater(expires2, expires + 90000, "显式 --ttl 必须压过项目默认")


class ConfigPlacementOrderTest(DisflowTestCase):
    """配置文件与 `start` 的**先后顺序**：今天只靠 README 那句“在 start 之前就位”保证，
    而文档会漂。这里把它钉成行为。

    根因：`disflow.json` 就是一个**普通文件**——引擎不特殊对待它（那是故意的：
    配置变更有时真是节点干的活，那就该进 touch_plan）。所以：
      - 在 `start` 之前就位（或提交进仓）⇒ 进基线，不算本节点的改动；
      - 在 `start` 之后才出现 ⇒ 被作用域门当成“本节点引入的改动”。
    两个分支都要钉：只钉前者的话，README 把配置挪到 run 块里没人会发现。
    """

    def _tree_with_one_node(self, engine: Engine, tree: str = "t") -> None:
        engine.init_tree(tree, "g")
        engine.expand(tree, "root", [{
            "id": "n1", "goal": "g", "touch_plan": ["src/"],
            "acceptance": acceptance()}])

    def test_config_in_place_before_start_counts_as_baseline(self):
        # `commit=True`：初始文件已跟踪且干净（脏树会在 `git status` 里把未跟踪目录
        # 折叠成 `src/` 一条，断言就精确不到文件了）
        engine, repo = git_repo(self, {"src/a.py": "x = 1\n",
                                       config.CONFIG_BASENAME: '{"size_budget": 50}'},
                                commit=True)
        self.assertEqual(engine.cfg.size_budget, 50)
        self._tree_with_one_node(engine)
        engine.start("t", "n1", holder="h")
        touch(os.path.join(repo, "src", "b.py"), "y = 2\n")
        engine.done("t", "n1", declarations={"approver": "me"})
        evidence = engine.load()["trees"]["t"]["nodes"]["n1"]["evidence"]
        self.assertEqual(evidence["introduced"], ["src/b.py"],
                         "开工前就位的配置属于基线，不该算本节点引入")
        self.assertNotIn(config.CONFIG_BASENAME, evidence["introduced"])

    def test_config_appearing_after_start_is_flagged_and_the_error_names_it(self):
        engine, repo = git_repo(self, {"src/a.py": "x = 1\n"}, commit=True)
        self._tree_with_one_node(engine)
        engine.start("t", "n1", holder="h")
        touch(os.path.join(repo, config.CONFIG_BASENAME), '{"size_budget": 50}')
        touch(os.path.join(repo, "src", "b.py"), "y = 2\n")
        with self.assertRaises(GateReject) as ctx:
            engine.done("t", "n1", declarations={"approver": "me"})
        message = "；".join(ctx.exception.missing)
        self.assertIn(config.CONFIG_BASENAME, message, "报错要点名是哪个文件")
        self.assertIn("rescope", message, "报错要给出修法")


class VocabularyValidationTest(DisflowTestCase):
    """`stages`/`roles`：**写时**校验（挡错别字），**读时**容忍（保可重放）。"""

    def test_stage_typo_is_rejected_at_expand(self):
        engine = Engine(cwd=self.tmp)
        engine.init_tree("t", "g")
        with self.assertRaises(EngineError) as ctx:
            engine.expand("t", "root", [{"id": "n1", "goal": "g", "acceptance": acceptance(),
                                         "stage": "audti"}])
        message = str(ctx.exception)
        self.assertIn("audti", message)
        self.assertIn("boards.json", message, "报错要指出出路（板定义在 boards.json）")
        # 拒收必须不落事件
        self.assertEqual(engine.load()["trees"]["t"]["nodes"].keys(), {"root"})

    def test_role_not_in_the_allowlist_is_rejected_at_expand(self):
        """未登记的 role 必须被拒。

        为何用 `sre`（一个未登记但拼写正常的词）而不用错别字：被测行为是“值不在白名单里”，
        错别字与合法未登记词走的是**同一条**分支。用错别字只会让拼写检查器把一个
        **故意的测试输入**当成文档笔误报出来（`typos` 的行内忽略对它不起作用，实测），
        而 `typos` 对 docs 里的真笔误是有用的——不应为了这个把它全局关掉。
        """
        engine = Engine(cwd=self.tmp)
        engine.init_tree("t", "g")
        with self.assertRaises(EngineError) as ctx:
            engine.expand("t", "root", [{"id": "n1", "goal": "g", "acceptance": acceptance(),
                                         "role": "sre"}])
        self.assertIn("sre", str(ctx.exception))
        self.assertIn("disflow.json", str(ctx.exception))

    def test_custom_stage_and_role_allowed_via_config(self):
        """换个项目看板定义本来就该不一样 ⇒ 可配（板定义走 boards.json）。"""
        cfg = Config(boards=DEFAULT_BOARDS + (Board("blocked", "阻塞"),),
                     roles=ROLES + ("sre",))
        engine = Engine(cwd=self.tmp, cfg=cfg)
        engine.init_tree("t", "g")
        engine.expand("t", "root", [{"id": "n1", "goal": "g", "acceptance": acceptance(),
                                     "stage": "blocked", "role": "sre"}])
        node = engine.load()["trees"]["t"]["nodes"]["n1"]
        self.assertEqual((node["stage"], node["role"]), ("blocked", "sre"))

    def test_missing_stage_or_role_is_still_allowed(self):
        """没写就是不写——校验只针对**写了但值不对**。"""
        engine = Engine(cwd=self.tmp)
        engine.init_tree("t", "g")
        engine.expand("t", "root", [{"id": "n1", "goal": "g", "acceptance": acceptance()}])
        node = engine.load()["trees"]["t"]["nodes"]["n1"]
        self.assertEqual(node["stage"], "dev")

    def test_unknown_stage_from_another_writer_replays_and_renders(self):
        """**可重放性**：事件里出现本版本不认识的 stage 时，读路径不能崩。

        这正是校验必须放在写时、不能放 reducer 的理由：reducer 一依赖配置，
        换个配置或换台机器重放旧事件就失败了——事件流不可重放是比错别字更严重的问题。
        `views.py` 的 `order += sorted(...)` 就是为此存在的**有意容忍**，别删。
        """
        engine = Engine(cwd=self.tmp)
        engine.init_tree("t", "g")
        # 直接落一条“来自未来版本”的事件（绕开写时校验，模拟别的写者/更新的版本）
        with store.lock(engine.storage):
            store.append_event(
                engine.storage, "node.expand", project=project, slug="t", node="root",
                extra={"children": [{"id": "f1", "kind": "task", "stage": "未来的列",
                                     "role": "未来角色", "goal": "g",
                                     "acceptance": acceptance()}]})

        state = engine.load()
        self.assertIn("f1", state["trees"]["t"]["nodes"])
        text = "\n".join(views.board_lines(state["trees"]["t"]))
        self.assertIn("f1", text)
        self.assertIn("未来的列", text)


if __name__ == "__main__":
    unittest.main()
