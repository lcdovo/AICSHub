"""回答调用的类型化观察头；SQL ModelCallRecord才是计量事实源。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProviderTokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def consistent_total(self):
        if (self.input_tokens is not None and self.output_tokens is not None
                and self.total_tokens is not None
                and self.total_tokens != self.input_tokens + self.output_tokens):
            raise ValueError("provider_usage_total_mismatch")
        return self


class GenerationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True,
                              protected_namespaces=("model_validate", "model_dump"))
    model: str | None = Field(default=None, max_length=100)
    provider: str | None = Field(default=None, max_length=100)
    model_version: str | None = Field(default=None, max_length=100)
    prompt_version: str | None = Field(default=None, max_length=100)
    schema_version: str | None = Field(default=None, max_length=100)
    route_version: str | None = Field(default=None, max_length=100)
    purpose: str | None = Field(default=None, max_length=100)
    route: str | None = Field(default=None, max_length=100)
    stream_provider: str | None = Field(default=None, max_length=100)
    tokens: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    estimated_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cost_basis: str | None = Field(default=None, max_length=100)
    degraded: bool = False
    degraded_reason: str | None = Field(default=None, max_length=100)
    budget_allowed: bool | None = None
    budget_reason: str | None = Field(default=None, max_length=100)
    injection_detected: bool | None = None
    provider_usage: ProviderTokenUsage = Field(default_factory=ProviderTokenUsage)
    usage_basis: Literal["provider_tokens", "partial_provider_tokens", "unknown"] = "unknown"

    @model_validator(mode="after")
    def derive_usage_basis(self):
        usage = self.provider_usage
        basis = ("provider_tokens" if usage.input_tokens is not None and usage.output_tokens is not None
                 else "partial_provider_tokens" if any(value is not None for value in usage.model_dump().values())
                 else "unknown")
        object.__setattr__(self, "usage_basis", basis)
        return self

    @classmethod
    def from_service_metadata(cls, raw: dict):
        if not isinstance(raw, dict):
            raise ValueError("model_metadata_not_object")
        # 输入摘要、完整structured_output/回答、注入原文及任何扩展字段不进入观察头。
        allowed = cls.model_fields.keys() - {"usage_basis", "provider_usage"}
        values = {key: value for key, value in raw.items() if key in allowed}
        from app.services.llm_governance import SensitiveDataRedactor
        redactor = SensitiveDataRedactor()
        values = {key: redactor.redact(value) if isinstance(value, str) else value
                  for key, value in values.items()}
        usage = raw.get("provider_usage")
        if usage is not None:
            if not isinstance(usage, dict):
                raise ValueError("provider_usage_not_object")
            values["provider_usage"] = {key: usage[key] for key in ProviderTokenUsage.model_fields if key in usage}
        return cls.model_validate(values)
