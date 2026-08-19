import logging

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.crud import submissions as submissions_crud
from db.crud import styles as styles_crud
from db.crud import users as users_crud
from db.crud import wardrobe as wardrobe_crud
from db.crud import wishlist as wishlist_crud
from keyboards.analysis_kb import analysis_actions_kb
from services import limits
from services.api_keys import NO_KEY_MESSAGE, resolve_api_key
from services.analysis_format import decorate_sections, verdict_icon
from services.crypto import KeyVault
from services.image_utils import downscale_image
from services.llm.base import LLMError
from services.llm_router import LLMRouter
from services.media_group_buffer import MediaGroupBuffer
from services.prompt_builder import PromptBuilder
from services.response_parser import parse_llm_response
from services.text_utils import split_message

logger = logging.getLogger(__name__)
router = Router(name="analysis")

RECENT_VERDICTS_LIMIT = 5
_media_buffer = MediaGroupBuffer(settle_delay=1.0)


@router.message(StateFilter(None), F.photo)
async def handle_photo(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    prompt_builder: PromptBuilder,
    llm_router: LLMRouter,
    vault: KeyVault,
) -> None:
    messages = await _media_buffer.collect(message)
    if messages is None:
        return

    photo_limit = limits.photos_per_analysis(message.from_user.id, settings)
    if limits.exceeds(len(messages), photo_limit):
        await message.answer(
            f"Максимум {photo_limit} фото за раз, прислано {len(messages)}. "
            "Отправь меньше или группами."
        )
        return

    # Ключ проверяем до скачивания: качать мегабайты фото без него бессмысленно
    user = await users_crud.get_or_create_user(session, message.from_user.id)
    if not resolve_api_key(user, vault, settings).present:
        await message.answer(NO_KEY_MESSAGE, disable_web_page_preview=True)
        return

    # Подпись у альбома одна, но висит на первом отправленном сообщении —
    # оно не обязательно то, которое доехало первым и стало лидером.
    user_text = next(
        (msg.caption.strip() for msg in messages if msg.caption and msg.caption.strip()),
        "",
    )
    input_type = "photo+text" if user_text else "photo"

    photo_data: list[bytes] = []
    file_ids: list[str] = []
    for msg in messages:
        if not msg.photo:
            continue
        largest = max(msg.photo, key=lambda p: p.width * p.height)
        tg_file = await bot.get_file(largest.file_id)
        raw_bytes = await bot.download_file(tg_file.file_path)
        if raw_bytes is not None:
            # file_ids должен перечислять ровно те фото, что видела модель:
            # добавляем id только вместе с успешно скачанными данными, иначе
            # /show и сохранённый submission ссылались бы на фото, которое
            # модель не получила.
            file_ids.append(largest.file_id)
            photo_data.append(downscale_image(raw_bytes.read()))

    if not photo_data:
        await message.answer("Не удалось загрузить фото. Попробуй ещё раз.")
        return

    await _analyze_and_reply(
        message,
        session,
        prompt_builder,
        llm_router,
        vault,
        user_text=user_text,
        images=photo_data,
        input_type=input_type,
        photo_file_ids=file_ids,
    )


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def handle_text_analysis(
    message: Message,
    session: AsyncSession,
    prompt_builder: PromptBuilder,
    llm_router: LLMRouter,
    vault: KeyVault,
) -> None:
    user_text = (message.text or "").strip()
    if len(user_text) < 3:
        await message.answer("Слишком коротко. Опиши вещь: бренд, размер, состояние, цена.")
        return

    await _analyze_and_reply(
        message,
        session,
        prompt_builder,
        llm_router,
        vault,
        user_text=user_text,
        images=None,
        input_type="text",
        photo_file_ids=None,
    )


async def _analyze_and_reply(
    message: Message,
    session: AsyncSession,
    prompt_builder: PromptBuilder,
    llm_router: LLMRouter,
    vault: KeyVault,
    *,
    user_text: str,
    images: list[bytes] | None,
    input_type: str,
    photo_file_ids: list[str] | None,
) -> None:
    user = await users_crud.get_or_create_user(session, message.from_user.id)

    # Проверяем до сборки промпта и до вызова модели: без ключа делать нечего
    key_source = resolve_api_key(user, vault, settings)
    if not key_source.present:
        await message.answer(NO_KEY_MESSAGE, disable_web_page_preview=True)
        return

    provider_name = user.default_llm_provider
    wardrobe_items = await wardrobe_crud.list_items(session, user.user_id)
    wishlist_items = await wishlist_crud.list_items(session, user.user_id)
    user_styles = await styles_crud.list_styles(session, user.user_id)
    recent = await submissions_crud.recent_submissions(
        session, user.user_id, RECENT_VERDICTS_LIMIT
    )
    system_prompt = prompt_builder.build(
        user, wardrobe_items, recent, wishlist_items, user_styles
    )

    photo_note = f" · {len(images)} фото" if images else ""
    status = await message.answer(f"⏳ Анализирую через {provider_name}{photo_note}…")
    try:
        response = await llm_router.analyze_single(
            provider_name, system_prompt, user_text, images, api_key=key_source.api_key
        )
    except LLMError as exc:
        logger.warning("Анализ не удался: %s", exc)
        if exc.tokens_input is not None or exc.tokens_output is not None:
            # Google уже списал эти токены (типично — MAX_TOKENS съеденный
            # thinking'ом), даже если пользователь не получил текста ответа.
            # Не залогировать их значило бы занизить фактический расход в БД.
            failed_submission = await submissions_crud.create_submission(
                session,
                user.user_id,
                input_type=input_type,
                input_text=user_text or None,
                photo_file_ids=photo_file_ids,
            )
            await submissions_crud.add_result(
                session,
                submission_id=failed_submission.id,
                provider=provider_name,
                verdict=None,
                full_response=None,
                raw_response=str(exc),
                tokens_input=exc.tokens_input,
                tokens_output=exc.tokens_output,
                latency_ms=exc.latency_ms,
            )
        await status.edit_text(_explain_llm_error(exc, key_source.is_own))
        return
    except (ValueError, NotImplementedError) as exc:
        logger.warning("Конфигурация провайдера: %s", exc)
        await status.edit_text(f"Провайдер недоступен: {exc}")
        return
    except Exception:
        logger.exception("Неожиданная ошибка анализа")
        await status.edit_text("Что-то сломалось на моей стороне. Загляни в логи.")
        return

    display_text, data = parse_llm_response(response.raw_text)
    # Иконки ставим до записи в БД: /show достаёт тот же текст, что был в чате
    display_text = decorate_sections(display_text, data["verdict"])

    submission = await submissions_crud.create_submission(
        session,
        user.user_id,
        input_type=input_type,
        input_text=user_text or None,
        photo_file_ids=photo_file_ids,
    )
    await submissions_crud.add_result(
        session,
        submission_id=submission.id,
        provider=provider_name,
        verdict=data["verdict"],
        full_response=display_text,
        raw_response=response.raw_text,
        tokens_input=response.tokens_input,
        tokens_output=response.tokens_output,
        latency_ms=response.latency_ms,
    )
    await submissions_crud.set_item_meta(
        session,
        submission_id=submission.id,
        user_id=user.user_id,
        item_title=data["title"],
        item_category=data["category"],
        final_verdict=data["verdict"],
    )

    await status.delete()
    chunks = split_message(display_text) or ["Модель вернула пустой разбор."]
    for chunk in chunks[:-1]:
        await message.answer(chunk)
    await message.answer(
        chunks[-1] + "\n\n" + _footer(provider_name, response, data),
        reply_markup=analysis_actions_kb(submission.id),
    )


def _explain_llm_error(exc: Exception, is_own_key: bool) -> str:
    """У публичного бота самая частая причина — личная квота, а не сбой сервиса."""
    text = str(exc)
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        if is_own_key:
            return (
                "🚦 Квота твоего ключа исчерпана. Free tier Gemini считает лимит "
                "в сутки и в минуту — подожди и пришли вещь снова.\n"
                "Проверить расход: ai.dev/rate-limit"
            )
        return "🚦 Квота ключа исчерпана. Подожди немного и попробуй снова."
    if "API_KEY_INVALID" in text or "API key not valid" in text:
        return "🔑 Ключ больше не действует. Пришли новый: /apikey"
    if "PERMISSION_DENIED" in text or "403" in text:
        return "🔑 У ключа нет доступа к Gemini API. Проверь ключ: /apikey"
    return "Провайдер не ответил. Попробуй ещё раз через минуту."


def _footer(provider_name: str, response: object, data: dict) -> str:
    tokens_in = getattr(response, "tokens_input", 0)
    tokens_out = getattr(response, "tokens_output", 0)
    latency = getattr(response, "latency_ms", 0)
    return (
        f"{verdict_icon(data['verdict'])} {provider_name} · {tokens_in}→{tokens_out} ток. · "
        f"{latency / 1000:.1f} с · вердикт: {data['verdict']}"
    )
