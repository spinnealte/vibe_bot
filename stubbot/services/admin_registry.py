"""Какие сущности есть в админке и чей сервис их обслуживает."""

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import get_settings
from stubbot.services.admin_courses import COURSE_SPEC, AdminCourseService
from stubbot.services.admin_dictionaries import (
    SPECS,
    AdminDictionaryService,
    AdminEntityService,
    DictionarySpec,
    DictKind,
)
from stubbot.services.admin_sessions import SESSION_SPEC, AdminSessionService
from stubbot.utils.dates import local_today

ADMIN_SPECS: dict[DictKind, DictionarySpec] = {
    **SPECS,
    DictKind.COURSES: COURSE_SPEC,
    DictKind.SESSIONS: SESSION_SPEC,
}


def admin_service(session: AsyncSession, kind: DictKind, admin_client_id: int) -> AdminEntityService:
    if kind is DictKind.COURSES:
        return AdminCourseService(session, admin_client_id)
    if kind is DictKind.SESSIONS:
        return AdminSessionService(session, admin_client_id, local_today(get_settings().timezone))
    return AdminDictionaryService(session, SPECS[kind], admin_client_id)
