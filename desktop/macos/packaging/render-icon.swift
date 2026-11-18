import AppKit

// Draw the proof branches at a single scale and rasterize for macOS icon sizes.
let plate = NSBezierPath(roundedRect: NSRect(x: 8, y: 8, width: 112, height: 112), xRadius: 25, yRadius: 25)
let ink = NSColor(srgbRed: 0.71, green: 0.83, blue: 0.70, alpha: 1)
let nodes: [NSPoint] = [
    NSPoint(x: 30, y: 64),
    NSPoint(x: 62, y: 43),
    NSPoint(x: 62, y: 85),
    NSPoint(x: 96, y: 64),
]

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

    NSColor(srgbRed: 0.11, green: 0.16, blue: 0.15, alpha: 1).setFill()
    plate.fill()

    let branches = NSBezierPath()
    branches.lineWidth = 4.5
    branches.lineCapStyle = .round
    for (start, end) in [(0, 1), (0, 2), (1, 3), (2, 3)] {
        let a = nodes[start]
        let b = nodes[end]
        let mid = (a.x + b.x) / 2
        branches.move(to: a)
        branches.curve(to: b, controlPoint1: NSPoint(x: mid, y: a.y), controlPoint2: NSPoint(x: mid, y: b.y))
    }
    ink.withAlphaComponent(0.72).setStroke()
    branches.stroke()
    for (index, point) in nodes.enumerated() {
        let radius: CGFloat = index == nodes.count - 1 ? 8 : 6
        ink.setFill()
        NSBezierPath(ovalIn: NSRect(
            x: point.x - radius, y: point.y - radius, width: radius * 2, height: radius * 2
        )).fill()
    }

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
