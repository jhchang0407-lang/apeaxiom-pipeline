"""Healthcare/Pharma-specific KPIs from SEC XBRL data.

Covers: pharma, biotech, medical devices, health services.
Key metrics: R&D intensity, pipeline proxy (R&D/revenue), gross margin,
SG&A efficiency, patent-related intangibles, revenue growth.
"""

from __future__ import annotations

from sec.sectors._utils import extract_annual_values, safe_div


def compute_healthcare_kpis(gaap: dict, years: int = 5) -> dict:
    # ── Revenue ─────────────────────────────────────────────────────────
    revenue = extract_annual_values(gaap, [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ], years + 1)

    # Product vs service revenue breakdown
    product_revenue = extract_annual_values(gaap, [
        "RevenueFromContractWithCustomerExcludingAssessedTaxProductAndService",
        "SalesRevenueGoodsNet",
    ], years)

    service_revenue = extract_annual_values(gaap, [
        "SalesRevenueServicesNet",
        "HealthCareOrganizationRevenue",
    ], years)

    # ── Costs ───────────────────────────────────────────────────────────
    cogs = extract_annual_values(gaap, [
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "CostOfGoodsSold",
    ], years)

    gross_profit = extract_annual_values(gaap, [
        "GrossProfit",
    ], years)

    # ── R&D (critical for pharma/biotech) ───────────────────────────────
    rd_expense = extract_annual_values(gaap, [
        "ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    ], years)

    # In-process R&D (M&A-related)
    iprd = extract_annual_values(gaap, [
        "ResearchAndDevelopmentInProcess",
        "InProcessResearchAndDevelopment",
    ], years)

    # ── Operating Expenses ──────────────────────────────────────────────
    sga = extract_annual_values(gaap, [
        "SellingGeneralAndAdministrativeExpense",
    ], years)

    operating_income = extract_annual_values(gaap, [
        "OperatingIncomeLoss",
    ], years)

    net_income = extract_annual_values(gaap, [
        "NetIncomeLoss",
    ], years)

    # ── Cash Flow ───────────────────────────────────────────────────────
    ocf = extract_annual_values(gaap, [
        "NetCashProvidedByUsedInOperatingActivities",
    ], years)

    capex = extract_annual_values(gaap, [
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ], years)

    # ── Intangibles (patents, acquired IP) ──────────────────────────────
    intangibles = extract_annual_values(gaap, [
        "IntangibleAssetsNetExcludingGoodwill",
        "FiniteLivedIntangibleAssetsNet",
    ], years)

    goodwill = extract_annual_values(gaap, [
        "Goodwill",
    ], years)

    amortization_intangibles = extract_annual_values(gaap, [
        "AmortizationOfIntangibleAssets",
    ], years)

    # ── Acquisitions (common in pharma for pipeline) ────────────────────
    acquisitions = extract_annual_values(gaap, [
        "PaymentsToAcquireBusinessesNetOfCashAcquired",
        "PaymentsToAcquireBusinessesGross",
    ], years)

    # ── Balance Sheet ───────────────────────────────────────────────────
    total_assets = extract_annual_values(gaap, [
        "Assets",
    ], years)

    equity = extract_annual_values(gaap, [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ], years)

    total_debt = extract_annual_values(gaap, [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ], years)

    cash = extract_annual_values(gaap, [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsAndShortTermInvestments",
    ], years)

    inventory = extract_annual_values(gaap, [
        "InventoryNet",
    ], years)

    shares = extract_annual_values(gaap, [
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ], years)

    # SBC
    sbc = extract_annual_values(gaap, [
        "ShareBasedCompensation",
        "AllocatedShareBasedCompensationExpense",
    ], years)

    # Deferred revenue (milestone payments, licensing)
    deferred_revenue = extract_annual_values(gaap, [
        "ContractWithCustomerLiability",
        "DeferredRevenueCurrent",
    ], years)

    # ── Build raw KPIs ──────────────────────────────────────────────────
    kpis = {
        "rdExpense": rd_expense,
        "inProcessRD": iprd,
        "intangibleAssets": intangibles,
        "goodwill": goodwill,
        "amortizationOfIntangibles": amortization_intangibles,
        "acquisitions": acquisitions,
        "deferredRevenue": deferred_revenue,
    }

    # ── Computed Metrics ────────────────────────────────────────────────
    computed = []
    rev_by_date = {e["date"]: e["val"] for e in revenue}
    cogs_by_date = {e["date"]: e["val"] for e in cogs}
    gp_by_date = {e["date"]: e["val"] for e in gross_profit}
    rd_by_date = {e["date"]: e["val"] for e in rd_expense}
    sga_by_date = {e["date"]: e["val"] for e in sga}
    opinc_by_date = {e["date"]: e["val"] for e in operating_income}
    ni_by_date = {e["date"]: e["val"] for e in net_income}
    ocf_by_date = {e["date"]: e["val"] for e in ocf}
    capex_by_date = {e["date"]: e["val"] for e in capex}
    intang_by_date = {e["date"]: e["val"] for e in intangibles}
    gw_by_date = {e["date"]: e["val"] for e in goodwill}
    assets_by_date = {e["date"]: e["val"] for e in total_assets}
    eq_by_date = {e["date"]: e["val"] for e in equity}
    debt_by_date = {e["date"]: e["val"] for e in total_debt}
    cash_by_date = {e["date"]: e["val"] for e in cash}
    sbc_by_date = {e["date"]: e["val"] for e in sbc}
    inv_by_date = {e["date"]: e["val"] for e in inventory}

    rev_dates = sorted(rev_by_date.keys(), reverse=True)
    for i, date in enumerate(rev_dates[:years]):
        rev = rev_by_date.get(date)
        cg = cogs_by_date.get(date)
        gp = gp_by_date.get(date)
        rd = rd_by_date.get(date)
        sg = sga_by_date.get(date)
        op = opinc_by_date.get(date)
        ni = ni_by_date.get(date)
        oc = ocf_by_date.get(date)
        cx = capex_by_date.get(date)
        intang = intang_by_date.get(date)
        gw = gw_by_date.get(date)
        assets = assets_by_date.get(date)
        eq = eq_by_date.get(date)
        debt = debt_by_date.get(date)
        cash_val = cash_by_date.get(date)
        sb = sbc_by_date.get(date)
        inv = inv_by_date.get(date)

        prior_date = rev_dates[i + 1] if i + 1 < len(rev_dates) else None
        prior_rev = rev_by_date.get(prior_date) if prior_date else None

        # ── Margins ─────────────────────────────────────────────────────
        gross_margin = None
        if gp and rev:
            gross_margin = gp / rev
        elif rev and cg:
            gross_margin = (rev - cg) / rev

        op_margin = safe_div(op, rev)
        net_margin = safe_div(ni, rev)

        # ── R&D Metrics ─────────────────────────────────────────────────
        rd_intensity = safe_div(rd, rev)
        rd_to_gross_profit = safe_div(rd, gp) if gp else None

        # ── SG&A Efficiency ─────────────────────────────────────────────
        sga_pct = safe_div(sg, rev)

        # ── FCF ─────────────────────────────────────────────────────────
        fcf = (oc - abs(cx)) if oc and cx else (oc if oc else None)
        fcf_margin = safe_div(fcf, rev)

        # ── Growth ──────────────────────────────────────────────────────
        rev_growth = safe_div((rev - prior_rev), abs(prior_rev)) if rev and prior_rev else None

        # ── Balance Sheet Quality ───────────────────────────────────────
        net_debt = (debt or 0) - (cash_val or 0)
        net_debt_to_ebitda = None
        ebitda = None
        da_val = 0
        if op:
            # Approximate D&A from amortization + assume some depreciation
            amort = {e["date"]: e["val"] for e in amortization_intangibles}.get(date, 0)
            da_val = amort  # partial
            ebitda = op + da_val
            net_debt_to_ebitda = safe_div(net_debt, ebitda) if ebitda else None

        # Intangible asset intensity
        intangible_pct = safe_div((intang or 0) + (gw or 0), assets) if assets else None

        # ── Returns ─────────────────────────────────────────────────────
        roa = safe_div(ni, assets)
        roe = safe_div(ni, eq)

        # ROIC
        invested_capital = (eq or 0) + (debt or 0)
        roic = safe_div(op, invested_capital) if invested_capital else None

        # Cash runway (for pre-revenue biotech)
        cash_runway = None
        if cash_val and oc and oc < 0:
            cash_runway = round(cash_val / abs(oc), 1)  # years of cash

        # SBC intensity
        sbc_pct = safe_div(sb, rev)

        computed.append({
            "date": date,
            # Margins
            "grossMargin": round(gross_margin, 4) if gross_margin is not None else None,
            "operatingMargin": round(op_margin, 4) if op_margin is not None else None,
            "netMargin": round(net_margin, 4) if net_margin is not None else None,
            "fcfMargin": round(fcf_margin, 4) if fcf_margin is not None else None,
            # R&D
            "rdIntensity": round(rd_intensity, 4) if rd_intensity else None,
            "rdToGrossProfit": round(rd_to_gross_profit, 4) if rd_to_gross_profit else None,
            # Efficiency
            "sgaAsPercentOfRevenue": round(sga_pct, 4) if sga_pct else None,
            "sbcAsPercentOfRevenue": round(sbc_pct, 4) if sbc_pct else None,
            # Growth
            "revenueGrowth": round(rev_growth, 4) if rev_growth is not None else None,
            # Balance sheet
            "intangibleAssetIntensity": round(intangible_pct, 4) if intangible_pct else None,
            "netDebtToEbitda": round(net_debt_to_ebitda, 2) if net_debt_to_ebitda else None,
            "cashRunwayYears": cash_runway,
            # Returns
            "roa": round(roa, 4) if roa else None,
            "roe": round(roe, 4) if roe else None,
            "roic": round(roic, 4) if roic else None,
        })

    kpis["computedMetrics"] = computed
    return kpis
