"""
TODO-047 关注表 4 表分流 — 全方位回归测试

测试矩阵：
  Layer 1: watchlist_manager 单元功能（隔离临时数据）
  Layer 2: 数据迁移完整性
  Layer 3: app.py Tab3 前端结构
  Layer 4: main.py auto_add_to_watchlist 集成
  Layer 5: 初心对照（验收标准 7 条）

运行：python test_todo_047.py
"""
import sys
import os
import json
import shutil
import tempfile
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding='utf-8')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# --------------------------------------------------
# 测试结果汇总
# --------------------------------------------------
PASSED = 0
FAILED = 0
FAIL_DETAILS = []


def assert_true(cond, msg):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {msg}")
    else:
        FAILED += 1
        FAIL_DETAILS.append(msg)
        print(f"  ❌ {msg}")


def assert_eq(a, b, msg):
    assert_true(a == b, f"{msg}（实际 {a!r} vs 期望 {b!r}）")


# --------------------------------------------------
# 隔离工具：备份/恢复 真实 4 表（避免污染生产数据）
# --------------------------------------------------
WATCHLIST_FILES = ['watchlist_model.json', 'watchlist_toohard.json',
                   'watchlist_my.json', 'blacklist.json']
BACKUPS = {}


def backup_real_data():
    """备份真实 4 表数据"""
    for f in WATCHLIST_FILES:
        path = os.path.join(SCRIPT_DIR, f)
        if os.path.exists(path):
            with open(path, encoding='utf-8') as fp:
                BACKUPS[f] = fp.read()
        else:
            BACKUPS[f] = None
    print(f"  💾 备份 {len([v for v in BACKUPS.values() if v is not None])} 个真实文件")


def restore_real_data():
    """恢复真实 4 表数据"""
    for f, content in BACKUPS.items():
        path = os.path.join(SCRIPT_DIR, f)
        if content is not None:
            with open(path, 'w', encoding='utf-8') as fp:
                fp.write(content)
        elif os.path.exists(path):
            os.remove(path)
    print(f"  ♻️ 恢复真实数据完成")


def reset_test_tables():
    """清空 4 表（测试用）"""
    for f in WATCHLIST_FILES:
        path = os.path.join(SCRIPT_DIR, f)
        with open(path, 'w', encoding='utf-8') as fp:
            json.dump([], fp)


# ========================================================
# Layer 1: watchlist_manager 单元功能
# ========================================================
def layer_1_unit():
    print("\n" + "=" * 60)
    print("Layer 1: watchlist_manager 单元功能（隔离测试）")
    print("=" * 60)

    backup_real_data()
    try:
        reset_test_tables()

        # ★ 要重新 import 让模块认到空表
        import importlib
        import watchlist_manager
        importlib.reload(watchlist_manager)
        wm = watchlist_manager

        # 1.1 加新股到 model
        print("\n[1.1] add_to_model 基本添加")
        ok, msg = wm.add_to_model({"code": "600519", "name": "贵州茅台", "category": "白酒"})
        assert_true(ok, "新股加入 model 表成功")
        assert_eq(len(wm._load('model')), 1, "model 表 1 只")

        # 1.2 重复添加（应拒绝）
        print("\n[1.2] add_to_model 重复添加")
        ok, msg = wm.add_to_model({"code": "600519", "name": "贵州茅台"})
        assert_true(not ok, "重复加入被拒绝")
        assert_true("已在 model 表" in msg, f"拒绝原因正确：{msg}")

        # 1.3 mark_too_hard：从 model → toohard
        print("\n[1.3] mark_too_hard 从 model 移到 toohard")
        ok, msg = wm.mark_too_hard("600519")
        assert_true(ok, "标记太难成功")
        assert_eq(len(wm._load('model')), 0, "model 表清空")
        assert_eq(len(wm._load('toohard')), 1, "toohard 表 1 只")
        toohard_item = wm._load('toohard')[0]
        assert_eq(toohard_item['analysis_status'], 'pending', "状态默认 pending")

        # 1.4 mark_analyzing
        print("\n[1.4] mark_analyzing 标记分析中")
        ok, msg = wm.mark_analyzing("600519", note="管理层访谈中")
        assert_true(ok, "标记分析中成功")
        item = wm._load('toohard')[0]
        assert_eq(item['analysis_status'], 'analyzing', "状态变 analyzing")
        assert_eq(item['analyzing_note'], '管理层访谈中', "备注保存")

        # 1.5 mark_good：从 toohard → my
        print("\n[1.5] mark_good 从 toohard 移到 my")
        ok, msg = wm.mark_good("600519")
        assert_true(ok, "标记好成功")
        assert_eq(len(wm._load('toohard')), 0, "toohard 表清空")
        assert_eq(len(wm._load('my')), 1, "my 表 1 只")
        my_item = wm._load('my')[0]
        assert_eq(my_item['analysis_result'], 'good', "结果标记为 good")
        assert_true('analysis_status' not in my_item, "analysis_status 已清理")

        # 1.6 防重新增：已在 my 不能加 model
        print("\n[1.6] add_to_model 防回灌（已在 my 表）")
        ok, msg = wm.add_to_model({"code": "600519", "name": "贵州茅台"})
        assert_true(not ok, "已在 my 表的不能再加 model")
        assert_true("已在 my 表" in msg, f"拒绝原因正确：{msg}")

        # 1.7 remove_from_my
        print("\n[1.7] remove_from_my 移除")
        ok, msg = wm.remove_from_my("600519")
        assert_true(ok, "从 my 表移除成功")
        assert_eq(len(wm._load('my')), 0, "my 表清空")

        # 1.8 mark_bad：从 toohard → blacklist
        print("\n[1.8] mark_bad 移到黑名单 12 个月")
        wm.add_to_model({"code": "002450", "name": "康得新"})
        wm.mark_too_hard("002450")
        ok, msg = wm.mark_bad("002450", blacklist_months=12)
        assert_true(ok, "标记坏成功")
        assert_eq(len(wm._load('blacklist')), 1, "blacklist 1 只")
        bl_item = wm._load('blacklist')[0]
        until = datetime.strptime(bl_item['blacklist_until'], '%Y-%m-%d')
        days = (until - datetime.now()).days
        assert_true(355 <= days <= 365, f"到期日约 1 年（{days} 天）")
        assert_eq(bl_item['analysis_result'], 'bad', "结果标记 bad")

        # 1.9 防重：黑名单未到期不能再加 model
        print("\n[1.9] add_to_model 黑名单未到期防重")
        ok, msg = wm.add_to_model({"code": "002450", "name": "康得新"})
        assert_true(not ok, "黑名单未到期被拒")
        assert_true("黑名单" in msg, f"拒绝原因含'黑名单'：{msg}")

        # 1.10 cleanup_expired_blacklist：构造已过期再清
        print("\n[1.10] cleanup_expired_blacklist 到期清理")
        bl = wm._load('blacklist')
        bl[0]['blacklist_until'] = '2020-01-01'  # 强制过期
        wm._save('blacklist', bl)
        n = wm.cleanup_expired_blacklist()
        assert_eq(n, 1, "清理 1 条过期")
        assert_eq(len(wm._load('blacklist')), 0, "黑名单清空")

        # 1.11 已过期黑名单可以重新加 model
        print("\n[1.11] add_to_model 黑名单过期后可重新加")
        # 重新构造一只过期的
        bl = [{"code": "002450", "name": "康得新", "blacklist_until": "2020-01-01"}]
        wm._save('blacklist', bl)
        ok, msg = wm.add_to_model({"code": "002450", "name": "康得新"})
        assert_true(ok, "过期黑名单的股可重新加 model")
        assert_eq(len(wm._load('blacklist')), 0, "过期项被自动清出黑名单")

        # 1.12 mark_too_hard 从 my 表
        print("\n[1.12] mark_too_hard 从 my 表也行")
        wm.add_to_model({"code": "601318", "name": "中国平安"})
        wm.mark_too_hard("601318")  # → toohard
        wm.mark_good("601318")      # → my
        ok, msg = wm.mark_too_hard("601318")  # 从 my → toohard
        assert_true(ok, "从 my 重新标记太难")
        assert_eq(len(wm._load('my')), 0, "my 表清空")
        assert_eq(len(wm._load('toohard')), 1, "回到 toohard")

        # 1.13 不存在的股操作
        print("\n[1.13] 不存在的股操作返回 False")
        ok, msg = wm.mark_good("999999")
        assert_true(not ok, "mark_good 不存在的股")
        ok, msg = wm.mark_bad("999999")
        assert_true(not ok, "mark_bad 不存在的股")
        ok, msg = wm.remove_from_my("999999")
        assert_true(not ok, "remove_from_my 不存在的股")
        ok, msg = wm.mark_analyzing("999999")
        assert_true(not ok, "mark_analyzing 不存在的股")

        # 1.14 get_all_blocked_codes
        print("\n[1.14] get_all_blocked_codes 集合正确")
        # 当前：toohard 1 只 (601318)
        blocked = wm.get_all_blocked_codes()
        assert_true("601318" in blocked, "toohard 中的股在 blocked 集合")

        # 1.15 get_summary（先把表全清，避免上文残留干扰断言）
        print("\n[1.15] get_summary 计数正确")
        reset_test_tables()
        wm.add_to_model({"code": "600519", "name": "茅台"})  # model 1
        wm.add_to_model({"code": "000333", "name": "美的"})  # model 2
        wm.mark_too_hard("000333")                            # toohard 1
        s = wm.get_summary()
        assert_eq(s['model'], 1, "model 统计")
        assert_eq(s['toohard'], 1, "toohard 统计")
        assert_eq(s['my'], 0, "my 统计")
        assert_eq(s['blacklist'], 0, "blacklist 统计（不含已过期）")

        # 1.16 代码补 6 位
        print("\n[1.16] 代码 zfill 补 6 位")
        reset_test_tables()
        ok, msg = wm.add_to_model({"code": "519", "name": "测试"})
        assert_true(ok, "代码 519 加入")
        items = wm._load('model')
        assert_eq(items[0]['code'], '000519', "代码自动补 6 位")

    finally:
        restore_real_data()


# ========================================================
# Layer 2: 数据迁移完整性
# ========================================================
def layer_2_migration():
    print("\n" + "=" * 60)
    print("Layer 2: 数据迁移完整性")
    print("=" * 60)

    # 验证现有 watchlist_my.json 是否含 11 只迁移后股票
    my_path = os.path.join(SCRIPT_DIR, 'watchlist_my.json')
    if not os.path.exists(my_path):
        assert_true(False, "watchlist_my.json 不存在")
        return
    with open(my_path, encoding='utf-8') as f:
        my = json.load(f)
    assert_true(len(my) >= 10, f"my 表至少 10 只（实际 {len(my)}）")

    # 字段完整性
    required_fields = ['code', 'name']
    for it in my:
        for f in required_fields:
            assert_true(f in it, f"{it.get('code', '?')} 含必要字段 {f}")

    # 代码 6 位
    bad_code = [it for it in my if len(str(it.get('code', ''))) != 6]
    assert_eq(len(bad_code), 0, "所有代码 6 位")


# ========================================================
# Layer 3: app.py Tab3 前端结构
# ========================================================
def layer_3_frontend():
    print("\n" + "=" * 60)
    print("Layer 3: app.py Tab3 前端结构")
    print("=" * 60)

    import py_compile
    try:
        py_compile.compile(os.path.join(SCRIPT_DIR, 'app.py'), doraise=True)
        assert_true(True, "app.py 编译通过")
    except Exception as e:
        assert_true(False, f"app.py 编译失败：{e}")
        return

    with open(os.path.join(SCRIPT_DIR, 'app.py'), encoding='utf-8') as f:
        src = f.read()

    # 关键串：4 子区
    keys_subtabs = ['📊 模型推荐', '🤔 太难表', '⭐ 我的关注', '🚫 黑名单']
    for k in keys_subtabs:
        assert_true(k in src, f"含子区标识 {k}")

    # 关键串：5 个操作函数
    keys_funcs = ['mark_too_hard', 'mark_good', 'mark_bad', 'mark_analyzing', 'remove_from_my']
    for k in keys_funcs:
        assert_true(k in src, f"调用 {k}()")

    # 关键串：操作按钮
    keys_btns = ['🤔 太难', '✅好', '❌坏', '🔬中', '🗑️ 取消']
    for k in keys_btns:
        assert_true(k in src, f"含按钮文本 {k}")

    # 关键串：分析中置顶
    assert_true("analyzing" in src and "_analyzing + _pending" in src, "分析中状态置顶逻辑")

    # 关键串：直接加我的关注表单
    assert_true('➕ 直接加入我的关注' in src, "含➕直接加入表单")

    # AST：检查 _render_stock_row 函数定义
    import ast
    tree = ast.parse(src)
    fn_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert_true('_render_stock_row' in fn_names, "_render_stock_row 函数定义存在")


# ========================================================
# Layer 4: main.py auto_add_to_watchlist 集成
# ========================================================
def layer_4_integration():
    print("\n" + "=" * 60)
    print("Layer 4: main.py 集成测试")
    print("=" * 60)

    backup_real_data()
    try:
        reset_test_tables()

        import importlib
        import watchlist_manager
        importlib.reload(watchlist_manager)
        import main
        importlib.reload(main)

        # 4.1 auto_add_to_watchlist 接入新模块
        candidates = [
            # 应加入：basic 全过 + buy_watch + 是 king
            {"code": "600036", "name": "招商银行", "passed": True,
             "signal": "buy_watch", "is_10y_king": True, "total_score": 45},
            # 不加：未通过基本面
            {"code": "000001", "name": "平安银行", "passed": False,
             "signal": "buy_watch", "is_10y_king": True, "total_score": 40},
            # 不加：是 buy 信号（价格已合适，自动加入持仓提醒，不放关注表）
            {"code": "601318", "name": "中国平安", "passed": True,
             "signal": "buy", "is_10y_king": True, "total_score": 48},
            # 不加：质量不够（既非 king 也非 good_quality）
            {"code": "002415", "name": "海康威视", "passed": True,
             "signal": "buy_watch", "is_10y_king": False, "is_good_quality": False,
             "total_score": 30},
        ]
        main.auto_add_to_watchlist(candidates, max_new_per_day=10)

        model = watchlist_manager._load('model')
        codes = [it['code'] for it in model]
        assert_true('600036' in codes, "招商银行（合格）已加入 model")
        assert_true('000001' not in codes, "平安银行（基本面不过）未加入")
        assert_true('601318' not in codes, "中国平安（buy 信号）未加入")
        assert_true('002415' not in codes, "海康威视（质量不够）未加入")

        # 4.2 重跑相同候选不重复添加
        main.auto_add_to_watchlist(candidates, max_new_per_day=10)
        model2 = watchlist_manager._load('model')
        assert_eq(len(model2), len(model), "重跑相同候选不重复添加")

        # 4.3 已在 my 表的不会加 model
        watchlist_manager._save('my', [{"code": "600036", "name": "招商银行"}])
        watchlist_manager._save('model', [])  # 清空 model
        main.auto_add_to_watchlist(candidates, max_new_per_day=10)
        model3 = watchlist_manager._load('model')
        codes3 = [it['code'] for it in model3]
        assert_true('600036' not in codes3, "已在 my 表的不会再加 model")

        # 4.4 holdings 中的不会加 model
        # （main 函数自己读 holdings.json，本测试不动它）

    finally:
        restore_real_data()


# ========================================================
# Layer 5: 初心对照（验收标准 7 条）
# ========================================================
def layer_5_acceptance():
    print("\n" + "=" * 60)
    print("Layer 5: 初心对照（验收标准 7 条）")
    print("=" * 60)

    # 验收 1：4 个 JSON 文件创建 + 数据迁移
    for f in WATCHLIST_FILES:
        path = os.path.join(SCRIPT_DIR, f)
        assert_true(os.path.exists(path), f"验收①：{f} 存在")

    # 验收 2：watchlist_manager 5 个核心函数
    import watchlist_manager
    expected_fns = ['add_to_model', 'mark_too_hard', 'mark_good',
                    'mark_bad', 'mark_analyzing', 'remove_from_my']
    for fn in expected_fns:
        assert_true(hasattr(watchlist_manager, fn),
                    f"验收②：watchlist_manager.{fn} 存在")

    # 验收 3：app.py Tab3 4 子区
    with open(os.path.join(SCRIPT_DIR, 'app.py'), encoding='utf-8') as f:
        src = f.read()
    assert_true("_t3_subtabs = st.tabs([" in src, "验收③：Tab3 用 st.tabs 4 子区结构")

    # 验收 4：每只股操作按钮
    assert_true('🤔 太难' in src and '✅好' in src and '🗑️ 取消' in src,
                "验收④：3 类按钮齐全")

    # 验收 5：黑名单到期自动恢复（每天检查）→ main.py 启动调
    with open(os.path.join(SCRIPT_DIR, 'main.py'), encoding='utf-8') as f:
        main_src = f.read()
    assert_true('cleanup_expired_blacklist()' in main_src,
                "验收⑤：main.py 调用 cleanup_expired_blacklist（每次扫描即每天）")

    # 验收 6：分析中状态置顶
    assert_true("_analyzing + _pending" in src, "验收⑥：分析中状态置顶")

    # 验收 7：核心模块可导入（兼容性）
    try:
        from watchlist_manager import (
            add_to_model, mark_too_hard, mark_good, mark_bad,
            mark_analyzing, remove_from_my, cleanup_expired_blacklist,
            get_all_blocked_codes, get_summary, migrate_old_watchlist
        )
        assert_true(True, "验收⑦：10 个核心 API 全部可导入")
    except Exception as e:
        assert_true(False, f"验收⑦：导入失败 {e}")


# ========================================================
# 主入口
# ========================================================
if __name__ == "__main__":
    print("=" * 60)
    print("TODO-047 关注表 4 表分流 — 全方位回归测试")
    print("=" * 60)

    layer_1_unit()
    layer_2_migration()
    layer_3_frontend()
    layer_4_integration()
    layer_5_acceptance()

    print("\n" + "=" * 60)
    print(f"测试结果：通过 {PASSED} / 失败 {FAILED} / 总 {PASSED + FAILED}")
    print("=" * 60)
    if FAILED:
        print("\n失败明细：")
        for d in FAIL_DETAILS:
            print(f"  ❌ {d}")
        sys.exit(1)
    else:
        print("✅ 全部通过")
        sys.exit(0)
