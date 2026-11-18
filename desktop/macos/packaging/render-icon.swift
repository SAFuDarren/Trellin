import AppKit

// Draw the Trellin mark (brand/BRAND.md) at a single scale and rasterize for macOS icon sizes:
// a white stepped T on a navy plate. The outline comes from brand/svg/trellin-mark-*.svg, on its
// 64-unit grid with y pointing down; it is placed at 74% of the plate, like the app icon SVGs.
let plate = NSBezierPath(roundedRect: NSRect(x: 8, y: 8, width: 112, height: 112), xRadius: 25, yRadius: 25)
let navy = NSColor(srgbRed: 0x16 / 255.0, green: 0x23 / 255.0, blue: 0x3F / 255.0, alpha: 1)
let outline: [(CGFloat, CGFloat)] = [
    (7.82, 17.68), (7.58, 18.03), (7.51, 18.35), (7.51, 23.61),
    (7.58, 23.92), (7.75, 24.2), (7.99, 24.41), (8.18, 24.52),
    (8.6, 24.6), (25, 24.6), (25, 51.9), (25.08, 52.32),
    (25.32, 52.68), (25.58, 52.87), (25.99, 52.99), (37.9, 53),
    (38.32, 52.92), (38.68, 52.68), (38.92, 52.32), (39, 51.9),
    (39, 31.6), (46, 24.6), (55.51, 24.59), (55.82, 24.52),
    (56.18, 24.28), (56.37, 24.02), (56.49, 23.61), (56.49, 11.99),
    (56.37, 11.58), (56.18, 11.32), (55.82, 11.08), (55.51, 11.01),
    (14.85, 11.01), (14.53, 11.08), (14.18, 11.32),
]

func markPath() -> NSBezierPath {
    let path = NSBezierPath()
    for (index, (x, y)) in outline.enumerated() {
        // Scale about the grid centre, then map the 64-unit tile onto the 112-point plate (y up).
        let u = 32 + (x - 32) * 0.74
        let v = 32 + (y - 32) * 0.74
        let point = NSPoint(x: 8 + u * 112 / 64, y: 8 + (64 - v) * 112 / 64)
        if index == 0 { path.move(to: point) } else { path.line(to: point) }
    }
    path.close()
    return path
}

func drawIcon(pixels: Int) throws -> Data {
    let bitmap = NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0
    )!
    let graphics = NSGraphicsContext(bitmapImageRep: bitmap)!
    NSGraphicsContext.saveGraphicsState()
    defer { NSGraphicsContext.restoreGraphicsState() }
    NSGraphicsContext.current = graphics
    let context = graphics.cgContext
    let bounds = CGRect(x: 0, y: 0, width: pixels, height: pixels)
    context.clear(bounds)
    context.scaleBy(x: CGFloat(pixels) / 128, y: CGFloat(pixels) / 128)

    navy.setFill()
    plate.fill()
    NSColor.white.setFill()
    markPath().fill()

    guard let png = bitmap.representation(using: .png, properties: [:]) else {
        throw CocoaError(.fileWriteUnknown)
    }
    return png
}

// The ICNS container has a four-byte tag and a big-endian length for each
// PNG representation. Include ordinary and Retina sizes for Launchpad.
func iconEntry(_ tag: String, _ body: Data) -> Data {
    var length = UInt32(body.count + 8).bigEndian
    var entry = Data(tag.utf8)
    withUnsafeBytes(of: &length) { entry.append(contentsOf: $0) }
    entry.append(body)
    return entry
}

let representations = [
    ("icp4", 16), ("icp5", 32), ("ic07", 128), ("ic08", 256), ("ic09", 512),
    ("ic11", 32), ("ic12", 64), ("ic13", 256), ("ic14", 512), ("ic10", 1024),
]
var contents = Data()
for (tag, pixels) in representations {
    contents.append(try iconEntry(tag, drawIcon(pixels: pixels)))
}
let destination = URL(fileURLWithPath: CommandLine.arguments[1])
try iconEntry("icns", contents).write(to: destination)
