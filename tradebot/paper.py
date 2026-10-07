"""Paper trading: forward-test the bot on live candles with a virtual account.

It follows the market candle by candle and trades a virtual account with exactly the rules
of the backtest: the same Engine (confirmation, real lot sizes, partial profit, break-even,
trailing stop, daily limits). Nothing is sent to a broker.

Everything lives in one folder per run:
  candles.csv   every candle seen since the start (plus the warm-up history)
  meta.json     settings, the starting balance and where trading started
  events.log    one line per event: order placed / cancelled, filled, partial, stop moved, closed
  trades.csv    every trade so far
  summary.md    balance, results by day, open position, recent events
  shots/        a technical screenshot for every order placed and every trade closed

Restarts are safe: on start-up the saved candles are replayed through the deterministic
engine, which rebuilds the open position, balance and limits exactly, then live trading resumes.
High-impact news can be used as a filter (no new orders 30 minutes either side).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from .backtest import Engine, _stats, BacktestResult
from .money import MoneyManagement
from .strategy import Market, StrategyConfig
from .structure import px

Feed = Callable[[str, str, int], pd.DataFrame]   # (symbol, interval, count) -> closed candles with "time"


class PaperTrader:
    def __init__(self, symbol: str, interval: str, cfg: StrategyConfig, money: MoneyManagement, equity: float,
                 folder: str | Path, feed: Feed, spread: float = 0.0, fee_bps: float = 0.0, history: int = 3000,
                 step_seconds: int = 300, screenshots: bool = True, news_veto: Callable | None = None,
                 log: Callable[[str], None] | None = None):
        self.symbol, self.interval, self.cfg, self.mm = symbol, interval, cfg, money
        self.equity, self.spread, self.fee_bps = equity, spread, fee_bps
        self.folder, self.feed, self.history, self.step_seconds = Path(folder), feed, history, step_seconds
        self.screenshots, self.news_veto = screenshots, news_veto
        self.log = log or (lambda msg: print(msg, flush=True))
        self.df: pd.DataFrame | None = None
        self.engine: Engine | None = None
        self.logged = 0            # events already written to events.log
        self.done = -1             # last candle processed

    # -- settings fingerprint: a restart must use the same rules or the replay is meaningless --
    def _settings(self) -> dict:
        return {"symbol": self.symbol, "interval": self.interval, "equity": self.equity, "spread": self.spread,
                "fee_bps": self.fee_bps, "config": asdict(self.cfg), "money": asdict(self.mm)}

    def _fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self._settings(), sort_keys=True, default=str).encode()).hexdigest()[:16]

    # -- start-up -----------------------------------------------------------------------
    def bootstrap(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        meta_path, candles_path = self.folder / "meta.json", self.folder / "candles.csv"
        if meta_path.exists() and candles_path.exists():
            meta = json.loads(meta_path.read_text())
            if meta["fingerprint"] != self._fingerprint():
                raise SystemExit(f"{self.folder} was started with different settings. Use the same flags, "
                                 f"or --reset to start a fresh paper account.")
            self.df = pd.read_csv(candles_path, parse_dates=["time"])
            self.df["time"] = pd.to_datetime(self.df["time"], utc=True)
            start = int(meta["start"])
            self.logged = int(meta.get("logged", 0))
            self.log(f"resuming paper account in {self.folder} (started {self.df.time.iloc[start]:%d %b %H:%M} UTC), "
                     f"replaying {len(self.df) - start} candles...")
        else:
            self.df = self.feed(self.symbol, self.interval, self.history).reset_index(drop=True)
            start = len(self.df)      # trade only candles that close from now on
            meta_path.write_text(json.dumps({"fingerprint": self._fingerprint(), "start": start, "logged": 0,
                                             "settings": self._settings()}, indent=2, default=str))
            self.df.to_csv(candles_path, index=False)
            self.log(f"new paper account: {self.equity:,.2f} USD on {self.symbol} {self.interval} "
                     f"(warm-up {len(self.df)} candles) -> {self.folder}")
        market = Market(self.df, self.cfg)
        self.engine = Engine(market, self.mm, self.equity, self.fee_bps, self.spread, start)
        self.vetoed: dict[str, str] = json.loads(meta_path.read_text()).get("vetoed", {})
        self.replaying = True
        self.engine.veto = self._veto
        for t in range(start, len(self.df)):
            self.engine.step(t, allow_last=True)
        self.replaying = False
        self.done = len(self.df) - 1
        self._flush(replayed=True)

    def _veto(self, t: int, signal) -> str | None:
        """News filter. Decisions are recorded so a replay after a restart makes the same ones,
        even once the calendar feed has moved on to another week."""
        key = str(self._time(t))
        if self.replaying:
            return self.vetoed.get(key)
        why = self.news_veto(self._time(t), signal) if self.news_veto is not None else None
        if why:
            self.vetoed[key] = why
        return why

    # -- one polling round ----------------------------------------------------------------
    def poll(self) -> int:
        """Fetch newly closed candles and trade them. Returns how many new candles were processed."""
        last = self.df.time.iloc[-1]
        missing = int((pd.Timestamp.now(tz="UTC") - last).total_seconds() // self.step_seconds) + 5
        fresh = self.feed(self.symbol, self.interval, int(min(max(missing, 20), 5000)))
        new = fresh[fresh.time > last]
        if new.empty:
            return 0
        self.df = pd.concat([self.df, new], ignore_index=True)
        new.to_csv(self.folder / "candles.csv", mode="a", header=False, index=False)
        self.engine.market = Market(self.df, self.cfg)
        for t in range(self.done + 1, len(self.df)):
            self.engine.step(t, allow_last=True)
        self.done = len(self.df) - 1
        self._flush()
        return len(new)

    def run(self, sleep: Callable[[float], None] = time.sleep) -> None:
        self.bootstrap()
        while True:
            try:
                n = self.poll()
                self.log(f"{self.df.time.iloc[-1]:%Y-%m-%d %H:%M} {self.symbol} {px(self.df.close.iloc[-1])}"
                         f"  balance {self.engine.cash:,.2f}" + ("" if n else "  (no new candle yet)"))
            except (SystemExit, Exception) as exc:   # keep going through network hiccups
                self.log(f"poll failed: {exc}")
            sleep(self.step_seconds - time.time() % self.step_seconds + 3)   # just after the next close

    # -- output ------------------------------------------------------------------------------
    def _time(self, bar: int) -> pd.Timestamp:
        return pd.Timestamp(self.df.time.iloc[bar])

    def _flush(self, replayed: bool = False) -> None:
        events = self.engine.events[self.logged:]
        with open(self.folder / "events.log", "a", encoding="utf-8") as f:
            for e in events:
                when = self._time(e["bar"])
                f.write(json.dumps({"time": str(when), "symbol": self.symbol, **e}, default=_jsonable) + "\n")
                if not replayed:
                    self.log(f"{when:%Y-%m-%d %H:%M} {self.symbol} {describe(e)}")
                    if self.screenshots and e["event"] in ("order placed", "closed"):
                        self._shot(e, when)
        self.logged = len(self.engine.events)
        meta_path = self.folder / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["logged"] = self.logged
        meta["vetoed"] = self.vetoed
        meta_path.write_text(json.dumps(meta, indent=2, default=str))
        self._write_trades()
        (self.folder / "summary.md").write_text(self.summary(), encoding="utf-8")

    def _shot(self, e: dict, when: pd.Timestamp) -> None:
        try:
            from .screenshot import technical_screenshot
            shots = self.folder / "shots"
            shots.mkdir(exist_ok=True)
            name = f"{when:%Y%m%d-%H%M}-{e['event'].replace(' ', '-')}.png"
            technical_screenshot(Market(self.df.iloc[: e["bar"] + 1], self.cfg), self.symbol, str(shots / name),
                                 self.interval, str(self.cfg.htf or "-"), None, f"paper: {describe(e)}",
                                 trades=self.engine.trades)
        except Exception as exc:   # a chart must never stop the trading loop
            self.log(f"screenshot failed: {exc}")

    def _write_trades(self) -> None:
        rows = []
        for t in self.engine.trades:
            rows.append({"opened": self._time(t.entry_bar), "side": t.side, "setup": t.setup, "lots": t.lots,
                         "entry": t.entry, "stop": t.stop, "target": t.target, "current_stop": t.current_stop,
                         "closed": self._time(t.exit_bar) if t.exit_bar is not None else "",
                         "exit": t.exit if t.exit is not None else "", "result": t.exit_reason or "open",
                         "pnl_usd": round(t.pnl, 2), "r": round(t.r_multiple, 2), "reasons": "; ".join(t.reasons)})
        pd.DataFrame(rows).to_csv(self.folder / "trades.csv", index=False)

    def summary(self) -> str:
        e = self.engine
        closed = [t for t in e.trades if t.exit_bar is not None]
        start_time = self._time(e.start) if e.start < len(self.df) else self.df.time.iloc[-1]
        equity_now = e.equity_at(len(self.df) - 1)
        lines = [f"# Paper trading - {self.symbol} {self.interval}", "",
                 f"Started {start_time:%a %d %b %Y %H:%M} UTC with {self.equity:,.2f} USD - "
                 f"last candle {self.df.time.iloc[-1]:%a %d %b %H:%M} UTC", "",
                 f"- **Balance** {e.cash:,.2f} USD ({e.cash / self.equity - 1:+.1%})   "
                 f"**Equity** {equity_now:,.2f} USD",
                 f"- **Trades** {len(closed)} closed"
                 + (f", win rate {np.mean([t.pnl > 0 for t in closed]):.0%}, "
                    f"avg R {np.mean([t.r_multiple for t in closed]):+.2f}" if closed else ""),
                 f"- **Setups skipped** (lot size / margin / news) {len([x for x in e.events if x['event'] == 'skipped'])}"]
        if e.pos is not None:
            p = e.pos
            lines += [f"- **Open** {p.side} {p.lots if p.lots is not None else round(p.size, 4)}"
                      f"{' lots' if p.lots is not None else ' units'} at {px(p.entry)}, stop {px(p.current_stop)} "
                      f"({p.stop_kind}), target {px(p.target)}"]
        elif e.order is not None:
            s = e.order.signal
            lines += [f"- **Pending** {s.side} order at {px(s.trigger or s.entry)}, stop {px(s.stop)}, "
                      f"target {px(s.target)}"]
        if closed:
            by_day: dict = {}
            for t in closed:
                d = self._time(t.exit_bar).date()
                by_day.setdefault(d, []).append(t.pnl)
            lines += ["", "| Day | Trades | P&L (USD) |", "|---|---|---|"]
            lines += [f"| {d:%a %d %b} | {len(v)} | {sum(v):+.2f} |" for d, v in sorted(by_day.items())]
        recent = e.events[-10:]
        if recent:
            lines += ["", "**Recent events**", ""]
            lines += [f"- {self._time(x['bar']):%d %b %H:%M} {describe(x)}" for x in recent]
        return "\n".join(lines) + "\n"

    def result(self) -> BacktestResult:
        e = self.engine
        eq = pd.Series([e.equity_at(len(self.df) - 1)])
        res = BacktestResult(e.trades, eq, self.equity)
        res.stats = _stats(res)
        return res


def describe(e: dict) -> str:
    kind, side = e["event"], e.get("side", "")
    if kind == "order placed":
        how = f"stop order at {px(e['trigger'])}" if e.get("trigger") is not None else f"at the open ~{px(e['entry'])}"
        return (f"ORDER {side.upper()} ({e['setup']}) {how}, SL {px(e['stop'])}, TP {px(e['target'])}, "
                f"R:R {e['rr']:.1f} - {'; '.join(e['reasons'])}")
    if kind == "order cancelled":
        return f"order cancelled ({e['reason']})"
    if kind == "skipped":
        return f"setup skipped: {e['reason']}"
    if kind == "filled":
        size = f"{e['lots']:g} lots" if e.get("lots") is not None else f"{e['units']:.4g} units"
        return f"FILLED {side.upper()} {size} at {px(e['price'])}" + (f" ({e['note']})" if e.get("note") else "")
    if kind == "partial":
        return f"partial profit at {px(e['price'])}: {e['pnl']:+.2f} USD, balance {e['balance']:,.2f}"
    if kind == "stop moved":
        return f"stop moved to {px(e['stop'])} ({e['stop_kind']})"
    if kind == "closed":
        return (f"CLOSED {side.upper()} at {px(e['price'])} ({e['reason']}): {e['pnl']:+.2f} USD ({e['r']:+.2f}R), "
                f"balance {e['balance']:,.2f}")
    return kind


def _jsonable(x):
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, (dt.date, pd.Timestamp)):
        return str(x)
    return str(x)
