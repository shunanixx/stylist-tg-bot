from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Float, а не INT из схемы: фактические параметры включают дробные (пояс 42.5)
    height_cm: Mapped[float | None] = mapped_column(Float)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    shoulders_cm: Mapped[float | None] = mapped_column(Float)
    chest_cm: Mapped[float | None] = mapped_column(Float)
    waist_cm: Mapped[float | None] = mapped_column(Float)
    belt_cm: Mapped[float | None] = mapped_column(Float)
    default_llm_provider: Mapped[str] = mapped_column(Text, default="gemini")
    # Ключ чужой — хранится зашифрованным, см. services/crypto.py
    google_api_key_enc: Mapped[str | None] = mapped_column(Text)
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    wardrobe_items: Mapped[list["WardrobeItem"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    wishlist_items: Mapped[list["WishlistItem"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    styles: Mapped[list["StyleItem"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    submissions: Mapped[list["Submission"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class StyleItem(Base):
    """Стиль, в котором одевается пользователь.

    Раньше список был вшит в system_prompt.txt один на всех. Стили задают
    систему координат для пунктов 1, 10 и 15, поэтому чужой набор делает
    разбор бесполезным. Количество не ограничено — жёсткое delete, а не
    active-флаг: строки нужны только для промпта, история по ним не ведётся.
    """

    __tablename__ = "style_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="styles")


class WardrobeItem(Base):
    __tablename__ = "wardrobe_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(Text)
    size: Mapped[str | None] = mapped_column(Text)
    source_submission_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("submissions.id", ondelete="SET NULL")
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="wardrobe_items")


class WishlistItem(Base):
    """Вещь, отложенная к покупке. Отдельно от гардероба: гардероб — что есть,
    вишлист — что присматриваю. Переезжает в гардероб, когда куплено."""

    __tablename__ = "wishlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)  # цена, ссылка, размер
    verdict: Mapped[str | None] = mapped_column(Text)  # вердикт на момент добавления
    source_submission_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("submissions.id", ondelete="SET NULL")
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="wishlist_items")


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    input_type: Mapped[str] = mapped_column(Text)  # photo | text | photo+text
    input_text: Mapped[str | None] = mapped_column(Text)
    # до MAX_PHOTOS_PER_ANALYSIS telegram file_id, разделены переводом строки
    photo_file_ids: Mapped[str | None] = mapped_column(Text)
    item_title: Mapped[str | None] = mapped_column(Text)
    item_category: Mapped[str | None] = mapped_column(Text)
    final_verdict: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="submissions")
    results: Mapped[list["SubmissionResult"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )
    followups: Mapped[list["ConversationFollowup"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )


class SubmissionResult(Base):
    __tablename__ = "submission_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(Text)
    verdict: Mapped[str | None] = mapped_column(Text)  # брать | не брать
    full_response: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[str | None] = mapped_column(Text)
    tokens_input: Mapped[int | None] = mapped_column(Integer)
    tokens_output: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    submission: Mapped["Submission"] = relationship(back_populates="results")


class ConversationFollowup(Base):
    __tablename__ = "conversation_followups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(Text)  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    submission: Mapped["Submission"] = relationship(back_populates="followups")


class ChatMessage(Base):
    """Журнал id сообщений в чате — только чтобы их потом удалить по /clear.

    Telegram не даёт боту прочитать историю чата: удалить можно лишь то,
    чей message_id известен. Текст здесь не хранится — разборы уже лежат
    в submissions, а дублировать переписку незачем.

    Отдельная таблица, не привязанная к submissions: чистить нужно и служебные
    «⏳ Анализирую…», и списки гардероба, и сам разбор — всё, что засоряет чат.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
