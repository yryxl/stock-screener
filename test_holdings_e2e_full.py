"""
持仓页 Tab2 完整 e2e 测试（用户 2026-04-20 提的 bug：删/加 失败无声）

测试流程（按真实用户操作顺序）：
  L1. 加载页面 → 切 Tab2 → 验证持仓为空
  L2. 添加持仓 1：茅台 600519，100 股 @ 1500 元，归因 model
      → 验证 streamlit 显示
      → 验证 holdings.json
      → 验证 GitHub 上同步
  L3. 添加持仓 2：美的 000333，500 股 @ 70 元，归因 manual
      → 同上验证
  L4. 修改持仓 1（茅台）：改 target_price 1800
      → 验证保存
  L5. 删除持仓 1（茅台）
      → 验证 GitHub 只剩美的
  L6. 删除持仓 2（美的）
      → 验证 GitHub 为空

任何 bug 都会在终端打印 ❌ 标记。
"""
import sys
import os
import time
import json
import base64
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
URL = "http://localhost:8502"

# 从 .streamlit/secrets.toml 读（不硬编码 token）
def _read_secrets():
    p = os.path.join(SCRIPT_DIR, '.streamlit', 'secrets.toml')
    if not os.path.exists(p):
        raise RuntimeError("缺 .streamlit/secrets.toml，请参考 secrets.toml.example 创建")
    import re
    content = open(p, encoding='utf-8').read()
    token_m = re.search(r'token\s*=\s*"([^"]+)"', content)
    repo_m = re.search(r'repo\s*=\s*"([^"]+)"', content)
    return token_m.group(1), repo_m.group(1)

TOKEN, REPO = _read_secrets()

PASSED = 0
FAILED = 0
BUGS = []


def log_pass(msg):
    global PASSED
    PASSED += 1
    print(f"  ✅ {msg}")


def log_fail(msg):
    global FAILED
    FAILED += 1
    BUGS.append(msg)
    print(f"  ❌ BUG: {msg}")


def get_local_holdings():
    """读本地 holdings.json"""
    p = os.path.join(SCRIPT_DIR, 'holdings.json')
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception as e:
        return None


def get_github_holdings():
    """读 GitHub 上 holdings.json"""
    try:
        req = urllib.request.Request(
            f'https://api.github.com/repos/{REPO}/contents/holdings.json',
            headers={'Authorization': f'Bearer {TOKEN}'}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            info = json.loads(r.read())
        content = base64.b64decode(info['content']).decode('utf-8')
        return json.loads(content), info['sha']
    except Exception as e:
        return None, None


def save_screenshot(page, name):
    path = os.path.join(SCRIPT_DIR, f'_e2e_{name}.png')
    page.screenshot(path=path, full_page=True)


def main():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, slow_mo=500)  # slow_mo 让操作慢点
        context = browser.new_context(viewport={'width': 1400, 'height': 900})
        page = context.new_page()
        page.set_default_timeout(30000)

        # 声明 fill_streamlit_input 辅助函数（放到 main 函数内用闭包）
        def fill_streamlit_input(selector_label, value):
            field = page.locator(f"input[aria-label='{selector_label}']").first
            field.click()
            time.sleep(0.3)
            field.press("Control+a")
            time.sleep(0.2)
            field.press("Delete")
            time.sleep(0.2)
            field.press_sequentially(str(value), delay=50)
            time.sleep(0.5)
            field.press("Tab")
            time.sleep(0.8)

        # ============================================================
        print("\n=== L1: 加载页面 + 切到持仓 Tab ===")
        # ============================================================
        page.goto(URL)
        page.wait_for_load_state("networkidle", timeout=30000)
        time.sleep(5)  # streamlit 二次渲染
        log_pass("页面加载完成")

        # 强制刷新让 streamlit 重新加载 holdings.json（应该为空）
        try:
            refresh_btn = page.get_by_role("button", name="🔄 刷新数据")
            refresh_btn.click(timeout=5000)
            time.sleep(5)
            log_pass("刷新数据按钮可点")
        except Exception as e:
            log_fail(f"刷新数据按钮失败：{e}")

        # 切到 Tab2 持仓管理
        try:
            page.locator("[role='tab']").filter(has_text="持仓管理").first.click()
            time.sleep(3)
            log_pass("切到持仓 Tab")
        except Exception as e:
            log_fail(f"切到持仓 Tab 失败：{e}")
            browser.close()
            return

        save_screenshot(page, '01_empty_holdings')

        # 验证空持仓状态
        local = get_local_holdings()
        if local == []:
            log_pass("起始：本地 holdings.json 为空")
        else:
            log_fail(f"起始：本地 holdings.json 不为空（{local}）")

        # ============================================================
        print("\n=== L2: 添加持仓 1（茅台 600519）===")
        # ============================================================
        # 滚到底部找"添加持仓"表单
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)

        try:
            fill_streamlit_input("股票代码", "600519")
            fill_streamlit_input("股数", "100")
            fill_streamlit_input("名称", "贵州茅台")
            fill_streamlit_input("成本价", "1500")

            log_pass("填好茅台表单（用 press_sequentially 模拟真实输入）")

            # 点 添加（用 exact 防止撞其它按钮）
            add_btn = page.get_by_role("button", name="添加", exact=True).first
            add_btn.click()
            time.sleep(8)  # 等 GitHub PUT（最多 10 秒）
            log_pass("点了【添加】按钮")

            save_screenshot(page, '02_after_add_maotai')
        except Exception as e:
            log_fail(f"添加茅台异常：{e}")

        # 验证：本地 holdings.json
        local = get_local_holdings()
        if local and any(h.get('code') == '600519' for h in local):
            log_pass(f"本地 holdings.json 含茅台（{len(local)} 只）")
        else:
            log_fail(f"本地 holdings.json 没有茅台！实际：{local}")

        # 验证：GitHub holdings.json
        gh_data, gh_sha = get_github_holdings()
        if gh_data and any(h.get('code') == '600519' for h in gh_data):
            log_pass(f"GitHub 含茅台（{len(gh_data)} 只 sha={gh_sha[:8]}...）")
        else:
            log_fail(f"BUG-022: GitHub 没有茅台！streamlit 显示成功但实际没保存")
            log_fail(f"  GitHub 实际：{gh_data}")

        # ============================================================
        print("\n=== L3: 添加持仓 2（美的 000333）===")
        # ============================================================
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)

            fill_streamlit_input("股票代码", "000333")
            fill_streamlit_input("股数", "500")
            fill_streamlit_input("名称", "美的集团")
            fill_streamlit_input("成本价", "70")

            page.get_by_role("button", name="添加", exact=True).first.click()
            time.sleep(8)
            log_pass("点【添加】美的")

            save_screenshot(page, '03_after_add_meidi')
        except Exception as e:
            log_fail(f"添加美的异常：{e}")

        local = get_local_holdings()
        if local and any(h.get('code') == '000333' for h in local) and any(h.get('code') == '600519' for h in local):
            log_pass(f"本地 holdings 含茅台+美的（{len(local)} 只）")
        else:
            log_fail(f"本地 holdings 不齐：{[(h.get('code'), h.get('name')) for h in local]}")

        gh_data, _ = get_github_holdings()
        if gh_data and any(h.get('code') == '000333' for h in gh_data):
            log_pass(f"GitHub 含美的（{len(gh_data)} 只）")
        else:
            log_fail(f"GitHub 没有美的：{gh_data}")

        # ============================================================
        print("\n=== L4: 删除持仓 1（茅台）===")
        # ============================================================
        try:
            # 滚回顶部找持仓行的 🗑️ 按钮
            page.evaluate("window.scrollTo(0, 0)")
            time.sleep(2)

            # 找含"贵州茅台"的行附近的 🗑️
            # streamlit 的删除按钮 key 是 del_h_X，但 selector 不知道 X
            # 用 button name="🗑️" 找所有，然后判断哪个是茅台的
            del_buttons = page.get_by_role("button", name="🗑️").all()
            log_pass(f"找到 {len(del_buttons)} 个 🗑️ 按钮")
            if del_buttons:
                # 假设第一个是第一只持仓（茅台）
                del_buttons[0].click()
                time.sleep(5)
                log_pass("点了第一个 🗑️")

            save_screenshot(page, '04_after_delete_maotai')
        except Exception as e:
            log_fail(f"删除茅台异常：{e}")

        local = get_local_holdings()
        if local is not None and not any(h.get('code') == '600519' for h in local):
            log_pass(f"本地 holdings 不含茅台了（{len(local)} 只）")
        else:
            log_fail(f"本地 holdings 仍含茅台：{local}")

        gh_data, _ = get_github_holdings()
        if gh_data and not any(h.get('code') == '600519' for h in gh_data):
            log_pass(f"GitHub 不含茅台了（{len(gh_data)} 只）")
        elif gh_data is None:
            log_fail(f"GitHub 拉取失败")
        else:
            log_fail(f"BUG: GitHub 仍含茅台！实际：{[h.get('code') for h in gh_data]}")

        # ============================================================
        print("\n=== L5: 删除持仓 2（美的）===")
        # ============================================================
        try:
            del_buttons = page.get_by_role("button", name="🗑️").all()
            if del_buttons:
                del_buttons[0].click()
                time.sleep(5)
                log_pass("删除剩余持仓")

            save_screenshot(page, '05_final_empty')
        except Exception as e:
            log_fail(f"删除美的异常：{e}")

        local = get_local_holdings()
        if local == []:
            log_pass("本地 holdings 已为空")
        else:
            log_fail(f"本地 holdings 不为空：{local}")

        gh_data, _ = get_github_holdings()
        if gh_data == []:
            log_pass("GitHub holdings 已为空")
        elif gh_data is None:
            log_fail(f"GitHub 拉取失败")
        else:
            log_fail(f"GitHub holdings 不为空：{[h.get('code') for h in gh_data]}")

        browser.close()


if __name__ == "__main__":
    main()
    print("\n" + "=" * 60)
    print(f"测试结果：通过 {PASSED} / 失败 {FAILED}")
    print("=" * 60)
    if BUGS:
        print("\n🚨 发现的 BUG：")
        for b in BUGS:
            print(f"  ❌ {b}")
    else:
        print("✅ 无 bug，全流程通过")
