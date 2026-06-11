import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)


def merge_images_to_pdf(image_paths: list[Path], output_pdf: Path) -> None:
    if not image_paths:
        raise ValueError("No screenshots to merge into PDF")

    images = [Image.open(path).convert("RGB") for path in image_paths]
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    images[0].save(
        output_pdf,
        format="PDF",
        save_all=True,
        append_images=images[1:],
        resolution=150.0,
    )
    logger.info("PDF saved: %s (%s pages)", output_pdf, len(images))
