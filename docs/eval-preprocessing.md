# Image clean-up: OCR before and after (2026-11-03, before the paper-detection fix)

Simulated phone photos of 9 synthetic documents. Each cell: right / total product lines. **off** = no clean-up, **standard** = current clean-up, **contrast** = standard + contrast stretch.

## photo

| measure | off | standard | contrast |
|---|---|---|---|
| qty+price+total on the right product | 3/58 | 51/58 | 52/58 |
| supplier code | 16/58 | 40/58 | 39/58 |
| description exact | 1/58 | 28/58 | 27/58 |
| quantity | 3/58 | 56/58 | 57/58 |
| unit price | 21/58 | 56/58 | 56/58 |
| line total | 22/58 | 55/58 | 55/58 |
| description character error rate | 90.5% | 10.0% | 10.4% |
| wrong values shown as OK | 25 | 42 | 43 |
| lines found (right count) | 1/9 | 9/9 | 9/9 |
| seconds per document | 8.7 | 32.0 | 32.1 |

## dark

| measure | off | standard | contrast |
|---|---|---|---|
| qty+price+total on the right product | 0/58 | 47/58 | 47/58 |
| supplier code | 15/58 | 40/58 | 40/58 |
| description exact | 1/58 | 33/58 | 33/58 |
| quantity | 1/58 | 57/58 | 57/58 |
| unit price | 18/58 | 49/58 | 49/58 |
| line total | 18/58 | 56/58 | 56/58 |
| description character error rate | 95.7% | 10.0% | 10.1% |
| wrong values shown as OK | 35 | 37 | 39 |
| lines found (right count) | 0/9 | 9/9 | 9/9 |
| seconds per document | 7.9 | 26.9 | 27.1 |

## low_contrast

| measure | off | standard | contrast |
|---|---|---|---|
| qty+price+total on the right product | 0/58 | 0/58 | 9/58 |
| supplier code | 14/58 | 28/58 | 20/58 |
| description exact | 1/58 | 4/58 | 5/58 |
| quantity | 0/58 | 9/58 | 14/58 |
| unit price | 18/58 | 18/58 | 32/58 |
| line total | 18/58 | 25/58 | 28/58 |
| description character error rate | 96.9% | 84.6% | 77.2% |
| wrong values shown as OK | 33 | 25 | 38 |
| lines found (right count) | 0/9 | 2/9 | 2/9 |
| seconds per document | 8.1 | 90.7 | 97.7 |

## faded

| measure | off | standard | contrast |
|---|---|---|---|
| qty+price+total on the right product | 0/58 | 45/58 | 40/58 |
| supplier code | 14/58 | 39/58 | 46/58 |
| description exact | 1/58 | 24/58 | 19/58 |
| quantity | 0/58 | 45/58 | 40/58 |
| unit price | 18/58 | 53/58 | 46/58 |
| line total | 18/58 | 53/58 | 47/58 |
| description character error rate | 95.7% | 30.2% | 36.8% |
| wrong values shown as OK | 29 | 29 | 41 |
| lines found (right count) | 0/9 | 8/9 | 7/9 |
| seconds per document | 8.7 | 37.9 | 35.3 |

## sideways

| measure | off | standard | contrast |
|---|---|---|---|
| qty+price+total on the right product | 0/58 | 47/58 | 46/58 |
| supplier code | 14/58 | 27/58 | 27/58 |
| description exact | 0/58 | 32/58 | 32/58 |
| quantity | 0/58 | 49/58 | 48/58 |
| unit price | 18/58 | 52/58 | 52/58 |
| line total | 18/58 | 51/58 | 51/58 |
| description character error rate | 100.0% | 21.5% | 22.2% |
| wrong values shown as OK | 0 | 23 | 22 |
| lines found (right count) | 0/9 | 8/9 | 8/9 |
| seconds per document | 8.7 | 35.6 | 35.7 |

## upside_down

| measure | off | standard | contrast |
|---|---|---|---|
| qty+price+total on the right product | 0/58 | 37/58 | 38/58 |
| supplier code | 14/58 | 29/58 | 29/58 |
| description exact | 0/58 | 20/58 | 20/58 |
| quantity | 0/58 | 37/58 | 38/58 |
| unit price | 18/58 | 45/58 | 45/58 |
| line total | 18/58 | 45/58 | 45/58 |
| description character error rate | 100.0% | 30.9% | 30.8% |
| wrong values shown as OK | 0 | 30 | 28 |
| lines found (right count) | 0/9 | 7/9 | 7/9 |
| seconds per document | 11.9 | 33.5 | 30.1 |


# After the paper-detection fix (standard clean-up only)

Same 9 documents and images. Grey photos went from 0/58 to 47/58; the other problems did not change.

## photo

| measure | standard |
|---|---|
| qty+price+total on the right product | 51/58 |
| supplier code | 40/58 |
| description exact | 28/58 |
| quantity | 56/58 |
| unit price | 56/58 |
| line total | 55/58 |
| description character error rate | 10.0% |
| wrong values shown as OK | 42 |
| lines found (right count) | 9/9 |
| seconds per document | 14.4 |

## low_contrast

| measure | standard |
|---|---|
| qty+price+total on the right product | 47/58 |
| supplier code | 25/58 |
| description exact | 31/58 |
| quantity | 49/58 |
| unit price | 52/58 |
| line total | 52/58 |
| description character error rate | 19.8% |
| wrong values shown as OK | 30 |
| lines found (right count) | 8/9 |
| seconds per document | 14.2 |

## dark

| measure | standard |
|---|---|
| qty+price+total on the right product | 47/58 |
| supplier code | 40/58 |
| description exact | 33/58 |
| quantity | 57/58 |
| unit price | 49/58 |
| line total | 56/58 |
| description character error rate | 10.0% |
| wrong values shown as OK | 37 |
| lines found (right count) | 9/9 |
| seconds per document | 13.5 |

## faded

| measure | standard |
|---|---|
| qty+price+total on the right product | 45/58 |
| supplier code | 39/58 |
| description exact | 24/58 |
| quantity | 45/58 |
| unit price | 53/58 |
| line total | 53/58 |
| description character error rate | 30.2% |
| wrong values shown as OK | 29 |
| lines found (right count) | 8/9 |
| seconds per document | 16.0 |

## sideways

| measure | standard |
|---|---|
| qty+price+total on the right product | 47/58 |
| supplier code | 27/58 |
| description exact | 32/58 |
| quantity | 49/58 |
| unit price | 52/58 |
| line total | 51/58 |
| description character error rate | 21.5% |
| wrong values shown as OK | 23 |
| lines found (right count) | 8/9 |
| seconds per document | 16.3 |

## upside_down

| measure | standard |
|---|---|
| qty+price+total on the right product | 37/58 |
| supplier code | 29/58 |
| description exact | 20/58 |
| quantity | 37/58 |
| unit price | 45/58 |
| line total | 45/58 |
| description character error rate | 30.9% |
| wrong values shown as OK | 30 |
| lines found (right count) | 7/9 |
| seconds per document | 16.2 |


Seconds per document were measured with 4 documents read at the same time, so they are slower than a single read.
