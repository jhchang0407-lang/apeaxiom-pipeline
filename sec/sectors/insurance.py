"""Insurance-specific KPIs from SEC XBRL data.

Covers: P&C, life, health, reinsurance, insurance brokers.
Key metrics: combined ratio, loss ratio, expense ratio, premiums growth,
investment yield, reserve development, float.
"""

from __future__ import annotations

from sec.sectors._utils import extract_annual_values, safe_div


def compute_insurance_kpis(gaap: dict, years: int = 5) -> dict:
    # ── Premiums ────────────────────────────────────────────────────────
    premiums_earned = extract_annual_values(gaap, [
        "PremiumsEarnedNet",
        "PremiumsEarned",
    ], years + 1)

    premiums_written = extract_annual_values(gaap, [
        "PremiumsWrittenNet",
        "PremiumsWrittenGross",
        "DirectPremiumsWritten",
    ], years + 1)

    premiums_ceded = extract_annual_values(gaap, [
        "CededPremiumsWritten",
        "CededPremiumsEarned",
    ], years)

    # ── Losses & Claims ─────────────────────────────────────────────────
    claims = extract_annual_values(gaap, [
        "PolicyholderBenefitsAndClaimsIncurredNet",
        "PolicyholderBenefitsAndClaimsIncurred",
        "IncurredClaimsPropertyCasualtyAndLiability",
        "BenefitsLossesAndExpenses",
    ], years)

    # ── Expenses ────────────────────────────────────────────────────────
    underwriting_expense = extract_annual_values(gaap, [
        "DeferredPolicyAcquisitionCostAmortizationExpense",
        "OtherUnderwritingExpense",
        "PolicyAcquisitionCosts",
    ], years)

    # ── Investment Income ───────────────────────────────────────────────
    investment_income = extract_annual_values(gaap, [
        "NetInvestmentIncome",
        "InvestmentIncomeNet",
        "InvestmentIncomeInterestAndDividend",
    ], years)

    realized_gains = extract_annual_values(gaap, [
        "RealizedInvestmentGainsLosses",
        "GainLossOnInvestments",
    ], years)

    total_investments = extract_annual_values(gaap, [
        "Investments",
        "AvailableForSaleSecuritiesDebtSecurities",
    ], years)

    # ── Reserves ────────────────────────────────────────────────────────
    loss_reserves = extract_annual_values(gaap, [
        "LiabilityForFuturePolicyBenefitsAndUnpaidClaimsAndClaimsAdjustmentExpense",
        "LiabilityForUnpaidClaimsAndClaimsAdjustmentExpenseNet",
        "LiabilityForClaimsAndClaimsAdjustmentExpense",
    ], years)

    unearned_premiums = extract_annual_values(gaap, [
        "UnearnedPremiums",
        "UnearnedPremiumsPolicy",
    ], years)

    # ── Balance Sheet ───────────────────────────────────────────────────
    equity = extract_annual_values(gaap, [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ], years)

    net_income = extract_annual_values(gaap, [
        "NetIncomeLoss",
    ], years)

    # ── Build raw KPIs ──────────────────────────────────────────────────
    kpis = {
        "premiumsEarned": premiums_earned[:years],
        "premiumsWritten": premiums_written[:years],
        "premiumsCeded": premiums_ceded,
        "claims": claims,
        "underwritingExpense": underwriting_expense,
        "investmentIncome": investment_income,
        "realizedGains": realized_gains,
        "lossReserves": loss_reserves,
        "unearnedPremiums": unearned_premiums,
        "totalInvestments": total_investments,
    }

    # ── Computed Ratios ─────────────────────────────────────────────────
    computed = []
    pe_by_date = {e["date"]: e["val"] for e in premiums_earned}
    pw_by_date = {e["date"]: e["val"] for e in premiums_written}
    cl_by_date = {e["date"]: e["val"] for e in claims}
    ue_by_date = {e["date"]: e["val"] for e in underwriting_expense}
    ii_by_date = {e["date"]: e["val"] for e in investment_income}
    ti_by_date = {e["date"]: e["val"] for e in total_investments}
    ni_by_date = {e["date"]: e["val"] for e in net_income}
    eq_by_date = {e["date"]: e["val"] for e in equity}
    reserves_by_date = {e["date"]: e["val"] for e in loss_reserves}

    pe_dates = sorted(pe_by_date.keys(), reverse=True)
    for i, date in enumerate(pe_dates[:years]):
        pe = pe_by_date.get(date)
        pw = pw_by_date.get(date)
        cl = cl_by_date.get(date)
        ue = ue_by_date.get(date)
        ii = ii_by_date.get(date)
        ti = ti_by_date.get(date)
        ni = ni_by_date.get(date)
        eq = eq_by_date.get(date)
        reserves = reserves_by_date.get(date)

        # Loss ratio = claims / premiums earned
        loss_ratio = safe_div(cl, pe)

        # Expense ratio = underwriting expense / premiums earned
        expense_ratio = safe_div(ue, pe)

        # Combined ratio = loss ratio + expense ratio (< 1.0 = underwriting profit)
        combined_ratio = None
        if loss_ratio is not None and expense_ratio is not None:
            combined_ratio = loss_ratio + expense_ratio
        elif loss_ratio is not None:
            combined_ratio = loss_ratio  # partial

        # Investment yield = investment income / total investments
        inv_yield = safe_div(ii, ti)

        # ROE
        roe = safe_div(ni, eq)

        # Premiums to equity (leverage measure)
        premiums_to_equity = safe_div(pw or pe, eq)

        # Premium growth
        prior_date = pe_dates[i + 1] if i + 1 < len(pe_dates) else None
        prior_pe = pe_by_date.get(prior_date) if prior_date else None
        pe_growth = safe_div((pe - prior_pe), abs(prior_pe)) if pe and prior_pe else None

        prior_pw = pw_by_date.get(prior_date) if prior_date else None
        pw_growth = safe_div((pw - prior_pw), abs(prior_pw)) if pw and prior_pw else None

        # Reserve to premium ratio
        reserve_to_premium = safe_div(reserves, pe)

        # Float (investable assets) — approximated by loss reserves
        float_val = reserves if reserves else None

        # Float leverage = float / equity
        float_leverage = safe_div(float_val, eq)

        computed.append({
            "date": date,
            # Core insurance ratios
            "lossRatio": round(loss_ratio, 4) if loss_ratio is not None else None,
            "expenseRatio": round(expense_ratio, 4) if expense_ratio is not None else None,
            "combinedRatio": round(combined_ratio, 4) if combined_ratio is not None else None,
            # Investment
            "investmentYield": round(inv_yield, 4) if inv_yield is not None else None,
            # Returns
            "roe": round(roe, 4) if roe is not None else None,
            # Growth
            "premiumsEarnedGrowth": round(pe_growth, 4) if pe_growth is not None else None,
            "premiumsWrittenGrowth": round(pw_growth, 4) if pw_growth is not None else None,
            # Leverage & reserves
            "premiumsToEquity": round(premiums_to_equity, 4) if premiums_to_equity is not None else None,
            "reserveToPremium": round(reserve_to_premium, 4) if reserve_to_premium is not None else None,
            "floatLeverage": round(float_leverage, 4) if float_leverage is not None else None,
        })

    kpis["computedRatios"] = computed
    return kpis
