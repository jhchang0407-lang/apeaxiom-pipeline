"""Industrials/Aerospace & Defense-specific KPIs from SEC XBRL data.

Covers: aerospace, defense, machinery, transportation equipment, industrial conglomerates.
Key metrics: backlog, book-to-bill, operating leverage, ROIC, capex intensity,
organic growth proxy, aftermarket mix.
"""

from __future__ import annotations

from sec.sectors._utils import extract_annual_values, safe_div


def compute_industrial_kpis(gaap: dict, years: int = 5) -> dict:
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
    ], years)

    # ── Backlog / RPO ───────────────────────────────────────────────────
    backlog = extract_annual_values(gaap, [
        "RevenueRemainingPerformanceObligation",
        "BacklogOfOrders",
    ], years + 1)

    # ── Cash Flow ───────────────────────────────────────────────────────
    ocf = extract_annual_values(gaap, [
        "NetCashProvidedByUsedInOperatingActivities",
    ], years)

    capex = extract_annual_values(gaap, [
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ], years)

    acquisitions = extract_annual_values(gaap, [
        "PaymentsToAcquireBusinessesNetOfCashAcquired",
    ], years)

    dividends = extract_annual_values(gaap, [
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfDividends",
    ], years)

    share_repurchases = extract_annual_values(gaap, [
        "PaymentsForRepurchaseOfCommonStock",
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

    contract_assets = extract_annual_values(gaap, [
        "ContractWithCustomerAssetNet",
        "ContractWithCustomerAssetNetCurrent",
    ], years)

    contract_liabilities = extract_annual_values(gaap, [
        "ContractWithCustomerLiability",
        "ContractWithCustomerLiabilityCurrent",
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

    pp_and_e = extract_annual_values(gaap, [
        "PropertyPlantAndEquipmentNet",
    ], years)

    goodwill = extract_annual_values(gaap, [
        "Goodwill",
    ], years)

    intangibles = extract_annual_values(gaap, [
        "IntangibleAssetsNetExcludingGoodwill",
    ], years)

    shares = extract_annual_values(gaap, [
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ], years)

    # ── Build raw KPIs ──────────────────────────────────────────────────
    kpis = {
        "backlog": backlog[:years],
        "rdExpense": rd_expense,
        "inventory": inventory,
        "contractAssets": contract_assets,
        "contractLiabilities": contract_liabilities,
        "acquisitions": acquisitions,
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
    inv_by_date = {e["date"]: e["val"] for e in inventory}
    ar_by_date = {e["date"]: e["val"] for e in accounts_receivable}
    ap_by_date = {e["date"]: e["val"] for e in accounts_payable}
    assets_by_date = {e["date"]: e["val"] for e in total_assets}
    eq_by_date = {e["date"]: e["val"] for e in equity}
    debt_by_date = {e["date"]: e["val"] for e in total_debt}
    ppe_by_date = {e["date"]: e["val"] for e in pp_and_e}
    gw_by_date = {e["date"]: e["val"] for e in goodwill}
    intang_by_date = {e["date"]: e["val"] for e in intangibles}
    backlog_by_date = {e["date"]: e["val"] for e in backlog}
    shares_by_date = {e["date"]: e["val"] for e in shares}
    div_by_date = {e["date"]: e["val"] for e in dividends}
    buyback_by_date = {e["date"]: e["val"] for e in share_repurchases}

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
        inv = inv_by_date.get(date)
        ar = ar_by_date.get(date)
        ap = ap_by_date.get(date)
        assets = assets_by_date.get(date)
        eq = eq_by_date.get(date)
        debt = debt_by_date.get(date)
        ppe = ppe_by_date.get(date)
        gw = gw_by_date.get(date)
        intang = intang_by_date.get(date)
        bl = backlog_by_date.get(date)
        shr = shares_by_date.get(date)
        div = div_by_date.get(date)
        buyback = buyback_by_date.get(date)

        prior_date = rev_dates[i + 1] if i + 1 < len(rev_dates) else None
        prior_rev = rev_by_date.get(prior_date) if prior_date else None
        prior_bl = backlog_by_date.get(prior_date) if prior_date else None

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

        # ── FCF ─────────────────────────────────────────────────────────
        fcf = (oc - abs(cx)) if oc and cx else (oc if oc else None)
        fcf_margin = safe_div(fcf, rev)

        # ── Growth ──────────────────────────────────────────────────────
        rev_growth = safe_div((rev - prior_rev), abs(prior_rev)) if rev and prior_rev else None

        # Organic growth proxy (revenue growth - acquisition impact)
        # Rough proxy since M&A revenue is hard to isolate from XBRL

        # ── Backlog / Book-to-Bill ──────────────────────────────────────
        backlog_to_revenue = safe_div(bl, rev)
        backlog_growth = safe_div((bl - prior_bl), abs(prior_bl)) if bl and prior_bl else None

        # Book-to-bill proxy: if backlog grows, B2B > 1
        book_to_bill = None
        if bl and prior_bl and rev:
            new_orders = rev + (bl - prior_bl)  # orders received ≈ revenue + Δbacklog
            book_to_bill = new_orders / rev

        # ── Capital Efficiency ──────────────────────────────────────────
        capex_intensity = safe_div(abs(cx) if cx else None, rev)
        asset_turnover = safe_div(rev, assets)
        ppe_turnover = safe_div(rev, ppe)

        # ── Returns ─────────────────────────────────────────────────────
        roa = safe_div(ni, assets)
        roe = safe_div(ni, eq)

        # ROIC = NOPAT / invested capital
        invested_capital = (eq or 0) + (debt or 0)
        tax_rate = 0.21  # approximate
        nopat = op * (1 - tax_rate) if op else None
        roic = safe_div(nopat, invested_capital) if invested_capital else None

        # ROCE
        roce = safe_div(op, invested_capital) if invested_capital else None

        # ── Leverage ────────────────────────────────────────────────────
        debt_to_ebitda = safe_div(debt, ebitda)
        debt_to_equity = safe_div(debt, eq)

        # ── Cash Conversion ─────────────────────────────────────────────
        # OCF to net income (quality of earnings)
        ocf_to_ni = safe_div(oc, ni)

        # ── R&D ─────────────────────────────────────────────────────────
        rd_intensity = safe_div(rd, rev)

        # ── Shareholder returns ─────────────────────────────────────────
        total_returns = abs(div or 0) + abs(buyback or 0)
        payout_ratio = safe_div(total_returns, oc) if total_returns and oc else None

        # ── Goodwill intensity (M&A driven) ─────────────────────────────
        goodwill_pct = safe_div((gw or 0) + (intang or 0), assets) if assets else None

        computed.append({
            "date": date,
            # Margins
            "grossMargin": round(gross_margin, 4) if gross_margin is not None else None,
            "operatingMargin": round(op_margin, 4) if op_margin is not None else None,
            "ebitdaMargin": round(ebitda_margin, 4) if ebitda_margin is not None else None,
            "netMargin": round(net_margin, 4) if net_margin is not None else None,
            "fcfMargin": round(fcf_margin, 4) if fcf_margin is not None else None,
            # Growth & backlog
            "revenueGrowth": round(rev_growth, 4) if rev_growth is not None else None,
            "backlogToRevenue": round(backlog_to_revenue, 2) if backlog_to_revenue else None,
            "backlogGrowth": round(backlog_growth, 4) if backlog_growth is not None else None,
            "bookToBill": round(book_to_bill, 2) if book_to_bill else None,
            # Capital efficiency
            "capexIntensity": round(capex_intensity, 4) if capex_intensity else None,
            "assetTurnover": round(asset_turnover, 4) if asset_turnover else None,
            "rdIntensity": round(rd_intensity, 4) if rd_intensity else None,
            # Returns
            "roa": round(roa, 4) if roa else None,
            "roe": round(roe, 4) if roe else None,
            "roic": round(roic, 4) if roic else None,
            "roce": round(roce, 4) if roce else None,
            # Leverage
            "debtToEbitda": round(debt_to_ebitda, 2) if debt_to_ebitda else None,
            "debtToEquity": round(debt_to_equity, 4) if debt_to_equity else None,
            # Quality
            "ocfToNetIncome": round(ocf_to_ni, 4) if ocf_to_ni else None,
            "payoutRatio": round(payout_ratio, 4) if payout_ratio else None,
            "goodwillIntensity": round(goodwill_pct, 4) if goodwill_pct else None,
        })

    kpis["computedMetrics"] = computed
    return kpis
