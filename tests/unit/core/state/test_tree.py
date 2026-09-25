"""tree 模块的单元测试（标准库 unittest，无第三方依赖）。

位置：tests/unit/core/state/
运行：在仓库根执行 `python run_tests.py`。
覆盖：
  - 草案 §6.3 的五级寻址：容器 → 条目 → 字段 → 列表元素 → 字典键；
  - 草案 §7.2 / D-34 的新增、删除、替换语义，以及"列表增删属于修改该列表"；
  - "路径不存在"与"值是 None"的区分；写操作不自动创建中间节点；
  - §13 涉及的确定性：字典键顺序稳定。
"""

import unittest

from core.state import (
    PathNotFoundError,
    PathSyntaxError,
    StateConflictError,
    StateShapeError,
    add_key,
    exists,
    get,
    list_insert,
    list_remove,
    remove_key,
    replace,
)


def make_state() -> dict:
    """造一棵覆盖五级寻址的示例树。

    输入：无。
    输出：新的示例 State 字典；每次调用返回新对象，避免用例之间互相污染。
    变量：
        state: 本函数构造的示例树，根是 dict，内部只用纯数据类型。
    """
    state = {
        "system": {"turn": 1, "weather": None},
        "entities": {
            "units": {
                "u1": {
                    "hp": 10,
                    "tags": ["infantry", "land"],
                    "attrs": {"0": "zero-key"},
                }
            }
        },
        "nodes": [{"id": "n0"}, {"id": "n1"}],
    }
    return state


class TestGet(unittest.TestCase):
    """get：按路径读取值。"""

    def test_whole_tree(self):
        """"" 读取整棵树，返回的就是原对象本身。"""
        state = make_state()
        self.assertIs(get(state, ""), state)

    def test_five_level_addressing(self):
        """容器 → 条目 → 字段 → 列表元素 / 字典键 都能寻址。"""
        state = make_state()
        self.assertEqual(get(state, "/entities/units/u1/tags/0"), "infantry")
        self.assertEqual(get(state, "/entities/units/u1/attrs/0"), "zero-key")

    def test_stored_none_is_a_value(self):
        """值就是 None 时正常返回 None，不算路径不存在。"""
        self.assertIsNone(get(make_state(), "/system/weather"))

    def test_missing_key(self):
        """字典里没有这个键 → PathNotFoundError。"""
        with self.assertRaises(PathNotFoundError):
            get(make_state(), "/system/not_there")

    def test_missing_index(self):
        """列表下标越界 → PathNotFoundError。"""
        with self.assertRaises(PathNotFoundError):
            get(make_state(), "/nodes/2")

    def test_scalar_in_the_middle(self):
        """中途遇到标量无法继续寻址 → StateShapeError。"""
        with self.assertRaises(StateShapeError):
            get(make_state(), "/system/turn/x")

    def test_bad_list_index_syntax(self):
        """前导零、负数、"-" 都不是本引擎接受的列表下标写法。"""
        for bad_path in ("/nodes/01", "/nodes/-1", "/nodes/-"):
            with self.assertRaises(PathSyntaxError):
                get(make_state(), bad_path)

    def test_root_must_be_dict(self):
        """State 根不是 dict 属于编程错误。"""
        with self.assertRaises(TypeError):
            get([], "")


class TestExists(unittest.TestCase):
    """exists：只判断路径是否走得通。"""

    def test_none_value_exists(self):
        """值为 None 的路径也算存在。"""
        self.assertTrue(exists(make_state(), "/system/weather"))

    def test_missing_paths_are_false(self):
        """键不存在、下标越界、中途是标量都返回 False。"""
        self.assertFalse(exists(make_state(), "/system/not_there"))
        self.assertFalse(exists(make_state(), "/nodes/2"))
        self.assertFalse(exists(make_state(), "/system/turn/x"))

    def test_root_always_exists(self):
        """根路径恒存在。"""
        self.assertTrue(exists(make_state(), ""))

    def test_bad_index_syntax_still_raises(self):
        """路径写法错误不是"不存在"，仍然要抛异常。"""
        with self.assertRaises(PathSyntaxError):
            exists(make_state(), "/nodes/01")

    def test_exists_collapses_both_failure_kinds_to_false(self):
        """exists 把"找不到"和"形状不允许"都压平成 False（见 errors.py 说明）。"""
        state = make_state()
        self.assertFalse(exists(state, "/nodes/2"))
        self.assertFalse(exists(state, "/system/turn/x"))


class TestAddKey(unittest.TestCase):
    """add_key：新增（往容器里加一个键）。"""

    def test_add_nested_key(self):
        """在已有容器里新增键后可以立刻读到。"""
        state = make_state()
        add_key(state, "/entities/units/u1", "morale", 7)
        self.assertEqual(get(state, "/entities/units/u1/morale"), 7)

    def test_add_top_level_container(self):
        """根节点允许新增顶层容器（"" 表示根容器）。"""
        state = make_state()
        add_key(state, "", "weather", {"name": "clear"})
        self.assertEqual(get(state, "/weather/name"), "clear")

    def test_duplicate_key_conflicts(self):
        """键已存在时新增失败。"""
        with self.assertRaises(StateConflictError):
            add_key(make_state(), "/system", "turn", 2)

    def test_parent_must_exist(self):
        """不自动创建中间节点：父容器不存在直接报错。"""
        state = make_state()
        with self.assertRaises(PathNotFoundError):
            add_key(state, "/system/nope", "x", 1)
        self.assertNotIn("nope", state["system"])

    def test_target_must_be_dict(self):
        """目标是列表时不能当字典新增键。"""
        with self.assertRaises(StateShapeError):
            add_key(make_state(), "/nodes", "x", 1)

    def test_key_must_be_str(self):
        """键名类型错误属于编程错误。"""
        with self.assertRaises(TypeError):
            add_key(make_state(), "/system", 123, 1)


class TestRemoveKey(unittest.TestCase):
    """remove_key：删除（从容器里删掉一个键）。"""

    def test_remove_returns_value(self):
        """删除返回被删掉的键值，并且键真的没了。"""
        state = make_state()
        self.assertEqual(remove_key(state, "/entities/units/u1", "hp"), 10)
        self.assertFalse(exists(state, "/entities/units/u1/hp"))

    def test_remove_top_level_key(self):
        """根节点允许删除顶层键。"""
        state = make_state()
        self.assertEqual(remove_key(state, "", "nodes"), [{"id": "n0"}, {"id": "n1"}])

    def test_missing_key_conflicts(self):
        """删除不存在的键属于与数据冲突。"""
        with self.assertRaises(StateConflictError):
            remove_key(make_state(), "/system", "not_there")

    def test_target_must_be_dict(self):
        """目标不是 dict 时不能按键删除。"""
        with self.assertRaises(StateShapeError):
            remove_key(make_state(), "/nodes", "x")


class TestReplace(unittest.TestCase):
    """replace：修改（替换已有值）。"""

    def test_replace_returns_old_value(self):
        """替换返回旧值，新值立刻可读。"""
        state = make_state()
        self.assertEqual(replace(state, "/system/turn", 2), 1)
        self.assertEqual(get(state, "/system/turn"), 2)

    def test_replace_list_element(self):
        """列表元素也是"被替换的值"，直接用同一路径寻址。"""
        state = make_state()
        old_value = replace(state, "/nodes/0", {"id": "n0-new"})
        self.assertEqual(old_value, {"id": "n0"})
        self.assertEqual(get(state, "/nodes/0/id"), "n0-new")

    def test_replace_subtree_with_scalar(self):
        """允许把整棵子树替换成标量（不设路径禁区）。"""
        state = make_state()
        replace(state, "/entities/units/u1/tags", 0)
        self.assertEqual(get(state, "/entities/units/u1/tags"), 0)

    def test_replace_root_is_forbidden(self):
        """不允许替换整棵树。"""
        with self.assertRaises(StateConflictError):
            replace(make_state(), "", {})

    def test_replace_missing_target(self):
        """目标不存在时报 PathNotFoundError。"""
        with self.assertRaises(PathNotFoundError):
            replace(make_state(), "/system/not_there", 1)


class TestListOps(unittest.TestCase):
    """list_insert / list_remove：列表增删元素（属于"修改该列表"）。"""

    def test_insert_at_front_middle_and_end(self):
        """插入位置 0、中间、末尾都要正确，顺序保持不变。"""
        state = make_state()
        list_insert(state, "/nodes", 0, {"id": "first"})
        list_insert(state, "/nodes", 2, {"id": "middle"})
        list_insert(state, "/nodes", len(get(state, "/nodes")), {"id": "last"})
        self.assertEqual(
            [item["id"] for item in get(state, "/nodes")],
            ["first", "n0", "middle", "n1", "last"],
        )

    def test_insert_out_of_range(self):
        """插入下标允许 0..len，越界报冲突。"""
        with self.assertRaises(StateConflictError):
            list_insert(make_state(), "/nodes", 3, {"id": "x"})
        with self.assertRaises(StateConflictError):
            list_insert(make_state(), "/nodes", -1, {"id": "x"})

    def test_insert_index_must_be_int(self):
        """bool 是 int 的子类，但语义上不是合法下标。"""
        with self.assertRaises(TypeError):
            list_insert(make_state(), "/nodes", True, {"id": "x"})

    def test_insert_target_must_be_list(self):
        """目标不是 list 时报 StateShapeError。"""
        with self.assertRaises(StateShapeError):
            list_insert(make_state(), "/system", 0, 1)

    def test_remove_returns_element(self):
        """删除返回被删掉的元素，后面的元素前移。"""
        state = make_state()
        self.assertEqual(list_remove(state, "/nodes", 0), {"id": "n0"})
        self.assertEqual(get(state, "/nodes/0/id"), "n1")

    def test_remove_out_of_range(self):
        """越界删除、对空列表删除都报冲突。"""
        with self.assertRaises(StateConflictError):
            list_remove(make_state(), "/nodes", 2)
        state = make_state()
        replace(state, "/nodes", [])
        with self.assertRaises(StateConflictError):
            list_remove(state, "/nodes", 0)


class TestTreeContract(unittest.TestCase):
    """树层的整体契约测试。"""

    def test_insertion_order_is_stable(self):
        """字典键按插入顺序排列，遍历顺序可复现（§13 确定性要求）。"""
        state = make_state()
        add_key(state, "/system", "phase", "combat")
        self.assertEqual(list(state["system"].keys()), ["turn", "weather", "phase"])

    def test_editing_one_leaf_does_not_touch_siblings(self):
        """改一个叶子不影响兄弟节点。"""
        state = make_state()
        replace(state, "/entities/units/u1/hp", 3)
        self.assertEqual(get(state, "/entities/units/u1/tags"), ["infantry", "land"])
        self.assertEqual(get(state, "/system/turn"), 1)

    def test_functions_mutate_the_given_tree_in_place(self):
        """本层函数直接修改传入的那棵树（是否改真实 State 由上层决定）。"""
        state = make_state()
        replace(state, "/system/turn", 5)
        self.assertEqual(state["system"]["turn"], 5)


class TestIndexErrorPolicy(unittest.TestCase):
    """钉住"路径下标越界 → 找不到路；参数下标越界 → 操作冲突"的口径。"""

    def test_path_index_out_of_range_is_not_found(self):
        """路径里的下标越界属于寻址失败。"""
        state = make_state()
        with self.assertRaises(PathNotFoundError):
            get(state, "/nodes/2")
        with self.assertRaises(PathNotFoundError):
            replace(state, "/nodes/2", {"id": "x"})

    def test_parameter_index_out_of_range_is_conflict(self):
        """参数形式的下标越界属于操作冲突。"""
        state = make_state()
        with self.assertRaises(StateConflictError):
            list_insert(state, "/nodes", 3, {"id": "x"})
        with self.assertRaises(StateConflictError):
            list_remove(state, "/nodes", 2)

    def test_negative_index_syntax_vs_parameter(self):
        """路径里的 "-1" 是语法错；参数里的 -1 是操作冲突。"""
        with self.assertRaises(PathSyntaxError):
            get(make_state(), "/nodes/-1")
        with self.assertRaises(StateConflictError):
            list_insert(make_state(), "/nodes", -1, {"id": "x"})

    def test_failed_token_is_decoded(self):
        """failed_token 是反转义后的段文本，不是路径里写的转义形式。"""
        state = {"a/b": {}}
        with self.assertRaises(PathNotFoundError) as ctx:
            get(state, "/a~1b/not_there")
        self.assertEqual(ctx.exception.failed_token, "not_there")

        with self.assertRaises(PathNotFoundError) as ctx_escaped:
            get({"other": 1}, "/a~1b")
        self.assertEqual(ctx_escaped.exception.failed_token, "a/b")


if __name__ == "__main__":
    unittest.main()
