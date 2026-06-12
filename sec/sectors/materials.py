"""Materials/Chemicals-specific KPIs from SEC XBRL data.

Covers: industrial gases, specialty chemicals, basic materials, metals, paper/packaging.
Key metrics: EBITDA margin, capex intensity, capex/D&A ratio (maintenance vs growth),
asset turnover, fixed-asset turnover, ROIC, leverage, FCF conversion.
"""

from __future__ import annotations

from sec.sectors._utils import extract_annual_values, safe_div


def compute_materials_kpis(gaap: dict, years: int = 5) -> dict:
    # ── Revenue ─────────────────────────────────────────────────────────
    revenue = extract_annual_values(gaap, [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ], years + 1)

    # ── Costs ───────────────────────────────────────────────────────────
    cogs = extract_annual_values(gaap, [
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "CostOfGoodsSold",
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

    sga = extract_annual_values(gaap, [
        "SellingGeneralAndAdministrativeExpense",
    ], years)

    rd_expense = extract_annual_values(gaap, [
        "ResearchAndDevelopmentExpense",
    ], years)

    depreciation = extract_annual_values(gaap, [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "Depreciation",
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

    share_repurchases = extract_annual_values(gaap, [
        "PaymentsForRepurchaseOfCommonStock",
    ], years)

    acquisitions = extract_annual_values(gaap, [
        "PaymentsToAcquireBusinessesNetOfCashAcquired",
    ], years)

    # ── Working Capital ─────────────────────────────────────────────────
    inventory = extract_annual_values(gaap, [
        "InventoryNet",
    ], years)

    accounts_receivable = extract_annual_values(gaap, [
        "AccountsReceivableNetCurrent",
        "AccountsReceivableNet",
    ], years)

    accounts_payable = extract_annual_values(gaap, [
        "AccountsPayableCurrent",
    ], years)

    # ── Balance Sheet ───────────────────────────────────────────────────
    total_assets = extract_annual_values(gaap, [
        "Assets",
    ], years)

    pp_and_e = extract_annual_values(gaap, [
        "PropertyPlantAndEquipmentNet",
    ], years)

    equity = extract_annual_values(gaap, [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ], years)

    total_debt = extract_annual_values(gaap, [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ], years)

    goodwill = extract_annual_values(gaap, [
        "Goodwill",
    ], years)

    intangibles = extract_annual_values(gaap, [
        "IntangibleAssetsNetExcludingGoodwill",
    ], years)

    dividends_per_share = extract_annual_values(gaap, [
        "CommonStockDividendsPerShareDeclared",
        "CommonStockDividendsPerShareCashPaid",
    ], years)

    # ── Build raw KPIs ──────────────────────────────────────────────────
    kpis = {
        "inventory": inventory,
        "ppAndE": pp_and_e,
        "goodwill": goodwill,
        "acquisitions": acquisitions,
        "dividendsPerShare": dividends_per_share,
    }

    # ── Computed Metrics ────────────────────────────────────────────────
    computed = []
    rev_by_date = {e["date"]: e["val"] for e in revenue}
    cogs_by_date = {e["date"]: e["val"] for e in cogs}
    gp_by_date = {e["date"]: e["val"] for e in gross_profit}
    opinc_by_date = {e["date"]: e["val"] for e in operating_income}
    ni_by_date = {e["date"]: e["val"] for e in net_income}
    sga_by_date = {e["date"]: e["val"] for e in sga}
    rd_by_date = {e["date"]: e["val"] for e in rd_expense}
    da_by_date = {e["date"]: e["val"] for e in depreciation}
    ocf_by_date = {e["date"]: e["val"] for e in ocf}
    capex_by_date = {e["date"]: e["val"] for e in capex}
    div_by_date = {e["date"]: e["val"] for e in dividends}
    buyback_by_date = {e["date"]: e["val"] for e in share_repurchases}
    inv_by_date = {e["date"]: e["val"] for e in inventory}
    ar_by_date = {e["date"]: e["val"] for e in accounts_receivable}
    ap_by_date = {e["date"]: e["val"] for e in accounts_payable}
    assets_by_date = {e["date"]: e["val"] for e in total_assets}
    ppe_by_date = {e["date"]: e["val"] for e in pp_and_e}
    eq_by_date = {e["date"]: e["val"] for e in equity}
    debt_by_date = {e["date"]: e["val"] for e in total_debt}
    gw_by_date = {e["date"]: e["val"] for e in goodwill}
    intang_by_date = {e["date"]: e["val"] for e in intangibles}
    dps_by_date = {e["date"]: e["val"] for e in dividends_per_share}

    rev_dates = sorted(rev_by_date.keys(), reverse=True)
    for i, date in enumerate(rev_dates[:years]):
        rev = rev_by_date.get(date)
        cg = cogs_by_date.get(date)
        gp = gp_by_date.get(date)
        op = opinc_by_date.get(date)
        ni = ni_by_date.get(date)
        sg = sga_by_date.get(date)
        rd = rd_by_date.get(date)
        da = da_by_date.get(date)
        oc = ocf_by_date.get(date)
        cx = capex_by_date.get(date)
        div = div_by_date.get(date)
        buyback = buyback_by_date.get(date)
        inv = inv_by_date.get(date)
        ar = ar_by_date.get(date)
        ap = ap_by_date.get(date)
        assets = assets_by_date.get(date)
        ppe = ppe_by_date.get(date)
        eq = eq_by_date.get(date)
        debt = debt_by_date.get(date)
        gw = gw_by_date.get(date)
        intang = intang_by_date.get(date)
        dps = dps_by_date.get(date)

        prior_date = rev_dates[i + 1] if i + 1 < len(rev_dates) else None
        prior_rev = rev_by_date.get(prior_date) if prior_date else None
        prior_dps = dps_by_date.get(prior_date) if prior_date else None

        # ── Margins ─────────────────────────────────────────────────────
        gross_margin = None
        if gp and rev:
            gross_margin = gp / rev
        elif rev and cg:
            gross_margin = (rev - cg) / rev

        op_margin = safe_div(op, rev)
        net_margin = safe_div(ni, rev)

        # EBITDA (primary profitability measure for materials)
        ebitda = (op + da) if op and da else None
        ebitda_margin = safe_div(ebitda, rev)

        # ── FCF ─────────────────────────────────────────────────────────
        fcf = (oc - abs(cx)) if oc and cx else (oc if oc else None)
        fcf_margin = safe_div(fcf, rev)

        # ── Growth ──────────────────────────────────────────────────────
        rev_growth = safe_div((rev - prior_rev), abs(prior_rev)) if rev and prior_rev else None
        div_growth = safe_div((dps - prior_dps), abs(prior_dps)) if dps and prior_dps else None

        # ── Capital Intensity (key for materials) ──────────────────────
        capex_intensity = safe_div(abs(cx) if cx else None, rev)
        capex_to_da = safe_div(abs(cx) if cx else None, da)  # >1 = growth investment
        da_pct_of_ppe = safe_div(da, ppe)  # depreciation rate

        # ── Asset Efficiency ────────────────────────────────────────────
        asset_turnover = safe_div(rev, assets)
        fixed_asset_turnover = safe_div(rev, ppe)  # key for asset-heavy businesses
        sga_pct = safe_div(sg, rev)

        # ── Working Capital Efficiency ──────────────────────────────────
        cogs_val = cg or cogs_by_date.get(date)
        inv_turnover = safe_div(cogs_val, inv)
        dio = safe_div(365, inv_turnover) if inv_turnover else None
        dso = safe_div(ar, rev / 365) if ar and rev else None
        dpo = safe_div(ap, cogs_val / 365) if ap and cogs_val else None

        ccc = None
        if dio is not None and dso is not None and dpo is not None:
            ccc = dio + dso - dpo

        # ── Returns ─────────────────────────────────────────────────────
        roe = safe_div(ni, eq)
        roa = safe_div(ni, assets)

        invested_capital = (eq or 0) + (debt or 0)
        tax_rate = 0.21
        nopat = op * (1 - tax_rate) if op else None
        roic = safe_div(nopat, invested_capital) if invested_capital else None
        roce = safe_div(op, invested_capital) if invested_capital else None

        # ── Leverage ────────────────────────────────────────────────────
        debt_to_ebitda = safe_div(debt, ebitda)
        debt_to_equity = safe_div(debt, eq)

        # ── Cash Conversion & Quality ──────────────────────────────────
        ocf_to_ni = safe_div(oc, ni)
        fcf_conversion = safe_div(fcf, ni)

        # ── Shareholder Returns ─────────────────────────────────────────
        total_returns = abs(div or 0) + abs(buyback or 0)
        payout_ratio = safe_div(total_returns, oc) if total_returns and oc else None

        # ── Goodwill Intensity ──────────────────────────────────────────
        goodwill_pct = safe_div((gw or 0) + (intang or 0), assets) if assets else None

        # ── R&D ─────────────────────────────────────────────────────────
        rd_intensity = safe_div(rd, rev)

        computed.append({
            "date": date,
            # Margins
            "grossMargin": round(gross_margin, 4) if gross_margin is not None else None,
            "operatingMargin": round(op_margin, 4) if op_margin is not None else None,
            "ebitdaMargin": round(ebitda_margin, 4) if ebitda_margin is not None else None,
            "netMargin": round(net_margin, 4) if net_margin is not None else None,
            "fcfMargin": round(fcf_margin, 4) if fcf_margin is not None else None,
            # Capital intensity (key differentiator)
            "capexIntensity": round(capex_intensity, 4) if capex_intensity is not None else None,
            "capexToDA": round(capex_to_da, 2) if capex_to_da is not None else None,
            "daPctOfPPE": round(da_pct_of_ppe, 4) if da_pct_of_ppe is not None else None,
            # Asset efficiency
            "assetTurnover": round(asset_turnover, 4) if asset_turnover is not None else None,
            "fixedAssetTurnover": round(fixed_asset_turnover, 4) if fixed_asset_turnover is not None else None,
            "sgaPctOfRevenue": round(sga_pct, 4) if sga_pct is not None else None,
            "rdIntensity": round(rd_intensity, 4) if rd_intensity is not None else None,
            # Growth
            "revenueGrowth": round(rev_growth, 4) if rev_growth is not None else None,
            "dividendGrowth": round(div_growth, 4) if div_growth is not None else None,
            # Working capital
            "inventoryTurnover": round(inv_turnover, 2) if inv_turnover is not None else None,
            "daysInventory": round(dio, 1) if dio is not None else None,
            "dso": round(dso, 1) if dso is not None else None,
            "dpo": round(dpo, 1) if dpo is not None else None,
            "cashConversionCycle": round(ccc, 1) if ccc is not None else None,
            # Returns
            "roe": round(roe, 4) if roe is not None else None,
            "roa": round(roa, 4) if roa is not None else None,
            "roic": round(roic, 4) if roic is not None else None,
            "roce": round(roce, 4) if roce is not None else None,
            # Leverage
            "debtToEbitda": round(debt_to_ebitda, 2) if debt_to_ebitda is not None else None,
            "debtToEquity": round(debt_to_equity, 4) if debt_to_equity is not None else None,
            # Quality
            "ocfToNetIncome": round(ocf_to_ni, 4) if ocf_to_ni is not None else None,
            "fcfConversion": round(fcf_conversion, 4) if fcf_conversion is not None else None,
            "payoutRatio": round(payout_ratio, 4) if payout_ratio is not None else None,
            # M&A
            "goodwillIntensity": round(goodwill_pct, 4) if goodwill_pct is not None else None,
        })

    kpis["computedMetrics"] = computed
    return kpis
