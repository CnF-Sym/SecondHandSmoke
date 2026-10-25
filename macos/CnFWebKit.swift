import Foundation
import WebKit

// MARK: - CnF web views
//
// For CnF Works apps that show their screens in a WKWebView. A normal WKWebView keeps its cookies, caches
// and localStorage in ~/Library/WebKit/<bundle ID> and ~/Library/Caches/<bundle ID>, which breaks the CnF
// rule. A web view made from CnFWebKit.configuration() writes nothing to disk. Whatever the page keeps in
// localStorage is mirrored, on every change, to one JSON file in the app's CnF-Libraries-Installs folder
// and put back when the app opens again.
//
// Upgrading from a build that used the normal store: the first launch (no JSON file yet) still opens the
// old store once, copies its localStorage into the JSON file, and from then on the old store is never used.
// CnFLibraries.removeSystemLeftovers() on quit deletes it.
//
// Also: use CnFWebKit.session for any URLSession work. URLSession.shared keeps cookies and a cache in
// ~/Library/HTTPStorages and ~/Library/Caches.
//
// This block is identical in every CnF Works web-view app. Copy it as-is.
@MainActor
final class CnFWebKit: NSObject, WKScriptMessageHandler {
    /// Nothing on disk: no cookies, no URL cache.
    nonisolated static let session = URLSession(configuration: .ephemeral)

    nonisolated private static let handlerName = "cnfStorage"

    let storageFile: URL?
    private let controller = WKUserContentController()
    private let adoptingOldStore: Bool
    private var snapshot: [String: String] = [:]

    /// storageFile: where localStorage is kept (e.g. <app folder>/Settings/web-storage.json), or nil for a
    /// page that keeps nothing.
    init(storageFile: URL?) {
        self.storageFile = storageFile
        var adopting = false
        if let file = storageFile {
            if let data = try? Data(contentsOf: file),
               let saved = (try? JSONSerialization.jsonObject(with: data)) as? [String: String] {
                snapshot = saved
            } else if !FileManager.default.fileExists(atPath: file.path) {
                adopting = true
            }
        }
        adoptingOldStore = adopting
        super.init()
        if storageFile != nil {
            controller.add(self, name: Self.handlerName)
            installScript()
        }
    }

    func configuration() -> WKWebViewConfiguration {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = adoptingOldStore ? .default() : .nonPersistent()
        config.userContentController = controller
        return config
    }

    private func installScript() {
        controller.removeAllUserScripts()
        let json = (try? JSONSerialization.data(withJSONObject: snapshot))
            .flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
        let source = """
        (function () {
          var host = location.hostname;
          if (window.top !== window || !(host === '' || host === '127.0.0.1' || host === 'localhost')) return;
          var ls;
          try { ls = window.localStorage; } catch (e) { return; }
          var proto = Object.getPrototypeOf(ls);
          var setItem = proto.setItem, removeItem = proto.removeItem, clear = proto.clear;
          function post() {
            var all = {};
            for (var i = 0; i < ls.length; i++) { var k = ls.key(i); all[k] = ls.getItem(k); }
            try { window.webkit.messageHandlers.\(Self.handlerName).postMessage(JSON.stringify(all)); } catch (e) {}
          }
          if (\(adoptingOldStore ? "true" : "false")) {
            post();
          } else {
            var saved = \(json);
            try {
              clear.call(ls);
              for (var key in saved) { if (Object.prototype.hasOwnProperty.call(saved, key)) setItem.call(ls, key, String(saved[key])); }
            } catch (e) {}
          }
          proto.setItem = function () { var r = setItem.apply(this, arguments); if (this === ls) post(); return r; };
          proto.removeItem = function () { var r = removeItem.apply(this, arguments); if (this === ls) post(); return r; };
          proto.clear = function () { var r = clear.apply(this, arguments); if (this === ls) post(); return r; };
        })();
        """
        controller.addUserScript(WKUserScript(source: source, injectionTime: .atDocumentStart, forMainFrameOnly: true))
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == Self.handlerName, message.frameInfo.isMainFrame,
              let text = message.body as? String, let data = text.data(using: .utf8),
              let all = (try? JSONSerialization.jsonObject(with: data)) as? [String: String],
              let file = storageFile else { return }
        snapshot = all
        if !adoptingOldStore { installScript() }   // a reload restores the newest values, not launch-time ones
        try? FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: file, options: .atomic)
    }
}
