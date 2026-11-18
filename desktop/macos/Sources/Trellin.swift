import AppKit
import WebKit

private func localized(_ key: String) -> String {
    Bundle.main.localizedString(forKey: key, value: nil, table: nil)
}

private struct LastWorkspace: Codable {
    let path: String
    var position: WorkbenchPosition?
}

@main
final class Trellin: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    private let defaults = UserDefaults.standard
    private let lastKey = "lastWorkspaceV1"
    private let bridge = "trellinPosition"
    private let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1260, height: 820),
                                  styleMask: [.titled, .closable, .miniaturizable, .resizable],
                                  backing: .buffered, defer: false)
    private var last: LastWorkspace?
    private var requested: URL?
    private var child: WorkbenchProcess?
    private var webView: WKWebView?
    private var serverURL: URL?
    private var starting = false

    static func main() {
        let app = NSApplication.shared
        let delegate = Trellin()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        withExtendedLifetime(delegate) { app.run() }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        setUpMenu()
        window.title = "Trellin"
        window.minSize = NSSize(width: 640, height: 460)
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        window.center()
        window.setFrameAutosaveName("TrellinWorkbench")
        if let data = defaults.data(forKey: lastKey) {
            last = try? JSONDecoder().decode(LastWorkspace.self, from: data)
        }
        if let last { open(URL(fileURLWithPath: last.path, isDirectory: true)) }
        else { showStatus("welcome", retry: false) }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) { stop() }

    private func setUpMenu() {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: localized("quit"), action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        menu.addItem(appItem)

        let fileItem = NSMenuItem()
        let fileMenu = NSMenu(title: localized("file"))
        let choose = fileMenu.addItem(withTitle: localized("choose"), action: #selector(chooseWorkspace), keyEquivalent: "o")
        choose.target = self
        fileMenu.addItem(withTitle: localized("close"), action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        fileItem.submenu = fileMenu
        menu.addItem(fileItem)
        NSApp.mainMenu = menu
    }

    @objc private func chooseWorkspace() {
        guard window.attachedSheet == nil else { return }
        let picker = NSOpenPanel()
        picker.title = localized("choose")
        picker.prompt = localized("open")
        picker.canChooseDirectories = true
        picker.canChooseFiles = false
        picker.canCreateDirectories = false
        picker.allowsMultipleSelection = false
        picker.directoryURL = requested?.deletingLastPathComponent()
        picker.beginSheetModal(for: window) { [weak self] result in
            if result == .OK, let url = picker.url { self?.open(url) }
        }
    }

    @objc private func retryWorkspace() {
        if let requested { open(requested) }
    }

    private func showStatus(_ key: String, retry: Bool) {
        window.subtitle = ""
        let title = NSTextField(labelWithString: localized("\(key).title"))
        title.font = .systemFont(ofSize: 30, weight: .semibold)
        title.alignment = .center
        let detail = NSTextField(wrappingLabelWithString: localized("\(key).detail"))
        detail.alignment = .center
        detail.textColor = .secondaryLabelColor
        detail.preferredMaxLayoutWidth = 430
        let choices = NSStackView()
        choices.orientation = .horizontal
        choices.spacing = 12
        if retry {
            choices.addArrangedSubview(NSButton(title: localized("retry"), target: self, action: #selector(retryWorkspace)))
        }
        if key != "loading" {
            choices.addArrangedSubview(NSButton(title: localized("choose"), target: self, action: #selector(chooseWorkspace)))
        }
        let group = NSStackView(views: [title, detail, choices])
        group.orientation = .vertical
        group.alignment = .centerX
        group.spacing = 18
        let view = NSView()
        group.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(group)
        NSLayoutConstraint.activate([
            group.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            group.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            detail.widthAnchor.constraint(equalToConstant: 430),
        ])
        window.contentView = view
    }

    private func open(_ folder: URL) {
        stop()
        let workspace = folder.standardizedFileURL.resolvingSymlinksInPath()
        requested = workspace
        guard let runtime = Bundle.main.resourceURL?.appendingPathComponent("python/bin/python3"),
              FileManager.default.isExecutableFile(atPath: runtime.path) else {
            showStatus("install", retry: false)
            return
        }
        showStatus("loading", retry: false)
        let process = WorkbenchProcess()
        child = process
        starting = true
        do {
            try process.start(workspace: workspace, runtime: runtime, onReady: { [weak self, weak process] result in
                guard let self, let process, self.child === process else { return }
                self.starting = false
                if let result, let url = result.sessionURL {
                    self.showWorkbench(url: url, workspace: workspace, leanMissing: result.warnings?.contains("lean") == true)
                } else {
                    self.stop()
                    let known = ["workspace", "postgres", "schema", "install"]
                    let code = result?.code.flatMap { known.contains($0) ? $0 : nil } ?? "startup"
                    self.showStatus(code, retry: code == "postgres" || code == "startup")
                }
            }, onExit: { [weak self, weak process] in
                guard let self, let process, self.child === process, !self.starting else { return }
                self.stop()
                self.showStatus("disconnected", retry: true)
            })
        } catch {
            stop()
            showStatus("install", retry: false)
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 25) { [weak self, weak process] in
            guard let self, let process, self.child === process, self.starting else { return }
            self.stop()
            self.showStatus("timeout", retry: true)
        }
    }

    private func showWorkbench(url: URL, workspace: URL, leanMissing: Bool) {
        if last?.path != workspace.path { last = LastWorkspace(path: workspace.path, position: nil) }
        saveLast()
        serverURL = url
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController.add(self, name: bridge)
        if let position = last?.position {
            configuration.userContentController.addUserScript(WKUserScript(
                source: position.script, injectionTime: .atDocumentStart, forMainFrameOnly: true
            ))
        }
        let web = WKWebView(frame: .zero, configuration: configuration)
        web.navigationDelegate = self
        webView = web
        window.subtitle = leanMissing ? localized("lean") : ""
        window.contentView = web
        web.load(URLRequest(url: url))
    }

    private func saveLast() {
        guard let last, let data = try? JSONEncoder().encode(last) else { return }
        defaults.set(data, forKey: lastKey)
    }

    private func stop() {
        if starting { child?.cancelStartup() }
        else { child?.stop() }
        child = nil
        starting = false
        serverURL = nil
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: bridge)
        webView?.stopLoading()
        webView = nil
    }

    func webView(_ web: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard web === webView, action.targetFrame?.isMainFrame == true,
              let destination = action.request.url, let serverURL,
              destination.scheme == serverURL.scheme, destination.host == serverURL.host,
              destination.port == serverURL.port, destination.user == nil, destination.password == nil
        else { decisionHandler(.cancel); return }
        decisionHandler(.allow)
    }

    func webViewWebContentProcessDidTerminate(_ web: WKWebView) {
        guard web === webView else { return }
        stop()
        showStatus("disconnected", retry: true)
    }

    func webView(_ web: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        guard web === webView else { return }
        stop()
        showStatus("disconnected", retry: true)
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        let origin = message.frameInfo.securityOrigin
        guard message.name == bridge, message.webView === webView, message.frameInfo.isMainFrame,
              let serverURL, origin.protocol == serverURL.scheme, origin.host == serverURL.host,
              origin.port == serverURL.port, let position = WorkbenchPosition(message: message.body),
              last?.position != position else { return }
        last?.position = position
        saveLast()
    }
}
