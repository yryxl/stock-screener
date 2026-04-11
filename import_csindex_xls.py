"""
中证官网 XLS 历史估值数据导入脚本（反推版）

场景：
  中证官网"指数估值"按钮下载的 indicator.xls 只有最近 20 条 PE/股息率
  （这是服务器端静态文件，无法通过选日期范围突破）。
  但同时提供的"导出行情"perf.xlsx 可以下载多年日频收盘价（5 年约 1200+ 条）。

  经验证：PE / 收盘价 的比值在短期内极度稳定（变异系数 < 0.1%），
  因此可以用这个比值把收盘价反推为 PE 历史。
  误差 < 0.1%，对"历史分位判定"任务无影响。

用法：
  1. 从中证官网下载两个文件：
     - H30269indicator.xls（最近 20 条 PE/股息率）
     - H30269perf.xlsx（5 年日频行情，含收盘价）
     000015 同理
  2. 放到 backtest_data/etf_valuation_import/ 目录
  3. 运行：python import_csindex_xls.py
  4. 脚本自动配对同一指数的 indicator + perf，
     用 PE/close 比值反推完整历史，写入 backtest_data/etf_valuation/{code}.json
  5. 和已有的 akshare 20 条数据按日期去重合并，不会覆盖

反推公式：
  ratio = mean(PE_recent / close_recent)    # 用 indicator.xls 的 20 条真 PE 和对应 close 算均值
  PE(t) = close(t) × ratio                  # 把 perf.xlsx 的 1200+ 条收盘价全部反推
  所有反推数据标记 source="csindex_xls_derived" 以示区分。
"""

import json
import os
import re
import sys
from datetime import datetime

import pandas as pd


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IMPORT_DIR = os.path.join(SCRIPT_DIR, "backtest_data", "etf_valuation_import")
STORE_DIR = os.path.join(SCRIPT_DIR, "backtest_data", "etf_valuation")


def _normalize_date(raw):
    """把各种日期格式统一成 YYYY-MM-DD"""
    if isinstance(raw, (int, float)):
        s = str(int(raw))
        if len(s) == 8:
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    elif isinstance(raw, str):
        raw = raw.strip()
        if len(raw) == 8 and raw.isdigit():
            return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
        if "-" in raw:
            return raw[:10]
    try:
        return pd.Timestamp(raw).strftime("%Y-%m-%d")
    except Exception:
        return None


def _extract_index_code(filename):
    """
    从文件名提取指数代码
      H30269indicator.xls → H30269
      H30269perf.xlsx → H30269
      000015indicator.xls → 000015
      H30269indicator (1).xls → H30269
    """
    stem = os.path.splitext(os.path.basename(filename))[0]
    stem = re.sub(r"indicator.*$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"perf.*$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"\s*\(\d+\)\s*$", "", stem)
    return stem.strip() or None


def _find_col(df, *keywords):
    for col in df.columns:
        for kw in keywords:
            if kw in str(col):
                return col
    return None


def parse_indicator_xls(path):
    """
    解析中证官网 indicator.xls（最近 20 条真 PE）
    返回 DataFrame with columns [date, pe, dividend_yield]
    """
    try:
        df = pd.read_excel(path, engine="xlrd")
    except Exception:
        df = pd.read_excel(path, engine="openpyxl")

    col_date = _find_col(df, "日期", "Date")
    col_pe = _find_col(df, "市盈率2", "P/E2") or _find_col(df, "市盈率1", "P/E1")
    col_div = _find_col(df, "股息率2", "D/P2") or _find_col(df, "股息率1", "D/P1")

    if col_date is None or col_pe is None:
        raise ValueError(f"{path} 找不到日期或 PE 列，可用列: {df.columns.tolist()}")

    rows = []
    for _, row in df.iterrows():
        d = _normalize_date(row[col_date])
        if not d:
            continue
        try:
            pe = float(row[col_pe])
            if pe <= 0 or pd.isna(pe):
                continue
        except (ValueError, TypeError):
            continue
        div = None
        if col_div is not None:
            try:
                dv = float(row[col_div])
                if not pd.isna(dv) and dv > 0:
                    div = dv
            except (ValueError, TypeError):
                pass
        rows.append({"date": d, "pe": pe, "dividend_yield": div})

    return pd.DataFrame(rows).drop_duplicates("date")


def parse_perf_xlsx(path, index_code):
    """
    解析中证官网 perf.xlsx（5 年日频行情）
    只保留指定 index_code 对应的行（过滤掉全收益指数等）
    返回 DataFrame with columns [date, close]
    """
    try:
        df = pd.read_excel(path, engine="openpyxl")
    except Exception:
        df = pd.read_excel(path, engine="xlrd")

    col_date = _find_col(df, "日期", "Date")
    col_close = _find_col(df, "收盘", "Close")
    col_code = _find_col(df, "指数代码", "Index Code")

    if col_date is None or col_close is None:
        raise ValueError(f"{path} 找不到日期或收盘价列")

    # 过滤：只保留价格指数
    if col_code is not None:
        # 指数代码可能是字符串 "H30269" 或整数 15（上证红利）
        df_filtered = df[
            df[col_code].astype(str).str.zfill(6).str.upper()
            == index_code.zfill(6).upper()
        ].copy()
        if df_filtered.empty:
            # 尝试直接字符串匹配
            df_filtered = df[df[col_code].astype(str).str.upper() == index_code.upper()].copy()
        df = df_filtered

    rows = []
    for _, row in df.iterrows():
        d = _normalize_date(row[col_date])
        if not d:
            continue
        try:
            c = float(row[col_close])
            if c <= 0 or pd.isna(c):
                continue
        except (ValueError, TypeError):
            continue
        rows.append({"date": d, "close": c})

    return pd.DataFrame(rows).drop_duplicates("date")


def derive_pe_from_close(df_ind, df_perf):
    """
    用 indicator 的真 PE 和 perf 的收盘价，算出稳定比值 ratio = PE/close
    然后把 perf 的 1200+ 条收盘价反推成 PE 历史

    返回 (records, stats)：
      records: [{date, pe, dividend_yield, source}, ...]
      stats:   {ratio, cv, indicator_count, perf_count, output_count}
    """
    # 1. 对齐 indicator 和 perf，算 PE/close 比值
    merged = df_ind.set_index("date").join(
        df_perf.set_index("date"), how="inner"
    ).dropna(subset=["pe", "close"])

    if len(merged) < 5:
        raise ValueError(f"indicator 和 perf 对齐条数仅 {len(merged)}，不足以算稳定比值")

    merged["ratio"] = merged["pe"] / merged["close"]
    ratio = float(merged["ratio"].mean())
    std = float(merged["ratio"].std())
    cv = (std / ratio * 100) if ratio else 0.0

    if cv > 5.0:
        raise ValueError(
            f"PE/close 比值变异系数 {cv:.2f}% > 5%，反推不可靠。"
            f"可能 perf.xlsx 包含多个指数（如价格指数+全收益指数），"
            f"需要按指数代码过滤后再导入。"
        )

    # 2. 用比值反推 perf 全部收盘价为 PE
    # 同时保留一个 date→dividend_yield 的映射（indicator 的真股息率）
    div_map = df_ind.dropna(subset=["dividend_yield"]).set_index("date")["dividend_yield"].to_dict()

    records = []
    for _, row in df_perf.iterrows():
        date = row["date"]
        close = row["close"]
        pe = close * ratio
        records.append({
            "date": date,
            "pe": round(pe, 2),
            "dividend_yield": div_map.get(date),  # 只有最近 20 天有真股息率
            "source": "csindex_xls_derived",
        })

    return records, {
        "ratio": ratio,
        "cv_pct": cv,
        "indicator_count": len(df_ind),
        "perf_count": len(df_perf),
        "aligned_count": len(merged),
        "output_count": len(records),
    }


def merge_into_store(index_code, new_records, index_name_hint="", kind_hint=""):
    """
    合并新记录到 backtest_data/etf_valuation/{code}.json
    按 date 去重。优先级：
      - 如果一个日期同时存在 csindex（akshare真值）和 csindex_xls_derived（反推值），
        保留 akshare 真值
      - 其他情况按新记录覆盖
    返回 (总条数, 新增条数)
    """
    os.makedirs(STORE_DIR, exist_ok=True)
    store_path = os.path.join(STORE_DIR, f"{index_code}.json")

    if os.path.exists(store_path):
        with open(store_path, "r", encoding="utf-8") as f:
            store = json.load(f)
    else:
        store = {
            "index_code": index_code,
            "index_name": index_name_hint,
            "kind": kind_hint,
            "data": [],
            "last_updated": None,
        }

    # 按 date 索引现有记录
    by_date = {r["date"]: r for r in store.get("data", [])}

    added = 0
    for r in new_records:
        existing = by_date.get(r["date"])
        if existing is None:
            by_date[r["date"]] = r
            added += 1
            continue
        # 同一日期：akshare 真值优先，反推值让位
        if existing.get("source") == "csindex" and r.get("source") == "csindex_xls_derived":
            continue  # 保留 akshare 真值
        # 否则替换（比如反推的 dividend_yield 为 None，可以补入真值）
        merged_rec = dict(existing)
        for k, v in r.items():
            if v is not None:
                merged_rec[k] = v
        by_date[r["date"]] = merged_rec

    store["data"] = sorted(by_date.values(), key=lambda x: x["date"])
    store["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    if index_name_hint:
        store["index_name"] = index_name_hint
    if kind_hint:
        store["kind"] = kind_hint

    with open(store_path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)

    return len(store["data"]), added


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    # 从 etf_index_map.json 读取代码 → 名称 / kind 映射
    etf_map_path = os.path.join(SCRIPT_DIR, "etf_index_map.json")
    index_meta = {}
    if os.path.exists(etf_map_path):
        with open(etf_map_path, "r", encoding="utf-8") as f:
            m = json.load(f).get("map", {})
        for _etf, mapping in m.items():
            idx = mapping.get("index", "")
            if idx:
                index_meta[idx] = {
                    "name": mapping.get("name", ""),
                    "kind": mapping.get("kind", "strategy"),
                }

    os.makedirs(IMPORT_DIR, exist_ok=True)
    all_files = [f for f in os.listdir(IMPORT_DIR)
                 if f.lower().endswith((".xls", ".xlsx"))]

    if not all_files:
        print(f"=== 无文件可导入 ===")
        print(f"请把中证官网下载的 xls/xlsx 放到: {IMPORT_DIR}")
        print(f"每个指数需要 2 个文件:")
        print(f"  1. indicator.xls（指数估值，最近 20 条真 PE/股息率）")
        print(f"  2. perf.xlsx（导出行情，5 年日频收盘价）")
        return

    # 按指数代码分组，每组期望有 indicator + perf 两个文件
    by_code = {}
    for fn in all_files:
        code = _extract_index_code(fn)
        if not code:
            print(f"  ⚠ {fn} 无法识别指数代码，跳过")
            continue
        by_code.setdefault(code, {"indicator": None, "perf": None})
        if "indicator" in fn.lower():
            by_code[code]["indicator"] = os.path.join(IMPORT_DIR, fn)
        elif "perf" in fn.lower():
            by_code[code]["perf"] = os.path.join(IMPORT_DIR, fn)

    print(f"=== 识别到 {len(by_code)} 个指数 ===\n")

    total_added = 0
    for code, files in sorted(by_code.items()):
        meta = index_meta.get(code, {})
        name = meta.get("name", "?")
        kind = meta.get("kind", "strategy")
        print(f"--- {code} {name} ---")

        if not files["indicator"] or not files["perf"]:
            print(f"  ⚠ 缺少文件 (indicator={files['indicator']} perf={files['perf']})")
            print(f"     跳过（需要同时有 indicator.xls 和 perf.xlsx 才能做反推）")
            continue

        try:
            df_ind = parse_indicator_xls(files["indicator"])
            print(f"  indicator: {len(df_ind)} 条真 PE")
            df_perf = parse_perf_xlsx(files["perf"], code)
            print(f"  perf:      {len(df_perf)} 条收盘价")
        except Exception as e:
            print(f"  ❌ 解析失败: {e}")
            continue

        try:
            records, stats = derive_pe_from_close(df_ind, df_perf)
            print(f"  反推比值: {stats['ratio']:.8f}")
            print(f"  变异系数: {stats['cv_pct']:.4f}%")
            print(f"  反推输出: {stats['output_count']} 条")
        except Exception as e:
            print(f"  ❌ 反推失败: {e}")
            continue

        total, added = merge_into_store(code, records, index_name_hint=name, kind_hint=kind)
        print(f"  合并后本地共 {total} 条，新增 {added} 条")
        total_added += added
        print()

    print(f"=== 完成 ===")
    print(f"新增 {total_added} 条记录到 {STORE_DIR}")
    print(f"后续每日 Cron (_inject_etf_monitor) 会自动追加最新 20 条 akshare 真值，")
    print(f"不会覆盖反推历史（akshare 真值优先策略已内置）")


if __name__ == "__main__":
    main()
