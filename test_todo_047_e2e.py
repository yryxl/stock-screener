"""
TODO-047 端到端 e2e 测试 — Playwright 真实点击 streamlit 页面

前置条件：streamlit run app.py --server.port 8502 已经在运行

测试用例：
  E1. 页面能加载，Tab3 可见
  E2. 4 个子区都存在
  E3. 模型推荐区显示候选股 → 点【太难】→ 太难表多 1 只 / 模型推荐少 1 只
  E4. 太难表点【🔬中】→ 该股置顶并显示"分析中"标记
  E5. 太难表点【✅好】→ 我的关注表多 1 只
  E6. 我的关注点【🗑️取消】→ 该股移除
  E7. 太难表点【❌坏】→ 黑名单多 1 只
  E8. 黑名单显示到期日

测试流程会污染真实 4 表，所以先备份再测，最后恢复。
"""
import sys
import os
import json
import time
import shutil

sys.stdout.reconfigure(encoding='utf-8')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
URL = "http://localhost:8502"

PASSED = 0
FAILED = 0
FAIL_DETAILS = []


def log_pass(msg):
    global PASSED
    PASSED += 1
    print(f"  ✅ {msg}")


def log_fail(msg):
    global FAILED
    FAILED += 1
    FAIL_DETAILS.append(msg)
    print(f"  ❌ {msg}")


# 备份/恢复
WATCHLIST_FILES = ['watchlist_model.json', 'watchlist_toohard.json',
                   'watchlist_my.json', 'blacklist.json']
BACKUPS = {}


def backup():
    for f in WATCHLIST_FILES:
        path = os.path.join(SCRIPT_DIR, f)
        if os.path.exists(path):
            BACKUPS[f] = open(path, encoding='utf-8').read()
        else:
            BACKUPS[f] = None
    print(f"💾 备份完成")


def restore():
    for f, c in BACKUPS.items():
        path = os.path.join(SCRIPT_DIR, f)
        if c is not None:
            with open(path, 'w', encoding='utf-8') as fp:
                fp.write(c)
        elif os.path.exists(path):
            os.remove(path)
    print(f"♻️ 恢复完成")


def setup_test_data():
    """构造测试数据：model 表 2 只测试股"""
    test_model = [
        {"code": "TEST01", "name": "测试股A", "category": "测试",
         "note": "e2e 测试用", "auto_added": True,
         "auto_added_date": "2026-04-19"},
        {"code": "TEST02", "name": "测试股B", "category": "测试",
         "note": "e2e 测试用", "auto_added": True,
         "auto_added_date": "2026-04-19"},
        {"code": "TEST03", "name": "测试股C", "category": "测试",
         "note": "e2e 测试用", "auto_added": True,
         "auto_added_date": "2026-04-19"},
    ]
    with open(os.path.join(SCRIPT_DIR, 'watchlist_model.json'), 'w', encoding='utf-8') as f:
        json.dump(test_model, f, ensure_ascii=False, indent=2)
    for fname in ['watchlist_toohard.json', 'watchlist_my.json', 'blacklist.json']:
        with open(os.path.join(SCRIPT_DIR, fname), 'w', encoding='utf-8') as f:
            json.dump([], f)
    print(f"📝 测试数据写入：model 表 3 只，其它表清空")


def count_table(name):
    """读 4 表当前数量"""
    fname = {'model': 'watchlist_model.json', 'toohard': 'watchlist_toohard.json',
             'my': 'watchlist_my.json', 'blacklist': 'blacklist.json'}[name]
    path = os.path.join(SCRIPT_DIR, fname)
    if not os.path.exists(path):
        return 0
    return len(json.load(open(path, encoding='utf-8')))


def main():
    from playwright.sync_api import sync_playwright

    backup()
    try:
        setup_test_data()
        # 计数初始
        c0 = {t: count_table(t) for t in ['model', 'toohard', 'my', 'blacklist']}
        print(f"初始：{c0}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(15000)

            # E1. 加载主页
            print("\n[E1] 加载页面")
            page.goto(URL)
            page.wait_for_load_state("networkidle", timeout=30000)
            time.sleep(3)  # streamlit 二次渲染
            title = page.title()
            log_pass(f"页面加载完成，title={title!r}")

            # 截屏存档
            shot_path = os.path.join(SCRIPT_DIR, 'e2e_screenshot_01_home.png')
            page.screenshot(path=shot_path, full_page=True)
            print(f"  📸 已保存截图 {shot_path}")

            # E2. 切到 Tab3 关注表
            print("\n[E2] 切到关注表 Tab")
            try:
                # 点 Tab3 标签
                page.get_by_role("tab", name="关注表（4 层流转）").click(timeout=5000)
                time.sleep(2)
                log_pass("已点击关注表 tab")
            except Exception as e:
                # 兜底：找含"关注"的 tab
                try:
                    tabs = page.locator("[role='tab']").all_text_contents()
                    print(f"  当前 tabs: {tabs}")
                    page.locator(f"[role='tab']").filter(has_text="关注").first.click()
                    time.sleep(2)
                    log_pass("已点击含'关注'的 tab")
                except Exception as e2:
                    log_fail(f"无法切到关注表 Tab：{e2}")
                    return

            page.screenshot(path=os.path.join(SCRIPT_DIR, 'e2e_screenshot_02_tab3.png'),
                           full_page=True)

            # E3. 检查 4 个子区
            print("\n[E3] 检查 4 子区都存在")
            page_text = page.content()
            for k in ["📊 模型推荐", "🤔 太难表", "⭐ 我的关注", "🚫 黑名单"]:
                if k in page_text:
                    log_pass(f"子区存在：{k}")
                else:
                    log_fail(f"子区缺失：{k}")

            # E4. 模型推荐子区可见 3 只测试股
            print("\n[E4] 模型推荐子区可见测试数据")
            for code in ["TEST01", "TEST02", "TEST03"]:
                if code in page_text:
                    log_pass(f"测试股 {code} 显示")
                else:
                    log_fail(f"测试股 {code} 不显示")

            # E5. 点击 TEST01 的【🤔 太难】按钮
            print("\n[E5] 点【🤔 太难】按钮")
            try:
                # streamlit 按钮的 key 是 "toohard_TEST01_0"
                # 使用 button text 定位（可能多个，取第一个）
                btn = page.get_by_role("button", name="🤔 太难").first
                btn.click(timeout=5000)
                time.sleep(3)  # 等 streamlit rerun
                model_after = count_table('model')
                toohard_after = count_table('toohard')
                if model_after == c0['model'] - 1 and toohard_after == c0['toohard'] + 1:
                    log_pass(f"点【太难】生效：model {c0['model']}→{model_after}, toohard {c0['toohard']}→{toohard_after}")
                else:
                    log_fail(f"点【太难】计数不对：model {c0['model']}→{model_after}, toohard {c0['toohard']}→{toohard_after}")
                # 更新基线
                c0['model'] = model_after
                c0['toohard'] = toohard_after
            except Exception as e:
                log_fail(f"点【太难】异常：{e}")

            page.screenshot(path=os.path.join(SCRIPT_DIR, 'e2e_screenshot_03_after_toohard.png'),
                           full_page=True)

            # E6. 切到太难表子区，点【✅好】
            print("\n[E6] 切到太难表子区，点【✅好】")
            try:
                page.locator("[role='tab']").filter(has_text="🤔 太难表").last.click()
                time.sleep(2)
                btn = page.get_by_role("button", name="✅好").first
                btn.click(timeout=5000)
                time.sleep(3)
                toohard_after = count_table('toohard')
                my_after = count_table('my')
                if toohard_after == c0['toohard'] - 1 and my_after == c0['my'] + 1:
                    log_pass(f"点【好】生效：toohard {c0['toohard']}→{toohard_after}, my {c0['my']}→{my_after}")
                else:
                    log_fail(f"点【好】计数不对：toohard {c0['toohard']}→{toohard_after}, my {c0['my']}→{my_after}")
                c0['toohard'] = toohard_after
                c0['my'] = my_after
            except Exception as e:
                log_fail(f"点【好】异常：{e}")

            # E7. 切回模型推荐再加一只到太难表，然后点【❌坏】
            print("\n[E7] 准备测试【❌坏】：先加一只到太难表")
            try:
                page.locator("[role='tab']").filter(has_text="📊 模型推荐").last.click()
                time.sleep(2)
                page.get_by_role("button", name="🤔 太难").first.click()
                time.sleep(3)
                c0['model'] = count_table('model')
                c0['toohard'] = count_table('toohard')

                # 切到太难表，点【❌坏】
                page.locator("[role='tab']").filter(has_text="🤔 太难表").last.click()
                time.sleep(2)
                page.get_by_role("button", name="❌坏").first.click()
                time.sleep(3)
                toohard_after = count_table('toohard')
                bl_after = count_table('blacklist')
                if toohard_after == c0['toohard'] - 1 and bl_after == c0['blacklist'] + 1:
                    log_pass(f"点【坏】生效：toohard {c0['toohard']}→{toohard_after}, blacklist {c0['blacklist']}→{bl_after}")
                else:
                    log_fail(f"点【坏】计数不对：toohard {c0['toohard']}→{toohard_after}, blacklist {c0['blacklist']}→{bl_after}")
                c0['toohard'] = toohard_after
                c0['blacklist'] = bl_after
            except Exception as e:
                log_fail(f"点【坏】异常：{e}")

            # E8. 切回模型推荐，再加一只到太难表，然后点【🔬中】
            print("\n[E8] 测试【🔬中】（分析中置顶）")
            try:
                page.locator("[role='tab']").filter(has_text="📊 模型推荐").last.click()
                time.sleep(2)
                page.get_by_role("button", name="🤔 太难").first.click()
                time.sleep(3)
                c0['model'] = count_table('model')
                c0['toohard'] = count_table('toohard')

                page.locator("[role='tab']").filter(has_text="🤔 太难表").last.click()
                time.sleep(2)
                page.get_by_role("button", name="🔬中").first.click()
                time.sleep(3)
                # 检查 toohard 文件中状态变 analyzing
                toohard_data = json.load(open(os.path.join(SCRIPT_DIR, 'watchlist_toohard.json'), encoding='utf-8'))
                analyzing_count = sum(1 for it in toohard_data if it.get('analysis_status') == 'analyzing')
                if analyzing_count >= 1:
                    log_pass(f"点【🔬中】生效：toohard 中有 {analyzing_count} 只 analyzing")
                else:
                    log_fail(f"点【🔬中】未标记 analyzing")
                # 检查页面有"🔬 分析中"显示（不 reload，避免主 tab 重置）
                if "🔬 分析中" in page.content():
                    log_pass("页面显示'🔬 分析中'置顶标识")
                else:
                    log_fail("页面未显示'🔬 分析中'标识")
            except Exception as e:
                log_fail(f"点【🔬中】异常：{e}")

            # E9. 切到我的关注，点【🗑️ 取消】
            print("\n[E9] 我的关注点【🗑️ 取消】")
            try:
                page.locator("[role='tab']").filter(has_text="⭐ 我的关注").last.click()
                time.sleep(2)
                my_before = count_table('my')
                page.get_by_role("button", name="🗑️ 取消").first.click()
                time.sleep(3)
                my_after = count_table('my')
                if my_after == my_before - 1:
                    log_pass(f"点【取消】生效：my {my_before}→{my_after}")
                else:
                    log_fail(f"点【取消】计数不对：my {my_before}→{my_after}")
            except Exception as e:
                log_fail(f"点【取消】异常：{e}")

            # E10. 黑名单显示到期日
            print("\n[E10] 黑名单子区显示到期日")
            try:
                page.locator("[role='tab']").filter(has_text="🚫 黑名单").last.click()
                time.sleep(2)
                content = page.content()
                if "到 20" in content and "解除" in content:
                    log_pass("黑名单子区显示'到 YYYY-MM-DD 解除'")
                else:
                    log_fail("黑名单子区未显示到期日")
            except Exception as e:
                log_fail(f"黑名单显示异常：{e}")

            page.screenshot(path=os.path.join(SCRIPT_DIR, 'e2e_screenshot_99_final.png'),
                           full_page=True)

            browser.close()
    finally:
        restore()

    print("\n" + "=" * 60)
    print(f"e2e 结果：通过 {PASSED} / 失败 {FAILED} / 总 {PASSED + FAILED}")
    print("=" * 60)
    if FAILED:
        for d in FAIL_DETAILS:
            print(f"  ❌ {d}")
        sys.exit(1)
    print("✅ 全部通过")


if __name__ == "__main__":
    main()
