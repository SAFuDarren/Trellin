import AppKit

/// The workbench's colour tokens (`web/src/styles/workbench.css`), so native screens and the web workbench
/// read as one product in both themes.
enum WorkbenchTokens {
    static let rail = dynamic(0xe8e8e8, 0x191919)
    static let panel = dynamic(0xffffff, 0x252525)
    static let hair = dynamic(0xdedede, 0x363636)
    static let nodeLine = dynamic(0xcecece, 0x434343)
    static let ink = dynamic(0x222222, 0xf1f1f1)
    static let sub = dynamic(0x555555, 0xb7b7b7)
    static let accent = dynamic(0x16233f, 0xa4b7df)
    static let accentInk = dynamic(0xffffff, 0x16233f)
    static let accentWash = dynamic(0xe4e6ea, 0x343a46)

    static func dynamic(_ light: UInt32, _ dark: UInt32) -> NSColor {
        NSColor(name: nil) { appearance in
            let value = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light
            return NSColor(srgbRed: CGFloat((value >> 16) & 0xff) / 255, green: CGFloat((value >> 8) & 0xff) / 255,
                           blue: CGFloat(value & 0xff) / 255, alpha: 1)
        }
    }
}

/// One native status screen: a centred panel with a symbol for the kind of state, a title in the researcher's
/// terms, one sentence of cause, one primary action that fixes it, and technical details behind a disclosure.
final class StatusScreen: NSView {
    struct Action {
        let title: String
        let perform: () -> Void
    }

    struct Content {
        let title: String
        let detail: String
        let presentation: StatusPresentation
        /// The first action is the primary one.
        let actions: [Action]
        /// Technical text (path and error code), shown only on request.
        let details: String?
        let showDetails: String
        let hideDetails: String
        let copyDetails: String
        let icon: NSImage?
    }

    private var targets: [ActionTarget] = []
    private var detailsBox: NSView?
    private var detailsToggle: NSButton?
    private let content: Content

    init(_ content: Content) {
        self.content = content
        super.init(frame: NSRect(x: 0, y: 0, width: 760, height: 560))
        wantsLayer = true
        build()
    }

    required init?(coder: NSCoder) { nil }

    override func updateLayer() {
        layer?.backgroundColor = WorkbenchTokens.rail.cgColor
    }

    override var wantsUpdateLayer: Bool { true }

    private func build() {
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 0
        stack.translatesAutoresizingMaskIntoConstraints = false

        let progress = content.presentation.kind == .progress
        if progress {
            if let icon = content.icon {
                let logo = NSImageView(image: icon)
                logo.imageScaling = .scaleProportionallyUpOrDown
                logo.setAccessibilityLabel("Trellin")
                logo.widthAnchor.constraint(equalToConstant: 72).isActive = true
                logo.heightAnchor.constraint(equalToConstant: 72).isActive = true
                stack.addArrangedSubview(logo)
                stack.setCustomSpacing(20, after: logo)
            }
            let spinner = NSProgressIndicator()
            spinner.style = .spinning
            spinner.controlSize = .small
            spinner.isIndeterminate = true
            spinner.startAnimation(nil)
            stack.addArrangedSubview(spinner)
            stack.setCustomSpacing(14, after: spinner)
        } else if let symbol = content.presentation.symbol {
            let badge = symbolBadge(symbol)
            stack.addArrangedSubview(badge)
            stack.setCustomSpacing(18, after: badge)
        }

        let title = NSTextField(labelWithString: content.title)
        title.font = .systemFont(ofSize: progress ? 15 : 18, weight: progress ? .medium : .semibold)
        title.textColor = progress ? WorkbenchTokens.sub : WorkbenchTokens.ink
        title.alignment = .center
        title.setAccessibilityRole(.staticText)
        stack.addArrangedSubview(title)
        stack.setCustomSpacing(8, after: title)

        let detail = NSTextField(wrappingLabelWithString: content.detail)
        detail.font = .systemFont(ofSize: 13)
        detail.textColor = WorkbenchTokens.sub
        detail.alignment = .center
        detail.preferredMaxLayoutWidth = 360
        detail.widthAnchor.constraint(lessThanOrEqualToConstant: 360).isActive = true
        stack.addArrangedSubview(detail)

        if !content.actions.isEmpty {
            stack.setCustomSpacing(22, after: detail)
            let buttons = NSStackView()
            buttons.orientation = .horizontal
            buttons.spacing = 10
            for (index, action) in content.actions.enumerated() {
                buttons.addArrangedSubview(button(action, primary: index == 0))
            }
            stack.addArrangedSubview(buttons)
        }

        if let details = content.details, !details.isEmpty {
            stack.setCustomSpacing(16, after: stack.arrangedSubviews.last!)
            let toggle = NSButton(title: content.showDetails, target: self, action: #selector(toggleDetails))
            toggle.isBordered = false
            toggle.font = .systemFont(ofSize: 12)
            toggle.contentTintColor = WorkbenchTokens.sub
            toggle.attributedTitle = NSAttributedString(string: content.showDetails, attributes: [
                .foregroundColor: WorkbenchTokens.sub, .font: NSFont.systemFont(ofSize: 12),
            ])
            detailsToggle = toggle
            stack.addArrangedSubview(toggle)
            let box = detailsView(details)
            box.isHidden = true
            detailsBox = box
            stack.setCustomSpacing(8, after: toggle)
            stack.addArrangedSubview(box)
        }

        let panel: NSView
        if progress {
            panel = stack
        } else {
            let card = Card()
            card.translatesAutoresizingMaskIntoConstraints = false
            card.addSubview(stack)
            NSLayoutConstraint.activate([
                stack.topAnchor.constraint(equalTo: card.topAnchor, constant: 32),
                stack.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -28),
                stack.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 36),
                stack.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -36),
                card.widthAnchor.constraint(equalToConstant: 440),
            ])
            card.setAccessibilityElement(true)
            card.setAccessibilityRole(.group)
            card.setAccessibilityLabel(content.title)
            panel = card
        }
        addSubview(panel)
        NSLayoutConstraint.activate([
            panel.centerXAnchor.constraint(equalTo: centerXAnchor),
            // Sit slightly above the optical centre, like system alerts.
            panel.centerYAnchor.constraint(equalTo: centerYAnchor, constant: -24),
            panel.widthAnchor.constraint(lessThanOrEqualTo: widthAnchor, constant: -32),
        ])
    }

    private func symbolBadge(_ name: String) -> NSView {
        let badge = Badge()
        badge.translatesAutoresizingMaskIntoConstraints = false
        let configuration = NSImage.SymbolConfiguration(pointSize: 20, weight: .regular)
        let image = NSImage(systemSymbolName: name, accessibilityDescription: nil)?.withSymbolConfiguration(configuration)
        let symbol = NSImageView(image: image ?? NSImage())
        symbol.contentTintColor = WorkbenchTokens.accent
        symbol.translatesAutoresizingMaskIntoConstraints = false
        badge.addSubview(symbol)
        NSLayoutConstraint.activate([
            badge.widthAnchor.constraint(equalToConstant: 48),
            badge.heightAnchor.constraint(equalToConstant: 48),
            symbol.centerXAnchor.constraint(equalTo: badge.centerXAnchor),
            symbol.centerYAnchor.constraint(equalTo: badge.centerYAnchor),
        ])
        return badge
    }

    private func button(_ action: Action, primary: Bool) -> NSButton {
        let target = ActionTarget(action.perform)
        targets.append(target)
        let button = WorkbenchButton(title: action.title, primary: primary)
        button.target = target
        button.action = #selector(ActionTarget.run)
        if primary { button.keyEquivalent = "\r" }
        return button
    }

    private func detailsView(_ text: String) -> NSView {
        let label = NSTextField(wrappingLabelWithString: text)
        label.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        label.textColor = WorkbenchTokens.sub
        label.isSelectable = true
        label.preferredMaxLayoutWidth = 340
        label.widthAnchor.constraint(lessThanOrEqualToConstant: 340).isActive = true
        let copyTarget = ActionTarget {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(text, forType: .string)
        }
        targets.append(copyTarget)
        let copy = WorkbenchButton(title: content.copyDetails, primary: false, compact: true)
        copy.target = copyTarget
        copy.action = #selector(ActionTarget.run)
        let column = NSStackView(views: [label, copy])
        column.orientation = .vertical
        column.alignment = .centerX
        column.spacing = 8
        return column
    }

    @objc private func toggleDetails() {
        guard let box = detailsBox, let toggle = detailsToggle else { return }
        box.isHidden.toggle()
        let title = box.isHidden ? content.showDetails : content.hideDetails
        toggle.attributedTitle = NSAttributedString(string: title, attributes: [
            .foregroundColor: WorkbenchTokens.sub, .font: NSFont.systemFont(ofSize: 12),
        ])
    }
}

/// The workbench's `.button` (filled primary, outlined secondary), drawn the same in key and inactive windows.
final class WorkbenchButton: NSButton {
    private let primary: Bool
    private let compact: Bool
    private var pressed = false

    init(title: String, primary: Bool, compact: Bool = false) {
        self.primary = primary
        self.compact = compact
        super.init(frame: .zero)
        self.title = title
        isBordered = false
        wantsLayer = true
        setButtonType(.momentaryPushIn)
        font = .systemFont(ofSize: compact ? 12 : 13, weight: .medium)
        focusRingType = .default
        heightAnchor.constraint(equalToConstant: compact ? 24 : 32).isActive = true
        updateTitle()
    }

    required init?(coder: NSCoder) { nil }

    override var intrinsicContentSize: NSSize {
        let text = attributedTitle.size()
        return NSSize(width: ceil(text.width) + (compact ? 20 : 32), height: compact ? 24 : 32)
    }

    override var wantsUpdateLayer: Bool { true }

    override func updateLayer() {
        guard let layer else { return }
        layer.cornerRadius = 6
        let fill = primary ? WorkbenchTokens.accent : WorkbenchTokens.panel
        layer.backgroundColor = (pressed ? fill.blended(withFraction: 0.15, of: .black) ?? fill : fill).cgColor
        layer.borderWidth = primary ? 0 : 1
        layer.borderColor = WorkbenchTokens.nodeLine.cgColor
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateTitle()
    }

    private func updateTitle() {
        let style = NSMutableParagraphStyle()
        style.alignment = .center
        attributedTitle = NSAttributedString(string: title, attributes: [
            .foregroundColor: primary ? WorkbenchTokens.accentInk : WorkbenchTokens.ink,
            .font: font ?? NSFont.systemFont(ofSize: 13, weight: .medium), .paragraphStyle: style,
        ])
    }

    override func mouseDown(with event: NSEvent) {
        pressed = true
        needsDisplay = true
        super.mouseDown(with: event)
        pressed = false
        needsDisplay = true
    }
}

private final class ActionTarget: NSObject {
    private let action: () -> Void
    init(_ action: @escaping () -> Void) { self.action = action }
    @objc func run() { action() }
}

private final class Card: NSView {
    override var wantsUpdateLayer: Bool { true }
    override func updateLayer() {
        layer?.backgroundColor = WorkbenchTokens.panel.cgColor
        layer?.borderColor = WorkbenchTokens.hair.cgColor
        layer?.borderWidth = 1
        layer?.cornerRadius = 12
        layer?.shadowColor = NSColor.black.cgColor
        layer?.shadowOpacity = 0.08
        layer?.shadowRadius = 16
        layer?.shadowOffset = CGSize(width: 0, height: -4)
    }

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.masksToBounds = false
    }

    required init?(coder: NSCoder) { nil }
}

private final class Badge: NSView {
    override var wantsUpdateLayer: Bool { true }
    override func updateLayer() {
        layer?.backgroundColor = WorkbenchTokens.accentWash.cgColor
        layer?.cornerRadius = 24
    }

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
    }

    required init?(coder: NSCoder) { nil }
}
