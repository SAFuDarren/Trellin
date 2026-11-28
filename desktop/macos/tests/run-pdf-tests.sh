#!/bin/bash
set -euo pipefail
desktop="$(cd -P "$(dirname "$0")/.." && pwd)"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/trellin-pdf-tests.XXXXXXXX")"
trap 'rm -rf "$scratch"' EXIT
xcrun swiftc -module-cache-path "$scratch/modules" -parse-as-library \
    "$desktop/Helpers/PDFPreview.swift" -o "$scratch/trellin-pdf-preview"

# Make independent fixtures using the native PDF writer, including pixels
# without a text layer and a document that requires a nonempty password.
cat > "$scratch/fixtures.swift" <<'SWIFT'
import CoreGraphics
import CoreText
import Foundation

let directory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let font = CTFontCreateWithName("Helvetica" as CFString, 18, nil)
let attributes = [NSAttributedString.Key(kCTFontAttributeName as String): font]
let bounds = CGRect(x: 0, y: 0, width: 240, height: 120)

func draw(_ text: String, in context: CGContext) {
    let line = CTLineCreateWithAttributedString(NSAttributedString(string: text, attributes: attributes))
    context.textPosition = CGPoint(x: 10, y: 50)
    CTLineDraw(line, context)
}

func fixture(_ name: String, _ pages: [String], locked: Bool = false, scanned: Bool = false) {
    var box = bounds
    let options = [kCGPDFContextUserPassword as String: "synthetic-user",
                   kCGPDFContextOwnerPassword as String: "synthetic-owner"]
    let url = directory.appendingPathComponent(name + ".pdf")
    let pdf = CGContext(url as CFURL, mediaBox: &box, locked ? options as CFDictionary : nil)!
    for page in pages {
        pdf.beginPDFPage(nil)
        if scanned {
            let bitmap = CGContext(data: nil, width: 240, height: 120, bitsPerComponent: 8, bytesPerRow: 0,
                                   space: CGColorSpaceCreateDeviceRGB(),
                                   bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
            bitmap.setFillColor(gray: 1, alpha: 1)
            bitmap.fill(bounds)
            bitmap.setFillColor(gray: 0, alpha: 1)
            draw(page, in: bitmap)
            pdf.draw(bitmap.makeImage()!, in: bounds)
        } else if !page.isEmpty {
            draw(page, in: pdf)
        }
        pdf.endPDFPage()
    }
    pdf.closePDF()
}

fixture("mixed", ["", "Café", "", "Later", ""])
fixture("scanned", ["SYNTHETIC SCAN"], scanned: true)
fixture("locked", ["Private fixture"], locked: true)
fixture("at-page-limit", Array(repeating: "", count: 127) + ["Last"])
fixture("over-page-limit", Array(repeating: "", count: 129))
SWIFT
xcrun swift -module-cache-path "$scratch/modules" "$scratch/fixtures.swift" "$scratch"

python3 - "$scratch" <<'PY'
import json
from pathlib import Path
import subprocess
import sys

scratch = Path(sys.argv[1])


def run(data, *args):
    result = subprocess.run(
        [scratch / "trellin-pdf-preview", *args], input=data, capture_output=True, timeout=10, check=True,
    )
    return json.loads(result.stdout.decode("utf-8"))


def fixture(name):
    return (scratch / (name + ".pdf")).read_bytes()


def available(count, through, *pages):
    return {
        "status": "available", "page_count": count, "pages_included_through": through,
        "pages": [{"page": page, "text": text} for page, text in pages],
    }


mixed = fixture("mixed")
first, last = (2, "Café"), (4, "Later")
cost = len(first[1].encode("utf-8"))
full = available(5, 5, first, last)
assert run(mixed, "18000") == full
assert run(mixed, str(cost + len(last[1]))) == full
assert run(mixed, str(cost)) == available(5, 3, first)
assert run(mixed, str(cost - 1)) == {"status": "pdf_text_limit"}
assert run(fixture("scanned"), "18000") == {"status": "pdf_no_text"}
assert run(fixture("locked"), "18000") == {"status": "pdf_locked"}
assert run(fixture("at-page-limit"), "18000") == available(128, 128, (128, "Last"))
assert run(fixture("over-page-limit"), "18000") == {"status": "pdf_page_limit"}
for data in (b"", b"not a PDF", b"%PDF-1.7\ncorrupt\n%%EOF"):
    assert run(data, "18000") == {"status": "pdf_invalid"}
for args in ([], ["0"], ["18001"], ["-1"], ["1.5"], ["abc"], ["10", "extra"]):
    assert run(mixed, *args) == {"status": "pdf_invalid"}

# Pad before startxref, keeping the cross-reference offsets and the short
# startxref/EOF trailer valid at both sizes.
before, after = mixed.rsplit(b"startxref", 1)
limit = 16 * 1024 * 1024
at_limit = before + b" " * (limit - len(mixed)) + b"startxref" + after
assert len(at_limit) == limit
assert run(at_limit, "18000") == full
assert run(at_limit + b"\n", "18000") == {"status": "pdf_invalid"}
print("Native PDF preview CLI checks passed")
PY
