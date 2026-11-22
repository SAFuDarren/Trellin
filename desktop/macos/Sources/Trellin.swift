import AppKit
import UniformTypeIdentifiers
import WebKit

func localized(_ key: String) -> String {
    Bundle.main.localizedString(forKey: key, value: nil, table: nil)
}

@main
final class Trellin: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate,
                     WKScriptMessageHandler, WKScriptMessageHandlerWithReply, NSWindowDelegate {
    private static let windowSurface = NSColor(name: nil) { appearance in
        let dark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        return NSColor(white: dark ? 0.10 : 0.91, alpha: 1)
    }
    private let defaults = UserDefaults.standard
    private let lastKey = "lastWorkspaceV1"
    private let positionsKey = "workspacePositionsV1"
    private let bridge = "trellinPosition"
    private let upgradeBridge = "trellinUpgradeProject"
    private let projectsBridge = "trellinProjects"
    private let chatSizeBridge = "trellinAgentChatSize"
    private let chatSizeKey = "agentChatSizeV1"
    private let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1260, height: 820),
                                  styleMask: [.titled, .closable, .miniaturizable, .resizable],
                                  backing: .buffered, defer: false)
    private var last: LastWorkspace?
    private var positions: [String: WorkbenchPosition] = [:]
    private var requested: URL?
    private var requestedLaunch: WorkbenchLaunch = .library
    private var child: WorkbenchProcess?
    private var webView: WKWebView?
    private var serverURL: URL?
    private var starting = false
    private var movingStorage = false
    private var upgradingProject = false
    private var resolvingProject = false

    private var storageBusy: Bool { movingStorage || upgradingProject }
    private lazy var storageSettings = StorageSettings(window: window) { [weak self] plan in
        self?.moveStorage(plan)
    }

    private var canPresent: Bool {
        !starting && !resolvingProject && !storageBusy && !storageSettings.busy && window.attachedSheet == nil
    }

    private var runtime: URL? {
        guard let runtime = Bundle.main.resourceURL?.appendingPathComponent("python/bin/python3"),
              FileManager.default.isExecutableFile(atPath: runtime.path) else { return nil }
        return runtime
    }

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
        // Keep AppKit's window controls while matching the rail's gray surface.
        window.titlebarAppearsTransparent = true
        window.backgroundColor = Self.windowSurface
        window.minSize = NSSize(width: 640, height: 460)
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("TrellinWorkbench")
        if let data = defaults.data(forKey: lastKey) {
            last = try? JSONDecoder().decode(LastWorkspace.self, from: data)
        }
        if let data = defaults.data(forKey: positionsKey),
           let saved = try? JSONDecoder().decode([String: WorkbenchPosition].self, from: data) {
            positions = saved
        }
        if let saved = localDirectoryURL(last?.path) { open(.restore(saved)) }
        else { open(.library) }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        storageBusy ? .terminateCancel : .terminateNow
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool { !storageBusy }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) { stop() }

    private func setUpMenu() {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        let settings = appMenu.addItem(withTitle: localized("settings"), action: #selector(showStorage), keyEquivalent: ",")
        settings.target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: localized("quit"), action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        menu.addItem(appItem)

        let fileItem = NSMenuItem()
        let fileMenu = NSMenu(title: localized("file"))
        let create = fileMenu.addItem(withTitle: localized("projects.new"), action: #selector(newProject), keyEquivalent: "n")
        create.target = self
        let project = fileMenu.addItem(withTitle: localized("projects.open"), action: #selector(chooseProject), keyEquivalent: "")
        project.target = self
        let choose = fileMenu.addItem(withTitle: localized("choose"), action: #selector(chooseWorkspace), keyEquivalent: "o")
        choose.target = self
        fileMenu.addItem(withTitle: localized("close"), action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        fileItem.submenu = fileMenu
        menu.addItem(fileItem)
        NSApp.mainMenu = menu
    }

    @objc private func showStorage() {
        guard canPresent, let runtime else { return }
        storageSettings.show(runtime: runtime)
    }

    @objc private func newProject() {
        guard canPresent else { return }
        let sheet = NSAlert()
        sheet.messageText = localized("projects.new.title")
        let name = NSTextField(frame: NSRect(x: 0, y: 0, width: 310, height: 28))
        name.placeholderString = localized("projects.name")
        name.setAccessibilityLabel(localized("projects.name"))
        sheet.accessoryView = name
        sheet.window.initialFirstResponder = name
        sheet.addButton(withTitle: localized("projects.create"))
        sheet.addButton(withTitle: localized("cancel"))
        sheet.beginSheetModal(for: window) { [weak self] response in
            let title = name.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            guard response == .alertFirstButtonReturn, !title.isEmpty else { return }
            self?.open(.newManaged(title))
        }
    }

    @objc private func chooseProject() {
        guard canPresent, let runtime else { return }
        storageSettings.chooseProject(runtime: runtime) { [weak self] workspace in
            self?.open(.restore(workspace))
        }
    }

    @objc private func chooseWorkspace() {
        guard canPresent else { return }
        let picker = NSOpenPanel()
        picker.title = localized("choose")
        picker.prompt = localized("open")
        picker.message = localized("location.detail")
        picker.canChooseDirectories = true
        picker.canChooseFiles = false
        picker.canCreateDirectories = false
        picker.allowsMultipleSelection = false
        picker.directoryURL = requested?.deletingLastPathComponent() ?? FileManager.default.homeDirectoryForCurrentUser
        picker.beginSheetModal(for: window) { [weak self] result in
            if result == .OK, let url = picker.url { self?.open(.select(url)) }
        }
    }

    @objc private func retryWorkspace() {
        if canPresent { open(requestedLaunch) }
    }

    private func moveStorage(_ plan: StoragePlan) {
        guard !starting, !storageBusy, let runtime else { return }
        movingStorage = true
        let resume = requestedLaunch
        let worker = child
        stop()
        showStatus("storage.moving", retry: false)

        let perform = { [weak self] in
            StorageProcess.run(.move(plan), runtime: runtime) { [weak self] reply in
                guard let self else { return }
                self.movingStorage = false
                self.open(resume)
                self.storageSettings.showMoveResult(reply, plan: plan)
            }
        }
        // Closing the lifetime pipe drains in-flight project writes before the copy.
        if let worker { worker.waitForExit(perform) }
        else { perform() }
    }

    private func confirmProjectUpgrade() {
        guard canPresent, let workspace = requested, let runtime else { return }
        let sheet = NSAlert()
        sheet.messageText = localized("upgrade.confirm.title")
        sheet.informativeText = String(format: localized("upgrade.confirm.detail"), workspace.path)
        sheet.addButton(withTitle: localized("cancel"))
        sheet.addButton(withTitle: localized("upgrade.action"))
        sheet.beginSheetModal(for: window) { [weak self] response in
            guard response == .alertSecondButtonReturn, let self, self.requested == workspace else { return }
            self.upgradeProject(workspace, runtime: runtime)
        }
    }

    private func upgradeProject(_ workspace: URL, runtime: URL) {
        upgradingProject = true
        let worker = child
        stop()
        showStatus("upgrade.running", retry: false)
        let perform = { [weak self] in
            StorageProcess.run(.upgrade(workspace), runtime: runtime) { [weak self] reply in
                guard let self else { return }
                self.upgradingProject = false
                self.open(.restore(workspace))
                self.showUpgradeResult(reply)
            }
        }
        if let worker { worker.waitForExit(perform) }
        else { perform() }
    }

    private func showUpgradeResult(_ reply: StorageReply?) {
        let sheet = NSAlert()
        if reply?.isReady == true {
            sheet.messageText = localized("upgrade.complete.title")
            if let backup = reply?.backupURL {
                sheet.informativeText = String(format: localized("upgrade.complete.detail"), backup.path)
            } else {
                sheet.informativeText = localized("upgrade.current.detail")
            }
        } else {
            sheet.alertStyle = .warning
            sheet.messageText = localized("upgrade.failed.title")
            switch reply?.code {
            case "upgrade-backup": sheet.informativeText = localized("upgrade.backup.detail")
            case "upgrade-unsupported": sheet.informativeText = localized("upgrade.unsupported.detail")
            case "login-missing", "login-invalid", "upgrade-login":
                sheet.informativeText = localized("upgrade.login.detail")
            default: sheet.informativeText = localized("upgrade.failed.detail")
            }
        }
        sheet.beginSheetModal(for: window, completionHandler: nil)
    }

    private func showStatus(_ key: String, retry: Bool) {
        window.subtitle = ""
        let title = NSTextField(labelWithString: localized("\(key).title"))
        title.font = .systemFont(ofSize: 30, weight: .semibold)
        title.alignment = .center
        let detail = NSTextField(wrappingLabelWithString: localized("\(key).detail"))
        detail.alignment = .center
        detail.textColor = .secondaryLabelColor
        detail.preferredMaxLayoutWidth = 470
        let group = NSStackView(views: [title])
        group.orientation = .vertical
        group.alignment = .centerX
        group.spacing = 18
        // Show the application icon while the workbench opens.
        if key == "loading", let icon = NSApp.applicationIconImage {
            let logo = NSImageView(image: icon)
            logo.imageScaling = .scaleProportionallyUpOrDown
            logo.setAccessibilityLabel("Trellin")
            NSLayoutConstraint.activate([
                logo.widthAnchor.constraint(equalToConstant: 96),
                logo.heightAnchor.constraint(equalToConstant: 96),
            ])
            group.insertArrangedSubview(logo, at: 0)
            group.setCustomSpacing(22, after: logo)
        }
        if let requested {
            let path = NSTextField(labelWithString: requested.path)
            path.textColor = .secondaryLabelColor
            path.toolTip = requested.path
            path.lineBreakMode = .byTruncatingMiddle
            path.widthAnchor.constraint(lessThanOrEqualToConstant: 520).isActive = true
            group.addArrangedSubview(path)
        }
        group.addArrangedSubview(detail)
        let choices = NSStackView()
        choices.orientation = .horizontal
        choices.spacing = 12
        if key != "loading" && !storageBusy {
            let choose = NSButton(title: localized("choose"), target: self, action: #selector(chooseWorkspace))
            choose.keyEquivalent = "\r"
            choose.bezelStyle = .rounded
            choices.addArrangedSubview(choose)
        }
        if retry {
            choices.addArrangedSubview(NSButton(title: localized("retry"), target: self, action: #selector(retryWorkspace)))
        }
        if !choices.arrangedSubviews.isEmpty { group.addArrangedSubview(choices) }
        let view = NSView()
        group.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(group)
        NSLayoutConstraint.activate([
            group.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            group.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            detail.widthAnchor.constraint(equalToConstant: 470),
        ])
        window.contentView = view
    }

    private func open(_ launch: WorkbenchLaunch) {
        let worker = child
        stop()
        requested = launch.location
        requestedLaunch = launch
        guard let runtime else {
            showStatus("install", retry: false)
            return
        }
        showStatus("loading", retry: false)
        starting = true
        let start = { [weak self] in
            guard let self, self.starting else { return }
            self.start(launch, runtime: runtime)
        }
        // The previous workbench can still hold a library lease while draining.
        if let worker { worker.waitForExit(start) }
        else { start() }
    }

    private func start(_ launch: WorkbenchLaunch, runtime: URL) {
        let process = WorkbenchProcess()
        child = process
        do {
            try process.start(runtime: runtime, launch: launch, onReady: { [weak self, weak process] result in
                guard let self, let process, self.child === process else { return }
                self.starting = false
                if let result, result.matches(launch), let url = result.sessionURL, let opened = result.workspaceURL {
                    self.requested = opened
                    self.requestedLaunch = launch.projectId.map { .registered($0, opened) } ?? .restore(opened)
                    self.last = LastWorkspace.opened(opened, projectId: result.project_id, previous: self.last,
                                                     restoredFrom: launch.isRestore ? launch.location : nil,
                                                     positions: self.positions)
                    self.showWorkbench(url: url, workspace: opened, name: result.name,
                                       leanMissing: result.warnings?.contains("lean") == true)
                } else {
                    self.stop()
                    let failure = result?.failure ?? .startup
                    self.showStatus(failure.rawValue, retry: failure.canRetry)
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
        guard launch.isRestore else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 25) { [weak self, weak process] in
            guard let self, let process, self.child === process, self.starting else { return }
            self.stop()
            self.showStatus("timeout", retry: true)
        }
    }

    private func showWorkbench(url: URL, workspace: URL, name: String?, leanMissing: Bool) {
        saveLast()
        serverURL = url
        let folderName = workspace.lastPathComponent == "Trellin"
            ? workspace.deletingLastPathComponent().lastPathComponent : workspace.lastPathComponent
        let projectName = name ?? folderName
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController.add(self, name: bridge)
        configuration.userContentController.add(self, name: upgradeBridge)
        configuration.userContentController.add(self, name: chatSizeBridge)
        configuration.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: projectsBridge)
        if let encoded = try? JSONEncoder().encode(projectName), let value = String(data: encoded, encoding: .utf8) {
            configuration.userContentController.addUserScript(WKUserScript(
                source: "window.trellinProjectName = \(value);", injectionTime: .atDocumentStart, forMainFrameOnly: true
            ))
        }
        if let size = validatedChatSize(defaults.dictionary(forKey: chatSizeKey)),
           let encoded = try? JSONEncoder().encode(size), let value = String(data: encoded, encoding: .utf8) {
            configuration.userContentController.addUserScript(WKUserScript(
                source: "window.trellinAgentChatSize = \(value);", injectionTime: .atDocumentStart, forMainFrameOnly: true
            ))
        }
        if let position = last?.position {
            configuration.userContentController.addUserScript(WKUserScript(
                source: position.script, injectionTime: .atDocumentStart, forMainFrameOnly: true
            ))
        }
        let web = WKWebView(frame: .zero, configuration: configuration)
        web.underPageBackgroundColor = Self.windowSurface
        web.navigationDelegate = self
        web.uiDelegate = self
        webView = web
        window.subtitle = leanMissing ? localized("lean") : projectName
        window.contentView = web
        web.load(URLRequest(url: url))
    }

    private func saveLast() {
        guard let last else { return }
        let encoder = JSONEncoder()
        if let id = last.projectId, let position = last.position {
            positions[id.uuidString.lowercased()] = position
            if let data = try? encoder.encode(positions) { defaults.set(data, forKey: positionsKey) }
        }
        guard let data = try? encoder.encode(last) else { return }
        defaults.set(data, forKey: lastKey)
    }

    private func stop() {
        if starting && requestedLaunch.isRestore { child?.cancelStartup() }
        else { child?.stop() }
        child = nil
        starting = false
        serverURL = nil
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: bridge)
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: upgradeBridge)
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: chatSizeBridge)
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: projectsBridge, contentWorld: .page)
        webView?.stopLoading()
        webView = nil
    }

    func webView(_ web: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard web === webView, let destination = action.request.url, let serverURL else {
            decisionHandler(.cancel)
            return
        }
        if action.navigationType == .linkActivated, action.targetFrame?.isMainFrame ?? true,
           trusted(action.sourceFrame), ReleaseNotesLink.isOfficial(destination) {
            decisionHandler(.cancel)
            NSWorkspace.shared.open(destination)
            return
        }
        guard action.targetFrame?.isMainFrame == true,
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

    func webView(_ web: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        guard web === webView, trusted(frame), window.attachedSheet == nil else {
            completionHandler(nil)
            return
        }
        let picker = NSOpenPanel()
        picker.canChooseFiles = true
        picker.canChooseDirectories = false
        picker.allowsMultipleSelection = false
        picker.allowedContentTypes = [.plainText] + ["md", "lean"].compactMap { UTType(filenameExtension: $0) }
        if let requested {
            picker.directoryURL = requested.lastPathComponent == "Trellin" ? requested.deletingLastPathComponent() : requested
        }
        picker.beginSheetModal(for: window) { [weak self, weak web] result in
            guard let self, let web, web === self.webView, result == .OK else {
                completionHandler(nil)
                return
            }
            completionHandler(picker.urls)
        }
    }

    private func trusted(_ frame: WKFrameInfo) -> Bool {
        guard frame.isMainFrame, let serverURL else { return false }
        let origin = frame.securityOrigin
        return origin.protocol == serverURL.scheme && origin.host == serverURL.host && origin.port == serverURL.port
    }

    private func validatedChatSize(_ value: Any?) -> [String: Int]? {
        guard let size = value as? [String: Int], size.count == 2,
              let width = size["width"], (280...4096).contains(width),
              let height = size["height"], (180...4096).contains(height) else { return nil }
        return size
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.webView === webView, trusted(message.frameInfo) else { return }
        if message.name == upgradeBridge {
            if message.body as? String == "confirm" { confirmProjectUpgrade() }
            return
        }
        if message.name == chatSizeBridge {
            if let size = validatedChatSize(message.body) { defaults.set(size, forKey: chatSizeKey) }
            return
        }
        guard message.name == bridge, let position = WorkbenchPosition(message: message.body),
              last?.position != position else { return }
        last?.position = position
        saveLast()
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        let unavailable = "projects-unavailable"
        guard message.name == projectsBridge, let web = message.webView, web === webView, trusted(message.frameInfo),
              let worker = child, let runtime, let request = DesktopProjectsRequest(message: message.body) else {
            replyHandler(nil, unavailable)
            return
        }
        switch request {
        case .list:
            StorageProcess.run(.information, runtime: runtime) { [weak self, weak web, weak worker] reply in
                guard let self, let web, let worker, self.webView === web, self.child === worker,
                      reply?.isReady == true, let projects = reply?.projects else {
                    replyHandler(nil, unavailable)
                    return
                }
                var result: [String: Any] = ["projects": projects.map(\.sidebarEntry)]
                if let id = self.last?.projectId { result["activeId"] = id.uuidString.lowercased() }
                replyHandler(result, nil)
            }
        case let .open(id):
            guard canPresent else { replyHandler(nil, unavailable); return }
            if last?.projectId == id { replyHandler(true, nil); return }
            resolvingProject = true
            StorageProcess.run(.resolve(id), runtime: runtime) { [weak self, weak web, weak worker] reply in
                self?.resolvingProject = false
                guard let self, let web, let worker, self.webView === web, self.child === worker,
                      self.canPresent, let workspace = reply?.workspaceURL else {
                    replyHandler(nil, unavailable)
                    return
                }
                replyHandler(true, nil)
                self.open(.registered(id, workspace))
            }
        case .newProject, .folder:
            guard canPresent else { replyHandler(nil, unavailable); return }
            if request == .newProject { newProject() }
            else { chooseWorkspace() }
            replyHandler(true, nil)
        }
    }
}
