"""Sector-Specific Research Prompts & KPI Configurations for Quarterly Pipeline.

Each sector family gets:
  - research_focus: key metrics the research agent must hunt for
  - extra_schema: additional JSON schema fields beyond the common schema
  - extraction_rules: sector-specific guidance for the research agent
  - writer_guidance: sector-specific prose guidance for the writer
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════════════
# SECTOR CONFIGURATIONS
# ═══════════════════════════════════════════════════════════════════════

SECTOR_CONFIGS: dict[str, dict] = {

    # ── BANKING ───────────────────────────────────────────────────────
    "banking": {
        "research_focus": (
            "Net interest income (NII), net interest margin (NIM), "
            "efficiency ratio, provision for credit losses (PCL), "
            "non-performing loan (NPL) ratio, CET1 capital ratio, "
            "net charge-off (NCO) rate, loan growth YoY, deposit costs, "
            "fee income / noninterest revenue breakdown."
        ),
        "extra_schema": {
            "sector_kpis": {
                "type": "object",
                "properties": {
                    "net_interest_income_m": {"type": ["number", "null"], "description": "NII in $M"},
                    "net_interest_margin_pct": {"type": ["number", "null"], "description": "NIM as %"},
                    "efficiency_ratio_pct": {"type": ["number", "null"], "description": "Efficiency ratio as %"},
                    "provision_for_credit_losses_m": {"type": ["number", "null"], "description": "PCL in $M"},
                    "cet1_ratio_pct": {"type": ["number", "null"], "description": "CET1 capital ratio as %"},
                    "nco_rate_pct": {"type": ["number", "null"], "description": "Net charge-off rate as %"},
                    "npl_ratio_pct": {"type": ["number", "null"], "description": "NPL ratio as %"},
                    "loan_growth_yoy_pct": {"type": ["number", "null"], "description": "Total loan growth YoY %"},
                    "noninterest_revenue_m": {"type": ["number", "null"], "description": "Noninterest / fee revenue in $M"},
                },
                "required": ["net_interest_income_m", "net_interest_margin_pct", "efficiency_ratio_pct",
                             "provision_for_credit_losses_m", "cet1_ratio_pct"],
                "additionalProperties": False,
            }
        },
        "extraction_rules": (
            "For banks: use total net revenue (NII + noninterest revenue) as the headline revenue figure. "
            "Do NOT use managed revenue or taxable-equivalent adjusted figures. "
            "NIM should be reported on a GAAP basis unless only TE-NIM is available, in which case note it. "
            "Provision for credit losses is the income statement charge, not the reserve balance. "
            "CET1 ratio should be the transitional or fully-phased figure as reported."
        ),
        "writer_guidance": (
            "Lead with NIM trajectory and its relationship to the rate environment. "
            "Credit quality (PCL, NCO, NPL) is the primary risk indicator — frame it in context of the credit cycle. "
            "Discuss fee income diversification and whether the bank is pivoting toward capital-light revenue. "
            "CET1 provides context for capital return capacity (buybacks, dividends)."
        ),
    },

    # ── INSURANCE ─────────────────────────────────────────────────────
    "insurance": {
        "research_focus": (
            "Combined ratio, loss ratio, expense ratio, net premiums written growth, "
            "investment income / portfolio yield, book value per share, "
            "reserve development (favorable/adverse), catastrophe losses."
        ),
        "extra_schema": {
            "sector_kpis": {
                "type": "object",
                "properties": {
                    "combined_ratio_pct": {"type": ["number", "null"], "description": "Combined ratio as %"},
                    "loss_ratio_pct": {"type": ["number", "null"], "description": "Loss ratio as %"},
                    "expense_ratio_pct": {"type": ["number", "null"], "description": "Expense ratio as %"},
                    "net_premiums_written_m": {"type": ["number", "null"], "description": "Net premiums written in $M"},
                    "net_premiums_written_growth_pct": {"type": ["number", "null"], "description": "NPW growth YoY %"},
                    "investment_income_m": {"type": ["number", "null"], "description": "Net investment income in $M"},
                    "book_value_per_share": {"type": ["number", "null"], "description": "Book value per share $"},
                    "cat_losses_m": {"type": ["number", "null"], "description": "Catastrophe losses in $M (P&C only)"},
                },
                "required": ["combined_ratio_pct", "loss_ratio_pct", "net_premiums_written_m"],
                "additionalProperties": False,
            }
        },
        "extraction_rules": (
            "For insurers: use total revenues including net premiums earned as headline revenue. "
            "Combined ratio = loss ratio + expense ratio; verify the math adds up. "
            "Distinguish between P&C and Life/Health metrics where applicable. "
            "Reserve development (favorable = prior year reserves released) is a key quality indicator."
        ),
        "writer_guidance": (
            "Lead with combined ratio trajectory — is the underwriting cycle hardening or softening? "
            "Cat losses provide context for whether the combined ratio is representative of underlying profitability. "
            "Investment income yield in context of portfolio duration and rate environment. "
            "Premium growth signals pricing power vs. market share trade-off."
        ),
    },

    # ── REITs ─────────────────────────────────────────────────────────
    "reits": {
        "research_focus": (
            "FFO per share, AFFO per share, same-store NOI growth, "
            "occupancy rate, weighted average lease term (WALT), "
            "lease spreads (cash and GAAP), acquisition/disposition activity, "
            "debt maturity profile, dividend per share."
        ),
        "extra_schema": {
            "sector_kpis": {
                "type": "object",
                "properties": {
                    "ffo_per_share": {"type": ["number", "null"], "description": "FFO per diluted share $"},
                    "affo_per_share": {"type": ["number", "null"], "description": "AFFO per diluted share $"},
                    "same_store_noi_growth_pct": {"type": ["number", "null"], "description": "Same-store NOI growth YoY %"},
                    "occupancy_pct": {"type": ["number", "null"], "description": "Portfolio occupancy rate %"},
                    "lease_spread_cash_pct": {"type": ["number", "null"], "description": "Cash lease spread on renewals %"},
                    "lease_spread_gaap_pct": {"type": ["number", "null"], "description": "GAAP lease spread on renewals %"},
                    "dividend_per_share": {"type": ["number", "null"], "description": "Quarterly dividend per share $"},
                },
                "required": ["ffo_per_share", "same_store_noi_growth_pct", "occupancy_pct"],
                "additionalProperties": False,
            }
        },
        "extraction_rules": (
            "For REITs: use total revenues as headline revenue, NOT FFO/AFFO. "
            "FFO and AFFO per share are the primary earnings metrics — report both if available. "
            "Do NOT use GAAP EPS as the primary metric (it includes depreciation which distorts REIT earnings). "
            "Occupancy should be end-of-period, not average."
        ),
        "writer_guidance": (
            "Lead with same-store NOI growth — this is the organic growth indicator. "
            "Occupancy and lease spreads signal pricing power and demand. "
            "FFO vs AFFO gap reveals maintenance capex intensity. "
            "Connect acquisition/disposition activity to portfolio strategy and cap rate environment."
        ),
    },

    # ── TECHNOLOGY ────────────────────────────────────────────────────
    "technology": {
        "research_focus": (
            "Revenue growth by product line (cloud/SaaS/license/hardware/services), "
            "ARR or RPO if SaaS, net dollar retention rate (NDR/DBNRR), "
            "rule of 40 (revenue growth + FCF margin), SBC as % of revenue, "
            "customer count / large deal metrics, AI-related revenue or commentary."
        ),
        "extra_schema": {
            "sector_kpis": {
                "type": "object",
                "properties": {
                    "arr_m": {"type": ["number", "null"], "description": "Annual Recurring Revenue in $M (SaaS only)"},
                    "rpo_m": {"type": ["number", "null"], "description": "Remaining Performance Obligations in $M"},
                    "ndr_pct": {"type": ["number", "null"], "description": "Net Dollar Retention / DBNRR %"},
                    "rule_of_40": {"type": ["number", "null"], "description": "Revenue growth % + FCF margin %"},
                    "sbc_pct_of_revenue": {"type": ["number", "null"], "description": "Stock-based comp as % of revenue"},
                    "crpo_m": {"type": ["number", "null"], "description": "Current RPO in $M (next 12 months)"},
                    "customer_count": {"type": ["number", "null"], "description": "Total customer count or >$100K customers"},
                },
                "required": [],
                "additionalProperties": False,
            }
        },
        "extraction_rules": (
            "For technology companies: always report subscription/cloud vs. one-time/license revenue split if available. "
            "ARR and RPO are critical forward indicators for SaaS — report even if mentioned in call but not press release. "
            "NDR/DBNRR above 120% signals strong expansion, below 100% signals churn. "
            "If AI revenue or AI-related metrics are disclosed, always include them."
        ),
        "writer_guidance": (
            "Lead with the growth driver — is it new logos, expansion, or pricing? "
            "ARR/RPO trajectory matters more than current-quarter revenue for SaaS. "
            "SBC as % of revenue is the hidden dilution cost — flag if rising. "
            "AI commentary is market-moving — what's real revenue vs. aspiration?"
        ),
    },

    # ── ENERGY ────────────────────────────────────────────────────────
    "energy": {
        "research_focus": (
            "Production volumes (BOE/d, MCF/d), realized prices per BOE/MCF, "
            "finding & development costs, reserve replacement ratio, "
            "breakeven price per barrel, capital discipline metrics, "
            "free cash flow yield, shareholder return framework (buybacks + dividends)."
        ),
        "extra_schema": {
            "sector_kpis": {
                "type": "object",
                "properties": {
                    "production_boe_d": {"type": ["number", "null"], "description": "Production in BOE/d (or MCF/d for gas)"},
                    "realized_price_per_boe": {"type": ["number", "null"], "description": "Realized price per BOE $"},
                    "capex_m": {"type": ["number", "null"], "description": "Capital expenditure in $M"},
                    "fcf_m": {"type": ["number", "null"], "description": "Free cash flow in $M"},
                    "shareholder_returns_m": {"type": ["number", "null"], "description": "Total shareholder returns (buybacks + dividends) in $M"},
                },
                "required": [],
                "additionalProperties": False,
            }
        },
        "extraction_rules": (
            "For energy companies: production volumes in BOE/d (barrels of oil equivalent per day). "
            "Realized prices must match the production unit. "
            "Distinguish upstream vs. downstream vs. midstream results if the company is integrated. "
            "Capital discipline (capex vs. cash flow) is the key theme in current cycle."
        ),
        "writer_guidance": (
            "Lead with production volumes and realized pricing — these are the top-line drivers. "
            "Capital discipline and FCF conversion signal management credibility. "
            "Shareholder return framework (variable dividend, buyback, debt reduction) is the equity story. "
            "Breakeven economics determine margin of safety at lower commodity prices."
        ),
    },

    # ── HEALTHCARE ────────────────────────────────────────────────────
    "healthcare": {
        "research_focus": (
            "Revenue by therapeutic area or product line, new patient starts / prescription trends, "
            "pipeline updates (approvals, trial data, regulatory milestones), "
            "R&D expense as % of revenue, patent cliff exposure, "
            "biosimilar / generic competition impact."
        ),
        "extra_schema": {
            "sector_kpis": {
                "type": "object",
                "properties": {
                    "rd_pct_of_revenue": {"type": ["number", "null"], "description": "R&D expense as % of revenue"},
                    "pipeline_milestone": {"type": ["string", "null"], "description": "Most significant pipeline event this quarter"},
                    "key_product_revenue_m": {"type": ["number", "null"], "description": "Revenue of largest product/franchise in $M"},
                    "key_product_growth_pct": {"type": ["number", "null"], "description": "Growth of largest product/franchise YoY %"},
                },
                "required": [],
                "additionalProperties": False,
            }
        },
        "extraction_rules": (
            "For healthcare: distinguish pharmaceutical, biotech, medical device, and services revenue. "
            "Pipeline catalysts (Phase 3 data, FDA approvals, PDUFA dates) are material non-financial events. "
            "Patent expirations and LOE (loss of exclusivity) impact forward revenue trajectory."
        ),
        "writer_guidance": (
            "Lead with the key growth franchise and whether it's accelerating or decelerating. "
            "Pipeline catalysts are forward-looking value drivers — frame them with timelines and probability. "
            "R&D intensity signals reinvestment rate and future growth potential. "
            "Generic/biosimilar exposure is the key risk for mature pharma."
        ),
    },

    # ── RETAIL ────────────────────────────────────────────────────────
    "retail": {
        "research_focus": (
            "Same-store sales (comp sales) growth, traffic vs. ticket breakdown, "
            "e-commerce penetration / digital sales growth, inventory levels vs. sales growth, "
            "store count changes (openings, closures, remodels), "
            "gross margin trend (shrink, promotion, freight impact)."
        ),
        "extra_schema": {
            "sector_kpis": {
                "type": "object",
                "properties": {
                    "comp_sales_growth_pct": {"type": ["number", "null"], "description": "Same-store / comp sales growth %"},
                    "traffic_growth_pct": {"type": ["number", "null"], "description": "Store traffic growth %"},
                    "ticket_growth_pct": {"type": ["number", "null"], "description": "Average ticket / basket growth %"},
                    "ecommerce_growth_pct": {"type": ["number", "null"], "description": "E-commerce / digital sales growth %"},
                    "ecommerce_pct_of_sales": {"type": ["number", "null"], "description": "E-commerce as % of total sales"},
                    "inventory_growth_pct": {"type": ["number", "null"], "description": "Inventory growth YoY %"},
                    "store_count": {"type": ["number", "null"], "description": "Total store count end of quarter"},
                },
                "required": ["comp_sales_growth_pct"],
                "additionalProperties": False,
            }
        },
        "extraction_rules": (
            "For retailers: comp sales (same-store sales) is the primary organic growth metric. "
            "Traffic vs. ticket decomposition reveals whether growth is volume or pricing-driven. "
            "Inventory growth should be compared to sales growth — divergence signals clearance risk."
        ),
        "writer_guidance": (
            "Lead with comp sales trajectory — is momentum building or fading? "
            "Traffic vs. ticket split reveals the quality of growth. "
            "Inventory-to-sales ratio trend is an early warning indicator. "
            "Gross margin bridge (shrink, freight, promotions, mix) drives profitability narrative."
        ),
    },

    # ── INDUSTRIALS ───────────────────────────────────────────────────
    "industrials": {
        "research_focus": (
            "Organic revenue growth (ex-acquisitions/FX), book-to-bill ratio, "
            "backlog / order pipeline, price vs. volume decomposition, "
            "margin expansion drivers (restructuring, price/cost, mix), "
            "free cash flow conversion."
        ),
        "extra_schema": {
            "sector_kpis": {
                "type": "object",
                "properties": {
                    "organic_growth_pct": {"type": ["number", "null"], "description": "Organic revenue growth (ex-M&A/FX) %"},
                    "book_to_bill": {"type": ["number", "null"], "description": "Book-to-bill ratio (orders / revenue)"},
                    "backlog_m": {"type": ["number", "null"], "description": "Order backlog in $M"},
                    "fcf_conversion_pct": {"type": ["number", "null"], "description": "FCF conversion (FCF / net income) %"},
                },
                "required": [],
                "additionalProperties": False,
            }
        },
        "extraction_rules": (
            "For industrials: organic growth (excluding M&A and currency) is the key top-line metric. "
            "Book-to-bill above 1.0 signals accelerating demand; below 1.0 signals deceleration. "
            "Backlog provides visibility into future quarters."
        ),
        "writer_guidance": (
            "Lead with organic growth and order trends — these signal cycle positioning. "
            "Book-to-bill ratio is the forward indicator. "
            "Margin bridge (price/cost, restructuring, mix, volume leverage) explains profitability. "
            "FCF conversion quality distinguishes earnings quality."
        ),
    },

    # ── CONSUMER STAPLES ──────────────────────────────────────────────
    "consumer_staples": {
        "research_focus": (
            "Organic sales growth (price vs. volume decomposition), "
            "gross margin trend and input cost impact, "
            "market share gains/losses in key categories, "
            "emerging market vs. developed market growth split."
        ),
        "extra_schema": {
            "sector_kpis": {
                "type": "object",
                "properties": {
                    "organic_sales_growth_pct": {"type": ["number", "null"], "description": "Organic sales growth %"},
                    "price_contribution_pct": {"type": ["number", "null"], "description": "Price contribution to organic growth %"},
                    "volume_contribution_pct": {"type": ["number", "null"], "description": "Volume/mix contribution to organic growth %"},
                },
                "required": [],
                "additionalProperties": False,
            }
        },
        "extraction_rules": (
            "For consumer staples: organic growth (ex-FX, ex-M&A) is the primary metric. "
            "Price vs. volume decomposition is critical — pure pricing growth without volume is unsustainable. "
            "Input cost trends (commodities, packaging, freight) drive gross margin trajectory."
        ),
        "writer_guidance": (
            "Lead with organic growth and whether it's price-led or volume-led. "
            "Volume elasticity — are consumers trading down? Is the brand maintaining share? "
            "Gross margin bridge connects input costs to profitability. "
            "Emerging market growth provides the secular growth offset to mature markets."
        ),
    },

    # ── UTILITIES ─────────────────────────────────────────────────────
    "utilities": {
        "research_focus": (
            "Rate base growth, authorized ROE vs. achieved ROE, "
            "regulatory rate case outcomes, capital expenditure plan, "
            "load growth / customer growth, renewable energy capacity additions, "
            "FFO-to-debt ratio, dividend growth."
        ),
        "extra_schema": {
            "sector_kpis": {
                "type": "object",
                "properties": {
                    "rate_base_b": {"type": ["number", "null"], "description": "Total rate base in $B"},
                    "rate_base_growth_pct": {"type": ["number", "null"], "description": "Rate base growth YoY %"},
                    "authorized_roe_pct": {"type": ["number", "null"], "description": "Authorized ROE %"},
                    "load_growth_pct": {"type": ["number", "null"], "description": "Electric load growth %"},
                    "capex_m": {"type": ["number", "null"], "description": "Capital expenditure in $M"},
                    "ffo_to_debt_pct": {"type": ["number", "null"], "description": "FFO-to-debt ratio %"},
                },
                "required": [],
                "additionalProperties": False,
            }
        },
        "extraction_rules": (
            "For utilities: EPS is the primary earnings metric (unlike REITs which use FFO). "
            "Rate base is the asset base on which the utility earns its authorized return. "
            "Regulatory outcomes (rate cases, riders, trackers) are the key growth catalysts."
        ),
        "writer_guidance": (
            "Lead with rate base growth and regulatory trajectory — this IS the growth story. "
            "Authorized vs. achieved ROE gap reveals regulatory relationship quality. "
            "Load growth (especially data center-driven) is the emerging secular catalyst. "
            "FFO-to-debt signals credit quality and ability to fund capex without equity issuance."
        ),
    },

    # ── TELECOM ───────────────────────────────────────────────────────
    "telecom": {
        "research_focus": (
            "Subscriber net adds (postpaid/prepaid/broadband), ARPU trends, "
            "churn rate, service revenue growth, EBITDA margin, "
            "capital intensity (capex as % of revenue), free cash flow, "
            "spectrum/network investment."
        ),
        "extra_schema": {
            "sector_kpis": {
                "type": "object",
                "properties": {
                    "postpaid_phone_net_adds": {"type": ["number", "null"], "description": "Postpaid phone net additions"},
                    "arpu": {"type": ["number", "null"], "description": "Average Revenue Per User $"},
                    "churn_pct": {"type": ["number", "null"], "description": "Monthly churn rate %"},
                    "service_revenue_growth_pct": {"type": ["number", "null"], "description": "Service revenue growth YoY %"},
                },
                "required": [],
                "additionalProperties": False,
            }
        },
        "extraction_rules": (
            "For telecoms: postpaid phone net adds is the primary subscriber quality metric. "
            "Service revenue (ex-equipment) is the core recurring revenue. "
            "ARPU trends signal pricing power and plan mix shift."
        ),
        "writer_guidance": (
            "Lead with subscriber trends — net adds and churn signal competitive positioning. "
            "ARPU trajectory reveals pricing power vs. competitive pressure. "
            "EBITDA margin and capex intensity determine FCF available for deleveraging and returns. "
            "5G/fiber investment is the long-term capital allocation story."
        ),
    },

    # ── MATERIALS ─────────────────────────────────────────────────────
    "materials": {
        "research_focus": (
            "Volume vs. price decomposition, realized prices for key commodities, "
            "cost per unit (mining: per ton/oz; chemicals: per pound), "
            "EBITDA margin by segment, capital allocation (growth vs. return), "
            "inventory levels and days on hand."
        ),
        "extra_schema": {
            "sector_kpis": {
                "type": "object",
                "properties": {
                    "realized_price": {"type": ["number", "null"], "description": "Avg realized price for key commodity"},
                    "production_volume": {"type": ["number", "null"], "description": "Production volume (units vary by commodity)"},
                    "cost_per_unit": {"type": ["number", "null"], "description": "All-in sustaining cost per unit"},
                },
                "required": [],
                "additionalProperties": False,
            }
        },
        "extraction_rules": (
            "For materials: volume vs. price decomposition is essential — distinguish market vs. company-specific. "
            "Cost per unit (AISC for miners, cost per pound for chemicals) is the profitability driver."
        ),
        "writer_guidance": (
            "Lead with volume and pricing trends — these are macro-driven. "
            "Cost discipline is the differentiator in a commodity business. "
            "Capital allocation (growth capex vs. returns to shareholders) signals management priorities. "
            "Inventory trends at the company and end-market level signal demand trajectory."
        ),
    },

    # ── CONSUMER DISCRETIONARY ────────────────────────────────────────
    "consumer_disc": {
        "research_focus": (
            "Revenue growth by channel (DTC, wholesale, digital), "
            "comparable store sales or like-for-like growth, "
            "average order value / units per transaction trends, "
            "brand momentum indicators, inventory position."
        ),
        "extra_schema": {
            "sector_kpis": {
                "type": "object",
                "properties": {
                    "dtc_revenue_growth_pct": {"type": ["number", "null"], "description": "Direct-to-consumer growth %"},
                    "dtc_pct_of_revenue": {"type": ["number", "null"], "description": "DTC as % of total revenue"},
                    "comp_sales_growth_pct": {"type": ["number", "null"], "description": "Comp / like-for-like sales growth %"},
                },
                "required": [],
                "additionalProperties": False,
            }
        },
        "extraction_rules": (
            "For consumer discretionary: channel mix shift (DTC vs. wholesale) is a structural theme. "
            "Like-for-like growth strips out new store openings and closures."
        ),
        "writer_guidance": (
            "Lead with same-store/like-for-like growth and what's driving it. "
            "DTC mix shift is a margin and brand control story. "
            "Consumer sentiment and discretionary spending trends provide macro context. "
            "Inventory health signals whether growth is sell-through vs. channel fill."
        ),
    },

    # ── GENERIC FALLBACK ──────────────────────────────────────────────
    "generic": {
        "research_focus": (
            "Revenue growth (total and organic), operating margin trend, "
            "free cash flow generation, key segment performance, "
            "any material one-time items or accounting changes."
        ),
        "extra_schema": {
            "sector_kpis": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }
        },
        "extraction_rules": "",
        "writer_guidance": (
            "Focus on revenue growth quality, margin trajectory, and cash flow conversion. "
            "Identify the primary growth driver and whether it's sustainable."
        ),
    },
}


def get_sector_config(sector_family: str) -> dict:
    """Return the sector configuration for the given family, with fallback to generic."""
    return SECTOR_CONFIGS.get(sector_family, SECTOR_CONFIGS["generic"])
