"""Все модели импортируются здесь, чтобы Base.metadata была полной (нужно Alembic)."""

from stubbot.db.base import Base
from stubbot.db.models.admins import Admin
from stubbot.db.models.analytics import AcquisitionSource, ClientEvent, ClientSourceTouch
from stubbot.db.models.catalog import (
    CourseSession,
    Lecturer,
    PriceOption,
    Program,
    SessionDay,
    Venue,
    program_specialties,
    session_lecturers,
)
from stubbot.db.models.clients import Client, Position, Specialty, client_positions, client_specialties
from stubbot.db.models.consents import ClientConsent, LegalDocument
from stubbot.db.models.enrollments import Enrollment, ProgramInterest

__all__ = [
    "AcquisitionSource",
    "Admin",
    "Base",
    "Client",
    "ClientConsent",
    "ClientEvent",
    "ClientSourceTouch",
    "CourseSession",
    "Enrollment",
    "LegalDocument",
    "Lecturer",
    "Position",
    "PriceOption",
    "Program",
    "ProgramInterest",
    "SessionDay",
    "Specialty",
    "Venue",
    "client_positions",
    "client_specialties",
    "program_specialties",
    "session_lecturers",
]
