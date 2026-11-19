import AppKit

final class StorageSettings {
    private let window: NSWindow
    private let move: (StoragePlan) -> Void
    private(set) var busy = false

    init(window: NSWindow, move: @escaping (StoragePlan) -> Void) {
        self.window = window
        self.move = move
    }

    func show(runtime: URL) {
        request(.information, runtime: runtime) { [weak self] reply in
            guard let self else { return }
            guard let root = reply?.rootURL else {
                self.showProblem("storage.openError", reply: reply)
                return
            }
            let sheet = NSAlert()
            sheet.messageText = localized("storage.title")
            sheet.informativeText = String(format: localized("storage.current"), root.path)
            sheet.addButton(withTitle: localized("storage.done"))
            sheet.addButton(withTitle: localized("storage.finder"))
            sheet.addButton(withTitle: localized("storage.move"))
            sheet.beginSheetModal(for: self.window) { [weak self] response in
                switch response {
                case .alertSecondButtonReturn: NSWorkspace.shared.open(root)
                case .alertThirdButtonReturn: self?.chooseDestination(current: root, runtime: runtime)
                default: break
                }
            }
        }
    }

    func chooseProject(runtime: URL, open: @escaping (URL) -> Void) {
        request(.information, runtime: runtime) { [weak self] reply in
            guard let self else { return }
            guard let reply, reply.isReady, let projects = reply.projects else {
                self.showProblem("storage.openError", reply: reply)
                return
            }
            let entries = projects.compactMap { project in
                project.workspace.map { (name: project.name, workspace: $0) }
            }
            let sheet = NSAlert()
            sheet.messageText = localized("projects.open.title")
            if entries.isEmpty {
                sheet.informativeText = localized("projects.empty")
                sheet.addButton(withTitle: localized("storage.done"))
                sheet.beginSheetModal(for: self.window)
                return
            }
            let project = NSPopUpButton(frame: NSRect(x: 0, y: 0, width: 310, height: 30), pullsDown: false)
            project.setAccessibilityLabel(localized("projects.open.title"))
            let projectsByName = Dictionary(grouping: entries, by: \.name)
            for entry in entries {
                let label = (projectsByName[entry.name]?.count ?? 0) > 1
                    ? "\(entry.name) — \(entry.workspace.path)" : entry.name
                project.addItem(withTitle: label)
                project.lastItem?.toolTip = entry.workspace.path
            }
            sheet.accessoryView = project
            sheet.addButton(withTitle: localized("open"))
            sheet.addButton(withTitle: localized("cancel"))
            sheet.beginSheetModal(for: self.window) { response in
                let index = project.indexOfSelectedItem
                guard response == .alertFirstButtonReturn, entries.indices.contains(index) else { return }
                open(entries[index].workspace)
            }
        }
    }

    func showMoveResult(_ reply: StorageReply?, plan: StoragePlan) {
        guard reply?.rootURL?.path == plan.destination.path, let backup = reply?.backupURL else {
            showProblem("storage.moveError", reply: reply)
            return
        }
        let sheet = NSAlert()
        sheet.messageText = localized("storage.moved.title")
        sheet.informativeText = String(format: localized("storage.moved.detail"), plan.destination.path, backup.path)
        sheet.addButton(withTitle: localized("storage.done"))
        sheet.beginSheetModal(for: window)
    }

    private func chooseDestination(current: URL, runtime: URL) {
        let picker = NSOpenPanel()
        picker.title = localized("storage.move")
        picker.prompt = localized("storage.chooseDestination")
        picker.message = localized("storage.destinationDetail")
        picker.canChooseDirectories = true
        picker.canChooseFiles = false
        picker.canCreateDirectories = true
        picker.allowsMultipleSelection = false
        picker.directoryURL = current.deletingLastPathComponent()
        picker.beginSheetModal(for: window) { [weak self] result in
            guard result == .OK, let folder = picker.url else { return }
            let destination = folder.resolvingSymlinksInPath().appendingPathComponent("Trellin Library", isDirectory: true)
            self?.preview(destination, runtime: runtime)
        }
    }

    private func preview(_ destination: URL, runtime: URL) {
        request(.preview(destination), runtime: runtime) { [weak self] reply in
            guard let self else { return }
            guard let plan = reply?.plan, plan.destination.path == destination.path else {
                self.showProblem("storage.previewError", reply: reply)
                return
            }
            let size = ByteCountFormatter.string(fromByteCount: plan.bytes, countStyle: .file)
            let sheet = NSAlert()
            sheet.messageText = localized("storage.confirm.title")
            sheet.informativeText = String(format: localized("storage.confirm.detail"),
                                           plan.root.path, plan.destination.path, plan.projectCount, plan.files, size)
            sheet.addButton(withTitle: localized("cancel"))
            sheet.addButton(withTitle: localized("storage.confirm.move"))
            sheet.beginSheetModal(for: self.window) { [weak self] response in
                if response == .alertSecondButtonReturn { self?.move(plan) }
            }
        }
    }

    private func request(_ command: StorageCommand, runtime: URL, completion: @escaping (StorageReply?) -> Void) {
        busy = true
        StorageProcess.run(command, runtime: runtime) { [weak self] reply in
            guard let self else { return }
            self.busy = false
            completion(reply)
        }
    }

    private func showProblem(_ key: String, reply: StorageReply?) {
        let sheet = NSAlert()
        sheet.alertStyle = .warning
        sheet.messageText = localized("\(key).title")
        let namedError = reply?.code.map { "storage.error.\($0)" }
        if let namedError, localized(namedError) != namedError {
            sheet.informativeText = localized(namedError)
        } else {
            sheet.informativeText = localized("\(key).detail")
        }
        sheet.addButton(withTitle: localized("storage.done"))
        sheet.beginSheetModal(for: window)
    }
}
