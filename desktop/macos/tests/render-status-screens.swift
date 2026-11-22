import AppKit

// Render every native status screen in light and dark (#286). Usage:
//   desktop/macos/tests/render-status-screens.sh OUTPUT_DIRECTORY [en|zh-Hans]
@main
struct RenderStatusScreens {
    static func main() throws {
        let arguments = CommandLine.arguments
        guard arguments.count >= 2 else { fatalError("pass an output directory") }
        let output = URL(fileURLWithPath: arguments[1], isDirectory: true)
        let language = arguments.count > 2 ? arguments[2] : "en"
        let resources = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("../Resources")
        let data = try Data(contentsOf: resources.appendingPathComponent("\(language).lproj/Localizable.strings"))
        let strings = try PropertyListSerialization.propertyList(from: data, format: nil) as! [String: String]
        let text = { (key: String) in strings[key] ?? key }
        _ = NSApplication.shared
        let icon = NSImage(contentsOf: resources.appendingPathComponent("../../../brand/png/app-icon-navy-256.png"))
        let failures = WorkbenchStartFailure.allCases.map { ($0.rawValue, $0.canRetry) }
        let keys = [("loading", false), ("workspace", false), ("disconnected", true)] + failures
        for (key, retry) in keys {
            let presentation = StatusPresentation.of(key, canRetry: retry)
            let titles: [StatusAction: String] = [
                .retry: text("retry"), .chooseFolder: text("choose"), .openLibrary: text("library.open"), .quit: text("quit"),
            ]
            let content = StatusScreen.Content(
                title: text("\(key).title"), detail: text("\(key).detail"), presentation: presentation,
                actions: presentation.actions.map { StatusScreen.Action(title: titles[$0]!) {} },
                details: presentation.kind == .problem ? "/Users/researcher/Research/Trellin\n\(text("details.code")) \(key)" : nil,
                showDetails: text("details.show"), hideDetails: text("details.hide"), copyDetails: text("details.copy"),
                icon: icon
            )
            for (theme, appearance) in [("light", NSAppearance.Name.aqua), ("dark", .darkAqua)] {
                let screen = StatusScreen(content)
                screen.appearance = NSAppearance(named: appearance)
                let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 520),
                                      styleMask: [.borderless], backing: .buffered, defer: false)
                window.appearance = NSAppearance(named: appearance)
                window.contentView = screen
                screen.layoutSubtreeIfNeeded()
                screen.displayIfNeeded()
                let bitmap = screen.bitmapImageRepForCachingDisplay(in: screen.bounds)!
                screen.cacheDisplay(in: screen.bounds, to: bitmap)
                let file = output.appendingPathComponent("\(language)-\(key)-\(theme).png")
                try bitmap.representation(using: .png, properties: [:])!.write(to: file)
            }
        }
        print("Rendered \(keys.count * 2) status screens to \(output.path)")
    }
}
