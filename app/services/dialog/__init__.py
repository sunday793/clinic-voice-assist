"""Диалоговый движок и сценарии."""

from app.services.dialog.engine import DialogEngine
from app.services.dialog.state import (
    DialogResponse,
    DialogState,
    STEP_ASK_NAME,
    STEP_ASK_SPECIALTY,
    STEP_BOOKED,
    STEP_CONFIRM,
    STEP_ESCALATED,
    STEP_OFFER_SLOTS,
)

__all__ = [
    "DialogEngine",
    "DialogResponse",
    "DialogState",
    "STEP_ASK_NAME",
    "STEP_ASK_SPECIALTY",
    "STEP_BOOKED",
    "STEP_CONFIRM",
    "STEP_ESCALATED",
    "STEP_OFFER_SLOTS",
]