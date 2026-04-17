from pydantic import BaseModel, Field
from typing import Optional


class FinancialStatement(BaseModel):
    """Financial data for one year"""
    year: int

    # Balance Sheet (CĐKT)
    total_assets: Optional[float] = Field(default=None, description="Tổng tài sản")
    current_assets: Optional[float] = Field(default=None, description="Tài sản ngắn hạn")
    cash_and_equivalents: Optional[float] = Field(default=None, description="Tiền và tương đương tiền")
    short_term_receivables: Optional[float] = Field(default=None, description="Phải thu ngắn hạn")
    inventories: Optional[float] = Field(default=None, description="Hàng tồn kho")
    non_current_assets: Optional[float] = Field(default=None, description="Tài sản dài hạn")
    fixed_assets: Optional[float] = Field(default=None, description="Tài sản cố định")
    total_liabilities: Optional[float] = Field(default=None, description="Tổng nợ phải trả")
    current_liabilities: Optional[float] = Field(default=None, description="Nợ ngắn hạn")
    long_term_liabilities: Optional[float] = Field(default=None, description="Nợ dài hạn")
    equity: Optional[float] = Field(default=None, description="Vốn chủ sở hữu")
    charter_capital_amount: Optional[float] = Field(default=None, description="Vốn điều lệ (số tiền)")

    # Income Statement (KQKD)
    net_revenue: Optional[float] = Field(default=None, description="Doanh thu thuần")
    gross_profit: Optional[float] = Field(default=None, description="Lợi nhuận gộp")
    operating_profit: Optional[float] = Field(default=None, description="Lợi nhuận từ hoạt động kinh doanh")
    profit_before_tax: Optional[float] = Field(default=None, description="Lợi nhuận trước thuế")
    net_profit: Optional[float] = Field(default=None, description="Lợi nhuận sau thuế")
    cost_of_goods_sold: Optional[float] = Field(default=None, description="Giá vốn hàng bán")
    selling_expenses: Optional[float] = Field(default=None, description="Chi phí bán hàng")
    admin_expenses: Optional[float] = Field(default=None, description="Chi phí quản lý doanh nghiệp")

    # Cash Flow (LCTT) - optional
    operating_cash_flow: Optional[float] = Field(default=None, description="Lưu chuyển tiền từ HĐKD")
    investing_cash_flow: Optional[float] = Field(default=None, description="Lưu chuyển tiền từ HĐ đầu tư")
    financing_cash_flow: Optional[float] = Field(default=None, description="Lưu chuyển tiền từ HĐ tài chính")


class FinancialRatios(BaseModel):
    """Calculated financial ratios"""
    year: int
    current_ratio: Optional[float] = Field(default=None, description="Tỷ số thanh toán hiện hành = TSNH/Nợ NH")
    quick_ratio: Optional[float] = Field(default=None, description="Tỷ số thanh toán nhanh = (TSNH-HTK)/Nợ NH")
    debt_to_equity: Optional[float] = Field(default=None, description="Tỷ lệ nợ/vốn chủ sở hữu")
    debt_to_assets: Optional[float] = Field(default=None, description="Tỷ lệ nợ/tổng tài sản")
    roe: Optional[float] = Field(default=None, description="ROE = LNST/Vốn CSH (%)")
    roa: Optional[float] = Field(default=None, description="ROA = LNST/Tổng tài sản (%)")
    net_profit_margin: Optional[float] = Field(default=None, description="Biên lợi nhuận ròng = LNST/DT thuần (%)")
    gross_profit_margin: Optional[float] = Field(default=None, description="Biên lợi nhuận gộp (%)")
    revenue_growth_yoy: Optional[float] = Field(default=None, description="Tăng trưởng doanh thu YoY (%)")
    net_profit_growth_yoy: Optional[float] = Field(default=None, description="Tăng trưởng LNST YoY (%)")


class FinancialData(BaseModel):
    """Financial data for all years"""
    statements: dict[int, FinancialStatement] = Field(default_factory=dict, description="Year -> Statement")
    ratios: dict[int, FinancialRatios] = Field(default_factory=dict, description="Year -> Ratios")
