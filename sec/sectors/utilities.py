"""Utilities-specific KPIs from SEC XBRL data.

Covers: regulated electric, gas, water utilities; renewable/independent power producers.
Key metrics: rate base proxy (PP&E), capex intensity, FFO, dividend coverage,
interest coverage, debt/EBITDA, regulatory asset base.
"""

from __future__ import annotations

from sec.sectors._utils import extract_annual_values, safe_div


def compute_utility_kpis(gaap: dict, years: int = 5) -> dict:
    # ── Revenue ─────────────────────────────────────────────────────────
    revenue = extract_annual_values(gaap, [
        "RegulatedAndUnregulatedOperatingRevenue",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ], years + 1)

    # ── Costs ───────────────────────────────────────────────────────────
    cogs = extract_annual_values(gaap, [
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "UtilitiesCost",
    ], years)

    gross_profit = extract_annual_values(gaap, [
        "GrossProfit",
    ], years)

    # ── Operating ───────────────────────────────────────────────────────
    operating_income = extract_annual_values(gaap, [
        "OperatingIncomeLoss",
    ], years)

    net_income = extract_annual_values(gaap, [
        "NetIncomeLoss",
    ], years)

    interest_expense = extract_annual_values(gaap, [
        "InterestExpense",
        "InterestExpenseDebt",
        "InterestIncomeExpenseNet",
    ], years)

    depreciation = extract_annual_values(gaap, [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
    ], years)

    # ── Cash Flow ───────────────────────────────────────────────────────
    ocf = extract_annual_values(gaap, [
        "NetCashProvidedByUsedInOperatingActivities",
    ], years)

    capex = extract_annual_values(gaap, [
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ], years)

    dividends = extract_annual_values(gaap, [
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfDividends",
    ], years)

    # ── Balance Sheet ───────────────────────────────────────────────────
    total_assets = extract_annual_values(gaap, [
        "Assets",
    ], years)

    pp_and_e = extract_annual_values(gaap, [
        "PropertyPlantAndEquipmentNet",
        "PublicUtilitiesPropertyPlantAndEquipmentNet",
    ], years + 1)

    equity = extract_annual_values(gaap, [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ], years)

    total_debt = extract_annual_values(gaap, [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ], years)

    short_term_debt = extract_annual_values(gaap, [
        "ShortTermBorrowings",
        "CommercialPaper",
    ], years)

    # ── Utility-Specific ────────────────────────────────────────────────
    regulatory_assets = extract_annual_values(gaap, [
        "RegulatoryAssets",
        "RegulatoryAssetsNoncurrent",
    ], years)

    regulatory_liabilities = extract_annual_values(gaap, [
        "RegulatoryLiabilities",
        "RegulatoryLiabilitiesNoncurrent",
    ], years)

    construction_wip = extract_annual_values(gaap, [
        "ConstructionInProgressGross",
    ], years)

    eps = extract_annual_values(gaap, [
        "EarningsPerShareDiluted",
    ], years)

    dividends_per_share = extract_annual_values(gaap, [
        "CommonStockDividendsPerShareDeclared",
        "CommonStockDividendsPerShareCashPaid",
    ], years)

    # ── Build raw KPIs ──────────────────────────────────────────────────
    kpis = {
        "regulatoryAssets": regulatory_assets,
        "regulatoryLiabilities": regulatory_liabilities,
        "constructionWIP": construction_wip,
        "ppAndE": pp_and_e[:years],
        "dividendsPerShare": dividends_per_share,
    }

    # ── Computed Metrics ────────────────────────────────────────────────
    computed = []
    rev_by_date = {e["date"]: e["val"] for e in revenue}
    cogs_by_date = {e["date"]: e["val"] for e in cogs}
    gp_by_date = {e["date"]: e["val"] for e in gross_profit}
    opinc_by_date = {e["date"]: e["val"] for e in operating_income}
    ni_by_date = {e["date"]: e["val"] for e in net_income}
    int_exp_by_date = {e["date"]: e["val"] for e in interest_expense}
    da_by_date = {e["date"]: e["val"] for e in depreciation}
    ocf_by_date = {e["date"]: e["val"] for e in ocf}
    capex_by_date = {e["date"]: e["val"] for e in capex}
    div_by_date = {e["date"]: e["val"] for e in dividends}
    assets_by_date = {e["date"]: e["val"] for e in total_assets}
    ppe_by_date = {e["date"]: e["val"] for e in pp_and_e}
    eq_by_date = {e["date"]: e["val"] for e in equity}
    debt_by_date = {e["date"]: e["val"] for e in total_debt}
    st_debt_by_date = {e["date"]: e["val"] for e in short_term_debt}
    reg_assets_by_date = {e["date"]: e["val"] for e in regulatory_assets}
    eps_by_date = {e["date"]: e["val"] for e in eps}
    dps_by_date = {e["date"]: e["val"] for e in dividends_per_share}

    rev_dates = sorted(rev_by_date.keys(), reverse=True)
    for i, date in enumerate(rev_dates[:years]):
        rev = rev_by_date.get(date)
        cg = cogs_by_date.get(date)
        gp = gp_by_date.get(date)
        op = opinc_by_date.get(date)
        ni = ni_by_date.get(date)
        int_exp = int_exp_by_date.get(date)
        da = da_by_date.get(date)
        oc = ocf_by_date.get(date)
        cx = capex_by_date.get(date)
        div = div_by_date.get(date)
        assets = assets_by_date.get(date)
        ppe = ppe_by_date.get(date)
        eq = eq_by_date.get(date)
        debt = debt_by_date.get(date)
        st_debt = st_debt_by_date.get(date)
        reg_a = reg_assets_by_date.get(date)
        ep = eps_by_date.get(date)
        dps = dps_by_date.get(date)

        prior_date = rev_dates[i + 1] if i + 1 < len(rev_dates) else None
        prior_rev = rev_by_date.get(prior_date) if prior_date else None
        prior_ppe = ppe_by_date.get(prior_date) if prior_date else None
        prior_dps = dps_by_date.get(prior_date) if prior_date else None

        # ── Margins ─────────────────────────────────────────────────────
        gross_margin = None
        if gp and rev:
            gross_margin = gp / rev
        elif rev and cg:
            gross_margin = (rev - cg) / rev

        op_margin = safe_div(op, rev)
        net_margin = safe_div(ni, rev)

        # EBITDA
        ebitda = (op + da) if op and da else None
        ebitda_margin = safe_div(ebitda, rev)

        # ── FFO (Funds From Operations) ────────────────────────────────
        # FFO = Net Income + D&A (approximation for utilities)
        ffo = (ni + da) if ni and da else None
        ffo_margin = safe_div(ffo, rev)

        # ── FCF ─────────────────────────────────────────────────────────
        fcf = (oc - abs(cx)) if oc and cx else (oc if oc else None)
        fcf_margin = safe_div(fcf, rev)

        # ── Growth ──────────────────────────────────────────────────────
        rev_growth = safe_div((rev - prior_rev), abs(prior_rev)) if rev and prior_rev else None

        # Rate base growth (PP&E as proxy)
        rate_base_growth = safe_div((ppe - prior_ppe), abs(prior_ppe)) if ppe and prior_ppe else None

        # Dividend growth
        div_growth = safe_div((dps - prior_dps), abs(prior_dps)) if dps and prior_dps else None

        # ── Capital Intensity ──────────────────────────────────────────
        capex_intensity = safe_div(abs(cx) if cx else None, rev)
        capex_to_da = safe_div(abs(cx) if cx else None, da)  # >1 = growth capex

        # ── Coverage Ratios ────────────────────────────────────────────
        # Dividend coverage = FFO / dividends paid
        div_coverage = safe_div(ffo, abs(div)) if ffo and div else None

        # FFO payout ratio
        ffo_payout = safe_div(abs(div), ffo) if ffo and div else None

        # EPS payout ratio
        eps_payout = safe_div(dps, ep) if dps and ep else None

        # Interest coverage = EBITDA / interest expense
        interest_coverage = safe_div(ebitda, abs(int_exp)) if ebitda and int_exp else None

        # ── Leverage ────────────────────────────────────────────────────
        total_debt_val = (debt or 0) + (st_debt or 0)
        debt_to_ebitda = safe_div(total_debt_val, ebitda) if total_debt_val else None
        debt_to_equity = safe_div(total_debt_val, eq) if total_debt_val else None
        debt_to_capital = safe_div(total_debt_val, total_debt_val + eq) if total_debt_val and eq else None

        # ── Returns ─────────────────────────────────────────────────────
        roe = safe_div(ni, eq)
        roa = safe_div(ni, assets)

        invested_capital = (eq or 0) + (debt or 0)
        tax_rate = 0.21
        nopat = op * (1 - tax_rate) if op else None
        roic = safe_div(nopat, invested_capital) if invested_capital else None

        # ── Asset Efficiency ────────────────────────────────────────────
        asset_turnover = safe_div(rev, assets)
        ppe_turnover = safe_div(rev, ppe)

        # ── OCF Quality ────────────────────────────────────────────────
        ocf_to_ni = safe_div(oc, ni)

        # ── Rate Base Proxy ────────────────────────────────────────────
        rate_base_pct_of_assets = safe_div(ppe, assets)
        regulatory_asset_pct = safe_div(reg_a, assets) if reg_a else None

        computed.append({
            "date": date,
            # Margins
            "grossMargin": round(gross_margin, 4) if gross_margin is not None else None,
            "operatingMargin": round(op_margin, 4) if op_margin is not None else None,
            "ebitdaMargin": round(ebitda_margin, 4) if ebitda_margin is not None else None,
            "netMargin": round(net_margin, 4) if net_margin is not None else None,
            "ffoMargin": round(ffo_margin, 4) if ffo_margin is not None else None,
            "fcfMargin": round(fcf_margin, 4) if fcf_margin is not None else None,
            # Growth
            "revenueGrowth": round(rev_growth, 4) if rev_growth is not None else None,
            "rateBaseGrowth": round(rate_base_growth, 4) if rate_base_growth is not None else None,
            "dividendGrowth": round(div_growth, 4) if div_growth is not None else None,
            # Capital intensity
            "capexIntensity": round(capex_intensity, 4) if capex_intensity is not None else None,
            "capexToDA": round(capex_to_da, 2) if capex_to_da is not None else None,
            # Coverage & payout
            "dividendCoverage": round(div_coverage, 2) if div_coverage is not None else None,
            "ffoPayout": round(ffo_payout, 4) if ffo_payout is not None else None,
            "epsPayout": round(eps_payout, 4) if eps_payout is not None else None,
            "interestCoverage": round(interest_coverage, 2) if interest_coverage is not None else None,
            # Leverage
            "debtToEbitda": round(debt_to_ebitda, 2) if debt_to_ebitda is not None else None,
            "debtToEquity": round(debt_to_equity, 4) if debt_to_equity is not None else None,
            "debtToCapital": round(debt_to_capital, 4) if debt_to_capital is not None else None,
            # Returns
            "roe": round(roe, 4) if roe is not None else None,
            "roa": round(roa, 4) if roa is not None else None,
            "roic": round(roic, 4) if roic is not None else None,
            # Asset efficiency
            "assetTurnover": round(asset_turnover, 4) if asset_turnover is not None else None,
            "ppeTurnover": round(ppe_turnover, 4) if ppe_turnover is not None else None,
            # Quality
            "ocfToNetIncome": round(ocf_to_ni, 4) if ocf_to_ni is not None else None,
            # Rate base
            "rateBasePctOfAssets": round(rate_base_pct_of_assets, 4) if rate_base_pct_of_assets is not None else None,
            "regulatoryAssetPct": round(regulatory_asset_pct, 4) if regulatory_asset_pct is not None else None,
        })

    kpis["computedMetrics"] = computed
    return kpis
