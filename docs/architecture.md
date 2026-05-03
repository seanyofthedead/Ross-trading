# Autonomous AI Trading Agent — Mimicking Ross Cameron’s Small-Account Process

A blueprint for building an autonomous agent that executes Ross Cameron’s documented Warrior Trading methodology, mapped one-to-one against his Stock Selection criteria, Sample Trading Plan, Small Account Challenge strategy, Gap and Go strategy, Micro Pullback strategy, Candlestick Pattern Reference, and Chart Pattern Study Guide.

> **Disclaimer up front.** Cameron is explicit that his results are not typical and that most day traders lose money. Mimicking his process does not entitle the agent to mimic his outcomes. Treat everything below as an engineering spec, not investment advice. Paper-trade for months before allocating real capital.

-----

## 1. The Process Being Mimicked

Cameron’s documented method is a tightly-defined small-cap momentum loop:

1. **Universe filter** — relative volume ≥ 5×, % change ≥ +10%, news catalyst, price $1–$20 ($5–$10 sweet spot per the Sample Trading Plan), float < 10M shares preferred (up to 20M in hot markets, 50M acceptable for Gap and Go).
1. **Focus on the top 2–3 obvious gainers** — “obvious to me means obvious to thousands of others, which means more predictable price action.”
1. **Two primary entry frameworks**:
- **Gap and Go** — pre-market gap of 20%+ on news, entry on break of pre-market high or pre-market bull-flag high, between 9:30–10:00 AM ET.
- **Micro Pullback** — for stocks already moving fast intraday, enter on the first tiny red candle / lower wick on 1-min, or on a sub-1-min bull flag / flat-top breakout, with stop at the low of the pullback.
1. **Risk per trade** — defined by stop distance; the Sample Trading Plan caps it at ~5% of account; the Small Account Strategy caps it at $50 to make $100 (2:1 R:R).
1. **Hard stops** — daily max loss = -$100 (10% of a $1K account), three consecutive losers and stop trading for the day, jackknife candle = exit immediately, MACD crossing signal line = exit, decreasing volume = exit, first red candle on a 5-min setup = exit.
1. **Regime awareness** — “hot market vs cold market” gates aggression. In cold markets, sit on hands.

The agent must replicate all six stages, not just the entry signals. Cameron repeatedly stresses that risk management and discipline — not pattern recognition — are what make the strategy profitable.

-----

## 2. Agent Architecture Overview

A pipeline of seven cooperating services, all coordinated by a state machine and supervised by a risk monitor:

```
┌────────────────────────────────────────────────────────────────┐
│                    RISK SUPERVISOR (kill switch)               │
│  - daily P&L tracking      - consecutive-loss counter          │
│  - PDT/settlement tracker  - force-flatten authority           │
└────────────────────────────────────────────────────────────────┘
            ▲                                         ▲
            │                                         │
┌───────────┴──────────┐                  ┌───────────┴──────────┐
│   MARKET REGIME      │                  │   JOURNAL & FEEDBACK │
│   (hot/cold gate)    │                  │   (logs every trade) │
└──────────────────────┘                  └──────────────────────┘
            │
            ▼
┌──────────────────────┐  ┌─────────────────────┐  ┌──────────────────────┐
│  SCANNER             │→ │  CATALYST           │→ │  DAILY CHART          │
│  (universe filter)   │  │  CLASSIFIER (LLM)   │  │  STRENGTH FILTER      │
└──────────────────────┘  └─────────────────────┘  └──────────────────────┘
                                                          │
                                                          ▼
                                       ┌──────────────────────────────────┐
                                       │  WATCHLIST (top 2–3, refreshed)  │
                                       └──────────────────────────────────┘
                                                          │
                                                          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  PATTERN DETECTOR                                                        │
│  ├── Gap & Go pre-market high break                                      │
│  ├── Gap & Go pre-market bull flag break                                 │
│  ├── Micro Pullback (1-min, 10-sec)                                      │
│  ├── Bull Flag breakout (1-min, 5-min)                                   │
│  ├── Flat-Top breakout                                                   │
│  └── First-candle-to-make-new-high                                       │
└──────────────────────────────────────────────────────────────────────────┘
                                                          │
                                                          ▼
                                       ┌──────────────────────────────────┐
                                       │  POSITION SIZER                  │
                                       │  shares = max_risk / stop_dist   │
                                       └──────────────────────────────────┘
                                                          │
                                                          ▼
                                       ┌──────────────────────────────────┐
                                       │  EXECUTION (bracket orders)      │
                                       └──────────────────────────────────┘
                                                          │
                                                          ▼
                                       ┌──────────────────────────────────┐
                                       │  EXIT MONITOR                    │
                                       │  ├── 2:1 target scale-out        │
                                       │  ├── Jackknife reversal candle   │
                                       │  ├── MACD signal-line cross      │
                                       │  ├── Volume drying up            │
                                       │  ├── First red 5-min candle      │
                                       │  └── L2 / time & sales weakness  │
                                       └──────────────────────────────────┘
```

The Risk Supervisor sits above everything and can pre-empt any service. The Market Regime detector and Journal feed back into both the scanner thresholds and the risk per trade.

-----

## 3. Module Specifications

### 3.1 Scanner (universe filter)

**Inputs:** real-time quote feed + 30-day historical volume per symbol + intraday news feed + float reference data.

**Hard filters (per Stock Selection PDF, page 3):**

|Criterion                            |Threshold                                                   |Source doc                       |
|-------------------------------------|------------------------------------------------------------|---------------------------------|
|Relative volume (today vs 30-day avg)|≥ 5×                                                        |Stock Selection #1               |
|% change today                       |≥ +10%                                                      |Stock Selection #2               |
|Price                                |$1.00 – $20.00 (preferred $5–$10 per Trading Plan)          |Stock Selection #4, Trading Plan |
|Float                                |≤ 20M shares (hard cap; tiered weight in ranker — see below)|Stock Selection #5, TA Series p.4|

**Float tiering.** Float is gated as a hard ≤ 20M cap *and* weighted in the ranker. Tiers (implemented in `scanner/ranking.py::float_tier_weight`):

* `< 10M` — *preferred* (weight 2). Surfaces above acceptable peers at equal % change.
* `10M – 20M` — *acceptable* (weight 1). Default tier; passes the hard cap.
* `> 20M` — *Gap-and-Go-only* (weight 0). Outside the scanner's 20M hard cap today; the band exists so the policy is uniform when Phase-3 Gap-and-Go pattern detection raises the cap to 50M.

Sort key in the ranker is `(-pct_change, -float_tier_weight, ticker)`. See "Resolved Decisions" appendix → ISSUE-008 for the rationale.

**News catalyst** is a *soft signal* (recorded on every pick, not a hard filter). The scanner does not gate on news presence; the catalyst classifier (§3.2) is the gate, downstream of the scanner. See "Resolved Decisions" appendix → D5/#39.

**Output:** ranked list, sorted by `(-pct_change, -float_tier_weight, ticker)`. Top N (N = 5) become the watchlist candidates. Refresh every 1–2 seconds during the 7:00–11:00 AM ET window (the Trading Plan’s defined trading hours).

**Implementation note:** Cameron uses his proprietary “Top Gainers” scanner. The agent should reproduce its semantics, not the UI. A polygon.io / Alpaca / IBKR data feed plus a rolling 30-day SMA of daily volume gives you the relative volume metric. Float is harder — pull from a paid reference dataset (Benzinga, Polygon reference, or scrape from broker fundamentals). Cache and refresh daily.

### 3.2 Catalyst Classifier

The cleanest opportunity for an LLM in this pipeline. For each scanner hit, fetch the top news headlines from the last 24h and classify. **Catalyst-based rejection (`is_real == false`, `is_dilutive == true`, `REVERSE_SPLIT`, non-bullish sentiment) lives at this stage — not in the scanner.** The scanner records `news_present` and `headline_count` as soft signals on every pick (per D5/#39); the classifier is the actual gate downstream.

**Schema:**

```json
{
  "ticker": "AVTX",
  "catalyst_type": "M&A | FDA | EARNINGS | CONTRACT | OFFERING | PARTNERSHIP | REVERSE_SPLIT | RUMOR | NONE",
  "sentiment": "STRONG_POSITIVE | POSITIVE | NEUTRAL | NEGATIVE",
  "is_dilutive": true | false,
  "is_real": true | false,   // is this a credible event or a fluff PR?
  "headline_summary": "...",
  "confidence": 0.0–1.0
}
```

**Critical filters that must reject the trade:**

- `is_dilutive == true` — secondary offerings, ATM offerings, registered direct offerings. Cameron’s Chart Patterns guide explicitly shows “Buyout Headline that Ends up Not Being Real” as a failure case (page 80 of the study guide).
- `catalyst_type == REVERSE_SPLIT` — these spike then collapse.
- `sentiment in (NEUTRAL, NEGATIVE)` — Cameron only takes long-side momentum on bullish catalysts.

**Prompt skeleton:**

```
You are screening news for day-trading momentum setups. The trader buys 
small-cap stocks that are gapping up on bullish catalysts. He AVOIDS 
trades when the catalyst is a stock offering, reverse split, or vague 
PR puffery. Given these headlines for {ticker}, classify... {schema}
```

The classifier should run on every new headline within the trading window, not just at watchlist add. If a dilutive offering hits mid-trade, the exit monitor should treat it as an immediate flatten signal.

### 3.3 Daily Chart Strength Filter

Per the Gap and Go strategy doc (TA Series p.4): “I may look at the daily chart to see if the stock is above or below its 20, 50, and 200 EMA. I like to avoid stocks that are below those moving averages.”

**Logic:**

```
daily_strength_score = 0
if close > EMA20: score += 1
if close > EMA50: score += 1
if close > EMA200: score += 1
if today is breakout above multi-month resistance: score += 1
if stock is at or near all-time / 52-week lows AND showing reversal volume: score += 1   # turnaround story per Stock Selection p.5 (AVTX example)
```

Score ≥ 2 → keep on watchlist. Score 0–1 → demote (still tradeable but with reduced size, per Cameron’s nuance that he sometimes plays turnaround stories with tighter risk).

### 3.4 Pattern Detector

This is the hardest module because Cameron’s patterns blend numeric definitions with visual judgment. Implement them as a hierarchy: deterministic rules first, ML as a tie-breaker only.

**3.4.1 Gap and Go entry triggers (TA Series, Gap and Go, p.4-5):**

```
PRE_MARKET_HIGH = max(high) over [04:00 - 09:30] ET
PRE_MARKET_BULL_FLAG_HIGH = upper boundary of pre-market consolidation 
                            after initial gap leg

trigger_entry IF:
  current_price crosses above PRE_MARKET_HIGH on volume > avg_5min_volume
  OR
  current_price crosses above PRE_MARKET_BULL_FLAG_HIGH 
    AND time within [09:30, 10:00] ET
```

Stop = below the pre-market consolidation low or below the breakout candle low (whichever is closer). Target = 2:1 R:R minimum.

**3.4.2 Micro Pullback (TA Series, Micro Pullback, p.7-8):**

A Micro Pullback is *not* a 1-min or 5-min pullback — it’s much smaller. Operationalized:

```
MICRO_PULLBACK on 1-min:
  - last 1-3 candles include exactly one small red candle 
    OR a candle with a lower wick > body but green close
  - red/wick candle range < 0.4 * ATR(14, 1-min)
  - stock is above VWAP and 9-EMA on 1-min
  - prior 3-candle move was positive ≥ 1× ATR(14)
  - entry trigger: price reclaims high of pullback candle on rising volume
  - stop: low of the pullback candle (Cameron: "my stop is the low of the pullback")

MICRO_PULLBACK on 10-sec:
  - same logic but on 10-second bars
  - additionally accepts "bull flag" or "flat top" formations 
    of 3-8 bars consolidating after a strong leg
  - per Cameron (p.8): "I'd wait generally for volume to come in 
    and a break through the highs"
```

Cameron explicitly notes (p.7) the Micro Pullback is more aggressive than waiting for a proper 1-min or 5-min pullback. The agent should reflect this with a higher confidence threshold (say, requires daily strength ≥ 3 or a confirmed Gap and Go context) before taking Micro Pullback entries.

**3.4.3 Bull Flag (1-min and 5-min):**

```
BULL_FLAG:
  - Pole: a strong upward move of ≥ 2× ATR(14) over 3-8 candles, 
          with volume > 1.5× recent avg
  - Flag: 3-10 candles of consolidation forming a slight downward channel,
          range < 50% of pole height,
          volume declining vs pole
  - Trigger: close above flag's upper trendline on volume ≥ pole avg volume
  - Stop: low of the flag
  - Target: pole height projected from breakout point (or 2:1 R:R, whichever first)
```

**3.4.4 Flat Top Breakout:**

```
FLAT_TOP:
  - 3+ consecutive candles with highs within X cents (X = 1% of price)
  - lows trending up or flat
  - Trigger: first candle to break the flat top high on volume confirmation
  - Stop: low of the flat top range
```

**3.4.5 First Candle to Make a New High** (Stock Selection PDF, p.6):

The simplest pattern. After a small pullback inside an uptrend on the 1-min, buy the first candle that closes above the prior swing high. Stop = low of the prior candle. This is Cameron’s core “bread and butter” entry.

### 3.5 Position Sizer

Cameron’s Sample Trading Plan: risk per trade ≈ 5% of account.
Cameron’s Small Account Strategy: risk $50 to make $100 (during first week of $1K account).

**Formula:**

```
max_risk_dollars = account_equity * risk_pct_per_trade   # default 0.05, can scale down
stop_distance = entry_price - stop_price
shares = floor(max_risk_dollars / stop_distance)

# Cap by buying power (cash account, no leverage if US broker)
max_shares_by_cash = floor(account_equity / entry_price)
shares = min(shares, max_shares_by_cash)
```

**Adaptive scaling (per Cameron’s “tier-down” practice):**

- After 2 consecutive losers in the day → halve `risk_pct_per_trade`.
- After 1 winner that hit target → restore to baseline.
- Cold market regime → use 0.5× baseline.
- Hot market regime → up to 1.5× baseline (capped at account-level max).

### 3.6 Execution Layer

For each entry signal, place an atomic OCO bracket order: market or marketable-limit entry, hard stop at the defined stop, and a limit at the 2:1 target for the first scale-out (typically 50% of position).

**Order routing considerations:**

- Use marketable limit orders rather than pure market orders to bound slippage on low-float names where the spread can blow out.
- Set a max-acceptable slippage (e.g., 0.5% or 1 ATR(14, 1-min), whichever is smaller). If the fill would exceed that, cancel and skip.
- For exits, use market orders — Cameron’s “breakout or bailout” mentality (TA Series p.4) prioritizes getting out fast over getting a good price.

### 3.7 Exit Monitor

Continuously evaluates these exit triggers as long as a position is open. Any trigger fires a flatten command.

|Trigger               |Definition                                                 |Source                            |
|----------------------|-----------------------------------------------------------|----------------------------------|
|Target hit            |First scale at 2:1 R:R; trail remaining position with 9-EMA|Stock Selection p.5, TA Series p.6|
|Hard stop             |Price hits pre-defined stop price                          |All docs                          |
|Jackknife candle      |Single candle range > 2× ATR(14) with close in lower 1/3   |Trading Plan; YouTube transcript  |
|MACD cross            |MACD line crosses below signal line on the trade timeframe |Trading Plan                      |
|Volume drying up      |Last 3 bars’ volume < 50% of 20-bar avg                    |Trading Plan; TA Series p.6       |
|First red 5-min candle|Used when entry was on a 5-min setup                       |TA Series p.6                     |
|L2 weakness           |Big offer appears on the inside ask, or lifts disappear    |TA Series p.6                     |
|Dilutive news appears |Catalyst classifier flags new offering/dilution            |Inferred from p.80 study guide    |

Cameron’s documented exit philosophy: “Get in, get green, get out. Cut losses faster. Don’t hold and hope.” (Trading Plan worksheet)

### 3.8 Risk Supervisor (kill switch)

Sits above everything and has authority to flatten all positions and disable new entries.

**Hard rules (verbatim from Small Account Strategy p.3):**

```
RULE 1: max_risk_per_trade = $50 (week 1, scales with account)
RULE 2: daily_max_loss = -$100 (10% of $1K account)
        → stop trading for the day, no exceptions
RULE 3: 3 consecutive losers → stop trading for the day
```

**Additional supervisor responsibilities:**

- Track PDT rule: in a margin account < $25K, allow ≤ 3 day trades per rolling 5 business days. Reject 4th attempt.
- Track cash settlement (T+1 since May 2024 per Small Account Strategy p.2): in a cash account, only allow trades funded by settled cash.
- Enforce single-position rule (Cameron is “all in” per his Buffett quote on p.2): reject new entries while a position is open unless the existing position is at break-even with stop moved up.

### 3.9 Pre-Market Routine Module

Per the Trading Plan worksheet pre-trading checklist, run this every day at ~07:00 ET:

```
- Pull overnight news for prior day's runners (continuation candidates)
- Run scanner against pre-market quotes (gap %, volume, float)
- Score top 5 candidates, output watchlist with notes
- Identify "the obvious stock" (Cameron's checklist Q4)
- Note: when was the last big squeeze? (regime indicator, checklist Q5)
- Compute today's risk_per_trade and daily_max_loss based on current equity
- Output a "trading plan for the day" summary (logged to journal)
```

### 3.10 Market Regime Detector

This is what saves the agent from the deepest drawdowns. Cameron’s documents repeatedly distinguish “hot cycles” from “cold cycles” (Trading Plan checklist Q6; Small Account Strategy “Being Present”).

**Regime score (computed daily, updated intraday):**

```
hot_market_score = 0
+ count(stocks meeting full scanner criteria today) / typical_count
+ avg_intraday_range of small-caps today / 30-day avg
+ # stocks gapping >20% pre-market / 30-day avg
+ recent (last 5 days) win rate of the agent
+ VIX-adjusted small-cap volume
```

Map score to one of: COLD, NEUTRAL, WARM, HOT.

|Regime |Action                                                                |
|-------|----------------------------------------------------------------------|
|COLD   |risk_per_trade × 0.25, max 1 trade/day, only highest-confidence setups|
|NEUTRAL|risk_per_trade × 0.5, max 3 trades/day                                |
|WARM   |baseline                                                              |
|HOT    |risk_per_trade × 1.5 (capped), no trade-count cap until daily max     |

Cameron: “Take it slow when the market is slow, and be aggressive only when the market gives me the green light.” This is a place humans fail and agents can excel — humans want action; agents do nothing happily.

-----

## 4. Decision Logic — the agent’s main loop

```python
async def trading_loop():
    if not within_trading_hours():    # 07:00 - 11:00 ET per Trading Plan
        return
    
    if risk_supervisor.day_locked():  # daily max loss or 3-loser streak
        return
    
    regime = regime_detector.current()
    if regime == COLD and confidence_threshold_not_met:
        return
    
    candidates = scanner.top_n(n=5)
    candidates = [c for c in candidates if catalyst_classifier.is_tradeable(c)]
    candidates = [c for c in candidates if daily_chart.strength_score(c) >= 2]
    
    watchlist = candidates[:3]    # Cameron's "top 2-3"
    
    for ticker in watchlist:
        signal = pattern_detector.evaluate(ticker)
        
        if not signal.fires:
            continue
        
        if has_open_position():
            continue   # one position at a time
        
        size = position_sizer.compute(
            entry=signal.entry_price,
            stop=signal.stop_price,
            account_equity=account.equity,
            regime=regime
        )
        
        if size == 0:
            continue
        
        if not pdt_compliant() or not cash_settled(size * signal.entry_price):
            continue
        
        execution.place_bracket_order(
            ticker=ticker,
            shares=size,
            entry=signal.entry_price,
            stop=signal.stop_price,
            target=signal.entry_price + 2 * (signal.entry_price - signal.stop_price)
        )
        
        journal.log_entry(signal, size, regime)


async def position_management_loop():
    while has_open_position():
        for trigger in exit_monitor.evaluate():
            if trigger.fires:
                execution.flatten()
                journal.log_exit(trigger.reason)
                risk_supervisor.update(trade_pnl)
                break
```

-----

## 5. Where AI Agents Genuinely Improve on Human Execution

**Discipline.** Cameron’s #1 cause of trader failure (per his “How to Day Trade” book and the SAC2024 strategy doc) is not strategy — it’s failure to follow rules. Agents follow rules trivially. The kill switch fires; the agent stops trading. No revenge trades, no averaging down, no “this time it’s different.”

**Watch breadth.** A human watches 1–3 charts. An agent monitors the entire universe simultaneously and only alerts/acts when criteria are met. This means the agent doesn’t suffer from FOMO on stocks it didn’t see, and doesn’t anchor on yesterday’s winners.

**Reaction time on exits.** Human exits lag by 2–10 seconds while the trader processes the chart. An agent reacts in milliseconds when an exit trigger fires — particularly valuable on jackknife candles where every tick of slippage compounds.

**Regime obedience.** Humans want to trade. Agents are happy to do nothing. The “cold market → don’t trade” rule is the single highest-leverage discipline rule in Cameron’s framework, and it’s the one humans break most often.

**Journaling and feedback.** Every trade gets logged with full state — entry conditions, regime, catalyst classification, pattern type, exit reason. This dataset becomes the training fuel for refining the catalyst classifier and pattern thresholds.

-----

## 6. Where the Agent Will Struggle

**Catalyst nuance.** “Strong news” vs “fluff PR” is a judgment call Cameron has refined over 15 years and 20,000+ trades. An LLM classifier is decent but will misfire on novel catalyst types, ambiguous biotech press releases, and crypto-adjacent news. Mitigation: require human review on any catalyst with classifier confidence < 0.8 during the agent’s first months of live trading.

**Pattern visual judgment.** Cameron’s chart pattern study guide (112 pages of annotated examples) shows that “Bull Flag Trap,” “Buyout Headline that Ends up Not Being Real,” and similar failures look almost identical to valid setups *until they fail*. The agent will take some of these. Risk management contains the damage; pattern training over time reduces frequency.

**Capacity ceiling.** The strategy works on $2K because you can scalp a few thousand shares of a low-float name without moving the tape. At $200K+, the agent starts being the marginal buyer/seller, slippage explodes, and edge disappears. The architecture should hard-cap shares per trade at some fraction (e.g., 1–2%) of the symbol’s average 1-minute volume.

**Latency competition.** News-driven low-float momentum is contested by HFT desks with co-located infrastructure and millisecond reaction. A retail-grade API stack (Alpaca, IBKR, Polygon) has 100–500ms of latency. The agent is not going to beat HFT to the news; its edge has to be discipline and selectivity, not speed.

**Strategy regime risk.** Low-float momentum had a generational bull run during 2020–2021 retail-mania. It’s still alive in 2024–2026 but compressed. If small-cap momentum dies as a category — fewer stocks meeting scanner criteria for months on end — the agent will sit idle, which is the correct response but won’t generate returns. This is structural and unfixable.

**Survivorship in the source material.** Cameron’s $583 → $10M is real and audited, but it’s one outlier path. Backtesting the rules across many simulated traders / paper-trading runs will produce a distribution where most outcomes are near zero or negative. The agent’s expected outcome is *that distribution*, not Cameron’s specific path. Calibrate expectations accordingly.

-----

## 7. Implementation Phases

**Phase 0 — Data plumbing (2–4 weeks).** Real-time market data, news feed, scanner with the four hard filters (relative volume, % change, price band, float ≤ 20M; news is a soft signal — see §3.1), float reference data, broker API integration in paper mode.

**Phase 1 — Scanner + journaling, no trades (4–6 weeks).** Run scanner live, log what it finds, manually review whether the watchlist matches what Cameron picks on his daily YouTube recap. Tune until alignment is strong.

**Phase 2 — Pattern detector, paper trading (6–12 weeks).** Add pattern detection and paper-trade every signal. Compare paper P&L against Cameron’s published trades for the same days. Iterate on pattern definitions until paper performance is stable.

**Phase 3 — Risk supervisor + execution, simulator only (4 weeks).** Add full risk supervision and execution simulation with realistic slippage models.

**Phase 4 — Live trading, micro-stakes (8+ weeks).** $500–$1000 of real capital, 1-share or minimum-size trades. The point isn’t profit; it’s verifying that live execution behaves like the simulator.

**Phase 5 — Scale to target account size, with continuous monitoring.** Even here, set a hard equity stop (e.g., -20% from peak) at which the agent halts and requires human review.

-----

## 8. Validation Approach

**Backtest against Cameron’s published trades.** His daily recap videos and broker statements provide a ground-truth dataset. The agent should reproduce the *entries* (same ticker, same time window, same direction) on a high percentage of days, even if exact prices differ.

**Out-of-sample paper trading.** Months of paper trading in current market conditions before any live capital. Track: hit rate, average R:R achieved, max drawdown, Sharpe, distribution of daily P&L (not just mean).

**Adversarial testing.** Inject failure cases into the catalyst feed: dilutive offerings disguised as positive PR, pumped microcaps with fabricated news, halt/resume sequences. Verify the agent rejects or exits appropriately.

**Regime stress test.** Replay the agent on historical periods where small-cap momentum was dead (e.g., 2018 Q4, mid-2022). The agent should sit on its hands, not force trades. If it doesn’t, the regime detector is broken.

-----

## 9. Final Word

The agent design above is faithful to Cameron’s documented rules. The hardest thing to build in is not the pattern detector or the scanner — those are mechanical. It’s the *restraint*: the willingness to skip trades, halve risk, and shut down for the day. Those rules exist in Cameron’s documents because he’s seen what happens when traders break them. An agent that mechanically enforces them is, ironically, more disciplined than 99% of the humans who try this strategy. That discipline is the largest single source of edge the agent has.

The strategy itself is high-variance, capacity-constrained, and structurally dependent on a market regime that may not persist. Build conservatively, paper-trade exhaustively, scale slowly, and treat any live deployment as an ongoing experiment with a hard kill switch.

-----

## Appendix: Resolved Decisions

Each entry: ID, source, one-line rationale, and the ground-truth code/doc location.

* **D5 / #39 — News catalyst is a soft signal, not a hard filter.** Scanner records `news_present` and `headline_count` on every pick but does not gate on them; rejection of dilutive / fluff catalysts lives at the catalyst classifier (§3.2). Rationale: the news feed has high recall but low precision at the headline level (re-listings, mis-tagged tickers, premarket spam) — gating early would discard tradeable setups before the LLM classifier can apply judgement. Code: `src/ross_trading/scanner/scanner.py::Scanner.scan` (no news predicate); `src/ross_trading/scanner/types.py::ScannerPick.news_present, headline_count`.

* **ISSUE-008 — Tiered-float ranking weight.** The scanner enforces a hard `float ≤ 20M` cap; the ranker additionally weights `< 10M` (preferred) above `10M–20M` (acceptable) at equal % change. The `> 20M – ≤ 50M` Gap-and-Go band has weight 0 and is currently unreachable (hard-capped at 20M); it is reserved for Phase-3 Gap-and-Go pattern detection that may raise the cap. Rationale: preserves the architecture's tiered intent without changing the hard-filter surface; one place to evolve when Gap-and-Go ships. Code: `src/ross_trading/scanner/ranking.py::float_tier_weight, rank_picks`. Tests: `tests/unit/test_scanner_ranking.py::test_float_tier_weight_boundaries` and siblings.

* **Indicators contract — hand-rolled `Decimal` semantics, not a TA-Lib dependency.** The phrase "TA-Lib parity" in earlier drafts meant *behavior parity*: indicators are hand-rolled with `Decimal` precision so live and replay produce bit-identical values. There is no plan to add a TA-Lib runtime dependency. Each indicator's docstring is its behavior contract (initialization, value type, boundary semantics); see `src/ross_trading/indicators/ema.py` for the canonical template. Code: `src/ross_trading/indicators/ema.py`.