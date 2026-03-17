"""
MFDFA Engine — Multifractal Detrended Fluctuation Analysis
-----------------------------------------------------------
Computes multifractal scaling properties of financial time series
to detect regime changes and market stress.

Key outputs:
  - Generalized Hurst exponent h(q) for multiple q-orders
  - Multifractal spectrum width (Δα) — wider = more complex/stressed
  - Stress score normalized to [0, 1]
"""
import numpy as np
from numpy.polynomial import polynomial as P
from typing import Optional
from config import config


def _cumulative_deviation(series: np.ndarray) -> np.ndarray:
    """Compute cumulative deviation (profile) from mean."""
    return np.cumsum(series - np.mean(series))


def _segment_variance(profile: np.ndarray, start: int, end: int, order: int = 1) -> float:
    """
    Compute variance of the detrended segment using polynomial fit.
    Returns the RMS of residuals after removing the polynomial trend.
    """
    segment = profile[start:end]
    n = len(segment)
    if n < order + 2:
        return 0.0
    x = np.arange(n)
    # Fit polynomial trend
    coeffs = P.polyfit(x, segment, order)
    trend = P.polyval(x, coeffs)
    residuals = segment - trend
    return np.mean(residuals ** 2)


def compute_dfa_fluctuation(
    profile: np.ndarray,
    scale: int,
    q: float,
    poly_order: int = 1,
) -> float:
    """
    Compute the q-th order fluctuation function F_q(s) for a given scale s.
    
    For each non-overlapping segment of length `scale`:
      1. Fit and remove polynomial trend (order = poly_order)
      2. Compute segment variance
    Then aggregate variances using the q-th order.
    """
    n = len(profile)
    n_segments = n // scale

    if n_segments < 1:
        return np.nan

    variances = []

    # Forward pass
    for v in range(n_segments):
        start = v * scale
        end = start + scale
        var = _segment_variance(profile, start, end, poly_order)
        if var > 0:
            variances.append(var)

    # Backward pass (use remainder from the end)
    for v in range(n_segments):
        start = n - (v + 1) * scale
        end = start + scale
        var = _segment_variance(profile, start, end, poly_order)
        if var > 0:
            variances.append(var)

    if not variances:
        return np.nan

    variances = np.array(variances)

    if q == 0:
        # Special case: geometric mean (log-average)
        return np.exp(0.5 * np.mean(np.log(variances)))
    else:
        return (np.mean(variances ** (q / 2.0))) ** (1.0 / q)


def compute_mfdfa(
    series: np.ndarray,
    q_orders: list[float] = None,
    scale_min: int = None,
    scale_max: int = None,
    num_scales: int = None,
    poly_order: int = 1,
) -> dict:
    """
    Full MFDFA computation.
    
    Parameters:
        series: 1D array of log-returns or price series
        q_orders: list of q values (default from config)
        scale_min/max: range of segment sizes
        num_scales: number of scales to evaluate
        poly_order: detrending polynomial order
    
    Returns dict with:
        - hq: dict of {q: h(q)} generalized Hurst exponents
        - scales: array of scales used
        - fluctuations: dict of {q: F_q(s) array}
        - tau_q: dict of {q: τ(q)} scaling exponents
        - alpha: singularity strengths
        - f_alpha: multifractal spectrum
        - spectral_width: Δα (width of spectrum)
        - hurst: h(2) classical Hurst exponent
    """
    cfg = config.mfdfa
    q_orders = q_orders or cfg.q_orders
    scale_min = scale_min or cfg.scale_min
    n = len(series)
    scale_max = min(scale_max or cfg.scale_max, n // 4)
    num_scales = num_scales or cfg.num_scales

    if n < cfg.min_bars:
        return {"error": f"Need at least {cfg.min_bars} bars, got {n}"}

    # Step 1: Compute profile (cumulative deviation of returns)
    if np.std(series) < 1e-12:
        return {"error": "Series has zero variance"}

    # If series looks like prices, convert to log-returns first
    if np.mean(series) > 10:  # heuristic: prices are large
        returns = np.diff(np.log(series))
    else:
        returns = series

    profile = _cumulative_deviation(returns)

    # Step 2: Define scales (log-spaced)
    scales = np.unique(
        np.logspace(np.log10(scale_min), np.log10(scale_max), num_scales).astype(int)
    )
    scales = scales[scales >= 4]  # minimum viable segment

    # Step 3: Compute fluctuation functions for each q and scale
    fluctuations = {}
    for q in q_orders:
        fq = []
        for s in scales:
            f = compute_dfa_fluctuation(profile, s, q, poly_order)
            fq.append(f)
        fluctuations[q] = np.array(fq)

    # Step 4: Estimate h(q) via log-log regression for each q
    log_scales = np.log(scales)
    hq = {}
    for q in q_orders:
        fq = fluctuations[q]
        valid = ~np.isnan(fq) & (fq > 0)
        if np.sum(valid) < 3:
            hq[q] = np.nan
            continue
        coeffs = np.polyfit(log_scales[valid], np.log(fq[valid]), 1)
        hq[q] = coeffs[0]  # slope = h(q)

    # Step 5: Compute τ(q) = q·h(q) - 1
    tau_q = {q: q * hq[q] - 1 for q in q_orders if not np.isnan(hq.get(q, np.nan))}

    # Step 6: Compute multifractal spectrum f(α) via Legendre transform
    valid_q = sorted([q for q in q_orders if q in tau_q])
    if len(valid_q) >= 3:
        q_arr = np.array(valid_q)
        tau_arr = np.array([tau_q[q] for q in valid_q])
        # α = dτ/dq (numerical derivative)
        alpha = np.gradient(tau_arr, q_arr)
        # f(α) = q·α - τ(q)
        f_alpha = q_arr * alpha - tau_arr
        spectral_width = float(np.max(alpha) - np.min(alpha))
    else:
        alpha = np.array([])
        f_alpha = np.array([])
        spectral_width = 0.0

    return {
        "hq": hq,
        "scales": scales.tolist(),
        "fluctuations": {q: fq.tolist() for q, fq in fluctuations.items()},
        "tau_q": tau_q,
        "alpha": alpha.tolist() if len(alpha) > 0 else [],
        "f_alpha": f_alpha.tolist() if len(f_alpha) > 0 else [],
        "spectral_width": spectral_width,
        "hurst": hq.get(2, np.nan),
    }


def compute_mfdcca(
    series_x: np.ndarray,
    series_y: np.ndarray,
    q_orders: list[float] = None,
    scale_min: int = None,
    scale_max: int = None,
    num_scales: int = None,
) -> dict:
    """
    MFDCCA — Multifractal Detrended Cross-Correlation Analysis.
    
    Measures multifractal coupling between two series
    (e.g., NIFTY vs BANKNIFTY, NIFTY vs VIX).
    
    Returns coupling metrics similar to MFDFA but based on
    cross-covariance of detrended profiles.
    """
    cfg = config.mfdfa
    q_orders = q_orders or cfg.q_orders
    scale_min = scale_min or cfg.scale_min
    n = min(len(series_x), len(series_y))
    scale_max = min(scale_max or cfg.scale_max, n // 4)
    num_scales = num_scales or cfg.num_scales

    if n < cfg.min_bars:
        return {"error": f"Need at least {cfg.min_bars} bars, got {n}"}

    # Convert to returns if needed
    def to_returns(s):
        if np.mean(s) > 10:
            return np.diff(np.log(s))
        return s

    rx = to_returns(series_x[:n])
    ry = to_returns(series_y[:n])
    min_len = min(len(rx), len(ry))
    rx, ry = rx[:min_len], ry[:min_len]

    profile_x = _cumulative_deviation(rx)
    profile_y = _cumulative_deviation(ry)

    scales = np.unique(
        np.logspace(np.log10(scale_min), np.log10(scale_max), num_scales).astype(int)
    )
    scales = scales[scales >= 4]

    # Cross-correlation fluctuation function
    hq_xy = {}
    log_scales = np.log(scales)

    for q in q_orders:
        fq = []
        for s in scales:
            n_seg = min_len // s
            if n_seg < 1:
                fq.append(np.nan)
                continue
            cross_vars = []
            for v in range(n_seg):
                start, end = v * s, (v + 1) * s
                seg_x = profile_x[start:end]
                seg_y = profile_y[start:end]
                t = np.arange(s)
                # Detrend both
                cx = P.polyfit(t, seg_x, 1)
                cy = P.polyfit(t, seg_y, 1)
                res_x = seg_x - P.polyval(t, cx)
                res_y = seg_y - P.polyval(t, cy)
                cv = np.mean(res_x * res_y)
                cross_vars.append(abs(cv))

            cross_vars = np.array(cross_vars)
            cross_vars = cross_vars[cross_vars > 0]
            if len(cross_vars) == 0:
                fq.append(np.nan)
            elif q == 0:
                fq.append(np.exp(0.5 * np.mean(np.log(cross_vars))))
            else:
                fq.append(np.mean(cross_vars ** (q / 2.0)) ** (1.0 / q))
        fq = np.array(fq)
        valid = ~np.isnan(fq) & (fq > 0)
        if np.sum(valid) >= 3:
            coeffs = np.polyfit(log_scales[valid], np.log(fq[valid]), 1)
            hq_xy[q] = coeffs[0]
        else:
            hq_xy[q] = np.nan

    # Coupling strength = average |h_xy(q)|
    valid_h = [v for v in hq_xy.values() if not np.isnan(v)]
    coupling_strength = float(np.mean(np.abs(valid_h))) if valid_h else 0.0

    # Compute ρ(q) cross-correlation coefficients
    # ρ_q = F²_xy(q,s) / sqrt(F²_x(q,s) * F²_y(q,s))
    mfdfa_x = compute_mfdfa(series_x[:n])
    mfdfa_y = compute_mfdfa(series_y[:n])
    rho_q = {}
    for q in q_orders:
        hx = mfdfa_x.get("hq", {}).get(q, np.nan)
        hy = mfdfa_y.get("hq", {}).get(q, np.nan)
        hxy = hq_xy.get(q, np.nan)
        if not (np.isnan(hx) or np.isnan(hy) or np.isnan(hxy)):
            # ρ(q) approximation via Hurst exponents
            if abs(hx) > 0 and abs(hy) > 0:
                rho_q[q] = round(float(hxy / np.sqrt(abs(hx * hy))), 4)
            else:
                rho_q[q] = 0.0
        else:
            rho_q[q] = 0.0

    # Scale-dependent correlation ρ(s) at q=2
    rho_s = {}
    q_ref = 2
    if q_ref in q_orders:
        for idx, s in enumerate(scales):
            s_int = int(s)
            n_seg = min_len // s_int
            if n_seg < 1:
                continue
            # Compute F²_xy, F²_x, F²_y at this scale for q=2
            cross_v, self_vx, self_vy = [], [], []
            for v in range(n_seg):
                start, end = v * s_int, (v + 1) * s_int
                seg_x = profile_x[start:end]
                seg_y = profile_y[start:end]
                t = np.arange(s_int)
                cx = P.polyfit(t, seg_x, 1)
                cy = P.polyfit(t, seg_y, 1)
                res_x = seg_x - P.polyval(t, cx)
                res_y = seg_y - P.polyval(t, cy)
                cross_v.append(abs(np.mean(res_x * res_y)))
                self_vx.append(np.mean(res_x ** 2))
                self_vy.append(np.mean(res_y ** 2))
            cross_v = np.array(cross_v)
            self_vx = np.array(self_vx)
            self_vy = np.array(self_vy)
            fxy = np.mean(cross_v)
            fx = np.mean(self_vx)
            fy = np.mean(self_vy)
            denom = np.sqrt(fx * fy)
            if denom > 0:
                rho_s[s_int] = round(float(fxy / denom), 4)

    # Joint multifractal spectrum
    tau_xy = {q: q * hq_xy[q] - 1 for q in q_orders if not np.isnan(hq_xy.get(q, np.nan))}
    valid_q_xy = sorted([q for q in q_orders if q in tau_xy])
    alpha_xy, f_alpha_xy, spectral_width_xy = [], [], 0.0
    if len(valid_q_xy) >= 3:
        q_arr = np.array(valid_q_xy)
        tau_arr = np.array([tau_xy[q] for q in valid_q_xy])
        alpha_xy = np.gradient(tau_arr, q_arr).tolist()
        f_alpha_xy = (q_arr * np.array(alpha_xy) - tau_arr).tolist()
        spectral_width_xy = float(max(alpha_xy) - min(alpha_xy))

    return {
        "hq_xy": hq_xy,
        "coupling_strength": coupling_strength,
        "rho_q": rho_q,
        "rho_s": rho_s,
        "hq_x": mfdfa_x.get("hq", {}),
        "hq_y": mfdfa_y.get("hq", {}),
        "alpha_xy": alpha_xy,
        "f_alpha_xy": f_alpha_xy,
        "spectral_width_xy": spectral_width_xy,
    }


def compute_scale_analysis(mfdfa_result: dict) -> dict:
    """
    Extract scale-dependent insights from MFDFA result.

    Returns:
        - dominant_scale: the timescale where Hurst is strongest
        - scale_hurst_map: mapping of scale ranges to local Hurst behavior
        - regime_persistence: how stable the current scaling regime is
    """
    if "error" in mfdfa_result or not mfdfa_result.get("scales"):
        return {"dominant_scale": 0, "scale_hurst_map": {}, "regime_persistence": 0.0}

    scales = mfdfa_result["scales"]
    hq = mfdfa_result["hq"]
    h2 = hq.get(2, 0.5)
    fluctuations = mfdfa_result.get("fluctuations", {})

    # Find scale with strongest trend-persistence
    fq2 = fluctuations.get(2, [])
    if len(fq2) >= 4 and len(scales) >= 4:
        log_s = np.log(np.array(scales, dtype=float))
        log_f = np.log(np.array(fq2, dtype=float))
        valid = np.isfinite(log_f)
        if np.sum(valid) >= 4:
            # Sliding window local Hurst
            window = max(3, len(scales) // 3)
            local_hursts = []
            for i in range(len(scales) - window + 1):
                seg_s = log_s[i:i + window]
                seg_f = log_f[i:i + window]
                v = np.isfinite(seg_f)
                if np.sum(v) >= 2:
                    c = np.polyfit(seg_s[v], seg_f[v], 1)
                    local_hursts.append({"scale_start": int(scales[i]), "scale_end": int(scales[min(i + window - 1, len(scales) - 1)]), "hurst": round(float(c[0]), 4)})

            # Dominant scale = where local Hurst is most persistent (highest h)
            if local_hursts:
                best = max(local_hursts, key=lambda x: x["hurst"])
                dominant_scale = (best["scale_start"] + best["scale_end"]) // 2
            else:
                dominant_scale = int(scales[len(scales) // 2])
        else:
            dominant_scale = int(scales[len(scales) // 2])
            local_hursts = []
    else:
        dominant_scale = int(scales[len(scales) // 2]) if scales else 0
        local_hursts = []

    # Regime persistence: how consistent h(2) is across scales
    if local_hursts:
        h_values = [lh["hurst"] for lh in local_hursts]
        regime_persistence = round(1.0 - min(1.0, float(np.std(h_values)) / 0.2), 4)
    else:
        regime_persistence = 0.5

    return {
        "dominant_scale": dominant_scale,
        "local_hursts": local_hursts,
        "regime_persistence": regime_persistence,
    }


def compute_market_memory(scale_analysis: dict, candle_interval_minutes: int = 5) -> dict:
    """
    Express the dominant timescale as human-readable market memory.

    Returns:
        {
            "memory_bars": int,
            "memory_minutes": int,
            "memory_time_str": str,
            "persistence_quality": str,
        }
    """
    dominant = scale_analysis.get("dominant_scale", 0)
    persistence = scale_analysis.get("regime_persistence", 0.5)

    memory_minutes = dominant * candle_interval_minutes

    if memory_minutes < 60:
        time_str = f"~{memory_minutes} minutes"
    elif memory_minutes < 480:
        hours = memory_minutes / 60
        time_str = f"~{hours:.1f} hours"
    else:
        sessions = memory_minutes / 375  # 6.25 hrs per trading session
        time_str = f"~{sessions:.1f} trading sessions"

    if persistence > 0.7:
        quality = "strong"
    elif persistence > 0.4:
        quality = "moderate"
    else:
        quality = "weak"

    return {
        "memory_bars": dominant,
        "memory_minutes": memory_minutes,
        "memory_time_str": time_str,
        "persistence_quality": quality,
        "persistence_score": round(persistence, 4),
    }
