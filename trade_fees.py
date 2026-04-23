"""
A 股交易手续费自动计算（前端两处共用：持仓管理 + 历史回测）

用户在 2026-04-22 给出的官方费率：

  上海交易所（代码以 6 / 5 开头，含沪市 ETF）
    佣金（手续费）：成交金额 × 0.3%，单笔最低 5 元，买卖均收
    过户费：成交金额 × 0.001%，买卖均收
    印花税：成交金额 × 0.05%，仅卖出收

  深圳交易所（代码以 0 / 3 / 1 开头，含深市 ETF）
    佣金（手续费）：成交金额 × 0.3%，单笔最低 5 元，买卖均收
    印花税：成交金额 × 0.05%，仅卖出收
    （深交所无过户费）

注意：
  1. 费率是用户指定的"上限"，后续若券商实际费率更低可在常量处改。
  2. 分红再投（action='dividend'）视同买入，不收印花税。
  3. 全部按"元"为单位，最终总费用四舍五入到分。
"""

from typing import Dict

# 费率常量（用户 2026-04-22 指定）
COMMISSION_RATE = 0.003    # 佣金 0.3%
COMMISSION_MIN = 5.0       # 佣金最低 5 元
TRANSFER_RATE = 0.00001    # 过户费 0.001%（仅沪市）
STAMP_RATE = 0.0005        # 印花税 0.05%（仅卖出）

BUY_ACTIONS = {"buy", "buy_add", "dividend"}
SELL_ACTIONS = {"sell_partial", "sell_all"}


def detect_exchange(code: str) -> str:
    """识别交易所。返回 'SH'（上交所） / 'SZ'（深交所）。

    规则（6 位代码首位）：
      - 6 / 5 → 沪市（5 是沪市 ETF，如 510330）
      - 0 / 3 / 1 → 深市（1 是深市 ETF，如 159919）
      - 其它未知情况默认深市（无过户费，少算不多算）
    """
    c = str(code).zfill(6)
    if c.startswith(("6", "5")):
        return "SH"
    return "SZ"


def calc_fees(code: str, price: float, shares: int, action: str) -> Dict[str, float]:
    """算一笔交易的各项手续费。

    Args:
      code: 6 位股票代码
      price: 成交单价（元）
      shares: 股数（正数）
      action: 'buy' / 'buy_add' / 'dividend' / 'sell_partial' / 'sell_all'

    Returns: {
      'amount': 成交金额（price × shares）,
      'commission': 佣金,
      'transfer': 过户费,
      'stamp': 印花税,
      'total': 合计手续费,
      'net': 净额（买入 = 花出金额 = amount+total；卖出 = 收回金额 = amount-total）,
      'exchange': 'SH' / 'SZ',
      'side': 'buy' / 'sell',
      'breakdown': 人类可读的拆分文字,
    }
    """
    amount = float(price) * int(shares)
    exch = detect_exchange(code)
    side = "sell" if action in SELL_ACTIONS else "buy"

    # 佣金：按比例算，不足 5 元按 5 元
    commission = max(amount * COMMISSION_RATE, COMMISSION_MIN)

    # 过户费：只沪市，买卖均收
    transfer = amount * TRANSFER_RATE if exch == "SH" else 0.0

    # 印花税：只卖出收
    stamp = amount * STAMP_RATE if side == "sell" else 0.0

    total = commission + transfer + stamp
    net = amount + total if side == "buy" else amount - total

    # 拆分文字（用于前端 caption 提示）
    parts = [f"佣金 ¥{commission:.2f}"]
    if transfer > 0:
        parts.append(f"过户费 ¥{transfer:.2f}")
    if stamp > 0:
        parts.append(f"印花税 ¥{stamp:.2f}")
    breakdown = " + ".join(parts) + f" = ¥{total:.2f}"

    return {
        "amount": round(amount, 2),
        "commission": round(commission, 2),
        "transfer": round(transfer, 2),
        "stamp": round(stamp, 2),
        "total": round(total, 2),
        "net": round(net, 2),
        "exchange": exch,
        "side": side,
        "breakdown": breakdown,
    }


# 自检
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    # 沪市买入 1000 股 @ ¥10（成交金额 1 万）
    r = calc_fees("600519", 10.0, 1000, "buy")
    print("沪市买入 1000 股 @ ¥10：", r["breakdown"], "净额", r["net"])

    # 深市买入 100 股 @ ¥50
    r = calc_fees("000538", 50.0, 100, "buy")
    print("深市买入 100 股 @ ¥50：", r["breakdown"], "净额", r["net"])

    # 沪市 ETF 卖出 700 股 @ ¥5
    r = calc_fees("510330", 5.0, 700, "sell_partial")
    print("沪市 ETF 卖 700 股 @ ¥5：", r["breakdown"], "净额", r["net"])

    # 小额：佣金低于 5 元会被抬到 5 元
    r = calc_fees("000001", 10.0, 100, "buy")
    print("深市 100 股 @ ¥10（小额）：", r["breakdown"], "净额", r["net"])
