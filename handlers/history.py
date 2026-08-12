from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from db.crud import submissions as submissions_crud
from services.listing import resolve_position
from services.text_utils import split_message

router = Router(name="history")

HISTORY_LIMIT = 10
VERDICT_ICONS = {"брать": "✅", "не брать": "❌"}


async def _recent_newest_first(session: AsyncSession, user_id: int):
    """Один порядок для показа и для /show, иначе номер укажет не на ту вещь.

    recent_submissions отдаёт старые сверху — разворачиваем: свежий разбор
    нужен первым и получает номер 1.
    """
    items = await submissions_crud.recent_submissions(session, user_id, HISTORY_LIMIT)
    return list(reversed(items))


@router.message(Command("history"))
async def cmd_history(message: Message, session: AsyncSession) -> None:
    items = await _recent_newest_first(session, message.from_user.id)
    if not items:
        await message.answer("📜 Разборов пока нет. Пришли описание вещи или фото.")
        return

    lines = []
    # Номер — позиция в списке, а не submission.id: id сквозной по всей
    # таблице, поэтому в нумерации зияли дыры, а свежий разбор получал
    # очередной глобальный номер вместо первого.
    for position, submission in enumerate(items, start=1):
        icon = VERDICT_ICONS.get(submission.final_verdict or "", "•")
        date = submission.created_at.strftime("%d.%m") if submission.created_at else "—"
        lines.append(f"{icon} {position}. {date} — {submission.item_title}")

    await message.answer(
        f"📜 <b>Последние разборы</b> — {len(items)} шт., свежие сверху\n"
        + "\n".join(lines)
        + "\n\n/show 1 — полный текст разбора"
    )


@router.message(Command("show"))
async def cmd_show(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    items = await _recent_newest_first(session, message.from_user.id)
    submission = resolve_position(items, (command.args or "").strip())
    if submission is None:
        await message.answer(
            "📜 Разборов пока нет. Пришли описание вещи или фото."
            if not items
            else f"Нет разбора с таким номером. Сейчас их {len(items)}, "
            "номер бери из /history: <code>/show 1</code>"
        )
        return

    results = await submissions_crud.results_for(session, submission.id)
    if not results:
        await message.answer("У этого разбора нет сохранённого ответа.")
        return

    for result in results:
        header = f"<b>{submission.item_title}</b> · {result.provider}"
        chunks = split_message(f"{header}\n\n{result.full_response or ''}".strip())
        for chunk in chunks:
            await message.answer(chunk)
