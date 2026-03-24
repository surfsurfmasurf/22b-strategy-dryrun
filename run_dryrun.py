"""
22B Strategy Engine — Dry-Run Mode Launcher.

Starts the engine with live Binance data but simulated execution.
All strategy signals are processed through DryRunExecutor with a virtual account.

Usage:
    python run_dryrun.py [--balance 10000] [--fee 0.04] [--slippage 0.05] [--size-pct 10]

Environment variables (alternative to CLI args):
    DRY_RUN_ENABLED=true              (auto-set by this script)
    DRY_RUN_INITIAL_BALANCE=10000
    DRY_RUN_POSITION_SIZE_PCT=0.10
    DRY_RUN_FEE_RATE=0.0004
    DRY_RUN_SLIPPAGE_PCT=0.0005
    DRY_RUN_REPORT_INTERVAL_MIN=60
"""

import argparse
import asyncio
import os
import sys


def main():
    parser = argparse.ArgumentParser(
        description="22B Strategy Engine - Dry-Run Mode (live data, simulated execution)"
    )
    parser.add_argument(
        "--balance", type=float, default=None,
        help="Initial virtual balance in USDT (default: 10000)",
    )
    parser.add_argument(
        "--fee", type=float, default=None,
        help="Fee rate in %% (e.g. 0.04 = 0.04%%)",
    )
    parser.add_argument(
        "--slippage", type=float, default=None,
        help="Slippage in %% (e.g. 0.05 = 0.05%%)",
    )
    parser.add_argument(
        "--size-pct", type=float, default=None,
        help="Position size as %% of balance (e.g. 10 = 10%%)",
    )
    parser.add_argument(
        "--report-interval", type=int, default=None,
        help="Dry-run report interval in minutes (default: 60)",
    )
    args = parser.parse_args()

    # Force dry-run mode
    os.environ["DRY_RUN_ENABLED"] = "true"

    # Override config from CLI args
    if args.balance is not None:
        os.environ["DRY_RUN_INITIAL_BALANCE"] = str(args.balance)
    if args.fee is not None:
        os.environ["DRY_RUN_FEE_RATE"] = str(args.fee / 100)
    if args.slippage is not None:
        os.environ["DRY_RUN_SLIPPAGE_PCT"] = str(args.slippage / 100)
    if args.size_pct is not None:
        os.environ["DRY_RUN_POSITION_SIZE_PCT"] = str(args.size_pct / 100)
    if args.report_interval is not None:
        os.environ["DRY_RUN_REPORT_INTERVAL_MIN"] = str(args.report_interval)

    # Print startup banner
    balance = os.environ.get("DRY_RUN_INITIAL_BALANCE", "10000")
    fee = float(os.environ.get("DRY_RUN_FEE_RATE", "0.0004")) * 100
    slip = float(os.environ.get("DRY_RUN_SLIPPAGE_PCT", "0.0005")) * 100
    size = float(os.environ.get("DRY_RUN_POSITION_SIZE_PCT", "0.10")) * 100

    print("=" * 60)
    print("  22B Strategy Engine — DRY-RUN MODE")
    print("=" * 60)
    print(f"  Virtual Balance : {balance} USDT")
    print(f"  Position Size   : {size:.1f}%")
    print(f"  Fee Rate        : {fee:.4f}%")
    print(f"  Slippage        : {slip:.4f}%")
    print("=" * 60)
    print("  Live market data from Binance")
    print("  NO real orders will be placed")
    print("=" * 60)
    print()

    from bot.main import Engine

    engine = Engine()
    try:
        asyncio.run(engine.start())
    except KeyboardInterrupt:
        print("\nDry-run stopped by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
