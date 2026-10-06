"""Explicit domain errors, independent of infrastructure."""


class DomainError(ValueError):
    """Base error for an invalid domain operation or value."""


class DomainValidationError(DomainError):
    """A domain model violates its data contract."""
