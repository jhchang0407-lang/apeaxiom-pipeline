"""Tech/SaaS-specific KPIs from SEC XBRL data.

Covers: software, hardware, semiconductors, cloud/SaaS, communications equipment.
Key metrics: Rule of 40, NRR proxy, RPO, billings proxy, R&D intensity,
gross margin, deferred revenue growth, SBC dilution.
"""

from __future__ import annotations

from sec.sectors._utils import extract_annual_values, safe_div


def compute_tech_kpis(gaap: dict, years: int = 5) -> dict:
    # ── Revenue & Cost ──────────────────────────────────────────────────
    revenue = extract_annual_values(gaap, [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ], years + 1)  # +1 for YoY calc

    cogs = extract_annual_values(gaap, [
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "CostOfGoodsSold",
    ], years + 1)

    gross_profit = extract_annual_values(gaap, [
        "GrossProfit",
    ], years + 1)

    # ── Operating Expenses ──────────────────────────────────────────────
    rd_expense = extract_annual_values(gaap, [
        "ResearchAndDevelopmentExpense",
    ], years)

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

    sbc = extract_annual_values(gaap, [
        "ShareBasedCompensation",
        "AllocatedShareBasedCompensationExpense",
    ], years)

    # ── SaaS / Subscription Metrics ─────────────────────────────────────
    rpo = extract_annual_values(gaap, [
        "RevenueRemainingPerformanceObligation",
    ], years + 1)

    deferred_revenue = extract_annual_values(gaap, [
        "ContractWithCustomerLiability",
        "DeferredRevenueCurrent",
        "ContractWithCustomerLiabilityCurrent",
        "DeferredRevenue",
    ], years + 1)

    deferred_revenue_noncurrent = extract_annual_values(gaap, [
        "ContractWithCustomerLiabilityNoncurrent",
        "DeferredRevenueNoncurrent",
    ], years + 1)

    # Contract acquisition costs (capitalized commissions — CAC proxy)
    contract_costs = extract_annual_values(gaap, [
        "CapitalizedContractCostNet",
        "CapitalizedContractCostNetCurrent",
    ], years)

    # ── Shares ──────────────────────────────────────────────────────────
    shares = extract_annual_values(gaap, [
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ], years + 1)

    shares_basic = extract_annual_values(gaap, [
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
    ], years + 1)

    # ── Build raw KPIs ──────────────────────────────────────────────────
    kpis = {
        "rpo": rpo[:years],
        "deferredRevenue": deferred_revenue[:years],
        "deferredRevenueNoncurrent": deferred_revenue_noncurrent[:years],
        "rdExpense": rd_expense,
        "stockBasedCompensation": sbc,
        "contractAcquisitionCosts": contract_costs,
    }

    # ── Computed Metrics ────────────────────────────────────────────────
    computed = []
    rev_by_date = {e["date"]: e["val"] for e in revenue}
    cogs_by_date = {e["date"]: e["val"] for e in cogs}
    gp_by_date = {e["date"]: e["val"] for e in gross_profit}
    ocf_by_date = {e["date"]: e["val"] for e in ocf}
    capex_by_date = {e["date"]: e["val"] for e in capex}
    dr_by_date = {e["date"]: e["val"] for e in deferred_revenue}
    dr_nc_by_date = {e["date"]: e["val"] for e in deferred_revenue_noncurrent}
    rd_by_date = {e["date"]: e["val"] for e in rd_expense}
    sga_by_date = {e["date"]: e["val"] for e in sga}
    sbc_by_date = {e["date"]: e["val"] for e in sbc}
    opinc_by_date = {e["date"]: e["val"] for e in operating_income}
    ni_by_date = {e["date"]: e["val"] for e in net_income}
    rpo_by_date = {e["date"]: e["val"] for e in rpo}
    shares_by_date = {e["date"]: e["val"] for e in shares}
    shares_basic_by_date = {e["date"]: e["val"] for e in shares_basic}

    rev_dates = sorted(rev_by_date.keys(), reverse=True)
    for i, date in enumerate(rev_dates[:years]):
        rev = rev_by_date.get(date)
        cg = cogs_by_date.get(date)
        gp = gp_by_date.get(date)
        oc = ocf_by_date.get(date)
        cx = capex_by_date.get(date)
        rd = rd_by_date.get(date)
        sg = sga_by_date.get(date)
        sb = sbc_by_date.get(date)
        op = opinc_by_date.get(date)
        ni = ni_by_date.get(date)

        # Prior period values
        prior_date = rev_dates[i + 1] if i + 1 < len(rev_dates) else None
        prior_rev = rev_by_date.get(prior_date) if prior_date else None
        prior_dr = dr_by_date.get(prior_date) if prior_date else None
        prior_rpo = rpo_by_date.get(prior_date) if prior_date else None

        # ── Margins ─────────────────────────────────────────────────────
        if gp and rev:
            gross_margin = gp / rev
        elif rev and cg:
            gross_margin = (rev - cg) / rev
        else:
            gross_margin = None

        operating_margin = safe_div(op, rev)
        net_margin = safe_div(ni, rev)

        # ── FCF ─────────────────────────────────────────────────────────
        fcf = (oc - abs(cx)) if oc and cx else (oc if oc else None)
        fcf_margin = safe_div(fcf, rev)

        # FCF adjusted for SBC (real cash generation)
        fcf_adj = (fcf - sb) if fcf is not None and sb else fcf
        fcf_adj_margin = safe_div(fcf_adj, rev)

        # ── Growth Rates ────────────────────────────────────────────────
        rev_growth = safe_div((rev - prior_rev), abs(prior_rev)) if rev and prior_rev else None

        # ── Rule of 40 ─────────────────────────────────────────────────
        rule_of_40 = None
        if rev_growth is not None and fcf_margin is not None:
            rule_of_40 = round((rev_growth + fcf_margin) * 100, 1)

        # ── R&D Intensity ──────────────────────────────────────────────
        rd_intensity = safe_div(rd, rev)

        # ── SBC as % of Revenue ─────────────────────────────────────────
        sbc_pct = safe_div(sb, rev)

        # ── S&M as % of Revenue ─────────────────────────────────────────
        sga_pct = safe_div(sg, rev)

        # ── Deferred Revenue Growth (NRR proxy) ────────────────────────
        dr_current = dr_by_date.get(date)
        dr_growth = safe_div((dr_current - prior_dr), abs(prior_dr)) if dr_current and prior_dr else None

        # ── Total Deferred Revenue (current + non-current) ─────────────
        dr_nc = dr_nc_by_date.get(date)
        total_deferred = (dr_current or 0) + (dr_nc or 0) if (dr_current or dr_nc) else None

        # ── Billings Proxy ──────────────────────────────────────────────
        # Billings ≈ Revenue + Δ Deferred Revenue
        billings = None
        if rev and dr_current is not None and prior_dr is not None:
            billings = rev + (dr_current - prior_dr)

        # ── RPO Growth ──────────────────────────────────────────────────
        rpo_val = rpo_by_date.get(date)
        rpo_growth = safe_div((rpo_val - prior_rpo), abs(prior_rpo)) if rpo_val and prior_rpo else None

        # ── NRR Proxy ───────────────────────────────────────────────────
        # Best proxy from public filings: revenue growth rate implies NRR
        nrr_proxy = None
        if rev_growth is not None:
            nrr_proxy = round((1 + rev_growth) * 100, 1)

        # ── SBC Dilution ────────────────────────────────────────────────
        diluted = shares_by_date.get(date)
        basic = shares_basic_by_date.get(date)
        sbc_dilution = safe_div((diluted - basic), basic) if diluted and basic else None

        # ── Magic Number (SaaS efficiency) ──────────────────────────────
        prior_sga = sga_by_date.get(prior_date) if prior_date else None
        magic_number = safe_div((rev - prior_rev) if rev and prior_rev else None, prior_sga)

        entry = {
            "date": date,
            # Margins
            "grossMargin": round(gross_margin, 4) if gross_margin is not None else None,
            "operatingMargin": round(operating_margin, 4) if operating_margin is not None else None,
            "netMargin": round(net_margin, 4) if net_margin is not None else None,
            "fcfMargin": round(fcf_margin, 4) if fcf_margin is not None else None,
            "fcfAdjForSbcMargin": round(fcf_adj_margin, 4) if fcf_adj_margin is not None else None,
            # Growth
            "revenueGrowth": round(rev_growth, 4) if rev_growth is not None else None,
            "deferredRevenueGrowth": round(dr_growth, 4) if dr_growth is not None else None,
            "rpoGrowth": round(rpo_growth, 4) if rpo_growth is not None else None,
            # SaaS metrics
            "ruleOf40": rule_of_40,
            "nrrProxy": nrr_proxy,
            "billingsProxy": round(billings) if billings is not None else None,
            "totalDeferredRevenue": round(total_deferred) if total_deferred is not None else None,
            "magicNumber": round(magic_number, 2) if magic_number is not None else None,
            # Efficiency
            "rdIntensity": round(rd_intensity, 4) if rd_intensity is not None else None,
            "sbcAsPercentOfRevenue": round(sbc_pct, 4) if sbc_pct is not None else None,
            "sgaAsPercentOfRevenue": round(sga_pct, 4) if sga_pct is not None else None,
            "sbcDilution": round(sbc_dilution, 4) if sbc_dilution is not None else None,
        }
        computed.append(entry)

    kpis["computedMetrics"] = computed
    return kpis
