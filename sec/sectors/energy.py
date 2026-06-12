"""Energy-specific KPIs from SEC XBRL data.

Covers: E&P, integrated oil, oilfield services, refining, pipelines.
Key metrics: reserve replacement, finding costs, capex intensity,
DD&A per BOE, FCF yield, production costs, reinvestment rate.
"""

from __future__ import annotations

from sec.sectors._utils import extract_annual_values, safe_div


def compute_energy_kpis(gaap: dict, years: int = 5) -> dict:
    # ── Revenue ─────────────────────────────────────────────────────────
    revenue = extract_annual_values(gaap, [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ], years + 1)

    oil_revenue = extract_annual_values(gaap, [
        "OilAndGasRevenue",
        "OilAndCondensateRevenue",
    ], years)

    gas_revenue = extract_annual_values(gaap, [
        "NaturalGasProductionRevenue",
        "GasGatheringTransportationMarketingAndProcessingRevenue",
    ], years)

    # ── Production / Reserves ───────────────────────────────────────────
    proved_reserves = extract_annual_values(gaap, [
        "ProvedDevelopedReservesVolume",
        "ProvedReservesVolume",
        "ProvedDevelopedAndUndevelopedReservesNetQuantityOfCrudeOilNaturalGasLiquidsAndNaturalGas",
    ], years)

    production_volumes = extract_annual_values(gaap, [
        "OilAndGasProductionVolume",
        "ProductionOfCrudeOilNaturalGasLiquidsAndNaturalGas",
    ], years)

    # ── Costs ───────────────────────────────────────────────────────────
    exploration_expense = extract_annual_values(gaap, [
        "ExplorationExpense",
        "ExplorationCosts",
        "ExplorationAndProductionCosts",
    ], years)

    production_costs = extract_annual_values(gaap, [
        "ProductionCosts",
        "LeaseOperatingExpense",
        "OilAndGasProductionExpense",
    ], years)

    dd_and_a = extract_annual_values(gaap, [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
    ], years)

    cogs = extract_annual_values(gaap, [
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
    ], years)

    # ── Operating Expenses ──────────────────────────────────────────────
    operating_income = extract_annual_values(gaap, [
        "OperatingIncomeLoss",
    ], years)

    net_income = extract_annual_values(gaap, [
        "NetIncomeLoss",
    ], years)

    # ── Cash Flow ───────────────────────────────────────────────────────
    capex = extract_annual_values(gaap, [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireOilAndGasPropertyAndEquipment",
        "PaymentsToExploreAndDevelopOilAndGasProperties",
    ], years)

    ocf = extract_annual_values(gaap, [
        "NetCashProvidedByUsedInOperatingActivities",
    ], years)

    dividends = extract_annual_values(gaap, [
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfDividends",
    ], years)

    share_repurchases = extract_annual_values(gaap, [
        "PaymentsForRepurchaseOfCommonStock",
    ], years)

    # ── Debt ────────────────────────────────────────────────────────────
    total_debt = extract_annual_values(gaap, [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ], years)

    # ── Balance Sheet ───────────────────────────────────────────────────
    equity = extract_annual_values(gaap, [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ], years)

    pp_and_e = extract_annual_values(gaap, [
        "PropertyPlantAndEquipmentNet",
        "OilAndGasPropertyFullCostMethodNet",
        "OilAndGasPropertySuccessfulEffortMethodNet",
    ], years)

    shares = extract_annual_values(gaap, [
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ], years)

    # ── Build raw KPIs ──────────────────────────────────────────────────
    kpis = {
        "provedReserves": proved_reserves,
        "productionVolumes": production_volumes,
        "explorationExpense": exploration_expense,
        "productionCosts": production_costs,
        "oilRevenue": oil_revenue,
        "gasRevenue": gas_revenue,
    }

    # ── Computed Metrics ────────────────────────────────────────────────
    computed = []
    rev_by_date = {e["date"]: e["val"] for e in revenue}
    capex_by_date = {e["date"]: e["val"] for e in capex}
    ocf_by_date = {e["date"]: e["val"] for e in ocf}
    dda_by_date = {e["date"]: e["val"] for e in dd_and_a}
    ni_by_date = {e["date"]: e["val"] for e in net_income}
    opinc_by_date = {e["date"]: e["val"] for e in operating_income}
    cogs_by_date = {e["date"]: e["val"] for e in cogs}
    debt_by_date = {e["date"]: e["val"] for e in total_debt}
    eq_by_date = {e["date"]: e["val"] for e in equity}
    div_by_date = {e["date"]: e["val"] for e in dividends}
    buyback_by_date = {e["date"]: e["val"] for e in share_repurchases}
    explore_by_date = {e["date"]: e["val"] for e in exploration_expense}
    prod_cost_by_date = {e["date"]: e["val"] for e in production_costs}
    ppe_by_date = {e["date"]: e["val"] for e in pp_and_e}
    shares_by_date = {e["date"]: e["val"] for e in shares}

    rev_dates = sorted(rev_by_date.keys(), reverse=True)
    for i, date in enumerate(rev_dates[:years]):
        rev = rev_by_date.get(date)
        cx = capex_by_date.get(date)
        oc = ocf_by_date.get(date)
        da = dda_by_date.get(date)
        ni = ni_by_date.get(date)
        op = opinc_by_date.get(date)
        cg = cogs_by_date.get(date)
        debt = debt_by_date.get(date)
        eq = eq_by_date.get(date)
        div = div_by_date.get(date)
        buyback = buyback_by_date.get(date)
        explore = explore_by_date.get(date)
        prod_cost = prod_cost_by_date.get(date)
        ppe = ppe_by_date.get(date)
        shr = shares_by_date.get(date)

        prior_date = rev_dates[i + 1] if i + 1 < len(rev_dates) else None
        prior_rev = rev_by_date.get(prior_date) if prior_date else None

        # Capex intensity = capex / revenue
        capex_to_rev = safe_div(abs(cx) if cx else None, rev)

        # Reinvestment rate = capex / OCF
        reinvestment_rate = safe_div(abs(cx) if cx else None, oc)

        # DD&A to revenue
        dda_to_rev = safe_div(da, rev)

        # FCF
        fcf = (oc - abs(cx)) if oc and cx else (oc if oc else None)
        fcf_margin = safe_div(fcf, rev)

        # Operating margin
        op_margin = safe_div(op, rev)

        # Net margin
        net_margin = safe_div(ni, rev)

        # Gross margin
        gross_margin = safe_div((rev - cg), rev) if rev and cg else None

        # Revenue growth
        rev_growth = safe_div((rev - prior_rev), abs(prior_rev)) if rev and prior_rev else None

        # Debt-to-EBITDA
        ebitda = (op + da) if op and da else None
        debt_to_ebitda = safe_div(debt, ebitda)

        # Debt-to-equity
        debt_to_equity = safe_div(debt, eq)

        # Shareholder returns = (dividends + buybacks) / OCF
        total_returns = (abs(div or 0)) + (abs(buyback or 0))
        shareholder_return_ratio = safe_div(total_returns, oc) if total_returns and oc else None

        # Exploration expense as % of revenue
        explore_pct = safe_div(explore, rev)

        # Production cost per revenue (lifting cost proxy)
        prod_cost_pct = safe_div(prod_cost, rev)

        # Capital efficiency = revenue / PP&E
        capital_efficiency = safe_div(rev, ppe)

        # ROCE = operating income / (equity + debt)
        invested_capital = (eq or 0) + (debt or 0)
        roce = safe_div(op, invested_capital) if invested_capital else None

        # FCF per share
        fcf_per_share = safe_div(fcf, shr)

        computed.append({
            "date": date,
            # Margins
            "grossMargin": round(gross_margin, 4) if gross_margin is not None else None,
            "operatingMargin": round(op_margin, 4) if op_margin is not None else None,
            "netMargin": round(net_margin, 4) if net_margin is not None else None,
            "fcfMargin": round(fcf_margin, 4) if fcf_margin is not None else None,
            # Capital allocation
            "capexToRevenue": round(capex_to_rev, 4) if capex_to_rev is not None else None,
            "reinvestmentRate": round(reinvestment_rate, 4) if reinvestment_rate is not None else None,
            "shareholderReturnRatio": round(shareholder_return_ratio, 4) if shareholder_return_ratio is not None else None,
            # Cost structure
            "ddaToRevenue": round(dda_to_rev, 4) if dda_to_rev is not None else None,
            "explorationPctOfRevenue": round(explore_pct, 4) if explore_pct is not None else None,
            "productionCostPctOfRevenue": round(prod_cost_pct, 4) if prod_cost_pct is not None else None,
            # Leverage
            "debtToEbitda": round(debt_to_ebitda, 2) if debt_to_ebitda is not None else None,
            "debtToEquity": round(debt_to_equity, 4) if debt_to_equity is not None else None,
            # Returns
            "roce": round(roce, 4) if roce is not None else None,
            "capitalEfficiency": round(capital_efficiency, 4) if capital_efficiency is not None else None,
            # Growth
            "revenueGrowth": round(rev_growth, 4) if rev_growth is not None else None,
            # Per share
            "fcfPerShare": round(fcf_per_share, 2) if fcf_per_share is not None else None,
        })

    kpis["computedMetrics"] = computed
    return kpis
