# MatterMatch

MatterMatch scans explicitly supplied image filenames, decodes Matter commissioning QR codes, derives their manual pairing codes, and joins them to an inventory CSV. It is local-only; shell glob expansion is the caller's responsibility and directories are never searched.

## Install

A current CPython (3.10+) and a platform with wheels for the native decoder are recommended:

```sh
python -m pip install .
```

`zxing-cpp`, Pillow, and NumPy are installed as runtime dependencies. Pillow headers are checked before pixel data is loaded, so oversized compressed images are rejected safely. If a wheel is unavailable, `zxing-cpp` requires a C++20-capable source-build environment.

## Usage

The inventory header must be exactly `code,descriptor`; codes are 11- or 21-digit Verhoeff-validated Matter manual codes. Spaces and hyphens may be used as visual separators.

```sh
mattermatch --inventory inventory.csv --output matches.csv images/*.png
mattermatch --inventory inventory.csv --expected-qr-count 2 --strict-count image-a.png image-b.jpg
```

Without `--output`, the exact `qr_code,descriptor,pairing_code` CSV is written to stdout. Warnings and errors go to stderr. A strict count mismatch still writes recovered output. Exit statuses are 0 (success), 2 (usage/inventory), 3 (output), 4 (image recovery), and 5 (strict count mismatch); output errors take precedence over image failures, and image failures take precedence over strict-count mismatches.

QR payloads and pairing codes are not printed in diagnostics, but the CSV contains sensitive commissioning data. Protect stdout and output files, and treat descriptor/CSV content as untrusted data. MatterMatch performs no network requests and bounds decoded image dimensions, QR text, and optional TLV data.
