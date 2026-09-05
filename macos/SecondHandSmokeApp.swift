// Second Hand Smoke — verified card offload for camera and sound media.
// Copyright (C) 2026  CnF Works
//
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License as published by the Free
// Software Foundation, either version 3 of the License, or (at your option)
// any later version.
//
// This program is distributed in the hope that it will be useful, but WITHOUT
// ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
// FOR A PARTICULAR PURPOSE.  See the GNU General Public License for details.
//
// You should have received a copy of the GNU General Public License along with
// this program.  If not, see <https://www.gnu.org/licenses/>.

// The native shell. It owns the window, starts the bundled Python server,
// waits for it to accept connections, and shuts it down again on quit.
//
// It deliberately does no work of its own. Every decision about cards, copies,
// checksums and the format gate stays in the Python that has been through real
// shoot days. This file exists so that none of that requires a Terminal.

import Cocoa
import WebKit

final class SecondHandSmokeApp: NSObject, NSApplicationDelegate, WKNavigationDelegate,
                                WKUIDelegate, WKDownloadDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var server: Process?
    private var port: UInt16 = 0
    private var logURL: URL!
    private var didLoadInterface = false

    // MARK: - Startup

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()

        logURL = prepareLogFile()
        showStatus("Starting Second Hand Smoke…")

        do {
            port = try reserveLocalPort()
            try startServer(on: port)
        } catch {
            failToStart("The offload server could not be started.\n\n\(error.localizedDescription)")
            return
        }

        waitForServer()
    }

    // MARK: - Window and menu

    private func buildWindow() {
        let config = WKWebViewConfiguration()
        config.defaultWebpagePreferences.allowsContentJavaScript = true

        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = false

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1320, height: 940),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Second Hand Smoke"
        window.contentView = webView
        window.center()
        window.minSize = NSSize(width: 900, height: 640)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // Built by hand because this app is compiled with swiftc rather than through
    // an Xcode target, and an app with no main menu has no working Command-Q and
    // no working Command-C. Both matter here: quitting is how the server gets
    // shut down cleanly, and every path in this interface is one the operator
    // will want to copy out of it.
    private func buildMenu() {
        let mainMenu = NSMenu()

        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Second Hand Smoke",
                        action: #selector(showAbout(_:)), keyEquivalent: "").target = self
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Reveal Server Log in Finder",
                        action: #selector(revealLog(_:)), keyEquivalent: "").target = self
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Hide Second Hand Smoke",
                        action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "Quit Second Hand Smoke",
                        action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        mainMenu.addItem(appItem)

        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All",
                         action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu
        mainMenu.addItem(editItem)

        let viewItem = NSMenuItem()
        let viewMenu = NSMenu(title: "View")
        viewMenu.addItem(withTitle: "Reload", action: #selector(reload(_:)),
                         keyEquivalent: "r").target = self
        viewMenu.addItem(withTitle: "Enter Full Screen",
                         action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        viewItem.submenu = viewMenu
        mainMenu.addItem(viewItem)

        NSApp.mainMenu = mainMenu
    }

    @objc private func reload(_ sender: Any?) {
        guard didLoadInterface else { return }
        webView.reload()
    }

    @objc private func revealLog(_ sender: Any?) {
        NSWorkspace.shared.activateFileViewerSelecting([logURL])
    }

    @objc private func showAbout(_ sender: Any?) {
        let info = Bundle.main.infoDictionary ?? [:]
        let version = info["CFBundleShortVersionString"] as? String ?? "unknown"
        let alert = NSAlert()
        alert.messageText = "Second Hand Smoke \(version)"
        alert.informativeText = """
            A CnF Works Project.

            Free software under the GNU General Public License v3.0.
            No telemetry, no accounts, no licence checks. Nothing leaves this Mac.

            Interface: http://127.0.0.1:\(port)
            Server log: \(logURL.path)
            """
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    // MARK: - The bundled server

    // Ask the kernel for a free port rather than assuming 3838 is available.
    // Two copies of this app fighting over one port is a confusing failure, and
    // the operator never types this number anyway.
    // Darwin.bind and Darwin.connect are spelled out because NSObject has its
    // own bind(_:to:withKeyPath:options:) and this class inherits it. Unqualified,
    // the compiler picks the wrong one.
    private func reserveLocalPort() throws -> UInt16 {
        for candidate in [UInt16(3838), UInt16(0)] {
            let fd = socket(AF_INET, SOCK_STREAM, 0)
            if fd < 0 { continue }
            defer { close(fd) }
            var yes: Int32 = 1
            setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, socklen_t(MemoryLayout<Int32>.size))

            var addr = sockaddr_in()
            addr.sin_family = sa_family_t(AF_INET)
            addr.sin_port = candidate.bigEndian
            addr.sin_addr.s_addr = INADDR_LOOPBACK.bigEndian
            let bound = withUnsafePointer(to: &addr) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
            if bound != 0 { continue }

            var out = sockaddr_in()
            var length = socklen_t(MemoryLayout<sockaddr_in>.size)
            let named = withUnsafeMutablePointer(to: &out) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    getsockname(fd, $0, &length)
                }
            }
            if named == 0 {
                let assigned = UInt16(bigEndian: out.sin_port)
                if assigned != 0 { return assigned }
            }
        }
        throw NSError(domain: "SecondHandSmoke", code: 1, userInfo: [
            NSLocalizedDescriptionKey: "No local port was available for the interface."
        ])
    }

    private func prepareLogFile() -> URL {
        let logs = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/Second Hand Smoke", isDirectory: true)
        try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        let url = logs.appendingPathComponent("server.log")
        // Keep the last few launches, do not truncate. The launch whose log
        // matters most is usually the one *before* the current one: somebody
        // whose app died mid-offload reopens it, and truncating here destroys
        // the only evidence of what happened at the exact moment they most
        // need an answer. server.log is current; server.1.log is the launch
        // before it, and so on to server.4.log.
        let keep = 5
        let fm = FileManager.default
        let rotated = { (n: Int) in
            logs.appendingPathComponent(n == 0 ? "server.log" : "server.\(n).log")
        }
        try? fm.removeItem(at: rotated(keep - 1))
        for n in stride(from: keep - 2, through: 0, by: -1) {
            if fm.fileExists(atPath: rotated(n).path) {
                try? fm.moveItem(at: rotated(n), to: rotated(n + 1))
            }
        }
        fm.createFile(atPath: url.path, contents: Data())
        return url
    }

    private func startServer(on port: UInt16) throws {
        guard let resources = Bundle.main.resourceURL else {
            throw NSError(domain: "SecondHandSmoke", code: 2, userInfo: [
                NSLocalizedDescriptionKey: "The application bundle has no Resources folder."
            ])
        }
        let executable = resources
            .appendingPathComponent("server", isDirectory: true)
            .appendingPathComponent("SecondHandSmoke-server")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw NSError(domain: "SecondHandSmoke", code: 3, userInfo: [
                NSLocalizedDescriptionKey:
                    "The offload server is missing from the application bundle.\n\n" +
                    "Expected it at:\n\(executable.path)"
            ])
        }

        let process = Process()
        process.executableURL = executable
        var environment = ProcessInfo.processInfo.environment
        environment["DIT_PORT"] = String(port)
        environment["PYTHONUNBUFFERED"] = "1"
        // Homebrew is not on a Finder-launched app's PATH. Python's own tool
        // discovery covers this too (toolpaths.py), but putting the usual
        // locations back on PATH also fixes anything ffmpeg itself shells out
        // to, and costs nothing when the folders do not exist.
        let inherited = environment["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"
        environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + inherited
        process.environment = environment

        if let handle = try? FileHandle(forWritingTo: logURL) {
            handle.seekToEndOfFile()
            process.standardOutput = handle
            process.standardError = handle
        }

        // If this shell dies without getting to applicationWillTerminate, the
        // server notices its parent has gone and exits on its own. An orphaned
        // server holding the catalog open is not something to leave behind on
        // someone's machine.
        try process.run()
        server = process
    }

    private func waitForServer() {
        let deadline = Date().addingTimeInterval(60)
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            while Date() < deadline {
                guard let self else { return }
                if self.server?.isRunning == false {
                    DispatchQueue.main.async {
                        self.failToStart("The offload server stopped while starting up.")
                    }
                    return
                }
                if self.canConnect(to: self.port) {
                    DispatchQueue.main.async { self.loadInterface() }
                    return
                }
                Thread.sleep(forTimeInterval: 0.25)
            }
            DispatchQueue.main.async {
                self?.failToStart("The offload server did not start listening within 60 seconds.")
            }
        }
    }

    private func canConnect(to port: UInt16) -> Bool {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        if fd < 0 { return false }
        defer { close(fd) }
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        addr.sin_addr.s_addr = INADDR_LOOPBACK.bigEndian
        let result = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        return result == 0
    }

    private func loadInterface() {
        didLoadInterface = true
        guard let url = URL(string: "http://127.0.0.1:\(port)/") else { return }
        webView.load(URLRequest(url: url))
    }

    // MARK: - Shutdown

    func applicationWillTerminate(_ notification: Notification) {
        stopServer()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    // A job in progress is the operator's decision to interrupt, not this
    // wrapper's. Interruption is already safe by design — partial writes are
    // discarded and the offload resumes — so the shutdown here is a plain,
    // prompt one rather than a dialog nobody reads at 4am.
    private func stopServer() {
        guard let process = server, process.isRunning else { return }
        process.terminate()
        let deadline = Date().addingTimeInterval(5)
        while process.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        if process.isRunning {
            kill(process.processIdentifier, SIGKILL)
        }
        server = nil
    }

    // MARK: - Failure

    private func failToStart(_ message: String) {
        stopServer()
        let alert = NSAlert()
        alert.messageText = "Second Hand Smoke could not start"
        alert.informativeText = message + "\n\nThe server log is at:\n\(logURL.path)"
        alert.alertStyle = .critical
        alert.addButton(withTitle: "Show Log")
        alert.addButton(withTitle: "Quit")
        if alert.runModal() == .alertFirstButtonReturn {
            NSWorkspace.shared.activateFileViewerSelecting([logURL])
        }
        NSApp.terminate(nil)
    }

    private func showStatus(_ text: String) {
        let escaped = text.replacingOccurrences(of: "&", with: "&amp;")
                          .replacingOccurrences(of: "<", with: "&lt;")
        webView.loadHTMLString("""
            <!doctype html><meta charset="utf-8">
            <style>
              html,body{height:100%;margin:0}
              body{display:flex;align-items:center;justify-content:center;
                   background:#101114;color:#e8e6e1;
                   font:15px/1.5 -apple-system,BlinkMacSystemFont,sans-serif}
            </style>
            <p>\(escaped)</p>
            """, baseURL: nil)
    }

    // MARK: - Navigation, downloads and dialogs

    // Everything on 127.0.0.1 is this application's own interface and stays in
    // the window. Anything else is a deliberate click on an outside link and
    // belongs in the operator's normal browser, not silently inside the app.
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if navigationAction.shouldPerformDownload {
            decisionHandler(.download)
            return
        }
        if let url = navigationAction.request.url {
            let scheme = url.scheme?.lowercased() ?? ""
            let isLocal = (url.host == "127.0.0.1" || url.host == "localhost")
            if ["http", "https"].contains(scheme) && !isLocal {
                NSWorkspace.shared.open(url)
                decisionHandler(.cancel)
                return
            }
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        decisionHandler(navigationResponse.canShowMIMEType ? .allow : .download)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction,
                 didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse,
                 didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.canCreateDirectories = true
        panel.isExtensionHidden = false
        panel.begin { result in completionHandler(result == .OK ? panel.url : nil) }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        guard didLoadInterface else { return }
        showStatus("The interface stopped responding. Reload with Command-R.")
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = "Second Hand Smoke"
        alert.informativeText = message
        alert.addButton(withTitle: "OK")
        alert.runModal()
        completionHandler()
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = "Second Hand Smoke"
        alert.informativeText = message
        alert.addButton(withTitle: "Continue")
        alert.addButton(withTitle: "Cancel")
        alert.alertStyle = .warning
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        // allowsDirectories is true only for a directory-picking input. A normal
        // file input must offer files, not folders.
        panel.canChooseFiles = true
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.begin { result in completionHandler(result == .OK ? panel.urls : nil) }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url { NSWorkspace.shared.open(url) }
        return nil
    }
}
