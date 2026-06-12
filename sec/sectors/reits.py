"""REIT-specific KPIs from SEC XBRL data.

Covers: office, retail, industrial, residential, healthcare, data center REITs.
Key metrics: FFO, AFFO, NOI, NOI margin, FFO payout ratio,
debt-to-EBITDA, same-property NOI growth, FFO per share.
"""

from __future__ import annotations

from sec.sectors._utils import extract_annual_values, safe_div


def compute_reit_kpis(gaap: dict, years: int = 5) -> dict:
    # ── Income ──────────────────────────────────────────────────────────
    net_income = extract_annual_values(gaap, [
        "NetIncomeLoss",
    ], years)

    revenue = extract_annual_values(gaap, [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "OperatingLeaseLeaseIncome",
        "RealEstateRevenueNet",
    ], years + 1)

    rental_revenue = extract_annual_values(gaap, [
        "OperatingLeaseLeaseIncome",
        "OperatingLeaseLeaseIncomeLeasePayments",
        "RealEstateRevenueNet",
    ], years)

    # ── Operating Expenses ──────────────────────────────────────────────
    property_expenses = extract_annual_values(gaap, [
        "DirectCostsOfLeasedAndRentedPropertyOrEquipment",
        "CostOfRealEstateRevenue",
        "RealEstateTaxExpense",
    ], years)

    general_admin = extract_annual_values(gaap, [
        "GeneralAndAdministrativeExpense",
    ], years)

    # ── D&A and Gains ───────────────────────────────────────────────────
    depreciation = extract_annual_values(gaap, [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "RealEstateDepreciationAndAmortization",
        "Depreciation",
    ], years)

    gains_on_sales = extract_annual_values(gaap, [
        "GainLossOnSaleOfProperties",
        "GainsLossesOnSalesOfInvestmentRealEstate",
        "GainLossOnDispositionOfAssets",
    ], years)

    impairments = extract_annual_values(gaap, [
        "ImpairmentOfRealEstate",
        "AssetImpairmentCharges",
    ], years)

    # ── Cash Flow ───────────────────────────────────────────────────────
    capex = extract_annual_values(gaap, [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireRealEstate",
        "PaymentsToAcquireAndDevelopRealEstate",
    ], years)

    # ── Dividends ───────────────────────────────────────────────────────
    dividends = extract_annual_values(gaap, [
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfDividends",
    ], years)

    # ── Debt ────────────────────────────────────────────────────────────
    total_debt = extract_annual_values(gaap, [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ], years)

    mortgage_debt = extract_annual_values(gaap, [
        "SecuredDebt",
        "NotesPayable",
    ], years)

    interest_expense = extract_annual_values(gaap, [
        "InterestExpense",
        "InterestExpenseDebt",
    ], years)

    # ── Balance Sheet ───────────────────────────────────────────────────
    total_assets = extract_annual_values(gaap, [
        "Assets",
    ], years)

    real_estate_assets = extract_annual_values(gaap, [
        "RealEstateInvestmentPropertyNet",
        "RealEstateInvestmentPropertyAtCost",
    ], years)

    # ── Shares ──────────────────────────────────────────────────────────
    shares = extract_annual_values(gaap, [
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ], years)

    # ── Build raw KPIs ──────────────────────────────────────────────────
    kpis = {
        "netIncome": net_income,
        "revenue": revenue[:years],
        "rentalRevenue": rental_revenue,
        "depreciation": depreciation,
        "gainsOnSales": gains_on_sales,
        "impairments": impairments,
        "totalDebt": total_debt,
        "mortgageDebt": mortgage_debt,
        "realEstateAssets": real_estate_assets,
        "dividendsPaid": dividends,
    }

    # ── Computed Metrics ────────────────────────────────────────────────
    computed = []
    ni_by_date = {e["date"]: e["val"] for e in net_income}
    rev_by_date = {e["date"]: e["val"] for e in revenue}
    da_by_date = {e["date"]: e["val"] for e in depreciation}
    gains_by_date = {e["date"]: e["val"] for e in gains_on_sales}
    imp_by_date = {e["date"]: e["val"] for e in impairments}
    capex_by_date = {e["date"]: e["val"] for e in capex}
    div_by_date = {e["date"]: e["val"] for e in dividends}
    debt_by_date = {e["date"]: e["val"] for e in total_debt}
    ie_by_date = {e["date"]: e["val"] for e in interest_expense}
    shares_by_date = {e["date"]: e["val"] for e in shares}
    assets_by_date = {e["date"]: e["val"] for e in total_assets}
    prop_exp_by_date = {e["date"]: e["val"] for e in property_expenses}
    rental_by_date = {e["date"]: e["val"] for e in rental_revenue}
    ga_by_date = {e["date"]: e["val"] for e in general_admin}

    ni_dates = sorted(ni_by_date.keys(), reverse=True)
    for i, date in enumerate(ni_dates[:years]):
        ni = ni_by_date.get(date)
        da = da_by_date.get(date, 0)
        gains = gains_by_date.get(date, 0)
        imp = imp_by_date.get(date, 0)
        cx = capex_by_date.get(date, 0)
        div = div_by_date.get(date)
        debt = debt_by_date.get(date)
        ie = ie_by_date.get(date)
        shr = shares_by_date.get(date)
        rev = rev_by_date.get(date)
        assets = assets_by_date.get(date)
        prop_exp = prop_exp_by_date.get(date)
        rental = rental_by_date.get(date)
        ga = ga_by_date.get(date)

        # FFO = Net Income + D&A - Gains on Sales + Impairments
        ffo = None
        if ni is not None and da:
            ffo = ni + da - gains + imp

        # AFFO = FFO - recurring capex
        affo = ffo - abs(cx) if ffo is not None and cx else ffo

        # FFO per share
        ffo_per_share = safe_div(ffo, shr)

        # AFFO per share
        affo_per_share = safe_div(affo, shr)

        # FFO payout ratio = dividends / FFO
        ffo_payout = safe_div(abs(div) if div else None, ffo) if ffo else None

        # AFFO payout ratio
        affo_payout = safe_div(abs(div) if div else None, affo) if affo else None

        # NOI = rental revenue - property expenses
        noi = None
        if rental and prop_exp:
            noi = rental - prop_exp
        elif rev and prop_exp:
            noi = rev - prop_exp

        # NOI margin
        noi_margin = safe_div(noi, rental or rev)

        # Same-property NOI growth (proxy using total NOI growth)
        prior_date = ni_dates[i + 1] if i + 1 < len(ni_dates) else None
        prior_rev = rev_by_date.get(prior_date) if prior_date else None
        prior_rental = rental_by_date.get(prior_date) if prior_date else None
        prior_prop = prop_exp_by_date.get(prior_date) if prior_date else None
        prior_noi = None
        if prior_rental and prior_prop:
            prior_noi = prior_rental - prior_prop
        elif prior_rev and prior_prop:
            prior_noi = prior_rev - prior_prop
        noi_growth = safe_div((noi - prior_noi), abs(prior_noi)) if noi and prior_noi else None

        # Debt-to-EBITDA (use NOI - G&A as EBITDA proxy for REITs)
        ebitda_proxy = (noi or 0) - (ga or 0) if noi else None
        debt_to_ebitda = safe_div(debt, ebitda_proxy) if ebitda_proxy and ebitda_proxy > 0 else None

        # Debt-to-assets
        debt_to_assets = safe_div(debt, assets)

        # Interest coverage = NOI / interest expense
        interest_coverage = safe_div(noi or (ffo + ie if ffo and ie else None), ie)

        # Capitalization rate proxy = NOI / total real estate assets
        re_assets = {e["date"]: e["val"] for e in real_estate_assets}.get(date)
        cap_rate = safe_div(noi, re_assets)

        computed.append({
            "date": date,
            # Core REIT metrics
            "ffo": round(ffo) if ffo is not None else None,
            "affo": round(affo) if affo is not None else None,
            "ffoPerShare": round(ffo_per_share, 2) if ffo_per_share is not None else None,
            "affoPerShare": round(affo_per_share, 2) if affo_per_share is not None else None,
            "noi": round(noi) if noi is not None else None,
            # Margins & growth
            "noiMargin": round(noi_margin, 4) if noi_margin is not None else None,
            "noiGrowth": round(noi_growth, 4) if noi_growth is not None else None,
            # Payout
            "ffoPayoutRatio": round(ffo_payout, 4) if ffo_payout is not None else None,
            "affoPayoutRatio": round(affo_payout, 4) if affo_payout is not None else None,
            # Leverage
            "debtToEbitda": round(debt_to_ebitda, 2) if debt_to_ebitda is not None else None,
            "debtToAssets": round(debt_to_assets, 4) if debt_to_assets is not None else None,
            "interestCoverage": round(interest_coverage, 2) if interest_coverage is not None else None,
            # Valuation
            "capRateProxy": round(cap_rate, 4) if cap_rate is not None else None,
        })

    kpis["computedMetrics"] = computed
    return kpis
