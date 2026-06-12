"""Calculate financial ratios from standardized statements.

All ratios are calculated from raw statement data — no third-party
pre-computed ratios. This ensures DSO, DPO, CCC etc. use the correct
inputs for each company.
"""

from __future__ import annotations


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    """Safe division returning None if inputs are missing or denominator is zero."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _avg(a: float | None, b: float | None) -> float | None:
    """Average of two values, handling None."""
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    return (a + b) / 2


def calculate_ratios(
    income_statements: list[dict],
    balance_sheets: list[dict],
    cash_flows: list[dict],
    is_bank: bool = False,
) -> list[dict]:
    """Calculate ratios for each annual period.

    Pairs each income statement period with its balance sheet and prior
    balance sheet for average calculations.

    Returns list of ratio dicts, one per period, sorted by date desc.
    """
    # Build lookup maps by date
    bs_by_date = {row["date"]: row for row in balance_sheets}
    cf_by_date = {row["date"]: row for row in cash_flows}

    # Sort balance sheet dates for finding prior period
    bs_dates = sorted(bs_by_date.keys())

    ratios_list = []

    for is_row in income_statements:
        date = is_row["date"]
        bs = bs_by_date.get(date, {})
        cf = cf_by_date.get(date, {})

        # Find prior period balance sheet
        try:
            idx = bs_dates.index(date)
            prior_date = bs_dates[idx - 1] if idx > 0 else None
        except ValueError:
            prior_date = None
        prior_bs = bs_by_date.get(prior_date, {}) if prior_date else {}

        rev = is_row.get("revenue")
        cogs = is_row.get("costOfRevenue")
        gross = is_row.get("grossProfit")
        op_inc = is_row.get("operatingIncome")
        net_inc = is_row.get("netIncome")
        ebitda = is_row.get("ebitda")
        if ebitda is None and op_inc and is_row.get("depreciationAndAmortization"):
            ebitda = op_inc + is_row["depreciationAndAmortization"]

        # For banks, use totalBankRevenue (NII + Non-Interest Income)
        bank_rev = is_row.get("totalBankRevenue")
        effective_rev = bank_rev if is_bank and bank_rev else rev

        ar = bs.get("accountsReceivables")
        ap = bs.get("accountsPayables")
        inv = bs.get("inventory")
        total_assets = bs.get("totalAssets")
        total_equity = bs.get("totalStockholdersEquity")
        total_debt_val = bs.get("totalDebt")
        current_assets = bs.get("totalCurrentAssets")
        current_liabilities = bs.get("totalCurrentLiabilities")

        prior_total_assets = prior_bs.get("totalAssets")
        prior_total_equity = prior_bs.get("totalStockholdersEquity")

        ocf = cf.get("operatingCashFlow")
        capex = cf.get("capitalExpenditure")
        fcf = cf.get("freeCashFlow")

        # ── Working Capital / Efficiency ──────────────────────────────────
        # DSO/DPO/CCC are meaningless for banks and insurance — skip
        dso = None
        dpo = None
        dio = None
        ccc = None
        if not is_bank:
            if ar is not None and effective_rev:
                dso = (ar / effective_rev) * 365

            if ap is not None and cogs and cogs != 0:
                dpo = (ap / abs(cogs)) * 365

            if inv is not None and cogs and cogs != 0:
                dio = (inv / abs(cogs)) * 365

            if dso is not None and dio is not None and dpo is not None:
                ccc = dso + dio - dpo

        # ── Profitability Margins ─────────────────────────────────────────
        # Use effective_rev for margin calculations (bank rev for banks)
        margin_rev = effective_rev or rev
        gross_margin = _safe_div(gross, margin_rev)
        operating_margin = _safe_div(op_inc, margin_rev)
        net_margin = _safe_div(net_inc, margin_rev)
        ebitda_margin = _safe_div(ebitda, margin_rev)

        # ── Return Metrics ────────────────────────────────────────────────
        avg_assets = _avg(total_assets, prior_total_assets)
        avg_equity = _avg(total_equity, prior_total_equity)

        roa = _safe_div(net_inc, avg_assets)
        roe = _safe_div(net_inc, avg_equity)

        # ROIC = NOPAT / Invested Capital
        tax_rate = _safe_div(is_row.get("incomeTaxExpense"), is_row.get("incomeBeforeTax"))
        nopat = None
        if op_inc is not None and tax_rate is not None:
            nopat = op_inc * (1 - tax_rate)

        invested_capital = None
        if total_debt_val is not None and total_equity is not None:
            cash = bs.get("cashAndCashEquivalents", 0) or 0
            invested_capital = total_debt_val + total_equity - cash

        prior_invested = None
        if prior_bs:
            prior_debt = prior_bs.get("totalDebt", 0) or 0
            prior_eq = prior_bs.get("totalStockholdersEquity", 0) or 0
            prior_cash = prior_bs.get("cashAndCashEquivalents", 0) or 0
            prior_invested = prior_debt + prior_eq - prior_cash

        avg_invested = _avg(invested_capital, prior_invested)
        roic = _safe_div(nopat, avg_invested)

        # ROCE = EBIT / Capital Employed
        capital_employed = None
        if total_assets is not None and current_liabilities is not None:
            capital_employed = total_assets - current_liabilities
        roce = _safe_div(op_inc, capital_employed)

        # ── Cash Flow Metrics ─────────────────────────────────────────────
        fcf_margin = _safe_div(fcf, margin_rev)
        ocf_margin = _safe_div(ocf, margin_rev)
        capex_to_rev = _safe_div(abs(capex) if capex else None, margin_rev)
        fcf_conversion = _safe_div(fcf, net_inc if net_inc and net_inc > 0 else None)
        sbc_to_rev = _safe_div(is_row.get("stockBasedCompensation"), margin_rev)

        # ── Leverage ──────────────────────────────────────────────────────
        debt_to_equity = _safe_div(total_debt_val, total_equity)
        net_debt = None
        if total_debt_val is not None:
            cash = bs.get("cashAndCashEquivalents", 0) or 0
            net_debt = total_debt_val - cash

        net_debt_to_ebitda = _safe_div(net_debt, ebitda)
        interest_coverage = _safe_div(op_inc, is_row.get("interestExpense"))
        current_ratio = _safe_div(current_assets, current_liabilities)

        # ── Income Quality ────────────────────────────────────────────────
        income_quality = _safe_div(ocf, net_inc)

        # ── Asset Metrics ─────────────────────────────────────────────────
        goodwill_pct = _safe_div(bs.get("goodwill"), total_assets)

        # ── Efficiency ratios (FMP-compatible) ───────────────────────────
        rd = is_row.get("researchAndDevelopmentExpenses")
        sga = is_row.get("sellingGeneralAndAdministrativeExpenses")
        rd_to_rev = _safe_div(rd, margin_rev)
        sga_to_rev = _safe_div(sga, margin_rev)

        # Effective tax rate
        eff_tax = _safe_div(is_row.get("incomeTaxExpense"), is_row.get("incomeBeforeTax"))

        # Dividend metrics (from cash flow)
        div_paid = cf.get("dividendsPaid")
        dividend_payout = _safe_div(abs(div_paid) if div_paid else None,
                                     net_inc if net_inc and net_inc > 0 else None)

        # Receivables growth (YoY)
        prior_ar = prior_bs.get("accountsReceivables")
        recv_growth = None
        if ar is not None and prior_ar is not None and prior_ar != 0:
            recv_growth = (ar - prior_ar) / abs(prior_ar)

        ratios_list.append({
            "date": date,
            "calendarYear": is_row.get("calendarYear", ""),
            "period": is_row.get("period", "FY"),
            # Working Capital
            "daysOfSalesOutstanding": round(dso, 1) if dso is not None else None,
            "daysOfPayablesOutstanding": round(dpo, 1) if dpo is not None else None,
            "daysOfInventoryOutstanding": round(dio, 1) if dio is not None else None,
            "cashConversionCycle": round(ccc, 1) if ccc is not None else None,
            # Margins
            "grossProfitMargin": gross_margin,
            "operatingProfitMargin": operating_margin,
            "netProfitMargin": net_margin,
            "ebitdaMargin": ebitda_margin,
            # Returns
            "returnOnAssets": roa,
            "returnOnEquity": roe,
            "returnOnInvestedCapital": roic,
            "returnOnCapitalEmployed": roce,
            # Cash Flow
            "freeCashFlowMargin": fcf_margin,
            "operatingCashFlowMargin": ocf_margin,
            "capitalExpenditureToRevenue": capex_to_rev,
            "freeCashFlowConversion": fcf_conversion,
            "stockBasedCompensationToRevenue": sbc_to_rev,
            "incomeQuality": income_quality,
            # Leverage
            "debtToEquity": debt_to_equity,
            "netDebtToEBITDA": net_debt_to_ebitda,
            "interestCoverage": interest_coverage,
            "currentRatio": current_ratio,
            # Assets
            "goodwillToAssets": goodwill_pct,
            # ── FMP-compatible aliases ───────────────────────────────
            "debtToEquityRatio": debt_to_equity,
            "interestCoverageRatio": interest_coverage,
            "capexToRevenue": capex_to_rev,
            # Note: FMP has a typo "Developement" — we match it for compat
            "researchAndDevelopementToRevenue": rd_to_rev,
            "salesGeneralAndAdministrativeToRevenue": sga_to_rev,
            "effectiveTaxRate": eff_tax,
            "dividendPayoutRatio": dividend_payout,
            "receivablesGrowth": recv_growth,
        })

    return ratios_list


def calculate_growth(
    income_statements: list[dict],
    cash_flows: list[dict],
) -> list[dict]:
    """Calculate YoY growth rates for key metrics.

    Returns a list of growth dicts for each period (except the earliest).
    """
    growth_list = []
    cf_by_date = {row["date"]: row for row in cash_flows}

    # Income statements are sorted desc — iterate pairs
    for i in range(len(income_statements) - 1):
        current = income_statements[i]
        prior = income_statements[i + 1]
        cf_current = cf_by_date.get(current["date"], {})
        cf_prior = cf_by_date.get(prior["date"], {})

        def yoy(curr_val, prev_val):
            if curr_val is None or prev_val is None or prev_val == 0:
                return None
            return (curr_val - prev_val) / abs(prev_val)

        # Use bank revenue if available, else standard revenue
        curr_rev = current.get("totalBankRevenue") or current.get("revenue")
        prior_rev = prior.get("totalBankRevenue") or prior.get("revenue")

        eps_growth = yoy(current.get("epsDiluted"), prior.get("epsDiluted"))
        rd_growth = yoy(
            current.get("researchAndDevelopmentExpenses"),
            prior.get("researchAndDevelopmentExpenses"),
        )

        growth_list.append({
            "date": current["date"],
            "calendarYear": current.get("calendarYear", ""),
            "period": current.get("period", "FY"),
            "revenueGrowth": yoy(curr_rev, prior_rev),
            "grossProfitGrowth": yoy(current.get("grossProfit"), prior.get("grossProfit")),
            "operatingIncomeGrowth": yoy(current.get("operatingIncome"), prior.get("operatingIncome")),
            "netIncomeGrowth": yoy(current.get("netIncome"), prior.get("netIncome")),
            "epsDilutedGrowth": eps_growth,
            "operatingCashFlowGrowth": yoy(
                cf_current.get("operatingCashFlow"),
                cf_prior.get("operatingCashFlow"),
            ),
            "freeCashFlowGrowth": yoy(
                cf_current.get("freeCashFlow"),
                cf_prior.get("freeCashFlow"),
            ),
            # FMP-compatible aliases
            "epsdilutedGrowth": eps_growth,  # FMP uses lowercase 'd'
            "rdexpenseGrowth": rd_growth,
        })

    return growth_list


def calculate_key_metrics(
    income_statements: list[dict],
    balance_sheets: list[dict],
    cash_flows: list[dict],
) -> list[dict]:
    """Calculate key metrics that don't require market data.

    Valuation multiples (P/E, EV/EBITDA) are excluded — those need market data from FMP.
    """
    cf_by_date = {row["date"]: row for row in cash_flows}
    bs_by_date = {row["date"]: row for row in balance_sheets}

    metrics_list = []
    for is_row in income_statements:
        date = is_row["date"]
        cf = cf_by_date.get(date, {})
        bs = bs_by_date.get(date, {})

        rev = is_row.get("revenue")
        ocf = cf.get("operatingCashFlow")
        fcf = cf.get("freeCashFlow")
        capex = cf.get("capitalExpenditure")
        total_debt = bs.get("totalDebt")
        cash = bs.get("cashAndCashEquivalents", 0) or 0
        total_equity = bs.get("totalStockholdersEquity")
        shares = is_row.get("weightedAverageSharesDiluted")

        net_debt = (total_debt - cash) if total_debt is not None else None

        # Revenue per share
        rev_per_share = _safe_div(rev, shares)

        # FCF per share
        fcf_per_share = _safe_div(fcf, shares)

        # Book value per share
        bv_per_share = _safe_div(total_equity, shares)

        # Tangible book value per share
        goodwill = bs.get("goodwill", 0) or 0
        intangibles = bs.get("intangibleAssets", 0) or 0
        tangible_equity = (total_equity - goodwill - intangibles) if total_equity else None
        tbv_per_share = _safe_div(tangible_equity, shares)

        metrics_list.append({
            "date": date,
            "calendarYear": is_row.get("calendarYear", ""),
            "period": is_row.get("period", "FY"),
            "revenuePerShare": rev_per_share,
            "freeCashFlowPerShare": fcf_per_share,
            "bookValuePerShare": bv_per_share,
            "tangibleBookValuePerShare": tbv_per_share,
            "netDebt": net_debt,
            "capexToOperatingCashFlow": _safe_div(
                abs(capex) if capex else None, ocf
            ),
        })

    return metrics_list


def calculate_owner_earnings(
    income_statements: list[dict],
    cash_flows: list[dict],
) -> list[dict]:
    """Calculate owner earnings (Buffett-style).

    Owner Earnings = Net Income + D&A + Other Non-Cash - Maintenance CapEx
    We approximate maintenance capex as D&A (conservative) and growth capex
    as total capex minus maintenance capex.
    """
    cf_by_date = {row["date"]: row for row in cash_flows}
    results = []

    for is_row in income_statements:
        date = is_row["date"]
        cf = cf_by_date.get(date, {})

        net_inc = is_row.get("netIncome")
        da = is_row.get("depreciationAndAmortization") or cf.get("depreciationAndAmortization")
        sbc = is_row.get("stockBasedCompensation") or cf.get("stockBasedCompensation")
        capex = cf.get("capitalExpenditure")

        # Maintenance capex approximation: min of D&A and total capex
        maintenance_capex = None
        growth_capex = None
        if da is not None and capex is not None:
            abs_capex = abs(capex)
            maintenance_capex = min(da, abs_capex)
            growth_capex = abs_capex - maintenance_capex

        owner_earnings = None
        if net_inc is not None and da is not None and maintenance_capex is not None:
            owner_earnings = net_inc + da - maintenance_capex

        shares = is_row.get("weightedAverageSharesDiluted")
        oe_per_share = _safe_div(owner_earnings, shares)

        cal_year = is_row.get("calendarYear", "")
        results.append({
            "date": date,
            "calendarYear": cal_year,
            "fiscalYear": cal_year,  # FMP compat alias
            "period": is_row.get("period", "FY"),
            "netIncome": net_inc,
            "depreciationAndAmortization": da,
            "stockBasedCompensation": sbc,
            "maintenanceCapex": maintenance_capex,
            "growthCapex": growth_capex,
            "ownerEarnings": owner_earnings,
            # FMP-compatible aliases
            "ownersEarnings": owner_earnings,
            "ownersEarningsPerShare": oe_per_share,
            "averagePPE": None,  # not computed from SEC data
        })

    return results
