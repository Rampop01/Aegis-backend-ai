import numpy as np
import pandas as pd

MIN_WARMUP_ROWS = 2  # need at least one prior period plus the warmup window


def compute_volatility_scores(df, window):
    """
    Adds 'return', 'rolling_std' and 'volatility_score' columns to df (which
    must have 'timestamp' and 'rate' columns, sorted ascending by timestamp).

    volatility_score is a 0-100 min-max normalization of the rolling realized
    volatility (std of pct returns) observed within the series itself. This is
    a realized-volatility proxy for the predictive VolatilityScore described in
    the roadmap (Issue #BK-3) -- once a trained model exists, its output can be
    substituted here without changing the simulation logic below.
    """
    df = df.copy()
    df["return"] = df["rate"].pct_change()
    df["rolling_std"] = df["return"].rolling(window=window, min_periods=window).std()

    valid = df["rolling_std"].dropna()
    if valid.empty:
        df["volatility_score"] = np.nan
        return df

    min_std, max_std = valid.min(), valid.max()
    if max_std == min_std:
        df["volatility_score"] = df["rolling_std"].where(df["rolling_std"].isna(), 0.0)
    else:
        df["volatility_score"] = (df["rolling_std"] - min_std) / (max_std - min_std) * 100

    return df


def _max_drawdown_pct(values):
    values = np.asarray(values, dtype=float)
    running_max = np.maximum.accumulate(values)
    drawdowns = (values - running_max) / running_max
    return float(abs(drawdowns.min()) * 100) if len(drawdowns) else 0.0


def _simulate(df, threshold, initial_capital, stable_apy, strategy_enabled):
    """
    Simulates a portfolio walking forward through df's periods.

    'rate' is the quote currency per 1 USD (e.g. NGN per USD for "USD/NGN").
    Holding local-currency exposure ("risky") loses USD value when rate rises;
    holding "stable" earns a flat annualized stable_apy and is immune to FX
    moves. When strategy_enabled, the allocation decision for the period
    starting at row i-1 uses the volatility_score known at i-1 (no lookahead).
    """
    timestamps = df["timestamp"].tolist()
    rates = df["rate"].tolist()
    if strategy_enabled:
        scores = [None if pd.isna(s) else s for s in df["volatility_score"].tolist()]
    else:
        scores = [None] * len(df)

    value = initial_capital
    values = [value]
    in_stable = False
    switches = 0
    stable_periods = 0
    total_periods = len(df) - 1

    for i in range(1, len(df)):
        prev_rate, curr_rate = rates[i - 1], rates[i]
        elapsed_days = (timestamps[i] - timestamps[i - 1]).total_seconds() / 86400.0

        should_be_stable = strategy_enabled and scores[i - 1] is not None and scores[i - 1] > threshold

        if should_be_stable != in_stable:
            switches += 1
        in_stable = should_be_stable

        if in_stable:
            stable_periods += 1
            period_return = (1 + stable_apy / 100) ** (elapsed_days / 365) - 1
        else:
            period_return = (prev_rate / curr_rate) - 1

        value *= (1 + period_return)
        values.append(value)

    return {
        "final_value": value,
        "total_return_pct": (value / initial_capital - 1) * 100,
        "max_drawdown_pct": _max_drawdown_pct(values),
        "num_regime_switches": switches if strategy_enabled else 0,
        "time_in_stable_pct": (stable_periods / total_periods * 100) if strategy_enabled and total_periods else 0.0,
    }


def run_backtest(rate_rows, volatility_window, threshold, initial_capital, stable_apy):
    """
    rate_rows: [(timestamp, rate), ...] for a single pair, any order.

    Returns a dict with 'data_points_used', 'strategy_metrics',
    'baseline_metrics' and 'comparison'. Raises ValueError for insufficient or
    invalid data.
    """
    df = pd.DataFrame(rate_rows, columns=["timestamp", "rate"])
    df["rate"] = df["rate"].astype(float)
    df = df.sort_values("timestamp").reset_index(drop=True)

    min_rows = volatility_window + MIN_WARMUP_ROWS
    if len(df) < min_rows:
        raise ValueError(
            f"Insufficient data: found {len(df)} rate points, "
            f"need at least {min_rows} for a {volatility_window}-period rolling window."
        )

    if (df["rate"] <= 0).any():
        raise ValueError("Non-positive rate encountered in fx_rates series; cannot backtest.")

    scored = compute_volatility_scores(df, volatility_window)

    strategy_metrics = _simulate(scored, threshold, initial_capital, stable_apy, strategy_enabled=True)
    baseline_metrics = _simulate(scored, threshold, initial_capital, stable_apy, strategy_enabled=False)

    comparison = {
        "return_improvement_pct": strategy_metrics["total_return_pct"] - baseline_metrics["total_return_pct"],
        "drawdown_reduction_pct": baseline_metrics["max_drawdown_pct"] - strategy_metrics["max_drawdown_pct"],
    }

    return {
        "data_points_used": len(df),
        "strategy_metrics": strategy_metrics,
        "baseline_metrics": baseline_metrics,
        "comparison": comparison,
    }
