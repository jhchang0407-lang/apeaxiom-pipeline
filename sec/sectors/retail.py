"""Retail-specific KPIs from SEC XBRL data.

Covers: department stores, specialty retail, grocery, restaurants, e-commerce.
Key metrics: inventory turnover, gross margin, revenue per store,
store count growth, working capital efficiency, SG&A leverage.
"""

from __future__ import annotations

from sec.sectors._utils import extract_annual_values, safe_div


def compute_retail_kpis(gaap: dict, years: int = 5) -> dict:
    # ── Revenue ─────────────────────────────────────────────────────────
    revenue = extract_annual_values(gaap, [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ], years + 1)

    # ── Cost & Margins ──────────────────────────────────────────────────
    cogs = extract_annual_values(gaap, [
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "CostOfGoodsSold",
    ], years + 1)

    gross_profit = extract_annual_values(gaap, [
        "GrossProfit",
    ], years)

    operating_income = extract_annual_values(gaap, [
        "OperatingIncomeLoss",
    ], years)

    net_income = extract_annual_values(gaap, [
        "NetIncomeLoss",
    ], years)

    sga = extract_annual_values(gaap, [
        "SellingGeneralAndAdministrativeExpense",
    ], years)

    # ── Working Capital ─────────────────────────────────────────────────
    inventory = extract_annual_values(gaap, [
        "InventoryNet",
        "InventoryFinishedGoods",
        "InventoryFinishedGoodsAndWorkInProcess",
    ], years + 1)

    accounts_receivable = extract_annual_values(gaap, [
        "AccountsReceivableNetCurrent",
        "AccountsReceivableNet",
    ], years)

    accounts_payable = extract_annual_values(gaap, [
        "AccountsPayableCurrent",
    ], years)

    # ── Store Count ─────────────────────────────────────────────────────
    store_count = extract_annual_values(gaap, [
        "NumberOfStores",
        "NumberOfRestaurants",
        "NumberOfRealEstateProperties",
        "EntityNumberOfEmployees",  # fallback for headcount
    ], years + 1)

    # ── Cash Flow ───────────────────────────────────────────────────────
    ocf = extract_annual_values(gaap, [
        "NetCashProvidedByUsedInOperatingActivities",
    ], years)

    capex = extract_annual_values(gaap, [
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ], years)

    # ── Lease Obligations ───────────────────────────────────────────────
    operating_lease_liability = extract_annual_values(gaap, [
        "OperatingLeaseLiability",
        "OperatingLeaseLiabilityCurrent",
    ], years)

    operating_lease_rou = extract_annual_values(gaap, [
        "OperatingLeaseRightOfUseAsset",
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

    # Deferred revenue (gift cards, loyalty programs)
    deferred_revenue = extract_annual_values(gaap, [
        "ContractWithCustomerLiability",
        "DeferredRevenueCurrent",
        "DeferredRevenue",
    ], years)

    shares = extract_annual_values(gaap, [
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ], years)

    # ── Build raw KPIs ──────────────────────────────────────────────────
    kpis = {
        "storeCount": store_count[:years],
        "inventory": inventory[:years],
        "deferredRevenue": deferred_revenue,
        "operatingLeaseROU": operating_lease_rou,
    }

    # ── Computed Metrics ────────────────────────────────────────────────
    computed = []
    rev_by_date = {e["date"]: e["val"] for e in revenue}
    cogs_by_date = {e["date"]: e["val"] for e in cogs}
    gp_by_date = {e["date"]: e["val"] for e in gross_profit}
    inv_by_date = {e["date"]: e["val"] for e in inventory}
    stores_by_date = {e["date"]: e["val"] for e in store_count}
    opinc_by_date = {e["date"]: e["val"] for e in operating_income}
    ni_by_date = {e["date"]: e["val"] for e in net_income}
    sga_by_date = {e["date"]: e["val"] for e in sga}
    ar_by_date = {e["date"]: e["val"] for e in accounts_receivable}
    ap_by_date = {e["date"]: e["val"] for e in accounts_payable}
    ocf_by_date = {e["date"]: e["val"] for e in ocf}
    capex_by_date = {e["date"]: e["val"] for e in capex}
    assets_by_date = {e["date"]: e["val"] for e in total_assets}
    eq_by_date = {e["date"]: e["val"] for e in equity}
    debt_by_date = {e["date"]: e["val"] for e in total_debt}
    lease_by_date = {e["date"]: e["val"] for e in operating_lease_liability}
    shares_by_date = {e["date"]: e["val"] for e in shares}

    rev_dates = sorted(rev_by_date.keys(), reverse=True)
    for i, date in enumerate(rev_dates[:years]):
        rev = rev_by_date.get(date)
        cg = cogs_by_date.get(date)
        gp = gp_by_date.get(date)
        inv = inv_by_date.get(date)
        stores = stores_by_date.get(date)
        op = opinc_by_date.get(date)
        ni = ni_by_date.get(date)
        sg = sga_by_date.get(date)
        ar = ar_by_date.get(date)
        ap = ap_by_date.get(date)
        oc = ocf_by_date.get(date)
        cx = capex_by_date.get(date)
        assets = assets_by_date.get(date)
        eq = eq_by_date.get(date)
        debt = debt_by_date.get(date)
        lease = lease_by_date.get(date)
        shr = shares_by_date.get(date)

        prior_date = rev_dates[i + 1] if i + 1 < len(rev_dates) else None
        prior_rev = rev_by_date.get(prior_date) if prior_date else None
        prior_inv = inv_by_date.get(prior_date) if prior_date else None
        prior_stores = stores_by_date.get(prior_date) if prior_date else None

        # ── Core Retail Metrics ─────────────────────────────────────────
        # Inventory turnover = COGS / avg inventory
        avg_inv = ((inv or 0) + (prior_inv or 0)) / 2 if inv and prior_inv else inv
        inv_turnover = safe_div(abs(cg) if cg else None, avg_inv)

        # Days inventory outstanding
        dio = (365 / inv_turnover) if inv_turnover and inv_turnover > 0 else None

        # Revenue per store
        rev_per_store = safe_div(rev, stores)

        # ── Margins ─────────────────────────────────────────────────────
        gross_margin = None
        if gp and rev:
            gross_margin = gp / rev
        elif rev and cg:
            gross_margin = (rev - cg) / rev

        operating_margin = safe_div(op, rev)
        net_margin = safe_div(ni, rev)
        sga_pct = safe_div(sg, rev)

        # ── Growth ──────────────────────────────────────────────────────
        rev_growth = safe_div((rev - prior_rev), abs(prior_rev)) if rev and prior_rev else None
        store_growth = safe_div((stores - prior_stores), abs(prior_stores)) if stores and prior_stores else None

        # Same-store sales proxy = revenue growth - store count growth
        same_store_proxy = None
        if rev_growth is not None and store_growth is not None:
            same_store_proxy = rev_growth - store_growth

        # ── Cash Flow ───────────────────────────────────────────────────
        fcf = (oc - abs(cx)) if oc and cx else (oc if oc else None)
        fcf_margin = safe_div(fcf, rev)

        # ── Working Capital Efficiency ──────────────────────────────────
        # Cash conversion cycle
        dso = safe_div(ar, rev / 365) if rev and ar else None
        dpo = safe_div(ap, abs(cg) / 365) if cg and ap else None
        ccc = None
        if dio is not None:
            ccc = dio + (dso or 0) - (dpo or 0)

        # ── Returns ────────────────────────────────────────────────────
        roa = safe_div(ni, assets)
        roe = safe_div(ni, eq)

        # ROIC = NOPAT / invested capital
        invested_capital = (eq or 0) + (debt or 0)
        tax_rate = 0.21  # approximate
        nopat = op * (1 - tax_rate) if op else None
        roic = safe_div(nopat, invested_capital) if invested_capital else None

        # Revenue per share
        rev_per_share = safe_div(rev, shr)

        computed.append({
            "date": date,
            # Core retail
            "inventoryTurnover": round(inv_turnover, 2) if inv_turnover is not None else None,
            "daysInventory": round(dio, 1) if dio is not None else None,
            "revenuePerStore": round(rev_per_store) if rev_per_store is not None else None,
            "revenuePerShare": round(rev_per_share, 2) if rev_per_share is not None else None,
            # Margins
            "grossMargin": round(gross_margin, 4) if gross_margin is not None else None,
            "operatingMargin": round(operating_margin, 4) if operating_margin is not None else None,
            "netMargin": round(net_margin, 4) if net_margin is not None else None,
            "sgaAsPercentOfRevenue": round(sga_pct, 4) if sga_pct is not None else None,
            "fcfMargin": round(fcf_margin, 4) if fcf_margin is not None else None,
            # Growth
            "revenueGrowth": round(rev_growth, 4) if rev_growth is not None else None,
            "storeGrowth": round(store_growth, 4) if store_growth is not None else None,
            "sameStoreSalesProxy": round(same_store_proxy, 4) if same_store_proxy is not None else None,
            # Working capital
            "cashConversionCycle": round(ccc, 1) if ccc is not None else None,
            # Returns
            "roa": round(roa, 4) if roa is not None else None,
            "roe": round(roe, 4) if roe is not None else None,
            "roic": round(roic, 4) if roic is not None else None,
        })

    kpis["computedMetrics"] = computed
    return kpis
