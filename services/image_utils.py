import io

from PIL import Image

MAX_SIDE = 1024


def downscale_image(image_bytes: bytes, max_side: int = MAX_SIDE) -> bytes:
    """Даунскейл перед отправкой в API — прямое сокращение стоимости запроса."""
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
