"""Generate app.ico for the Windows executable and installer."""

import os
from PIL import Image, ImageDraw, ImageFont


def create_icon(output_path: str = "app.ico"):
    sizes = [16, 32, 48, 64, 128, 256]
    images = []

    for size in sizes:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Orange circle
        margin = max(1, size // 32)
        draw.ellipse(
            [margin, margin, size - margin, size - margin],
            fill="#e67e22",
        )

        # White "C" letter
        font_size = size // 2
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except (OSError, IOError):
            font = ImageFont.load_default()

        text = "C"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (size - tw) // 2
        y = (size - th) // 2 - bbox[1]
        draw.text((x, y), text, fill="white", font=font)

        images.append(img)

    # Save as multi-resolution .ico
    images[0].save(
        output_path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[1:],
    )
    print(f"Created {output_path} with sizes {sizes}")


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
    create_icon(out)
