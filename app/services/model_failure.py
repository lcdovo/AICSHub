"""Allowlisted non-sensitive failure codes; never persist exception messages."""
from __future__ import annotations
import asyncio

ADMISSION_CODES={'daily_model_budget_exhausted','request_model_budget_exceeded',
                 'run_model_budget_exhausted','model_concurrency_exhausted',
                 'run_model_deadline_exceeded','late_model_result_rejected',
                 'trusted_model_user_required','model_context_required','model_context_user_mismatch'}


def failure_code(exc: Exception) -> str:
    if isinstance(exc,RuntimeError) and str(exc) in ADMISSION_CODES:return str(exc)
    if isinstance(exc,(TimeoutError,asyncio.TimeoutError)):return 'provider_timeout'
    if isinstance(exc,ValueError):return 'structured_output_invalid'
    name=type(exc).__name__
    return {'APITimeoutError':'provider_timeout','APIConnectionError':'provider_connection_error',
            'RateLimitError':'provider_rate_limited','AuthenticationError':'provider_authentication_failed',
            'BadRequestError':'provider_request_rejected'}.get(name,'provider_or_internal_error')
