"""Milestone 6: fair value, margin of safety, and status for every company.

Fair value methods (rules.MODEL_RULES[...]["fair_value_method"]):
  standard  fair EV/EBIT x min(TTM EBIT, 3-yr average EBIT) - net debt - preferred - minority,
            per share (TTM only, low confidence, after a material corporate action until the
            next 10-K). Fair EV/EBIT = w x sub-industry median + (1 - w) x anchor median,
            w = n / (n + 8), n = valid sub-industry peers (w = 0 if n < 2); anchor = industry
            group (>= 8 valid) -> sector (>= 8) -> all standard; capped at 25x. Valid = EBIT > 0,
            EV > 0, EV/EBIT <= 100; medians winsorized 5/95 within each group
  reit      peer-median P/FFO x min(TTM FFO, 3-yr average FFO) per share; peers: GICS
            sub-industry if >= 5 valid, else all
  bank      OLS P/TBV ~ ROTCE across banks; fair P/TBV at own ROTCE x TBV per share
  insurer   OLS P/B ~ ROE across insurers; fair P/B at own ROE x book value per share
Margin of safety = (fair value - price) / fair value.

Status precedence: excluded > corporate_action_pending > insufficient_data > not_applicable >
disqualified > flag / watch / no_flag.
  flag   normal confidence and 30% <= MoS <= 60% (and the Treasury guard passes)
  watch  would pass but low confidence, or MoS > 60% (extreme_mos_review)
Disqualified rows also record status_without_dq and the top Beneish contributor.
"""
import numpy as np
import pandas as pd

from screener import rules as R

MIN_R2_NORMAL = 0.30  # regression fits below this make bank/insurer valuations low confidence


def _truthy(v) -> bool:
    """True for True/np.True_/'True'; False for False, NaN, None."""
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA:
        return False
    return str(v).lower() == "true"


def _winsorized_median(values: pd.Series, limits: tuple[float, float]) -> float:
    lo, hi = values.quantile(limits[0]), values.quantile(limits[1])
    return float(values.clip(lo, hi).median())


def standard_peers(df: pd.DataFrame, eligible: pd.Series) -> pd.DataFrame:
    """Per standard company: shrinkage-blended, capped fair EV/EBIT with its components."""
    cfg = R.MODEL_RULES["standard"]["fair_value_method"]
    std = df[df["sector_model"] == "standard"]
    valid = std[eligible.reindex(std.index, fill_value=False)
                & (std["ebit_ttm"] > 0) & (std["enterprise_value"] > 0)
                & std["ev_ebit"].between(0, cfg["max_multiple"])]
    out = {}
    for idx, row in std.iterrows():
        sub = valid[valid[cfg["sub_level"]] == row[cfg["sub_level"]]]["ev_ebit"]
        n = len(sub)
        sub_median = _winsorized_median(sub, cfg["winsorize"]) if n else None
        for level in cfg["anchor_levels"]:
            anchor = valid[valid[level] == row[level]]["ev_ebit"]
            if len(anchor) >= cfg["anchor_min"] or level == cfg["anchor_levels"][-1]:
                break
        anchor_median = _winsorized_median(anchor, cfg["winsorize"]) if len(anchor) else None
        w = n / (n + cfg["shrinkage_k"]) if n >= cfg["min_sub_peers"] else 0.0
        blended = None
        if anchor_median is not None:
            blended = w * sub_median + (1 - w) * anchor_median if w else anchor_median
        capped = blended is not None and blended > cfg["max_fair_multiple"]
        out[idx] = {
            "peer_level": "shrinkage", "peer_group": f"{row[cfg['sub_level']]} | {row[level]}",
            "peer_count": n + len(anchor), "peer_n_sub": n, "peer_w": w,
            "peer_median_sub": sub_median, "peer_median_anchor": anchor_median,
            "peer_anchor_level": level, "peer_anchor_group": row[level], "peer_anchor_n": len(anchor),
            "peer_blended": blended, "peer_cap_applied": capped,
            "peer_multiple": min(blended, cfg["max_fair_multiple"]) if blended is not None else None,
        }
    return pd.DataFrame.from_dict(out, orient="index")


def reit_peers(df: pd.DataFrame, eligible: pd.Series) -> pd.DataFrame:
    cfg = R.MODEL_RULES["reit"]["fair_value_method"]
    reits = df[df["sector_model"] == "reit"]
    valid = reits[eligible.reindex(reits.index, fill_value=False) & (reits["p_ffo"] > 0)]
    out = {}
    for idx, row in reits.iterrows():
        peers = valid[valid["gics_sub_industry"] == row["gics_sub_industry"]]["p_ffo"]
        level, group = "gics_sub_industry", row["gics_sub_industry"]
        if len(peers) < cfg["min_peers"]:
            peers, level, group = valid["p_ffo"], "sector_model", "reit"
        out[idx] = {"peer_level": level, "peer_group": group, "peer_count": len(peers),
                    "peer_multiple": float(peers.median()) if len(peers) else None}
    return pd.DataFrame.from_dict(out, orient="index")


def regression(df: pd.DataFrame, model: str, eligible: pd.Series) -> tuple[pd.DataFrame, dict]:
    cfg = R.MODEL_RULES[model]["fair_value_method"]
    x_col, y_col = cfg["x"], cfg["y"]
    sub = df[df["sector_model"] == model]
    fit_set = sub[eligible.reindex(sub.index, fill_value=False) & sub[x_col].notna() & (sub[y_col] > 0)]
    fit = {"model": model, "y": y_col, "x": x_col, "n": len(fit_set)}
    if len(fit_set) < 3:
        return pd.DataFrame(index=sub.index), {**fit, "r2": None}
    slope, intercept = np.polyfit(fit_set[x_col], fit_set[y_col], 1)
    pred = intercept + slope * fit_set[x_col]
    ss_res = float(((fit_set[y_col] - pred) ** 2).sum())
    ss_tot = float(((fit_set[y_col] - fit_set[y_col].mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot else None
    fit.update(intercept=float(intercept), slope=float(slope), r2=r2)
    out = pd.DataFrame({"peer_level": "regression", "peer_group": model, "peer_count": len(fit_set),
                        "fair_multiple": intercept + slope * sub[x_col]}, index=sub.index)
    return out, fit


def value_all(df: pd.DataFrame, summary: pd.DataFrame, corp: pd.DataFrame,
              treasury_yield_pct: float) -> tuple[pd.DataFrame, list[dict]]:
    df = df.copy()
    df = df.merge(summary[["cik", "stale_10k", "has_10k", "latest_10k_filed"]], on="cik", how="left")
    df = df.merge(corp, on="cik", how="left")
    df["corporate_action_pending"] = df["corporate_action_pending"].astype("boolean").fillna(False)
    df["excluded_reason"] = df["ticker"].map(R.MANUAL_EXCLUSIONS)
    eligible = df["excluded_reason"].isna() & ~df["corporate_action_pending"]

    peers = pd.concat([standard_peers(df, eligible), reit_peers(df, eligible)])
    fits = []
    for model in ("bank", "insurer"):
        out, fit = regression(df, model, eligible)
        peers = pd.concat([peers, out])
        fits.append(fit)
    df = df.join(peers)

    treasury = treasury_yield_pct / 100
    records = [_evaluate(row, treasury, {f["model"]: f for f in fits}) for _, row in df.iterrows()]
    result = pd.concat([df, pd.DataFrame(records, index=df.index)], axis=1)
    result["treasury_yield"] = treasury
    return result, fits


def _evaluate(row: pd.Series, treasury: float, fits: dict) -> dict:
    model = row["sector_model"]
    missing, dq, warnings, low = [], [], [], []
    rec = {"method": R.MODEL_RULES[model]["fair_value_method"]["type"], "fair_value": None, "mos": None,
           "base_value": None, "company_multiple": None}

    def g(col):
        v = row.get(col)
        return None if v is None or (isinstance(v, float) and np.isnan(v)) else v

    # ---- inputs ----
    price, shares = g("price"), g("shares")
    if price is None:
        missing.append("price")
    if shares is None:
        missing.append("shares")
    if not _truthy(row.get("has_10k")):
        missing.append("10-K")
    elif _truthy(row.get("stale_10k")):
        missing.append("current 10-K (latest is stale)")

    # Post-corporate-action regime: an event after the latest 10-K's fiscal year end, already
    # followed by a 10-Q. Annual history predates the event, so value on TTM only (low
    # confidence) until a 10-K covering a period after the event is filed.
    event, fy_end = g("event_filed"), g("fy_end")
    in_window = (isinstance(event, str) and isinstance(fy_end, str) and event > fy_end
                 and not _truthy(row.get("corporate_action_pending")))
    post_action = False
    if in_window:
        acq, removed, spun = g("acquisitions_ttm_to_assets"), g("revenue_restated_share_removed"), g("disposed_revenue_share")
        disposed = max([v for v in (removed, spun) if v is not None], default=None)
        parts = []
        if acq is not None:
            parts.append(f"acquisitions {acq:.0%} of assets")
        if disposed is not None:
            parts.append(f"disposed {disposed:.0%} of revenue")
        post_action = ((acq or 0) >= R.CORPORATE_ACTION_MIN_ACQ_TO_ASSETS
                       or (disposed or 0) >= R.CORPORATE_ACTION_MIN_DISPOSED_REVENUE)
        rec["corporate_action_materiality"] = (("material: " if post_action else "immaterial: ")
                                               + (", ".join(parts) or "no measurable size"))
    if post_action:
        low.append(f"post-corporate-action ({row.get('event_source')} {event}): TTM only until next 10-K")

    fair_value, not_applicable = None, None
    if model == "standard":
        rec["company_multiple"] = g("ev_ebit")
        ttm, avg3 = g("ebit_ttm"), g("ebit_3y_avg")
        if g("total_debt") is None:
            missing.append(f"total_debt ({row.get('debt_problem')})")
        if g("cash_like") is None:
            missing.append("cash")
        if post_action:
            base, basis = ttm, "ttm (post-corporate-action)"
            if ttm is None:
                missing.append("ebit_ttm")
        else:
            # Conservative base: the lower of TTM and 3-yr average EBIT.
            if avg3 is None:
                missing.append(f"ebit_3y_avg (only {int(g('ebit_3y_n') or 0)} years)")
            base, basis = _conservative_min(ttm, avg3, "ebit")
        if base is not None and base <= 0:
            not_applicable = ("3-yr average EBIT <= 0" if avg3 is not None and avg3 <= 0 and not post_action
                              else "TTM EBIT <= 0")
        rec.update(base_value=base, base_basis=basis)
        if g("peer_multiple") is None:
            missing.append("peer multiple")
        if not missing and not not_applicable:
            claims = g("enterprise_value") - g("market_cap") - g("net_debt")  # preferred + minority
            equity = g("peer_multiple") * base - g("net_debt") - claims
            fair_value = equity / shares
    elif model == "reit":
        rec["company_multiple"] = g("p_ffo")
        ttm, avg3 = g("ffo_ttm"), g("ffo_3y_avg")
        if ttm is None:
            missing.append("ffo (net income, D&A, or gains on property sales)")
        if post_action:
            base, basis = ttm, "ttm (post-corporate-action)"
        else:
            base, basis = _conservative_min(ttm, avg3, "ffo")
            if ttm is not None and avg3 is None:
                low.append("FFO 3-yr average unavailable (TTM only)")
        rec.update(base_value=base, base_basis=basis)
        if base is not None and base <= 0:
            not_applicable = "FFO <= 0"
        if not missing and not not_applicable and g("peer_multiple") is not None:
            fair_value = g("peer_multiple") * base / shares
    else:
        book_col, mult_col = ("tbv", "p_tbv") if model == "bank" else ("book_value", "p_b")
        x_col = "rotce" if model == "bank" else "roe"
        rec["company_multiple"] = g(mult_col)
        book = g(book_col)
        rec["base_value"] = book
        if book is None:
            missing.append(book_col)
        elif book <= 0:
            not_applicable = f"{book_col} <= 0"
        if g(x_col) is None:
            missing.append(x_col)
        fit = fits.get(model, {})
        if fit.get("r2") is None:
            missing.append("regression fit")
        elif fit["r2"] < MIN_R2_NORMAL:
            low.append(f"weak regression (R2={fit['r2']:.2f})")
        if not missing and not not_applicable:
            fm = g("fair_multiple")
            if fm is None or fm <= 0:
                not_applicable = "regression predicts multiple <= 0"
            else:
                fair_value = fm * book / shares

    # ---- disqualifiers (standard and REIT only) ----
    if "beneish_m" in R.MODEL_RULES[model]["disqualifiers"]:
        strict, imputed, acq = g("m_score"), g("m_score_imputed"), g("acquisitions_to_assets")
        if strict is not None and strict > R.BENEISH_THRESHOLD:
            if acq is not None and acq > R.BENEISH_MA_EXCEPTION:
                warnings.append(f"beneish review: M={strict:.2f} but acquisitions {acq:.0%} of assets")
            else:
                dq.append(f"beneish M={strict:.2f} > {R.BENEISH_THRESHOLD}")
        elif strict is None and imputed is not None and imputed > R.BENEISH_THRESHOLD:
            warnings.append(f"beneish imputed M={imputed:.2f} > {R.BENEISH_THRESHOLD} (warning only)")
    if "piotroski_f" in R.MODEL_RULES[model]["disqualifiers"]:
        tests, ratio = g("f_tests"), g("f_ratio")
        if tests is not None and tests >= R.PIOTROSKI_MIN_TESTS:
            if ratio is not None and ratio <= R.PIOTROSKI_DQ_RATIO:
                dq.append(f"piotroski {int(g('f_points'))}/{int(tests)} <= 1/3")
        else:
            warnings.append(f"f-score low confidence ({int(tests or 0)} tests)")
            low.append("f-score low confidence")

    if isinstance(row.get("ebit_adjustment"), str):
        warnings.append(row["ebit_adjustment"])

    # ---- confidence ----
    if _truthy(row.get("captive_finance")):
        low.append("captive finance")
    if row.get("debt_confidence") == "low":
        low.append(f"debt basis {row.get('debt_basis')}")
    if model == "standard" and row.get("ebit_basis") == "pretax_income":
        low.append("EBIT from pretax income only")
    if model == "standard" and row.get("ebit_period_basis") == "ttm_restated":
        low.append("TTM EBIT rebuilt from restated quarters (prior-year remainder scaled)")

    # ---- margin of safety and guard ----
    if fair_value is not None and price:
        rec["fair_value"] = fair_value
        rec["mos"] = (fair_value - price) / fair_value if fair_value > 0 else None
    ey = g("earnings_yield")
    guard_ok = model != "standard" or (ey is not None and ey > treasury)
    rec["treasury_guard_pass"] = guard_ok if model == "standard" else None

    # ---- status ----
    def screen_tier() -> tuple[str, str]:
        """flag / watch / no_flag from MoS, guard, and confidence (ignoring disqualifiers)."""
        mos = rec["mos"]
        if mos is None:
            return R.STATUS_NO_FLAG, "fair value <= 0"
        if mos < R.MOS_FLAG_THRESHOLD:
            return R.STATUS_NO_FLAG, f"MoS {mos:.0%}"
        if not guard_ok:
            return R.STATUS_NO_FLAG, f"MoS {mos:.0%} but earnings yield below 10-yr Treasury"
        if mos > R.MOS_EXTREME:
            return R.STATUS_WATCH, f"extreme_mos_review (MoS {mos:.0%})"
        if low:
            return R.STATUS_WATCH, f"low confidence (MoS {mos:.0%})"
        return R.STATUS_FLAG, f"MoS {mos:.0%}"

    tier, tier_reason = screen_tier() if not missing and not not_applicable else (None, None)
    if isinstance(row.get("excluded_reason"), str):
        status, reason = R.STATUS_EXCLUDED, row["excluded_reason"]
    elif _truthy(row.get("corporate_action_pending")):
        status, reason = (R.STATUS_CORPORATE_ACTION,
                          f"{row.get('event_source')} filed {event}; no 10-Q/10-K yet covers a post-event period")
    elif missing:
        status, reason = R.STATUS_INSUFFICIENT, "; ".join(missing)
    elif not_applicable:
        status, reason = R.STATUS_NOT_APPLICABLE, not_applicable
    elif dq:
        status, reason = R.STATUS_DISQUALIFIED, "; ".join(dq)
    else:
        status, reason = tier, tier_reason

    rec.update(status=status, status_reason=reason,
               confidence=R.CONFIDENCE_LOW if low else R.CONFIDENCE_NORMAL,
               confidence_reasons="; ".join(low), warnings="; ".join(warnings),
               disqualify_reasons="; ".join(dq), missing_inputs="; ".join(missing))
    if dq:  # diagnostics: what would have happened without the disqualification
        rec["status_without_dq"] = tier
        rec["beneish_top_index"] = _beneish_top_index(row.get("m_detail"))
    return rec


def _conservative_min(ttm, avg3, label: str) -> tuple[float | None, str | None]:
    if ttm is None and avg3 is None:
        return None, None
    if ttm is None:
        return avg3, f"{label}_3y_avg (no TTM)"
    if avg3 is None:
        return ttm, f"{label}_ttm (no 3-yr average)"
    return (ttm, f"min = {label}_ttm") if ttm <= avg3 else (avg3, f"min = {label}_3y_avg")


def _beneish_top_index(detail) -> str | None:
    """The index adding most to the M-Score relative to neutral: weight x (index - 1)."""
    from screener.metrics import BENEISH_WEIGHTS
    if not isinstance(detail, str):
        return None
    contrib = {}
    for part in detail.split():
        name, _, val = part.partition("=")
        if val not in ("", "-") and name in BENEISH_WEIGHTS:
            neutral = 0.0 if name == "tata" else 1.0
            contrib[name] = BENEISH_WEIGHTS[name] * (float(val) - neutral)
    if not contrib:
        return None
    top = max(contrib, key=contrib.get)
    return f"{top.upper()} (+{contrib[top]:.2f})"
