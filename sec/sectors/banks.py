"""Bank-specific KPIs from SEC XBRL data.

Covers: commercial banks, savings institutions, bank holding companies.
Key metrics: NIM, efficiency ratio, CET1, NPL ratio, loan-to-deposit,
net charge-off rate, ROA, ROE, fee income mix, reserve coverage.
"""

from __future__ import annotations

from sec.sectors._utils import extract_annual_values, safe_div


def compute_bank_kpis(gaap: dict, years: int = 5) -> dict:
    """Compute comprehensive bank-specific KPIs."""

    # ── Net Interest Income ─────────────────────────────────────────────
    nii = extract_annual_values(gaap, [
        "InterestIncomeExpenseNet",
        "InterestIncomeExpenseAfterProvisionForLoanLoss",
        "NetInterestIncome",
        "InterestRevenueExpenseNet",
    ], years)

    interest_income = extract_annual_values(gaap, [
        "InterestAndDividendIncomeOperating",
        "InterestIncomeOperating",
        "InterestAndFeeIncomeLoansAndLeases",
    ], years)

    interest_expense = extract_annual_values(gaap, [
        "InterestExpense",
        "InterestExpenseDeposits",
    ], years)

    # ── Non-Interest Income (Fee Income) ────────────────────────────────
    non_interest_income = extract_annual_values(gaap, [
        "NoninterestIncome",
    ], years)

    # Fee income breakdown
    service_charges = extract_annual_values(gaap, [
        "FeesAndCommissions",
        "ServiceChargesOnDepositAccounts",
    ], years)

    trading_revenue = extract_annual_values(gaap, [
        "TradingRevenue",
        "TradingGainsLosses",
    ], years)

    investment_banking_fees = extract_annual_values(gaap, [
        "InvestmentBankingRevenue",
        "InvestmentBankingAdvisoryBrokerageAndUnderwritingFeesAndCommissions",
    ], years)

    wealth_management = extract_annual_values(gaap, [
        "AssetManagementFees",
        "InvestmentAdvisoryManagementAndAdministrativeFees",
    ], years)

    mortgage_banking = extract_annual_values(gaap, [
        "MortgageBankingRevenue",
        "GainLossOnSaleOfMortgageLoans",
    ], years)

    # ── Non-Interest Expense ────────────────────────────────────────────
    non_interest_expense = extract_annual_values(gaap, [
        "NoninterestExpense",
    ], years)

    compensation = extract_annual_values(gaap, [
        "LaborAndRelatedExpense",
        "SalariesAndWages",
    ], years)

    # ── Credit Quality ──────────────────────────────────────────────────
    provision = extract_annual_values(gaap, [
        "ProvisionForLoanLeaseAndOtherLosses",
        "ProvisionForLoanAndLeaseLosses",
        "ProvisionForCreditLosses",
    ], years)

    allowance_for_losses = extract_annual_values(gaap, [
        "FinancingReceivableAllowanceForCreditLosses",
        "AllowanceForLoanAndLeaseLosses",
        "AllowanceForLoanAndLeaseLossesRealEstate",
        "AllowanceForNotesAndLoansReceivableCurrent",
        "AllowanceForCreditLossesOnFinancingReceivables",
    ], years)

    npl = extract_annual_values(gaap, [
        "FinancingReceivableNonaccrual",
        "FinancingReceivableRecordedInvestmentNonaccrualStatus",
        "NonperformingFinancingReceivable",
        "FinancingReceivableNonaccrualNoAllowance",
        "ImpairedFinancingReceivableRecordedInvestment",
    ], years)

    charge_offs = extract_annual_values(gaap, [
        "AllowanceForLoanAndLeaseLossesWriteoffsNet",
        "AllowanceForLoanAndLeaseLossesChargeOffsNet",
        "FinancingReceivableAllowanceForCreditLossesWriteOffs",
        "AllowanceForCreditLossesOnFinancingReceivablesChargeOffsNet",
        "ChargeOffsNet",
    ], years)

    # ── Loan Portfolio ──────────────────────────────────────────────────
    total_loans = extract_annual_values(gaap, [
        "FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLoss",
        "LoansAndLeasesReceivableNetReportedAmount",
        "LoansAndLeasesReceivableNetOfDeferredIncome",
        "NotesReceivableNet",
    ], years)

    commercial_loans = extract_annual_values(gaap, [
        "FinancingReceivableRecordedInvestmentCommercialRealEstate",
        "LoansAndLeasesReceivableCommercial",
    ], years)

    consumer_loans = extract_annual_values(gaap, [
        "FinancingReceivableRecordedInvestmentConsumer",
        "LoansAndLeasesReceivableConsumer",
    ], years)

    mortgage_loans = extract_annual_values(gaap, [
        "FinancingReceivableRecordedInvestmentResidentialMortgage",
        "LoansAndLeasesReceivableRealEstate",
    ], years)

    # ── Deposits ────────────────────────────────────────────────────────
    total_deposits = extract_annual_values(gaap, [
        "Deposits",
    ], years)

    non_interest_deposits = extract_annual_values(gaap, [
        "NoninterestBearingDepositLiabilities",
    ], years)

    # ── Balance Sheet ───────────────────────────────────────────────────
    total_assets = extract_annual_values(gaap, [
        "Assets",
    ], years)

    equity = extract_annual_values(gaap, [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ], years)

    goodwill = extract_annual_values(gaap, [
        "Goodwill",
    ], years)

    intangibles = extract_annual_values(gaap, [
        "IntangibleAssetsNetExcludingGoodwill",
    ], years)

    net_income = extract_annual_values(gaap, [
        "NetIncomeLoss",
    ], years)

    # Securities
    securities_afs = extract_annual_values(gaap, [
        "AvailableForSaleSecuritiesDebtSecurities",
        "AvailableForSaleSecurities",
    ], years)

    securities_htm = extract_annual_values(gaap, [
        "HeldToMaturitySecurities",
        "HeldToMaturitySecuritiesAmortizedCostBeforeOtherThanTemporaryImpairment",
    ], years)

    # ── Capital Ratios (often in custom bank XBRL extensions) ──────────
    # Banks report these under various us-gaap tags AND custom extensions.
    # The merged gaap dict (us-gaap + custom namespaces) lets us find them.
    cet1 = extract_annual_values(gaap, [
        "CommonEquityTier1CapitalRatio",
        "CommonEquityTier1RiskBasedCapitalRatio",
        "CommonEquityTier1CapitalToRiskWeightedAssetsRatio",
        "CommonEquityTier1RatioBaselIIIStandardizedApproach",
        "CommonEquityTier1RatioBaselIIIAdvancedApproach",
        "CET1RiskBasedCapitalRatio",
        "CommonEquityTier1ToRiskWeightedAssets",
    ], years)

    tier1_ratio = extract_annual_values(gaap, [
        "Tier1RiskBasedCapitalRatio",
        "CapitalAdequacyTier1RiskBasedCapitalRatio",
        "Tier1CapitalToRiskWeightedAssetsRatio",
        "Tier1RiskBasedCapitalToRiskWeightedAssets",
    ], years)

    total_capital_ratio = extract_annual_values(gaap, [
        "TotalRiskBasedCapitalRatio",
        "CapitalAdequacyTotalRiskBasedCapitalRatio",
        "TotalCapitalToRiskWeightedAssetsRatio",
        "TotalRiskBasedCapitalToRiskWeightedAssets",
    ], years)

    leverage_ratio = extract_annual_values(gaap, [
        "Tier1LeverageRatio",
        "CapitalAdequacyTier1LeverageRatio",
        "Tier1LeverageRatioToAverageAssets",
        "SupplementaryLeverageRatio",
    ], years)

    # Shares
    shares = extract_annual_values(gaap, [
        "CommonStockSharesOutstanding",
    ], years)

    # ── Build raw KPIs ──────────────────────────────────────────────────
    kpis = {
        "netInterestIncome": nii,
        "interestIncome": interest_income,
        "interestExpense": interest_expense,
        "nonInterestIncome": non_interest_income,
        "nonInterestExpense": non_interest_expense,
        "provisionForCreditLosses": provision,
        "allowanceForLosses": allowance_for_losses,
        "totalLoans": total_loans,
        "totalDeposits": total_deposits,
        "nonPerformingLoans": npl,
        "netChargeOffs": charge_offs,
        # Capital ratios
        "cet1Ratio": cet1,
        "tier1Ratio": tier1_ratio,
        "totalCapitalRatio": total_capital_ratio,
        "leverageRatio": leverage_ratio,
        # Fee income breakdown
        "serviceCharges": service_charges,
        "tradingRevenue": trading_revenue,
        "investmentBankingFees": investment_banking_fees,
        "wealthManagement": wealth_management,
        "mortgageBanking": mortgage_banking,
        # Loan composition
        "commercialLoans": commercial_loans,
        "consumerLoans": consumer_loans,
        "mortgageLoans": mortgage_loans,
        # Securities
        "securitiesAFS": securities_afs,
        "securitiesHTM": securities_htm,
    }

    # ── Computed Ratios ─────────────────────────────────────────────────
    computed = []
    nii_by_date = {e["date"]: e["val"] for e in nii}
    ii_by_date = {e["date"]: e["val"] for e in interest_income}
    ie_by_date = {e["date"]: e["val"] for e in interest_expense}
    nie_by_date = {e["date"]: e["val"] for e in non_interest_expense}
    nii2_by_date = {e["date"]: e["val"] for e in non_interest_income}
    loans_by_date = {e["date"]: e["val"] for e in total_loans}
    deps_by_date = {e["date"]: e["val"] for e in total_deposits}
    npl_by_date = {e["date"]: e["val"] for e in npl}
    assets_by_date = {e["date"]: e["val"] for e in total_assets}
    equity_by_date = {e["date"]: e["val"] for e in equity}
    ni_by_date = {e["date"]: e["val"] for e in net_income}
    prov_by_date = {e["date"]: e["val"] for e in provision}
    co_by_date = {e["date"]: e["val"] for e in charge_offs}
    allow_by_date = {e["date"]: e["val"] for e in allowance_for_losses}
    goodwill_by_date = {e["date"]: e["val"] for e in goodwill}
    intang_by_date = {e["date"]: e["val"] for e in intangibles}
    comp_by_date = {e["date"]: e["val"] for e in compensation}
    shares_by_date = {e["date"]: e["val"] for e in shares}
    nid_by_date = {e["date"]: e["val"] for e in non_interest_deposits}

    # Use the union of all major metric dates to maximize coverage.
    # Some banks stop reporting certain tags (e.g. loans) in some years.
    all_dates = (
        set(nii_by_date.keys()) | set(assets_by_date.keys())
        | set(loans_by_date.keys()) | set(deps_by_date.keys())
        | set(nie_by_date.keys()) | set(ni_by_date.keys())
    )
    dates = sorted(all_dates, reverse=True)[:years]

    for date in dates:
        ni_val = nii_by_date.get(date)
        nie = nie_by_date.get(date)
        nii2 = nii2_by_date.get(date)
        loans = loans_by_date.get(date)
        deps = deps_by_date.get(date)
        npl_val = npl_by_date.get(date)
        assets = assets_by_date.get(date)
        eq = equity_by_date.get(date)
        ni = ni_by_date.get(date)
        prov = prov_by_date.get(date)
        co = co_by_date.get(date)
        allow = allow_by_date.get(date)
        gw = goodwill_by_date.get(date)
        intang = intang_by_date.get(date)
        comp = comp_by_date.get(date)
        shr = shares_by_date.get(date)
        nid = nid_by_date.get(date)

        # Efficiency ratio = non-interest expense / (NII + non-interest income)
        total_revenue = (ni_val or 0) + (nii2 or 0)
        efficiency = safe_div(nie, total_revenue) if total_revenue else None

        # NIM = NII / earning assets (proxy: total assets * 0.85)
        earning_assets = assets * 0.85 if assets else None
        nim = safe_div(ni_val, earning_assets)

        # Loan-to-deposit ratio
        ldr = safe_div(loans, deps)

        # NPL ratio = NPLs / total loans
        npl_ratio = safe_div(npl_val, loans)

        # Net charge-off rate = net charge-offs / avg loans
        nco_rate = safe_div(co, loans)

        # Reserve coverage = allowance / NPLs
        reserve_coverage = safe_div(allow, npl_val)

        # Allowance to loans ratio
        allow_to_loans = safe_div(allow, loans)

        # Provision to loans ratio
        prov_to_loans = safe_div(prov, loans)

        # ROA = net income / avg total assets
        roa = safe_div(ni, assets)

        # ROE = net income / equity
        roe = safe_div(ni, eq)

        # Tangible book value per share
        tbv = None
        if eq and shr:
            tangible_eq = eq - (gw or 0) - (intang or 0)
            tbv = tangible_eq / shr if shr else None

        # Fee income ratio = non-interest income / total revenue
        fee_ratio = safe_div(nii2, total_revenue) if total_revenue else None

        # Compensation ratio = comp / total revenue
        comp_ratio = safe_div(comp, total_revenue) if total_revenue else None

        # Non-interest deposit ratio (funding quality)
        nid_ratio = safe_div(nid, deps)

        # Cost of deposits = interest expense / total deposits
        ie = ie_by_date.get(date)
        cost_of_deposits = safe_div(ie, deps)

        # Yield on earning assets = interest income / earning assets
        ii = ii_by_date.get(date)
        yield_on_assets = safe_div(ii, earning_assets)

        # Net interest spread = yield on assets - cost of funds
        net_spread = None
        if yield_on_assets is not None and cost_of_deposits is not None:
            net_spread = yield_on_assets - cost_of_deposits

        computed.append({
            "date": date,
            # Core banking ratios
            "efficiencyRatio": round(efficiency, 4) if efficiency is not None else None,
            "netInterestMargin": round(nim, 4) if nim is not None else None,
            "loanToDepositRatio": round(ldr, 4) if ldr is not None else None,
            "nplRatio": round(npl_ratio, 4) if npl_ratio is not None else None,
            "netChargeOffRate": round(nco_rate, 4) if nco_rate is not None else None,
            # Credit quality
            "reserveCoverage": round(reserve_coverage, 4) if reserve_coverage is not None else None,
            "allowanceToLoans": round(allow_to_loans, 4) if allow_to_loans is not None else None,
            "provisionToLoans": round(prov_to_loans, 4) if prov_to_loans is not None else None,
            # Returns
            "roa": round(roa, 4) if roa is not None else None,
            "roe": round(roe, 4) if roe is not None else None,
            "tangibleBookValuePerShare": round(tbv, 2) if tbv is not None else None,
            # Revenue mix
            "feeIncomeRatio": round(fee_ratio, 4) if fee_ratio is not None else None,
            "compensationRatio": round(comp_ratio, 4) if comp_ratio is not None else None,
            # Funding
            "nonInterestDepositRatio": round(nid_ratio, 4) if nid_ratio is not None else None,
            "costOfDeposits": round(cost_of_deposits, 4) if cost_of_deposits is not None else None,
            "yieldOnEarningAssets": round(yield_on_assets, 4) if yield_on_assets is not None else None,
            "netInterestSpread": round(net_spread, 4) if net_spread is not None else None,
        })

    kpis["computedRatios"] = computed
    return kpis
