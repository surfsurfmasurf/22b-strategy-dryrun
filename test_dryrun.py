"""
Dry-Run Mode Integration Tests.
Run: python test_dryrun.py
"""
import sys
import os
import asyncio

sys.stdout.reconfigure(encoding="utf-8")

os.environ["DRY_RUN_ENABLED"] = "true"
os.environ["DRY_RUN_INITIAL_BALANCE"] = "10000"
os.environ["BINANCE_API_KEY"] = "test"
os.environ["BINANCE_API_SECRET"] = "test"

passed = 0
failed = 0


def header(name):
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")


def ok():
    global passed
    passed += 1
    print("  PASS")


def fail(msg):
    global failed
    failed += 1
    print(f"  FAIL: {msg}")


# ================================================================
# TEST 1: Config dry-run validation
# ================================================================
header("TEST 1: Config dry-run validation")
try:
    from bot.config import Config
    c = Config()
    c.validate()
    assert c.dry_run_enabled is True, "dry_run should be True"
    assert c.system_mode == "OBSERVE", "should force OBSERVE"
    assert c.dry_run_initial_balance == 10000.0
    print(f"  dry_run_enabled={c.dry_run_enabled}")
    print(f"  system_mode={c.system_mode} (forced OBSERVE)")
    print(f"  balance={c.dry_run_initial_balance}")
    ok()
except Exception as e:
    fail(str(e))

# ================================================================
# TEST 2: Mutual exclusion (dry_run + validation_dataset)
# ================================================================
header("TEST 2: Mutual exclusion")
try:
    c2 = Config()
    c2.dry_run_enabled = True
    c2.validation_dataset_enabled = True
    c2.validate()
    assert c2.dry_run_enabled is False, "should disable dry_run"
    print(f"  dry_run disabled when validation_dataset active: True")
    ok()
except Exception as e:
    fail(str(e))

# ================================================================
# TEST 3: ReplayAccount full lifecycle
# ================================================================
header("TEST 3: ReplayAccount full lifecycle")
try:
    from bot.data.replay_account import ReplayAccount
    acct = ReplayAccount(
        initial_balance=10000.0,
        position_size_pct=0.10,
        fee_rate=0.0004,
        slippage_pct=0.0005,
    )

    # Trade 1: LONG win (+3%)
    acct.open_position("p1", "overreaction_reversal", "BTCUSDT", "LONG", 60000.0, 1000)
    t1 = acct.close_position("p1", 61800.0, 2000, "TP hit")

    # Trade 2: SHORT loss (-4%)
    acct.open_position("p2", "volatility_expansion", "ETHUSDT", "SHORT", 3000.0, 3000)
    t2 = acct.close_position("p2", 3120.0, 4000, "SL hit")

    # Trade 3: LONG win (+2%)
    acct.open_position("p3", "early_trend_capture", "SOLUSDT", "LONG", 150.0, 5000)
    t3 = acct.close_position("p3", 153.0, 6000, "TP hit")

    metrics = acct.compute_metrics()
    assert metrics["trade_count"] == 3
    assert metrics["win_count"] == 2
    assert metrics["loss_count"] == 1
    print(f"  trades: {metrics['trade_count']} (W:{metrics['win_count']} L:{metrics['loss_count']})")
    print(f"  win_rate: {metrics['win_rate']*100:.1f}%")
    print(f"  total_pnl: {metrics['total_pnl_usdt']:.2f} USDT")
    print(f"  total_return: {metrics['total_return_pct']:.2f}%")
    print(f"  mdd: {metrics['mdd_pct']:.2f}%")
    print(f"  profit_factor: {metrics['profit_factor']}")
    print(f"  fees: {metrics['total_fee_usdt']:.4f} USDT")
    print(f"  per_strategy: {list(metrics['per_strategy'].keys())}")
    ok()
except Exception as e:
    fail(str(e))

# ================================================================
# TEST 4: DryRunExecutor with mock store
# ================================================================
header("TEST 4: DryRunExecutor")
try:
    from bot.execution.dry_run_executor import DryRunExecutor
    from bot.strategies._base import Signal

    class MockStore:
        _last_order = None
        def get_regime(self): return {"regime": "BTC_BULLISH"}
        def get_ticker(self, sym): return {"price": 65432.10, "ts": 1000}
        def get_candles(self, sym, interval, limit=1): return []
        def save_order(self, rec): self._last_order = rec
        def _broadcast(self, evt, data): pass

    class MockSM:
        def create(self, **kw): pass
        def transition(self, *a, **kw): pass

    class MockKS:
        is_active = False

    store = MockStore()
    sm = MockSM()
    ks = MockKS()
    account2 = ReplayAccount(initial_balance=10000.0)
    exe = DryRunExecutor(
        config=c, store=store, state_machine=sm,
        kill_switch=ks, replay_account=account2,
    )

    async def test_executor():
        await exe.start()

        sig = Signal(
            strategy="test", symbol="BTCUSDT", action="BUY",
            mode="PAPER", confidence=0.8, regime="BTC_BULLISH", reason="test",
        )
        r = await exe.submit_order(sig, qty=0.015)
        assert r["status"] == "FILLED"
        assert r["dry_run"] is True
        assert r["avg_price"] == 65432.10
        print(f"  fill: status={r['status']} price={r['avg_price']} dry_run={r['dry_run']}")

        # Verify order persisted
        assert store._last_order is not None
        assert store._last_order["dry_run"] is True
        print(f"  order saved: {store._last_order['symbol']} {store._last_order['side']}")

        # Duplicate check
        r2 = await exe.submit_order(sig, qty=0.015)
        assert r2["error"] == "duplicate_signal"
        print(f"  duplicate blocked: {r2['error']}")

        # Kill switch check
        ks.is_active = True
        sig2 = Signal(
            strategy="test", symbol="ETHUSDT", action="SELL",
            mode="PAPER", confidence=0.7, regime="BTC_BULLISH", reason="test2",
        )
        r3 = await exe.submit_order(sig2, qty=0.1)
        assert r3["error"] == "kill_switch_active"
        print(f"  kill switch blocked: {r3['error']}")

        # Balance query
        bal = await exe.get_account_balance()
        assert bal == account2.balance
        print(f"  virtual balance: {bal:.2f}")

        # No-op methods
        assert await exe.get_open_positions() == []
        assert await exe.get_open_orders() == []
        print("  no-op methods OK")

        await exe.stop()

    asyncio.run(test_executor())
    ok()
except Exception as e:
    fail(str(e))

# ================================================================
# TEST 5: DB schema + dashboard app factory
# ================================================================
header("TEST 5: DB schema + dashboard app factory")
try:
    from db.schema import init_db
    from bot.data.store import DataStore
    from dashboard.app import create_app

    conn = init_db(":memory:")
    real_store = DataStore(conn)
    app = create_app(store=real_store, config=c)
    routes = [r.path for r in app.routes if hasattr(r, "path")]
    assert "/api/dry-run" in routes, "/api/dry-run route missing"
    assert "/api/snapshot" in routes
    print(f"  /api/dry-run endpoint: OK")
    print(f"  /api/snapshot endpoint: OK")
    print(f"  total routes: {len(routes)}")
    ok()
except Exception as e:
    fail(str(e))

# ================================================================
# TEST 6: PaperRecorder + ReplayAccount integration
# ================================================================
header("TEST 6: PaperRecorder + ReplayAccount integration")
try:
    from bot.strategies.paper_recorder import PaperRecorder

    acct3 = ReplayAccount(initial_balance=10000.0, position_size_pct=0.10)
    recorder = PaperRecorder(store=real_store, replay_account=acct3)

    # Inject a ticker price
    asyncio.run(real_store.update_ticker(
        "BTCUSDT",
        {"symbol": "BTCUSDT", "ts": 1000, "price": 62000.0, "volume_24h": 1e9, "change_pct": 1.5},
    ))

    sig_buy = Signal(
        strategy="test_strat", symbol="BTCUSDT", action="BUY",
        mode="PAPER", confidence=0.9, regime="BTC_BULLISH",
        reason="test", tp=64000.0, sl=60000.0,
    )
    recorder.on_signal(sig_buy)

    open_pos = recorder.get_open_positions()
    assert len(open_pos) == 1, f"expected 1 open, got {len(open_pos)}"
    print(f"  opened: {open_pos[0]['side']} {open_pos[0]['symbol']} @ {open_pos[0]['entry_price']}")
    print(f"  balance after open: {acct3.balance:.4f}")

    # Simulate price hitting TP
    asyncio.run(real_store.update_ticker(
        "BTCUSDT",
        {"symbol": "BTCUSDT", "ts": 2000, "price": 64500.0, "volume_24h": 1e9, "change_pct": 2.0},
    ))
    recorder.check_positions()

    open_pos2 = recorder.get_open_positions()
    assert len(open_pos2) == 0, f"expected 0 after TP, got {len(open_pos2)}"
    print("  position closed by TP hit")
    print(f"  balance after close: {acct3.balance:.4f}")

    m = acct3.compute_metrics()
    assert m["trade_count"] == 1
    assert m["total_pnl_usdt"] > 0, "should be profitable"
    print(f"  trade result: pnl={m['total_pnl_usdt']:.2f} USDT, return={m['total_return_pct']:.2f}%")
    ok()
except Exception as e:
    fail(str(e))

# ================================================================
# TEST 7: run_dryrun.py CLI
# ================================================================
header("TEST 7: run_dryrun.py CLI")
try:
    import subprocess
    result = subprocess.run(
        [sys.executable, "run_dryrun.py", "--help"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, f"exit code {result.returncode}"
    assert "--balance" in result.stdout
    assert "--fee" in result.stdout
    assert "--slippage" in result.stdout
    assert "--size-pct" in result.stdout
    print("  CLI --help output OK")
    ok()
except Exception as e:
    fail(str(e))

# ================================================================
# TEST 8: Dashboard /api/dry-run endpoint (ASGI test client)
# ================================================================
header("TEST 8: Dashboard /api/dry-run endpoint")
try:
    from httpx import AsyncClient, ASGITransport

    # Rebuild app with a mock engine that has replay_account
    class MockEngine:
        _replay_account = ReplayAccount(initial_balance=5000.0)

    mock_engine = MockEngine()
    # Do a trade so metrics are non-empty
    mock_engine._replay_account.open_position("x1", "test", "BTCUSDT", "LONG", 50000.0, 100)
    mock_engine._replay_account.close_position("x1", 51000.0, 200, "TP")

    c_dr = Config()
    c_dr.validate()
    app2 = create_app(store=real_store, config=c_dr, engine=mock_engine)

    async def test_api():
        transport = ASGITransport(app=app2)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # /api/dry-run
            resp = await client.get("/api/dry-run")
            assert resp.status_code == 200
            data = resp.json()
            assert data["enabled"] is True
            assert data["trade_count"] == 1
            assert "equity_curve" in data
            print(f"  /api/dry-run: enabled={data['enabled']}, trades={data['trade_count']}")
            print(f"  return={data['total_return_pct']:.2f}%, balance={data['final_balance']:.2f}")

            # /api/snapshot should include dry_run
            resp2 = await client.get("/api/snapshot")
            assert resp2.status_code == 200
            snap = resp2.json()
            assert snap["dry_run_enabled"] is True
            assert "dry_run" in snap
            print(f"  /api/snapshot: dry_run_enabled={snap['dry_run_enabled']}")

    asyncio.run(test_api())
    ok()
except Exception as e:
    fail(str(e))


# ================================================================
# Summary
# ================================================================
print(f"\n{'='*60}")
total = passed + failed
if failed == 0:
    print(f"  ALL {total} TESTS PASSED")
else:
    print(f"  {passed}/{total} PASSED, {failed} FAILED")
print(f"{'='*60}")
sys.exit(0 if failed == 0 else 1)
