"""Verification models for claim-level confidence scoring.

Replaces the coarse overall-score approach with per-claim verification,
enabling the pipeline to identify exactly which claims need human review.
"""

from pydantic import BaseModel, Field
from typing import Literal, Optional


class ClaimVerification(BaseModel):
    claim_text: str = Field(description="The specific claim being verified")
    claim_type: Literal[
        "financial_fact",    # e.g. "ROE = 15.2%"
        "ratio",             # e.g. "D/E thấp hơn ngành"
        "trend",             # e.g. "Doanh thu tăng trưởng ổn định"
        "sector_claim",      # e.g. "Ngành xây dựng tăng trưởng 8%/năm"
        "risk_assessment",   # e.g. "Rủi ro tín dụng thấp"
        "completeness",      # e.g. "Có đủ thông tin cổ đông"
    ]
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score 0.0–1.0")
    source_reference: Optional[str] = Field(
        default=None,
        description="Source reference, e.g. 'CDKT 2024 line A100' or 'Tavily search result'"
    )
    verified: bool = Field(description="Whether the claim passed verification")
    issues: list[str] = Field(default_factory=list, description="Specific issues found")
    regulation_refs: list[str] = Field(
        default_factory=list,
        description="Relevant regulation references, e.g. 'NHNN Circular 11/2021 Article 3'"
    )

    def to_dict(self) -> dict:
        return self.model_dump()


class VerificationSummary(BaseModel):
    layers_run: list[str]
    total_claims: int
    verified_count: int
    low_confidence_count: int  # confidence < 0.5
    unverified_count: int
    overall_confidence: float  # mean confidence across all claims

    def needs_escalation(self, threshold: int = 3) -> bool:
        return self.low_confidence_count > threshold
