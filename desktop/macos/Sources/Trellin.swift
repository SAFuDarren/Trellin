import AppKit
import UniformTypeIdentifiers
import WebKit

func localized(_ key: String) -> String {
    Bundle.main.localizedString(forKey: key, value: nil, table: nil)
}

/// The project Trellin creates keeps an English name on disk and is shown in the app's language (#302).
func displayProjectName(_ name: String) -> String {
    name == "My research" ? localized("project.default") : name
}

@main
final class Trellin: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate,
                     WKScriptMessageHandler, WKScriptMessageHandlerWithReply, NSWindowDelegate {
    private static let windowSurface = WorkbenchTokens.rail
    private let defaults = UserDefaults.standard
    private let bridge = "trellinPosition"
    private let upgradeBridge = "trellinUpgradeProject"
    private let projectsBridge = "trellinProjects"
    private let chatSizeBridge = "trellinAgentChatSize"
    private let chatSizeKey = "agentChatSizeV1"
    private let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1260, height: 820),
                                  styleMask: [.titled, .closable, .miniaturizable, .resizable],
                                  backing: .buffered, defer: false)
    private var last: LastWorkspace?
    private var requested: URL?
    private var requestedLaunch: WorkbenchLaunch = .reopen
    private var child: WorkbenchProcess?
    private var webView: WKWebView?
    private var noticeView: NSView?
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
        // Restore from the library's own settings; migrate legacy macOS preferences once (#285).
        open(LegacyPreferencesMigration.initialLaunch(from: defaults))
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
        let presentation = StatusPresentation.of(key, canRetry: retry)
        let actions: [StatusScreen.Action] = presentation.actions.compactMap { action in
            switch action {
            case .retry:
                return StatusScreen.Action(title: localized("retry")) { [weak self] in self?.retryWorkspace() }
            case .chooseFolder:
                guard !storageBusy else { return nil }
                return StatusScreen.Action(title: localized("choose")) { [weak self] in self?.chooseWorkspace() }
            case .openLibrary:
                guard !storageBusy else { return nil }
                return StatusScreen.Action(title: localized("library.open")) { [weak self] in
                    if self?.canPresent == true { self?.open(.library) }
                }
            case .quit:
                return StatusScreen.Action(title: localized("quit")) { NSApp.terminate(nil) }
            }
        }
        // Paths and codes help support, not the researcher, so they stay behind Show Details.
        let details = presentation.kind == .problem
            ? ([requested?.path, "\(localized("details.code")) \(key)"].compactMap { $0 }.joined(separator: "\n"))
            : nil
        window.contentView = StatusScreen(StatusScreen.Content(
            title: localized("\(key).title"), detail: localized("\(key).detail"), presentation: presentation,
            actions: actions, details: details, showDetails: localized("details.show"),
            hideDetails: localized("details.hide"), copyDetails: localized("details.copy"),
            icon: NSApp.applicationIconImage
        ))
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
                    let reopened = result.reopenedLibrary
                    LegacyPreferencesMigration.complete(in: self.defaults)
                    self.requested = opened
                    self.requestedLaunch = result.project_id.map { .registered($0, opened) } ?? .restore(opened)
                    self.last = LastWorkspace(path: opened.path, projectId: result.project_id, position: result.position)
                    self.showWorkbench(url: url, workspace: opened, name: result.name,
                                       leanMissing: result.warnings?.contains("lean") == true)
                    if reopened { self.showReopenNotice() }
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
        serverURL = url
        let folderName = workspace.lastPathComponent == "Trellin"
            ? workspace.deletingLastPathComponent().lastPathComponent : workspace.lastPathComponent
        let projectName = displayProjectName(name ?? folderName)
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

    /// A quiet notice over the workbench: the last project could not be reopened, so the library opened.
    private func showReopenNotice() {
        guard let web = webView else { return }
        noticeView?.removeFromSuperview()
        let symbol = NSImageView(image: NSImage(systemSymbolName: "arrow.uturn.backward.circle",
                                                accessibilityDescription: nil) ?? NSImage())
        symbol.contentTintColor = .secondaryLabelColor
        symbol.symbolConfiguration = .init(pointSize: 15, weight: .regular)
        let text = NSTextField(wrappingLabelWithString: localized("reopen.notice"))
        text.font = .systemFont(ofSize: 13)
        text.textColor = .labelColor
        text.preferredMaxLayoutWidth = 380
        let open = NSButton(title: localized("projects.open"), target: self, action: #selector(chooseProject))
        open.bezelStyle = .rounded
        let dismiss = NSButton(image: NSImage(systemSymbolName: "xmark", accessibilityDescription: localized("notice.dismiss"))
                               ?? NSImage(), target: self, action: #selector(dismissNotice))
        dismiss.isBordered = false
        dismiss.contentTintColor = .secondaryLabelColor
        dismiss.toolTip = localized("notice.dismiss")
        let row = NSStackView(views: [symbol, text, open, dismiss])
        row.orientation = .horizontal
        row.alignment = .centerY
        row.spacing = 10
        row.edgeInsets = NSEdgeInsets(top: 10, left: 14, bottom: 10, right: 10)
        row.translatesAutoresizingMaskIntoConstraints = false
        let banner = NSVisualEffectView()
        banner.material = .popover
        banner.state = .active
        banner.wantsLayer = true
        banner.layer?.cornerRadius = 10
        banner.layer?.masksToBounds = true
        banner.translatesAutoresizingMaskIntoConstraints = false
        banner.setAccessibilityRole(.group)
        banner.setAccessibilityLabel(localized("reopen.notice"))
        banner.addSubview(row)
        web.addSubview(banner)
        NSLayoutConstraint.activate([
            row.leadingAnchor.constraint(equalTo: banner.leadingAnchor),
            row.trailingAnchor.constraint(equalTo: banner.trailingAnchor),
            row.topAnchor.constraint(equalTo: banner.topAnchor),
            row.bottomAnchor.constraint(equalTo: banner.bottomAnchor),
            banner.centerXAnchor.constraint(equalTo: web.centerXAnchor),
            banner.bottomAnchor.constraint(equalTo: web.bottomAnchor, constant: -20),
            banner.widthAnchor.constraint(lessThanOrEqualTo: web.widthAnchor, constant: -32),
        ])
        noticeView = banner
        NSAccessibility.post(element: banner, notification: .announcementRequested,
                             userInfo: [.announcement: localized("reopen.notice"), .priority: NSAccessibilityPriorityLevel.high.rawValue])
    }

    @objc private func dismissNotice() {
        noticeView?.removeFromSuperview()
        noticeView = nil
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
        noticeView = nil
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
        if let projectId = last?.projectId, let runtime {
            StorageProcess.run(.savePosition(projectId, position), runtime: runtime) { _ in }
        }
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
                let active = self.last?.projectId
                var result: [String: Any] = ["projects": StorageProject.listed(projects, active: active).map(\.sidebarEntry)]
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
        case let .rename(id, name):
            StorageProcess.run(.rename(id, name), runtime: runtime) { [weak self, weak web] reply in
                guard let self, let web, self.webView === web, reply?.isReady == true else {
                    replyHandler(nil, unavailable)
                    return
                }
                if self.last?.projectId == id, self.window.subtitle != localized("lean") {
                    self.window.subtitle = displayProjectName(name)
                }
                replyHandler(true, nil)
            }
        case let .remove(id):
            // The open project stays in the list; switch to another project first.
            guard last?.projectId != id else { replyHandler(nil, unavailable); return }
            StorageProcess.run(.remove(id), runtime: runtime) { [weak self, weak web] reply in
                guard let self, let web, self.webView === web, reply?.isReady == true else {
                    replyHandler(nil, unavailable)
                    return
                }
                replyHandler(true, nil)
            }
        case .newProject, .folder, .storage:
            guard canPresent else { replyHandler(nil, unavailable); return }
            switch request {
            case .newProject: newProject()
            case .storage: showStorage()
            default: chooseWorkspace()
            }
            replyHandler(true, nil)
        }
    }
}
