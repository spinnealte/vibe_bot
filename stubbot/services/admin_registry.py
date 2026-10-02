"""Какие сущности есть в админке и чей сервис их обслуживает."""

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.services.admin_courses import COURSE_SPEC, AdminCourseService
from stubbot.services.admin_dictionaries import (
    SPECS,
    AdminDictionaryService,
    AdminEntityService,
    DictionarySpec,
    DictKind,
)

ADMIN_SPECS: dict[DictKind, DictionarySpec] = {**SPECS, DictKind.COURSES: COURSE_SPEC}


def admin_service(session: AsyncSession, kind: DictKind, admin_client_id: int) -> AdminEntityService:
    if kind is DictKind.COURSES:
        return AdminCourseService(session, admin_client_id)
    return AdminDictionaryService(session, SPECS[kind], admin_client_id)
