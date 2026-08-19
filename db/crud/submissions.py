from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Submission, SubmissionResult


async def create_submission(
    session: AsyncSession,
    user_id: int,
    input_type: str,
    input_text: str | None = None,
    photo_file_ids: list[str] | None = None,
) -> Submission:
    submission = Submission(
        user_id=user_id,
        input_type=input_type,
        input_text=input_text,
        photo_file_ids="\n".join(photo_file_ids) if photo_file_ids else None,
    )
    session.add(submission)
    await session.flush()
    return submission


async def add_result(
    session: AsyncSession,
    submission_id: int,
    provider: str,
    verdict: str | None,
    full_response: str | None,
    raw_response: str | None,
    tokens_input: int | None = None,
    tokens_output: int | None = None,
    latency_ms: int | None = None,
) -> SubmissionResult:
    result = SubmissionResult(
        submission_id=submission_id,
        provider=provider,
        verdict=verdict,
        full_response=full_response,
        raw_response=raw_response,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        latency_ms=latency_ms,
    )
    session.add(result)
    await session.flush()
    return result


async def set_item_meta(
    session: AsyncSession,
    submission_id: int,
    user_id: int,
    item_title: str | None,
    item_category: str | None,
    final_verdict: str | None,
) -> Submission | None:
    """Мета «основной» модели: краткое название, категория и итоговый вердикт.

    Проверка владельца — как в get_submission: без неё чужой submission_id
    (например, из гонки между двумя параллельными разборами) молча перезаписал
    бы результат другого пользователя.
    """
    submission = await session.get(Submission, submission_id)
    if submission is None or submission.user_id != user_id:
        return None
    submission.item_title = item_title
    submission.item_category = item_category
    submission.final_verdict = final_verdict
    await session.flush()
    return submission


async def recent_submissions(
    session: AsyncSession, user_id: int, limit: int = 5
) -> list[Submission]:
    result = await session.scalars(
        select(Submission)
        .where(Submission.user_id == user_id, Submission.item_title.is_not(None))
        .order_by(Submission.created_at.desc(), Submission.id.desc())
        .limit(limit)
    )
    return list(reversed(list(result)))


async def get_submission(
    session: AsyncSession, user_id: int, submission_id: int
) -> Submission | None:
    submission = await session.get(Submission, submission_id)
    if submission is None or submission.user_id != user_id:
        return None
    return submission


async def results_for(
    session: AsyncSession, submission_id: int
) -> list[SubmissionResult]:
    result = await session.scalars(
        select(SubmissionResult)
        .where(SubmissionResult.submission_id == submission_id)
        .order_by(SubmissionResult.id)
    )
    return list(result)
