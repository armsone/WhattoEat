#if targetEnvironment(macCatalyst)
import CryptoKit
import Foundation
import SwiftUI
import UIKit

final class MacDirectUpdateManager: NSObject, ObservableObject, URLSessionDownloadDelegate {
    enum Phase {
        case idle, checking, current, available, downloading, ready, error
    }

    struct Release {
        let tag: String
        let version: String
        let build: String
        let notes: String
        let name: String
        let size: Int64
        let sha256: String
        let url: URL
    }

    static let shared = MacDirectUpdateManager()

    @Published private(set) var phase: Phase = .idle
    @Published private(set) var message = "업데이트를 확인할 수 있어요."
    @Published private(set) var release: Release?
    @Published private(set) var downloadedBytes: Int64 = 0
    @Published private(set) var downloadSize: Int64 = 0
    @Published var automaticDownload: Bool {
        didSet { defaults.set(automaticDownload, forKey: Self.automaticKey) }
    }

    private let defaults = UserDefaults.standard
    private var task: URLSessionDownloadTask?
    private var downloadSession: URLSession?
    private var automaticallyDownloading = false

    private override init() {
        if defaults.object(forKey: Self.automaticKey) == nil {
            automaticDownload = true
        } else {
            automaticDownload = defaults.bool(forKey: Self.automaticKey)
        }
        super.init()
    }

    func start() {
        guard phase == .idle else { return }
        check(automatic: true)
    }

    func check(automatic: Bool = false) {
        guard phase != .checking, phase != .downloading else { return }
        phase = .checking
        message = "새 버전을 확인하는 중…"
        var request = URLRequest(url: Self.apiURL)
        request.timeoutInterval = 15
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.setValue("2022-11-28", forHTTPHeaderField: "X-GitHub-Api-Version")
        request.setValue("WhattoEat-Mac-updater", forHTTPHeaderField: "User-Agent")
        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            guard let self else { return }
            let result = Result { try Self.parseRelease(data: data, response: response, error: error) }
            DispatchQueue.main.async {
                switch result {
                case .success(nil):
                    self.release = nil
                    self.phase = .current
                    self.message = "최신 버전을 사용 중이에요."
                case .success(let candidate?):
                    self.release = candidate
                    self.phase = .available
                    self.downloadSize = candidate.size
                    self.downloadedBytes = 0
                    self.message = "새 버전 \(candidate.version)을 받을 수 있어요."
                    if automatic, self.automaticDownload {
                        if ProcessInfo.processInfo.isLowPowerModeEnabled {
                            self.message = "저전력 모드가 끝나면 자동으로 다운로드해요."
                        } else {
                            self.download(automatic: true)
                        }
                    }
                case .failure:
                    self.phase = .error
                    self.message = "업데이트를 확인하지 못했어요. 다시 시도해 주세요."
                }
            }
        }.resume()
    }

    func download(automatic: Bool = false) {
        guard let release, phase != .downloading else { return }
        automaticallyDownloading = automatic
        let configuration = URLSessionConfiguration.default
        configuration.waitsForConnectivity = true
        configuration.allowsConstrainedNetworkAccess = !automatic
        configuration.allowsExpensiveNetworkAccess = !automatic
        configuration.isDiscretionary = automatic
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: .main)
        downloadSession = session
        var request = URLRequest(url: release.url)
        request.setValue("WhattoEat-Mac-updater", forHTTPHeaderField: "User-Agent")
        task = session.downloadTask(with: request)
        phase = .downloading
        message = automatic ? "업데이트를 자동으로 다운로드하는 중…" : "다운로드 중…"
        task?.resume()
    }

    func cancel() {
        task?.cancel()
        task = nil
        downloadSession?.invalidateAndCancel()
        downloadSession = nil
        downloadedBytes = 0
        phase = .available
        message = "다운로드를 취소했어요."
    }

    func retry() {
        if release == nil { check() } else { download() }
    }

    func openInstaller() {
        guard let release else { return }
        let file = Self.destination(for: release)
        guard FileManager.default.fileExists(atPath: file.path) else {
            phase = .error
            message = "설치 파일을 찾지 못했어요. 다시 다운로드해 주세요."
            return
        }
        UIApplication.shared.open(file, options: [:]) { [weak self] opened in
            DispatchQueue.main.async {
                if !opened {
                    self?.phase = .error
                    self?.message = "설치 파일을 열지 못했어요. 다시 시도해 주세요."
                }
            }
        }
    }

    func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didWriteData bytesWritten: Int64,
        totalBytesWritten: Int64,
        totalBytesExpectedToWrite: Int64
    ) {
        downloadedBytes = totalBytesWritten
        if totalBytesExpectedToWrite > 0 { downloadSize = totalBytesExpectedToWrite }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        guard let release else { return }
        do {
            let response = downloadTask.response as? HTTPURLResponse
            guard response?.statusCode == 200,
                  response?.url?.scheme == "https",
                  ["github.com", "release-assets.githubusercontent.com"].contains(response?.url?.host?.lowercased() ?? ""),
                  response?.expectedContentLength == release.size || (response?.expectedContentLength ?? -1) <= 0
            else { throw UpdateError.invalidDownload }
            let destination = Self.destination(for: release)
            try FileManager.default.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
            try? FileManager.default.removeItem(at: destination)
            try FileManager.default.moveItem(at: location, to: destination)
            message = "파일의 안전성을 확인하는 중…"
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                let valid = Self.validate(file: destination, release: release)
                DispatchQueue.main.async {
                    guard let self else { return }
                    if valid {
                        self.phase = .ready
                        self.downloadedBytes = release.size
                        self.message = "설치 준비가 끝났어요."
                    } else {
                        try? FileManager.default.removeItem(at: destination)
                        self.phase = .error
                        self.message = "파일 확인값이 달라 설치하지 않았어요."
                    }
                }
            }
        } catch {
            phase = .error
            message = "다운로드 파일을 저장하지 못했어요. 다시 시도해 주세요."
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        self.task = nil
        session.finishTasksAndInvalidate()
        downloadSession = nil
        if error != nil, phase == .downloading {
            phase = .error
            message = automaticallyDownloading
                ? "자동 다운로드를 마치지 못했어요. 수동으로 다시 시도할 수 있어요."
                : "다운로드하지 못했어요. 다시 시도해 주세요."
        }
    }

    private static func parseRelease(data: Data?, response: URLResponse?, error: Error?) throws -> Release? {
        if let error { throw error }
        guard let http = response as? HTTPURLResponse, http.statusCode == 200, let data,
              let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              json["draft"] as? Bool == false, json["prerelease"] as? Bool == false,
              let tag = json["tag_name"] as? String,
              tag.range(of: #"^v\d+\.\d+\.\d+$"#, options: .regularExpression) != nil
        else { throw UpdateError.invalidMetadata }
        let version = String(tag.dropFirst())
        guard compareVersions(version, currentVersion) == .orderedDescending else { return nil }
        guard let assets = json["assets"] as? [[String: Any]] else { throw UpdateError.invalidMetadata }
        let pattern = #"^WhattoEat-Mac-"# + NSRegularExpression.escapedPattern(for: version) + #"-(\d{12})\.dmg$"#
        for asset in assets {
            guard asset["state"] as? String == "uploaded",
                  let name = asset["name"] as? String,
                  name.range(of: pattern, options: .regularExpression) != nil,
                  let build = name.firstMatch(pattern: pattern, group: 1),
                  build > currentBuild,
                  let sizeNumber = asset["size"] as? NSNumber, sizeNumber.int64Value > 0,
                  let digest = asset["digest"] as? String,
                  digest.range(of: #"^sha256:[0-9a-fA-F]{64}$"#, options: .regularExpression) != nil,
                  let urlText = asset["browser_download_url"] as? String,
                  let url = URL(string: urlText),
                  url.scheme == "https", url.host?.lowercased() == "github.com",
                  url.query == nil, url.fragment == nil,
                  url.path == "/armsone/WhattoEat/releases/download/\(tag)/\(name)"
            else { continue }
            return Release(
                tag: tag,
                version: version,
                build: build,
                notes: json["body"] as? String ?? "",
                name: name,
                size: sizeNumber.int64Value,
                sha256: String(digest.dropFirst("sha256:".count)).lowercased(),
                url: url
            )
        }
        throw UpdateError.invalidMetadata
    }

    private static func validate(file: URL, release: Release) -> Bool {
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: file.path),
              (attributes[.size] as? NSNumber)?.int64Value == release.size,
              let input = try? FileHandle(forReadingFrom: file)
        else { return false }
        defer { try? input.close() }
        var hasher = SHA256()
        while autoreleasepool(invoking: {
            let data = input.readData(ofLength: 1_048_576)
            guard !data.isEmpty else { return false }
            hasher.update(data: data)
            return true
        }) {}
        return hasher.finalize().map { String(format: "%02x", $0) }.joined() == release.sha256
    }

    private static func destination(for release: Release) -> URL {
        let root = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return root.appendingPathComponent("WhattoEat/Updates", isDirectory: true).appendingPathComponent(release.name)
    }

    private static var currentVersion: String { Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0" }
    private static var currentBuild: String { Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0" }
    private static let apiURL = URL(string: "https://api.github.com/repos/armsone/WhattoEat/releases/latest")!
    private static let automaticKey = "directUpdateAutomaticDownload"
    private enum UpdateError: Error { case invalidMetadata, invalidDownload }
}

private func compareVersions(_ left: String, _ right: String) -> ComparisonResult {
    let a = left.split(separator: ".").map { Int($0) ?? 0 }
    let b = right.split(separator: ".").map { Int($0) ?? 0 }
    for index in 0..<max(a.count, b.count) {
        let lhs = index < a.count ? a[index] : 0
        let rhs = index < b.count ? b[index] : 0
        if lhs != rhs { return lhs < rhs ? .orderedAscending : .orderedDescending }
    }
    return .orderedSame
}

private extension String {
    func firstMatch(pattern: String, group: Int) -> String? {
        guard let expression = try? NSRegularExpression(pattern: pattern),
              let match = expression.firstMatch(in: self, range: NSRange(startIndex..., in: self)),
              let range = Range(match.range(at: group), in: self)
        else { return nil }
        return String(self[range])
    }
}

struct MacDirectUpdateSettingsView: View {
    @ObservedObject var updater: MacDirectUpdateManager

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Toggle("업데이트 자동 다운로드", isOn: $updater.automaticDownload)
            Text(updater.message).font(AppTypography.supporting).foregroundStyle(Color.charcoalSoft)
            if let release = updater.release {
                Text("\(release.version) · 빌드 \(release.build) · \(ByteCountFormatter.string(fromByteCount: release.size, countStyle: .file))")
                    .font(AppTypography.supporting)
                if updater.phase == .downloading {
                    ProgressView(value: Double(updater.downloadedBytes), total: Double(max(updater.downloadSize, 1)))
                }
                if !release.notes.isEmpty { Text(release.notes).font(AppTypography.supporting).lineLimit(4) }
            }
            HStack {
                switch updater.phase {
                case .available: Button("다운로드") { updater.download() }
                case .downloading: Button("취소") { updater.cancel() }
                case .ready: Button("설치 파일 열기") { updater.openInstaller() }
                case .error: Button("다시 시도") { updater.retry() }
                default: EmptyView()
                }
                Button("업데이트 확인") { updater.check() }
                    .disabled(updater.phase == .checking || updater.phase == .downloading)
            }
        }
    }
}
#endif
