"""Админ-команды владельца. Публичный доступ к боту это не меняет."""

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.crud import users as users_crud
from db.models import Submission, SubmissionResult

logger = logging.getLogger(__name__)
router = Router(name="admin")


@router.message(Command("stats"))
async def cmd_stats(message: Message, session: AsyncSession) -> None:
    users_total = await users_crud.count_users(session)
    with_key = await users_crud.count_users_with_key(session)

    submissions = await session.scalar(select(func.count()).select_from(Submission)) or 0
    row = (
        await session.execute(
            select(
                func.count(),
                func.sum(SubmissionResult.tokens_input),
                func.sum(SubmissionResult.tokens_output),
                func.avg(SubmissionResult.latency_ms),
            ).select_from(SubmissionResult)
        )
    ).one()
    results, tokens_in, tokens_out, avg_latency = row

    active = await session.scalar(
        select(func.count(func.distinct(Submission.user_id))).select_from(Submission)
    ) or 0

    lines = [
        "📊 <b>Статистика</b>",
        f"Пользователей: {users_total} (со своим ключом: {with_key})",
        f"Прислали хотя бы одну вещь: {active}",
        f"Разборов: {submissions}, ответов модели: {results or 0}",
    ]
    if results:
        total = (tokens_in or 0) + (tokens_out or 0)
        lines.append(
            f"Токенов: {tokens_in or 0}→{tokens_out or 0} (всего {total}, "
            f"в среднем {total // results} на разбор)"
        )
        lines.append(f"Средняя задержка: {(avg_latency or 0) / 1000:.1f} с")
    else:
        lines.append("Расход токенов: разборов ещё не было")

    await message.answer("\n".join(lines))
