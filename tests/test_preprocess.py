import numpy as np
import pytest
from PIL import Image

from ocr_app import preprocess


def _page(ink=0, paper=255):
    a = np.full((200, 300), paper, np.uint8)
    a[80:120, 50:250] = ink  # a "line of text"
    return a


def test_off_returns_the_image_unchanged():
    img = Image.fromarray(_page()).convert("RGB")
    out, steps, q = preprocess.preprocess(img, is_photo=True, mode="off")
    assert out is img and steps == []
    assert "contrast" in q


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        preprocess.preprocess(Image.new("RGB", (10, 10)), is_photo=False, mode="magic")


def test_clear_page_is_left_alone():
    out, changed = preprocess.stretch_contrast(_page())
    assert not changed and np.array_equal(out, _page())


def test_faded_ink_gets_darker_without_new_marks():
    faded = _page(ink=190, paper=235)
    out, changed = preprocess.stretch_contrast(faded)
    assert changed
    assert out[100, 100] < 60 and out[10, 10] > 230  # ink dark, paper white
    # Paper stays one colour: nothing appears where there was no ink.
    assert len(np.unique(out[faded == 235])) == 1


def test_low_contrast_is_flagged():
    q = preprocess.quality(_page(ink=120, paper=170))
    assert "low_contrast" in q["warnings"]
