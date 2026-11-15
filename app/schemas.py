from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MedicationFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    dosage: str = Field(min_length=1, max_length=100)
    frequency: str = Field(min_length=1, max_length=120)
    start_date: date
    end_date: date | None = None
    instructions: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("The end date must be on or after the start date.")
        return self
