"""Company-level domain model.

Deliberately thin for now; the finance/strategy phase will add income,
production, market, and R&D books and treat cash as a ledger of the periods.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Company(BaseModel):
    """The simulated automaker itself."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    founded_year: int = Field(ge=1886, description="Year the company was founded")
    headquarters: str = Field(default="Unspecified")
    cash_eur: float = Field(ge=0, description="Opening cash position")
    currency: str = Field(default="EUR")
