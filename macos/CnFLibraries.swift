import Foundation

// MARK: - CnF-Libraries-Installs
//
// Every CnF Works app keeps ALL of its files in one visible place, never scattered through ~/Library:
//
//     CnF-Libraries-Installs/
//         README.txt
//         <App Name>/
//             Settings/      preferences
//             Save Files/    history, anything the app remembers
//             Caches/        safe to delete any time
//             …              whatever else the app needs, each in its own folder
//
// Remove an app completely: delete the app, then delete its folder here. Nothing else is left behind —
// on quit, the app also removes what macOS itself drops in ~/Library under its bundle ID
// (preferences plist, saved window state, caches, HTTP storage).
//
// The folder is found in this order:
//   1. $CNF_LIBRARIES_INSTALLS
//   2. a "CnF-Libraries-Installs" folder beside the app, or in any folder above it on the same drive
//      (put one at the top of the Mac mini's programs SSD and every app on that drive uses it)
//   3. ~/CnF-Libraries-Installs (created on first use)
//
// This block is identical in every CnF Works app. Copy it as-is into new apps.
enum CnFLibraries {
    static let folderName = "CnF-Libraries-Installs"

    static let root: URL = resolveRoot()

    static func resolveRoot(bundleURL: URL = Bundle.main.bundleURL,
                            home: URL = FileManager.default.homeDirectoryForCurrentUser,
                            environment: [String: String] = ProcessInfo.processInfo.environment) -> URL {
        if let value = environment["CNF_LIBRARIES_INSTALLS"], !value.isEmpty {
            return URL(fileURLWithPath: (value as NSString).expandingTildeInPath, isDirectory: true).standardizedFileURL
        }
        var dir = bundleURL.standardizedFileURL.deletingLastPathComponent()
        while dir.path != "/" && dir.path != "/Volumes" && !dir.path.isEmpty {
            let candidate = dir.appendingPathComponent(folderName, isDirectory: true)
            var isDir: ObjCBool = false
            if FileManager.default.fileExists(atPath: candidate.path, isDirectory: &isDir), isDir.boolValue {
                return candidate
            }
            dir = dir.deletingLastPathComponent()
        }
        return home.appendingPathComponent(folderName, isDirectory: true)
    }

    /// <root>/<appName>/<sub…>, created on demand. The README at the top is written once.
    static func folder(_ appName: String, _ sub: String? = nil, root: URL = CnFLibraries.root) -> URL {
        var url = root.appendingPathComponent(appName, isDirectory: true)
        if let sub { url.appendPathComponent(sub, isDirectory: true) }
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        writeReadme(in: root)
        return url
    }

    static func writeReadme(in root: URL) {
        let url = root.appendingPathComponent("README.txt")
        guard !FileManager.default.fileExists(atPath: url.path) else { return }
        let text = """
        CnF-Libraries-Installs
        ======================

        Every CnF Works app keeps ALL of its files here — settings, save files, caches, logs, libraries —
        one folder per app, instead of scattering them through hidden system folders.

        To remove an app completely: delete the app, then delete its folder here. Nothing else is left behind.

        To keep these on another drive (e.g. the Mac mini's programs SSD): put a folder named
        "CnF-Libraries-Installs" at the top of that drive, next to the apps. Apps on that drive use it.

        """
        try? text.data(using: .utf8)?.write(to: url, options: .atomic)
    }

    /// True when both paths are on the same volume (nearest existing ancestor is compared).
    static func sameVolume(_ a: URL, _ b: URL) -> Bool {
        func device(_ url: URL) -> Int? {
            var p = url.standardizedFileURL
            while !FileManager.default.fileExists(atPath: p.path) && p.path != "/" { p = p.deletingLastPathComponent() }
            return (try? FileManager.default.attributesOfItem(atPath: p.path))?[.systemNumber] as? Int
        }
        guard let da = device(a), let db = device(b) else { return false }
        return da == db
    }

    /// Moves an app's old folder (e.g. ~/Library/Application Support/<App>) to its CnF folder, once.
    /// Only a same-volume rename — never a copy — and only if the new folder is missing or empty.
    @discardableResult
    static func migrate(from old: URL, to new: URL) -> Bool {
        let fm = FileManager.default
        var isDir: ObjCBool = false
        guard fm.fileExists(atPath: old.path, isDirectory: &isDir), isDir.boolValue,
              (try? fm.destinationOfSymbolicLink(atPath: old.path)) == nil else { return false }
        if fm.fileExists(atPath: new.path) {
            let visible = ((try? fm.contentsOfDirectory(atPath: new.path)) ?? []).filter { !$0.hasPrefix(".") }
            guard visible.isEmpty else { return false }
            try? fm.removeItem(at: new)
        }
        try? fm.createDirectory(at: new.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard sameVolume(old, new.deletingLastPathComponent()) else { return false }
        do { try fm.moveItem(at: old, to: new) } catch { return false }
        return true
    }

    /// What macOS writes under ~/Library for any app, by bundle ID. Called when the app quits.
    static func removeSystemLeftovers(bundleID: String? = Bundle.main.bundleIdentifier,
                                      home: URL = FileManager.default.homeDirectoryForCurrentUser) {
        guard let id = bundleID, !id.isEmpty, !id.contains("/"), id.contains(".") else { return }
        UserDefaults.standard.removePersistentDomain(forName: id)
        UserDefaults.standard.synchronize()
        let fm = FileManager.default
        let library = home.appendingPathComponent("Library", isDirectory: true)
        // The plist file is listed too. macOS's preferences daemon (cfprefsd) may still write it back
        // as an EMPTY file after the app exits — ~40 bytes, "{}", emptied again on every quit, never
        // grows. That one empty file is the only thing that can remain in ~/Library.
        for rel in ["Preferences/\(id).plist", "Saved Application State/\(id).savedState", "Caches/\(id)", "HTTPStorages/\(id)",
                    "HTTPStorages/\(id).binarycookies", "WebKit/\(id)"] {
            let url = library.appendingPathComponent(rel)
            guard fm.fileExists(atPath: url.path), (try? fm.destinationOfSymbolicLink(atPath: url.path)) == nil else { continue }
            try? fm.removeItem(at: url)
        }
    }
}
