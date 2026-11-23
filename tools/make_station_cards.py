"""Generate two fictional reference images in a new directory for station qualification."""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def make_cards(destination):
    destination.mkdir(parents=True, exist_ok=False)
    for name, x in (("first-test.png", 200), ("last-test.png", 560)):
        image = Image.new("RGB", (960, 544), (38, 52, 64))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 370, 959, 543), fill=(111, 89, 73))
        draw.ellipse((x, 275, x + 92, 367), fill=(214, 145, 73))
        image.save(destination / name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("new_directory", type=Path)
    make_cards(parser.parse_args().new_directory)
