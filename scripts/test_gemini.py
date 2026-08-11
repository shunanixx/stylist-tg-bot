"""Проверка ключа и доступности модели — раздел 13 документации.

Запуск: .venv/bin/python scripts/test_gemini.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from google import genai

load_dotenv()

model = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")
client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

response = client.models.generate_content(
    model=model,
    contents=["Ответь одним словом: работаешь?"],
)
usage = response.usage_metadata
print(f"модель: {model}")
print(f"ответ: {response.text}")
print(
    f"токены: in={usage.prompt_token_count} out={usage.candidates_token_count} "
    f"thinking={getattr(usage, 'thoughts_token_count', 0)}"
)
