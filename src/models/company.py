from pydantic import BaseModel, Field, field_validator
from typing import Optional


class BoardMember(BaseModel):
    name: str
    role: str
    age: Optional[str] = None

    @field_validator("age", mode="before")
    @classmethod
    def coerce_age_to_str(cls, v: object) -> Optional[str]:
        return str(v) if v is not None else None


class Shareholder(BaseModel):
    name: str
    shares: Optional[int] = None
    percentage: Optional[float] = None
    as_of_date: Optional[str] = None


class CompanyInfo(BaseModel):
    company_name: str = Field(description="Tên công ty")
    tax_code: Optional[str] = Field(default=None, description="Mã số thuế / mã ĐKDN")
    address: Optional[str] = Field(default=None, description="Địa chỉ trụ sở")
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    charter_capital: Optional[str] = Field(default=None, description="Vốn điều lệ")
    main_business: Optional[str] = Field(default=None, description="Ngành nghề kinh doanh chính")
    stock_code: Optional[str] = Field(default=None, description="Mã chứng khoán")
    stock_exchange: Optional[str] = Field(default=None, description="Sàn niêm yết")
    established_date: Optional[str] = None
    legal_representative: Optional[str] = Field(default=None, description="Người đại diện pháp luật / Tổng GĐ")
    board_of_directors: list[BoardMember] = Field(default_factory=list, description="Hội đồng quản trị")
    supervisory_board: list[BoardMember] = Field(default_factory=list, description="Ban kiểm soát")
    management: list[BoardMember] = Field(default_factory=list, description="Ban giám đốc")
    shareholders: list[Shareholder] = Field(default_factory=list, description="Cổ đông lớn")
    registration_authority: Optional[str] = Field(default=None, description="Cơ quan cấp Giấy CNĐKKD (vd: Sở KH&ĐT TP Hà Nội)")
    company_history: Optional[str] = Field(default=None, description="Quá trình hình thành và phát triển")
