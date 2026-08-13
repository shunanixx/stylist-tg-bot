"""Админ-команды владельца. Публичный доступ к боту это не меняет."""

import logging
from datetime import datetime
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.crud import users as users_crud
from db.models import Submission, SubmissionResult
from services.text_utils import split_message

logger = logging.getLogger(__name__)
router = Router(name="admin")

# Столько строк влезает в разумное число сообщений; остальное — по /stats
NUMBERS_LIMIT = 100


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


NO_PHONES_NOTE = (
    "📱 Номера телефонов Telegram боту не отдаёт — их видно только если "
    "пользователь сам нажмёт «Поделиться контактом», а сбор не включён."
)


@router.message(Command("numbers"))
async def cmd_numbers(message: Message, session: AsyncSession) -> None:
    """Кто пользуется ботом: @username, имя, id и живость.

    Команда владельческая (router под IsOwner) и в список команд бота не
    добавлена: чужие имена не должны светиться в меню у всех.
    """
    total = await users_crud.count_users(session)
    if not total:
        await message.answer("Пользователей пока нет.")
        return

    rows = await users_crud.list_users_with_activity(session, limit=NUMBERS_LIMIT)
    lines = [f"📇 <b>Пользователи</b> — {total}", ""]
    for number, row in enumerate(rows, start=1):
        lines.append(f"{number}. {_who(row.user)}")
        lines.append(f"   {_details(row)}")
    if total > len(rows):
        lines.append("")
        lines.append(f"Показал последних {len(rows)} из {total}.")
    lines.append("")
    lines.append(NO_PHONES_NOTE)

    for chunk in split_message("\n".join(lines)):
        await message.answer(chunk, disable_web_page_preview=True)


def _who(user) -> str:
    """@username и имя — то, что вообще известно о человеке.

    Экранируем: имя приходит от пользователя, а parse_mode у бота HTML —
    «<b>» в имени иначе поломает всё сообщение.
    """
    name = escape(user.first_name) if user.first_name else ""
    if user.username:
        handle = f"@{user.username}"
        return f"{handle} · {name}" if name else handle
    return f"{name} (без username)" if name else "без username и имени"


def _details(row: users_crud.UserActivity) -> str:
    parts = [f"id <code>{row.user.user_id}</code>", f"с {_date(row.user.created_at)}"]
    if row.submissions:
        parts.append(f"разборов {row.submissions}, последний {_date(row.last_at)}")
    else:
        parts.append("разборов нет")
    parts.append("🔑 есть" if row.user.google_api_key_enc else "🔑 нет")
    return " · ".join(parts)


def _date(value: object) -> str:
    if not isinstance(value, datetime):
        return "—"
    return value.strftime("%d.%m.%Y")
