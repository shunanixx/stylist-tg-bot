from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from services.measurements import MEASUREMENTS, describe, format_measurement

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Совместимость: часть кода импортирует метки отсюда
MEASUREMENT_LABELS: tuple[tuple[str, str, str], ...] = tuple(
    (m.field, m.label, m.unit) for m in MEASUREMENTS
)

__all__ = [
    "MEASUREMENT_LABELS",
    "PROMPTS_DIR",
    "PromptBuilder",
    "format_measurement",
]


class PromptBuilder:
    """Собирает system_prompt: базовая методология + актуальные данные пользователя.

    Порядок блоков зафиксирован в разделе 7.1 документации. Пользовательское
    сообщение передаётся отдельно и в промпт не попадает.
    """

    def __init__(self, prompts_dir: Path | None = None) -> None:
        directory = prompts_dir or PROMPTS_DIR
        self._base = (directory / "system_prompt.txt").read_text(encoding="utf-8").strip()
        self._rules = (directory / "behavior_rules.txt").read_text(encoding="utf-8").strip()
        self._output_format = (
            (directory / "output_format.txt").read_text(encoding="utf-8").strip()
        )

    def build(
        self,
        user: Any = None,
        wardrobe_items: Iterable[Any] = (),
        recent_submissions: Sequence[Any] = (),
        wishlist_items: Iterable[Any] = (),
        styles: Iterable[Any] = (),
    ) -> str:
        blocks = [
            self._base,
            self._styles_block(styles),
            self._measurements_block(user),
            self._wardrobe_block(wardrobe_items),
        ]
        wishlist = self._wishlist_block(wishlist_items)
        if wishlist:
            blocks.append(wishlist)
        verdicts = self._verdicts_block(recent_submissions)
        if verdicts:
            blocks.append(verdicts)
        blocks.extend([self._rules, self._output_format])
        return "\n\n".join(blocks)

    @staticmethod
    def _styles_block(styles: Iterable[Any]) -> str:
        """Стили задают систему координат для пунктов 1, 10 и 15.

        Подставлять чужой набор по умолчанию нельзя: разбор в чужих
        координатах хуже, чем честное «стили не заданы».
        """
        names = []
        for style in styles:
            name = style if isinstance(style, str) else getattr(style, "name", "")
            name = (name or "").strip()
            if name:
                names.append(name)
        if not names:
            return (
                "[МОИ СТИЛИ]\n"
                "не заданы — не приписывай мне вкус по догадкам. В пункте 1 назови "
                "стиль вещи как есть, в пункте 10 оценивай тренд в целом, и упомяни, "
                "что без моих стилей оценка приблизительная."
            )
        return (
            "[МОИ СТИЛИ]\n"
            + ", ".join(names)
            + "\nОценивай вещь в этих стилях: пункт 1 — к какому из них она ближе, "
            "пункт 10 — актуальность внутри них, пункт 15 — сочетаемость с моим "
            "гардеробом в их рамках. Если вещь им не подходит — так и скажи."
        )

    def _wishlist_block(self, wishlist_items: Iterable[Any]) -> str:
        """Отложенное — не купленное. Блок пустой, если вишлист пуст: лишний
        текст в промпте стоит токенов на каждом запросе."""
        lines = [line for line in (self._item_line(item) for item in wishlist_items) if line]
        if not lines:
            return ""
        return (
            "[ОТЛОЖЕНО К ПОКУПКЕ]\n"
            + "\n".join(f"- {line}" for line in lines)
            + "\nЭтих вещей у меня ещё нет. Если новая вещь дублирует отложенную "
            "или конфликтует с ней по образу — скажи об этом в пункте 15."
        )

    def _measurements_block(self, user: Any) -> str:
        parts = []
        for measurement in MEASUREMENTS:
            value = getattr(user, measurement.field, None) if user is not None else None
            if value is not None:
                parts.append(describe(measurement, value))
        if not parts:
            return (
                "[МОИ ПАРАМЕТРЫ]\n"
                "не указаны — в пункте 5 скажи, что данных о параметрах нет, "
                "и не подбирай размер по догадкам"
            )
        return (
            "[МОИ ПАРАМЕТРЫ]\n"
            + ", ".join(parts)
            + "\n"
            + "Замеры вещи у продавцов обычно даны в полуобхвате (вещь разложена "
            "по плоскости) — сравнивай их с полуобхватами выше, а не с обхватами."
        )

    def _wardrobe_block(self, wardrobe_items: Iterable[Any]) -> str:
        lines = [line for line in (self._item_line(item) for item in wardrobe_items) if line]
        if not lines:
            return (
                "[МОЙ ГАРДЕРОБ]\n"
                "пуст — в пункте 15 напиши, что данных для сравнения нет"
            )
        return "[МОЙ ГАРДЕРОБ]\n" + "\n".join(f"- {line}" for line in lines)

    @staticmethod
    def _item_line(item: Any) -> str:
        if isinstance(item, str):
            return item.strip()
        title = (getattr(item, "title", "") or "").strip()
        if not title:
            return ""
        # note есть только у вишлиста (цена, ссылка), color/size — у гардероба
        details = [
            str(value).strip()
            for value in (
                getattr(item, "color", None),
                getattr(item, "size", None),
                getattr(item, "note", None),
            )
            if value
        ]
        return f"{title} ({', '.join(details)})" if details else title

    @staticmethod
    def _verdicts_block(recent_submissions: Sequence[Any]) -> str:
        lines = []
        for entry in recent_submissions:
            if isinstance(entry, (tuple, list)) and len(entry) == 2:
                title, verdict = entry
            else:
                title = getattr(entry, "item_title", None)
                verdict = getattr(entry, "final_verdict", None)
            if title and verdict:
                lines.append(f"- {str(title).strip()}: {str(verdict).strip()}")
        if not lines:
            return ""
        return "[ПОСЛЕДНИЕ ВЕРДИКТЫ]\n" + "\n".join(lines)
