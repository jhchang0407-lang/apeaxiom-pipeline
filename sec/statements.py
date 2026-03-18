"""Build standardized financial statements from raw XBRL companyfacts."""

from __future__ import annotations

from sec.client import get_companyfacts
from sec.mapper import build_tag_map


def _extract_periods(
    facts_data: dict,
    tag: str | None,
    form_filter: str,
    fp_filter: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Extract values for a single XBRL tag, filtered by form and fiscal period.

    Returns a list of {end, fy, fp, val, form} dicts sorted by end date desc.
    Deduplicates by (end, fp) keeping the value from the most recent filing.
    """
    if tag is None:
        return []

    gaap = facts_data.get("facts", {}).get("us-gaap", {})
    if tag not in gaap:
        return []

    units = gaap[tag].get("units", {})
    # Pick the first available unit (usually USD or shares or pure)
    if not units:
        return []
    unit_key = list(units.keys())[0]
    entries = units[unit_key]

    # Filter by form type
    filtered = [e for e in entries if e.get("form") == form_filter]

    # Filter by fiscal period if specified
    if fp_filter:
        filtered = [e for e in filtered if e.get("fp") == fp_filter]

    # Duration items must have a 'start' date — filter out instant entries
    # that are incorrectly tagged under duration XBRL tags (e.g., individual
    # dividend payment dates filed with fp=FY on 10-K forms).
    filtered = [e for e in filtered if "start" in e]

    # Deduplicate by (end, fp) — keep latest filed
    seen: dict[tuple, dict] = {}
    for e in filtered:
        key = (e["end"], e.get("fp", ""))
        existing = seen.get(key)
        if existing is None or e.get("filed", "") > existing.get("filed", ""):
            seen[key] = e

    # Sort by end date descending
    result = sorted(seen.values(), key=lambda x: x["end"], reverse=True)
    return result[:limit]


def _extract_instant(
    facts_data: dict,
    tag: str | None,
    form_filter: str,
    limit: int = 20,
) -> list[dict]:
    """Extract instant (point-in-time) values for balance sheet items.

    Balance sheet items don't have a 'start' date — they're snapshots.
    We filter to get fiscal year end or quarter end values.
    """
    if tag is None:
        return []

    gaap = facts_data.get("facts", {}).get("us-gaap", {})
    if tag not in gaap:
        return []

    units = gaap[tag].get("units", {})
    if not units:
        return []
    unit_key = list(units.keys())[0]
    entries = units[unit_key]

    # Filter by form type
    filtered = [e for e in entries if e.get("form") == form_filter]

    # Deduplicate by (end, fp) — keep latest filed
    seen: dict[tuple, dict] = {}
    for e in filtered:
        key = (e["end"], e.get("fp", ""))
        existing = seen.get(key)
        if existing is None or e.get("filed", "") > existing.get("filed", ""):
            seen[key] = e

    result = sorted(seen.values(), key=lambda x: x["end"], reverse=True)
    return result[:limit]


def _is_duration_tag(facts_data: dict, tag: str | None) -> bool:
    """Check if an XBRL tag represents a duration (has 'start') or instant."""
    if tag is None:
        return True  # default assumption
    gaap = facts_data.get("facts", {}).get("us-gaap", {})
    if tag not in gaap:
        return True
    units = gaap[tag].get("units", {})
    if not units:
        return True
    unit_key = list(units.keys())[0]
    entries = units[unit_key]
    if entries:
        return "start" in entries[0]
    return True


# Duration items (income statement, cash flow)
_DURATION_ITEMS = {
    "revenue", "costOfRevenue", "grossProfit", "researchAndDevelopmentExpenses",
    "sellingGeneralAndAdministrativeExpenses", "operatingExpenses", "operatingIncome",
    "interestExpense", "interestIncome", "incomeBeforeTax", "incomeTaxExpense",
    "netIncome", "epsDiluted", "epsBasic", "weightedAverageSharesDiluted",
    "weightedAverageSharesBasic", "depreciationAndAmortization", "ebitda",
    "stockBasedCompensation",
    "operatingCashFlow", "capitalExpenditure", "acquisitionsNet",
    "investingCashFlow", "dividendsPaid", "shareRepurchases",
    "debtRepayment", "debtIssuance", "financingCashFlow",
    "changeInWorkingCapital",
    # Bank-specific
    "netInterestIncome", "interestIncomeOperating", "interestExpenseOperating",
    "nonInterestIncome", "nonInterestExpense", "provisionForCreditLosses",
    # Insurance-specific
    "premiumsEarned", "policyholderBenefits", "netInvestmentIncome",
}

# Instant items (balance sheet)
_INSTANT_ITEMS = {
    "cashAndCashEquivalents", "shortTermInvestments", "accountsReceivables",
    "inventory", "totalCurrentAssets", "propertyPlantAndEquipment",
    "goodwill", "intangibleAssets", "totalAssets", "accountsPayables",
    "shortTermDebt", "currentPortionOfLongTermDebt", "totalCurrentLiabilities",
    "longTermDebt", "totalLiabilities", "totalStockholdersEquity",
    "retainedEarnings", "totalDebt", "deferredRevenue",
    "deferredRevenueNonCurrent", "commonSharesOutstanding",
}


def _build_statement_rows(
    facts_data: dict,
    tag_map: dict[str, str | None],
    items: list[str],
    form: str,
    fp: str | None,
    limit: int,
) -> list[dict]:
    """Build rows for a statement — one dict per period with all line items."""

    # Collect all period end dates
    all_periods: dict[str, dict] = {}

    for item_name in items:
        tag = tag_map.get(item_name)
        if item_name in _INSTANT_ITEMS:
            entries = _extract_instant(facts_data, tag, form, limit=limit * 2)
        else:
            entries = _extract_periods(facts_data, tag, form, fp, limit=limit * 2)

        if fp is None and item_name in _DURATION_ITEMS:
            # For annual statements, filter to FY only
            entries = [e for e in entries if e.get("fp") == "FY"]

        for entry in entries:
            end = entry["end"]
            fy = entry.get("fy")
            fp_val = entry.get("fp", "")

            # For quarterly, use (end, fp) as key; for annual use end
            if fp:
                period_key = f"{end}_{fp_val}"
            else:
                period_key = end

            if period_key not in all_periods:
                all_periods[period_key] = {
                    "date": end,
                    "calendarYear": end[:4],
                    "period": fp_val if fp_val else "FY",
                }
            all_periods[period_key][item_name] = entry["val"]

    # Sort by date descending and limit
    rows = sorted(all_periods.values(), key=lambda x: x["date"], reverse=True)
    return rows[:limit]


def _add_fmp_aliases(
    income_statement: list[dict],
    balance_sheet: list[dict],
    cash_flow: list[dict],
) -> None:
    """Add FMP-compatible field aliases so downstream n8n code nodes work unchanged.

    Adds alias fields with FMP naming conventions alongside our cleaner names.
    """
    for row in income_statement:
        # weightedAverageSharesDiluted → weightedAverageShsOutDil
        shares = row.get("weightedAverageSharesDiluted")
        if shares is not None:
            row["weightedAverageShsOutDil"] = shares

        # Compute effectiveTaxRate
        tax = row.get("incomeTaxExpense")
        pretax = row.get("incomeBeforeTax")
        if tax is not None and pretax is not None and pretax != 0:
            row["effectiveTaxRate"] = tax / pretax

    for row in balance_sheet:
        # Compute netDebt
        debt = row.get("totalDebt")
        cash = row.get("cashAndCashEquivalents", 0) or 0
        if debt is not None:
            row["netDebt"] = debt - cash

        # Compute goodwillAndIntangibleAssets
        gw = row.get("goodwill", 0) or 0
        intang = row.get("intangibleAssets", 0) or 0
        if gw or intang:
            row["goodwillAndIntangibleAssets"] = gw + intang

    for row in cash_flow:
        # dividendsPaid → commonDividendsPaid
        div = row.get("dividendsPaid")
        if div is not None:
            row["commonDividendsPaid"] = div

        # shareRepurchases → commonStockRepurchased
        buyback = row.get("shareRepurchases")
        if buyback is not None:
            row["commonStockRepurchased"] = buyback


def get_annual_statements(ticker: str, years: int = 5) -> dict:
    """Build annual financial statements for a ticker.

    Returns dict with income_statement, balance_sheet, cash_flow lists.
    """
    facts_data = get_companyfacts(ticker)
    gaap = facts_data.get("facts", {}).get("us-gaap", {})
    tag_map = build_tag_map(gaap)

    is_items = [
        "revenue", "costOfRevenue", "grossProfit",
        "researchAndDevelopmentExpenses", "sellingGeneralAndAdministrativeExpenses",
        "operatingExpenses", "operatingIncome",
        "interestExpense", "interestIncome",
        "incomeBeforeTax", "incomeTaxExpense", "netIncome",
        "epsDiluted", "epsBasic",
        "weightedAverageSharesDiluted", "weightedAverageSharesBasic",
        "depreciationAndAmortization", "ebitda", "stockBasedCompensation",
        # Bank-specific (will be None for non-banks)
        "netInterestIncome", "interestIncomeOperating", "interestExpenseOperating",
        "nonInterestIncome", "nonInterestExpense", "provisionForCreditLosses",
        # Insurance-specific
        "premiumsEarned", "policyholderBenefits", "netInvestmentIncome",
    ]

    bs_items = [
        "cashAndCashEquivalents", "shortTermInvestments",
        "accountsReceivables", "inventory",
        "totalCurrentAssets", "propertyPlantAndEquipment",
        "goodwill", "intangibleAssets", "totalAssets",
        "accountsPayables", "shortTermDebt", "currentPortionOfLongTermDebt",
        "totalCurrentLiabilities", "longTermDebt",
        "totalLiabilities", "totalStockholdersEquity",
        "retainedEarnings", "totalDebt", "deferredRevenue",
        "commonSharesOutstanding",
    ]

    cf_items = [
        "operatingCashFlow", "capitalExpenditure", "acquisitionsNet",
        "investingCashFlow", "dividendsPaid", "shareRepurchases",
        "debtRepayment", "debtIssuance", "financingCashFlow",
        "depreciationAndAmortization", "stockBasedCompensation",
        "changeInWorkingCapital",
    ]

    # Add deferredRevenueNonCurrent to balance sheet
    bs_items.append("deferredRevenueNonCurrent")

    income_statement = _build_statement_rows(
        facts_data, tag_map, is_items, "10-K", None, years
    )
    balance_sheet = _build_statement_rows(
        facts_data, tag_map, bs_items, "10-K", None, years
    )
    cash_flow = _build_statement_rows(
        facts_data, tag_map, cf_items, "10-K", None, years
    )

    # Compute derived fields
    for row in income_statement:
        rev = row.get("revenue", 0)
        cogs = row.get("costOfRevenue", 0)
        # Compute gross profit if not tagged
        if "grossProfit" not in row and rev and cogs:
            row["grossProfit"] = rev - cogs
        # Compute EBITDA if not tagged
        if "ebitda" not in row:
            op_inc = row.get("operatingIncome", 0)
            da = row.get("depreciationAndAmortization", 0)
            if op_inc and da:
                row["ebitda"] = op_inc + da

        # ── Bank-specific derived fields ────────────────────────────────
        nii = row.get("netInterestIncome")
        nii2 = row.get("nonInterestIncome")
        nie = row.get("nonInterestExpense")
        prov = row.get("provisionForCreditLosses")

        # Compute totalBankRevenue = NII + Non-Interest Income
        if nii is not None and nii2 is not None:
            row["totalBankRevenue"] = nii + nii2
            # If standard revenue is missing or is just interest income, fix it
            if not rev or rev == row.get("interestIncomeOperating"):
                row["revenue"] = nii + nii2

        # Pre-provision net revenue (PPNR) = NII + Non-Interest Income - Non-Interest Expense
        if nii is not None and nii2 is not None and nie is not None:
            row["preProvisionIncome"] = nii + nii2 - nie

        # For banks: grossProfit ≈ NII (after interest expense is already deducted)
        if nii is not None and "grossProfit" not in row:
            row["grossProfit"] = nii

        # For banks: operatingIncome ≈ PPNR - Provision
        if "operatingIncome" not in row:
            ppnr = row.get("preProvisionIncome")
            if ppnr is not None and prov is not None:
                row["operatingIncome"] = ppnr - prov

        # ── Insurance-specific derived fields ───────────────────────────
        premiums = row.get("premiumsEarned")
        claims = row.get("policyholderBenefits")
        inv_income = row.get("netInvestmentIncome")

        # Insurance total revenue = premiums + investment income
        # Always override: SEC XBRL "Revenues" tag may return a narrow value
        # for insurance companies (e.g., MetLife post-2023).
        if premiums is not None:
            ins_rev = premiums + (inv_income or 0)
            row["revenue"] = ins_rev
            # Underwriting income = premiums - claims
            if claims is not None:
                row["underwritingIncome"] = premiums - claims

    # Compute freeCashFlow
    for row in cash_flow:
        ocf = row.get("operatingCashFlow", 0)
        capex = row.get("capitalExpenditure", 0)
        if ocf and capex:
            row["freeCashFlow"] = ocf - abs(capex)
        elif ocf:
            row["freeCashFlow"] = ocf

    # Compute totalDebt if not tagged
    for row in balance_sheet:
        if "totalDebt" not in row:
            st = abs(row.get("shortTermDebt", 0) or 0)
            lt = abs(row.get("longTermDebt", 0) or 0)
            cplt = abs(row.get("currentPortionOfLongTermDebt", 0) or 0)
            if st or lt or cplt:
                row["totalDebt"] = st + lt + cplt

    # ── FMP-compatible aliases & derived fields ─────────────────────────
    _add_fmp_aliases(income_statement, balance_sheet, cash_flow)

    return {
        "income_statement": income_statement,
        "balance_sheet": balance_sheet,
        "cash_flow": cash_flow,
        "tag_map": {k: v for k, v in tag_map.items() if v is not None},
    }


def get_quarterly_statements(ticker: str, quarters: int = 8) -> dict:
    """Build quarterly financial statements for a ticker."""
    facts_data = get_companyfacts(ticker)
    gaap = facts_data.get("facts", {}).get("us-gaap", {})
    tag_map = build_tag_map(gaap)

    is_items = [
        "revenue", "costOfRevenue", "grossProfit",
        "researchAndDevelopmentExpenses", "sellingGeneralAndAdministrativeExpenses",
        "operatingExpenses", "operatingIncome",
        "interestExpense", "interestIncome",
        "incomeBeforeTax", "incomeTaxExpense", "netIncome",
        "epsDiluted", "epsBasic",
        "weightedAverageSharesDiluted", "weightedAverageSharesBasic",
        "depreciationAndAmortization", "stockBasedCompensation",
        # Bank-specific
        "netInterestIncome", "interestIncomeOperating", "interestExpenseOperating",
        "nonInterestIncome", "nonInterestExpense", "provisionForCreditLosses",
        # Insurance-specific
        "premiumsEarned", "policyholderBenefits", "netInvestmentIncome",
    ]

    bs_items = [
        "cashAndCashEquivalents", "shortTermInvestments",
        "accountsReceivables", "inventory",
        "totalCurrentAssets", "propertyPlantAndEquipment",
        "goodwill", "intangibleAssets", "totalAssets",
        "accountsPayables", "shortTermDebt", "currentPortionOfLongTermDebt",
        "totalCurrentLiabilities", "longTermDebt",
        "totalLiabilities", "totalStockholdersEquity",
        "retainedEarnings", "totalDebt", "deferredRevenue",
        "commonSharesOutstanding",
    ]

    cf_items = [
        "operatingCashFlow", "capitalExpenditure",
        "investingCashFlow", "financingCashFlow",
        "depreciationAndAmortization", "stockBasedCompensation",
    ]

    # For quarterly, we need to get Q1-Q4 entries
    all_is: list[dict] = []
    all_bs: list[dict] = []
    all_cf: list[dict] = []

    for qtr in ["Q1", "Q2", "Q3", "Q4"]:
        is_rows = _build_statement_rows(
            facts_data, tag_map, is_items, "10-Q", qtr, quarters
        )
        all_is.extend(is_rows)

        bs_rows = _build_statement_rows(
            facts_data, tag_map, bs_items, "10-Q", qtr, quarters
        )
        all_bs.extend(bs_rows)

        cf_rows = _build_statement_rows(
            facts_data, tag_map, cf_items, "10-Q", qtr, quarters
        )
        all_cf.extend(cf_rows)

    # Also include FY (10-K) entries that might be Q4 equivalent
    is_fy = _build_statement_rows(
        facts_data, tag_map, is_items, "10-K", None, quarters
    )
    # Tag FY entries as Q4 equivalent if not already present
    existing_dates = {r["date"] for r in all_is}
    for row in is_fy:
        if row["date"] not in existing_dates:
            row["period"] = "FY"
            all_is.append(row)

    # Sort and limit
    all_is.sort(key=lambda x: x["date"], reverse=True)
    all_bs.sort(key=lambda x: x["date"], reverse=True)
    all_cf.sort(key=lambda x: x["date"], reverse=True)

    # Compute derived fields (same logic as annual)
    for row in all_is:
        rev = row.get("revenue", 0)
        cogs = row.get("costOfRevenue", 0)
        if "grossProfit" not in row and rev and cogs:
            row["grossProfit"] = rev - cogs

        # Bank derived fields
        nii = row.get("netInterestIncome")
        nii2 = row.get("nonInterestIncome")
        nie = row.get("nonInterestExpense")
        prov = row.get("provisionForCreditLosses")

        if nii is not None and nii2 is not None:
            row["totalBankRevenue"] = nii + nii2
            if not rev or rev == row.get("interestIncomeOperating"):
                row["revenue"] = nii + nii2
        if nii is not None and nii2 is not None and nie is not None:
            row["preProvisionIncome"] = nii + nii2 - nie
        if nii is not None and "grossProfit" not in row:
            row["grossProfit"] = nii
        if "operatingIncome" not in row:
            ppnr = row.get("preProvisionIncome")
            if ppnr is not None and prov is not None:
                row["operatingIncome"] = ppnr - prov

        # Insurance derived fields — always override revenue with premiums-based calc
        premiums = row.get("premiumsEarned")
        claims = row.get("policyholderBenefits")
        inv_income = row.get("netInvestmentIncome")
        if premiums is not None:
            ins_rev = premiums + (inv_income or 0)
            row["revenue"] = ins_rev
            if claims is not None:
                row["underwritingIncome"] = premiums - claims

    for row in all_cf:
        ocf = row.get("operatingCashFlow", 0)
        capex = row.get("capitalExpenditure", 0)
        if ocf and capex:
            row["freeCashFlow"] = ocf - abs(capex)
        elif ocf:
            row["freeCashFlow"] = ocf

    # ── FMP-compatible aliases ─────────────────────────────────────────
    _add_fmp_aliases(all_is, all_bs, all_cf)

    return {
        "income_statement": all_is[:quarters],
        "balance_sheet": all_bs[:quarters],
        "cash_flow": all_cf[:quarters],
    }
