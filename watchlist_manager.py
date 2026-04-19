"""
TODO-047 关注表三层分流 + 太难表（2026-04-18 用户完整设计）

4 个表结构：
  watchlist_model.json    模型推荐关注（自动加入"基本面好但价格不到位"的股）
  watchlist_toohard.json  太难表（用户标记"看不懂/复杂"，含 analysis_status）
  watchlist_my.json       我的关注（用户精选 + 太难表"好"转入）
  blacklist.json          黑名单（太难表"坏"转入，1 年后自动恢复）

流程：
  模型扫描 → 自动加入 model 表
  用户从 model 点[太难] → 移到 toohard 表（status=pending）
  用户在 toohard 点[好] → 移到 my 表
  用户在 toohard 点[坏] → 移到 blacklist（1 年到期）
  用户在 toohard 点[分析中] → 留在 toohard（status=analyzing，置顶）
  用户在 my 点[取消] → 移除
  blacklist 1 年后自动恢复到 model 表（不重复加）
"""

import json
import os
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BEIJING = timezone(timedelta(hours=8))

WATCHLIST_FILES = {
    'model': 'watchlist_model.json',
    'toohard': 'watchlist_toohard.json',
    'my': 'watchlist_my.json',
    'blacklist': 'blacklist.json',
}


# 启动时确保 4 个文件都存在（在 _ensure_files 定义之后调用）
def _init_files_at_module_load():
    """模块导入时自动跑一次，保证 4 个文件存在。"""
    for fname in WATCHLIST_FILES.values():
        path = os.path.join(SCRIPT_DIR, fname)
        if not os.path.exists(path):
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump([], f)
            except Exception:
                pass


_init_files_at_module_load()


# ============================================================
# 基础读写
# ============================================================

def _ensure_files():
    """启动时确保 4 个表文件都存在（即使为空）。
    避免接手者在文件系统看不到 toohard/blacklist 误判数据丢失。
    """
    for fname in WATCHLIST_FILES.values():
        path = os.path.join(SCRIPT_DIR, fname)
        if not os.path.exists(path):
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump([], f)
            except Exception:
                pass


def _load(table_name):
    """读某个表（4 个表之一）"""
    fname = WATCHLIST_FILES.get(table_name)
    if not fname:
        return []
    path = os.path.join(SCRIPT_DIR, fname)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def _save(table_name, data):
    """写某个表"""
    fname = WATCHLIST_FILES.get(table_name)
    if not fname:
        return False
    path = os.path.join(SCRIPT_DIR, fname)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _today():
    return datetime.now(_BEIJING).strftime('%Y-%m-%d')


def _find_and_remove(table_name, code):
    """从某表中找出某代码并移除，返回该条目"""
    items = _load(table_name)
    code = str(code).zfill(6)
    found = None
    new_items = []
    for it in items:
        if str(it.get('code', '')).zfill(6) == code:
            found = it
        else:
            new_items.append(it)
    if found:
        _save(table_name, new_items)
    return found


def _exists(table_name, code):
    """某表是否含某代码"""
    code = str(code).zfill(6)
    return any(str(it.get('code', '')).zfill(6) == code
               for it in _load(table_name))


# ============================================================
# 状态转移操作
# ============================================================

def add_to_model(stock):
    """加入模型推荐表（main.py auto_add_to_watchlist 调用）

    跳过条件：已在任何表里（model/toohard/my/blacklist）+ 黑名单未到期
    """
    code = str(stock.get('code', '')).zfill(6)
    # 跳过已在 my / toohard 表（用户已知）
    for t in ('my', 'toohard'):
        if _exists(t, code):
            return False, f'已在 {t} 表'
    # 黑名单：检查是否过期
    blacklist = _load('blacklist')
    today = _today()
    for it in blacklist:
        if str(it.get('code', '')).zfill(6) == code:
            until = it.get('blacklist_until', '')
            if until and until > today:
                return False, f'在黑名单中，到 {until} 解除'
            # 已过期，先从黑名单移除
            blacklist = [x for x in blacklist
                         if str(x.get('code', '')).zfill(6) != code]
            _save('blacklist', blacklist)

    model = _load('model')
    if any(str(it.get('code', '')).zfill(6) == code for it in model):
        return False, '已在 model 表'

    new_item = {
        **stock,
        'code': code,
        'auto_added': True,
        'auto_added_date': _today(),
    }
    model.append(new_item)
    _save('model', model)
    return True, '已加入模型推荐'


def mark_too_hard(code):
    """从 model 表移到 toohard 表（用户点[太难]）"""
    item = _find_and_remove('model', code)
    if not item:
        # 可能从 my 表点的太难
        item = _find_and_remove('my', code)
    if not item:
        return False, '股票不在 model/my 表'

    toohard = _load('toohard')
    item['analysis_status'] = 'pending'
    item['toohard_added_date'] = _today()
    toohard.append(item)
    _save('toohard', toohard)
    return True, '已移入太难表'


def mark_analyzing(code, note=''):
    """太难表标记"分析中"（置顶展示）"""
    items = _load('toohard')
    code = str(code).zfill(6)
    for it in items:
        if str(it.get('code', '')).zfill(6) == code:
            it['analysis_status'] = 'analyzing'
            it['analyzing_since'] = _today()
            if note:
                it['analyzing_note'] = note
            _save('toohard', items)
            return True, '已标记分析中（置顶）'
    return False, '股票不在太难表'


def mark_good(code):
    """太难表标记"好" → 移到 my 表"""
    item = _find_and_remove('toohard', code)
    if not item:
        return False, '股票不在太难表'
    item['from_toohard_date'] = _today()
    item['analysis_result'] = 'good'
    item.pop('analysis_status', None)
    item.pop('analyzing_since', None)
    item.pop('analyzing_note', None)

    my = _load('my')
    my.append(item)
    _save('my', my)
    return True, '已移入我的关注（标记为好）'


def mark_bad(code, blacklist_months=12):
    """太难表标记"坏" → 移到黑名单 N 个月"""
    item = _find_and_remove('toohard', code)
    if not item:
        return False, '股票不在太难表'

    until = (datetime.now(_BEIJING) + timedelta(days=blacklist_months * 30)).strftime('%Y-%m-%d')
    item['blacklist_until'] = until
    item['blacklist_added_date'] = _today()
    item['analysis_result'] = 'bad'
    item.pop('analysis_status', None)

    blacklist = _load('blacklist')
    blacklist.append(item)
    _save('blacklist', blacklist)
    return True, f'已移入黑名单（{blacklist_months} 个月，到 {until}）'


def remove_from_my(code):
    """我的关注表移除（用户点[取消]）"""
    item = _find_and_remove('my', code)
    if item:
        return True, '已从我的关注表移除'
    return False, '股票不在我的关注表'


# ============================================================
# 工具函数
# ============================================================

def cleanup_expired_blacklist():
    """每天调用一次：清理已过期的黑名单（自动恢复到 model 池）"""
    blacklist = _load('blacklist')
    today = _today()
    still_blocked = []
    expired_count = 0
    for it in blacklist:
        until = it.get('blacklist_until', '')
        if until and until <= today:
            expired_count += 1
            # 不恢复到 model（让模型下次扫描时自然加回）
        else:
            still_blocked.append(it)
    if expired_count > 0:
        _save('blacklist', still_blocked)
    return expired_count


def get_all_blocked_codes():
    """返回当前在黑名单+太难+我的关注 表中的代码集合（用于 model 表去重）"""
    blocked = set()
    for t in ('toohard', 'my'):
        for it in _load(t):
            blocked.add(str(it.get('code', '')).zfill(6))
    today = _today()
    for it in _load('blacklist'):
        until = it.get('blacklist_until', '')
        if until and until > today:
            blocked.add(str(it.get('code', '')).zfill(6))
    return blocked


def get_summary():
    """返回 4 个表的统计"""
    return {
        'model': len(_load('model')),
        'toohard': len(_load('toohard')),
        'my': len(_load('my')),
        'blacklist': len([it for it in _load('blacklist')
                          if it.get('blacklist_until', '') > _today()]),
    }


def migrate_old_watchlist():
    """一次性迁移：把旧 watchlist.json 拆分到 my / model"""
    old_path = os.path.join(SCRIPT_DIR, 'watchlist.json')
    if not os.path.exists(old_path):
        return False, '旧 watchlist.json 不存在'
    try:
        with open(old_path, encoding='utf-8') as f:
            old = json.load(f)
    except Exception as e:
        return False, f'读取失败: {e}'

    my = _load('my')
    model = _load('model')
    moved_my = 0
    moved_model = 0
    for it in old:
        code = str(it.get('code', '')).zfill(6)
        if not code:
            continue
        if it.get('auto_added'):
            # 自动加入的 → model
            if not any(str(x.get('code', '')).zfill(6) == code for x in model):
                model.append(it)
                moved_model += 1
        else:
            # 手动加入的 → my
            if not any(str(x.get('code', '')).zfill(6) == code for x in my):
                my.append(it)
                moved_my += 1

    _save('my', my)
    _save('model', model)
    return True, f'迁移完成：{moved_my} 只到 my 表，{moved_model} 只到 model 表'


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    # 一次性迁移
    print('=== 数据迁移 ===')
    ok, msg = migrate_old_watchlist()
    print(f'  {msg}')

    # 摘要
    print()
    print('=== 当前 4 表摘要 ===')
    s = get_summary()
    print(f'  📊 模型推荐: {s["model"]} 只')
    print(f'  🤔 太难表: {s["toohard"]} 只')
    print(f'  ⭐ 我的关注: {s["my"]} 只')
    print(f'  🚫 黑名单: {s["blacklist"]} 只（1 年内）')
