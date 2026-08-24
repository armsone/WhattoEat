import SwiftUI
import CoreLocation
import MapKit
import UserNotifications
import UIKit

enum Phase {
    case idle
    case waitingPermission
    case denied
    case locating
    case searchingRegion
    case manualIdle
    case loading
    case failed(String)
    case empty
    case results([Restaurant])
}

enum LocationMode: String {
    case auto
    case manual
}

enum MapProvider: String, CaseIterable, Identifiable {
    case apple
    case naver
    case kakao
    case google

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .apple: return "Apple 지도"
        case .naver: return "네이버 지도"
        case .kakao: return "카카오맵(다음)"
        case .google: return "Google 지도"
        }
    }

    var shortName: String {
        switch self {
        case .apple: return "Apple"
        case .naver: return "네이버"
        case .kakao: return "카카오"
        case .google: return "Google"
        }
    }

    var iconAsset: String {
        switch self {
        case .apple: return "MapApple"
        case .naver: return "MapNaver"
        case .kakao: return "MapKakao"
        case .google: return "MapGoogle"
        }
    }
}

struct Decision: Identifiable {
    let menu: String
    let restaurant: Restaurant
    var id: String { menu + "|" + restaurant.id }
}

private extension Restaurant {
    var displayPhone: String? {
        guard let phone else { return nil }
        let trimmed = phone.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    var telURL: URL? {
        guard let trimmed = displayPhone else { return nil }
        let digits = trimmed.filter { "0123456789".contains($0) }
        guard digits.count >= 3 else { return nil }
        let number = (trimmed.first == "+" ? "+" : "") + digits
        return URL(string: "tel:\(number)")
    }
}

// MARK: - 점심 알림

/// 이 앱 기능 전용 로컬 알림. 서버 푸시가 아니며, 알림 문구도 실시간 데이터를 주장하지 않는다.
enum LunchReminder {
    static let identifier = "whattoeat.lunch.daily"

    static func cancel() {
        UNUserNotificationCenter.current()
            .removePendingNotificationRequests(withIdentifiers: [identifier])
    }

    static func schedule(hour: Int, minute: Int, leadMinutes: Int,
                         regionLabel: String, topMenu: String?) {
        let total = (hour * 60 + minute - leadMinutes + 1440) % 1440
        var components = DateComponents()
        components.hour = total / 60
        components.minute = total % 60

        let content = UNMutableNotificationContent()
        content.title = "곧 점심시간이에요"
        if let menu = topMenu, !menu.isEmpty {
            content.body = "\(regionLabel) 근처에서 마지막으로 본 추천 후보는 ‘\(menu)’였어요. 앱을 열면 주변 후보를 다시 찾아 드려요."
        } else {
            content.body = "\(regionLabel) 근처 점심 후보를 앱에서 찾아보세요."
        }
        content.sound = .default

        let trigger = UNCalendarNotificationTrigger(dateMatching: components, repeats: true)
        let request = UNNotificationRequest(identifier: identifier, content: content, trigger: trigger)
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [identifier])
        center.add(request)
    }
}

// MARK: - 메인 화면

private enum AppPage { case home, region, result, decision, history, favorites, profile }
private enum BottomDestination { case home, history, region, recommend, favorites }

struct ContentView: View {
    @StateObject private var locationManager = LocationManager()
    @StateObject private var store = ChoiceStore()
    @State private var phase: Phase = .idle
    @State private var decision: Decision?
    @State private var page: AppPage = .home
    @State private var pageBeforeSettings: AppPage = .home
    @State private var regionQuery = ""
    @State private var autoRegionName = ""
    @State private var autoLocationStatus = "현 위치를 다시 확인할 수 있어요"
    @State private var nearbyRegions: [String] = []
    @State private var recordRegionOnNextLoad = false
    @FocusState private var regionSearchFocused: Bool
    /// 비동기 검색/API 결과가 현재 상태를 덮어쓰지 못하게 하는 취소 토큰.
    @State private var loadToken = UUID()
    @Environment(\.scenePhase) private var scenePhase

    @AppStorage("locationMode") private var locationModeRaw = LocationMode.auto.rawValue
    @AppStorage("manualRegionText") private var manualRegionText = ""
    @AppStorage("manualResolvedName") private var manualResolvedName = ""
    @AppStorage("manualLatitude") private var manualLatitude = 0.0
    @AppStorage("manualLongitude") private var manualLongitude = 0.0
    @AppStorage("lastTopMenu") private var lastTopMenu = ""
    @AppStorage("lunchNotifyEnabled") private var lunchNotifyEnabled = false
    @AppStorage("lunchHour") private var lunchHour = 12
    @AppStorage("lunchMinute") private var lunchMinute = 0
    @AppStorage("lunchLeadMinutes") private var lunchLeadMinutes = 5

    private var mode: LocationMode { LocationMode(rawValue: locationModeRaw) ?? .auto }
    private var hasManualCoordinate: Bool { manualLatitude != 0 || manualLongitude != 0 }

    /// 알림·화면에 쓰는 현재 지역 표시명.
    private var activeRegionLabel: String {
        switch mode {
        case .auto:
            return autoRegionName.isEmpty ? "현재 위치" : autoRegionName
        case .manual:
            if !manualResolvedName.isEmpty { return manualResolvedName }
            if !manualRegionText.isEmpty { return manualRegionText }
            return "지정 지역"
        }
    }

    var body: some View {
        ZStack {
            Color.mintBase.ignoresSafeArea()
            Group {
                switch page {
                case .home:
                    ReferenceHomeScreen(
                    mode: mode,
                    onAuto: {
                        locationModeRaw = LocationMode.auto.rawValue
                        recordRegionOnNextLoad = true
                        page = .region
                        autoLocationStatus = "현 위치 확인 중…"
                        handleAuthorization(locationManager.authorization)
                    },
                    onManual: {
                        locationModeRaw = LocationMode.manual.rawValue
                        page = .region
                    },
                    onRecommend: {
                        if mode == .manual && !hasManualCoordinate {
                            page = .region
                        } else {
                            page = .result
                            start()
                        }
                    },
                    onSettings: { openSettings() }
                )
                case .region:
                    ReferenceRegionScreen(
                    query: $regionQuery,
                    mode: mode,
                    locationStatus: autoLocationStatus,
                    nearby: nearbyRegions,
                    frequent: store.frequentRegions,
                    searchFocused: $regionSearchFocused,
                    onSettings: { openSettings() },
                    onAuto: {
                        locationModeRaw = LocationMode.auto.rawValue
                        recordRegionOnNextLoad = true
                        autoLocationStatus = "현 위치 확인 중…"
                        handleAuthorization(locationManager.authorization)
                    },
                    onManual: { locationModeRaw = LocationMode.manual.rawValue },
                    onSearch: { searchRegion() },
                    onPreset: { searchRegion($0) }
                )
                case .result:
                    ReferenceResultsPage(
                    phase: phase,
                    regionLabel: activeRegionLabel,
                    store: store,
                    onBack: { page = .region },
                    onSettings: { openSettings() },
                    onPick: {
                        decision = $0
                        page = .decision
                    },
                    onRetry: { retry() }
                )
                case .decision:
                    if let decision {
                        DecisionView(decision: decision,
                                     regionLabel: activeRegionLabel,
                                     store: store,
                                     onClose: { page = .result })
                    } else {
                        Color.clear.onAppear { page = .result }
                    }
                case .history:
                    RecentMealsView(store: store, onSettings: { openSettings() })
                case .favorites:
                    FavoritesView(store: store, onSettings: { openSettings() })
                case .profile:
                    SettingsView(regionLabel: activeRegionLabel, onReminderSettingsChanged: {
                        refreshLunchReminder()
                    }, onClose: { page = pageBeforeSettings })
                }
            }
            .frame(maxWidth: 600)
        }
        .preferredColorScheme(.light)
        .tint(Color.caramelDeep)
        .padding(.bottom, 72)
        .overlay(alignment: .bottom) {
            ReferenceBottomBar(
                selected: selectedBottomDestination,
                onHome: { regionSearchFocused = false; page = .home },
                onRegion: { regionSearchFocused = false; page = .region },
                onRecommend: { showRecommendations() },
                onHistory: { regionSearchFocused = false; page = .history },
                onFavorites: { regionSearchFocused = false; page = .favorites }
            )
        }
        .ignoresSafeArea(.keyboard, edges: .bottom)
        .onAppear {
            // 시안의 첫 진입 상태와 동일하게 자동 위치를 기본 선택으로 보여 준다.
            locationModeRaw = LocationMode.auto.rawValue
            regionQuery = manualRegionText
        }
        .onChange(of: locationManager.authorization) { _, newValue in
            guard mode == .auto, page == .result || page == .region else { return }
            handleAuthorization(newValue)
        }
        .onChange(of: locationManager.lastLocation) { _, location in
            guard mode == .auto, page == .result || page == .region,
                  let location, case .locating = phase else { return }
            let token = beginLoad()
            Task {
                let shouldRecordRegion = recordRegionOnNextLoad
                let regionTask = Task { await resolveAutoRegion(location, token: token) }
                guard page == .result else {
                    _ = await regionTask.value
                    guard token == loadToken else { return }
                    phase = .idle
                    return
                }
                await load(latitude: location.coordinate.latitude,
                           longitude: location.coordinate.longitude,
                           token: token)
                if shouldRecordRegion,
                   case .results = phase,
                   let resolvedRegion = await regionTask.value,
                   token == loadToken {
                    store.recordRegionSelection(resolvedRegion,
                                                latitude: location.coordinate.latitude,
                                                longitude: location.coordinate.longitude)
                }
                if token == loadToken { recordRegionOnNextLoad = false }
            }
        }
        .onChange(of: locationManager.errorMessage) { _, message in
            guard mode == .auto, page == .result || page == .region,
                  let message, case .locating = phase else { return }
            if page == .region {
                autoLocationStatus = message
                phase = .idle
            } else {
                phase = .failed(message)
            }
        }
        .onChange(of: locationModeRaw) { _, _ in
            _ = beginLoad()   // 이전 모드의 진행 중 결과 무효화
            refreshLunchReminder()
        }
        .onChange(of: scenePhase) { _, newValue in
            // 알림 탭 등으로 앱이 다시 열리면 현재 모드 기준으로 결과를 새로 고친다.
            guard newValue == .active else { return }
            guard page == .result else { return }
            switch phase {
            case .results, .empty: retry()
            default: break
            }
        }
    }

    private var selectedBottomDestination: BottomDestination {
        switch page {
        case .home: return .home
        case .history: return .history
        case .region: return .region
        case .favorites: return .favorites
        case .profile:
            switch pageBeforeSettings {
            case .home: return .home
            case .history: return .history
            case .region: return .region
            case .favorites: return .favorites
            case .result, .decision: return .recommend
            case .profile: return .home
            }
        case .result, .decision: return .recommend
        }
    }

    private func showRecommendations() {
        regionSearchFocused = false
        decision = nil
        if mode == .manual && !hasManualCoordinate {
            page = .region
        } else {
            page = .result
            retry()
        }
    }

    private func openSettings() {
        regionSearchFocused = false
        pageBeforeSettings = page
        page = .profile
    }

    // MARK: 흐름 제어

    private func beginLoad() -> UUID {
        let token = UUID()
        loadToken = token
        return token
    }

    private func start() {
        switch mode {
        case .auto:
            handleAuthorization(locationManager.authorization)
        case .manual:
            if hasManualCoordinate {
                let token = beginLoad()
                Task {
                    await load(latitude: manualLatitude, longitude: manualLongitude, token: token)
                }
            } else {
                phase = .manualIdle
            }
        }
    }

    /// 위치 권한 처리는 자동 위치 모드에서만 상태를 바꾼다. 권한 거부가 지역 지정 모드를 막지 않는다.
    private func handleAuthorization(_ status: CLAuthorizationStatus) {
        guard mode == .auto else { return }
        switch status {
        case .notDetermined:
            phase = .waitingPermission
            locationManager.requestPermission()
        case .denied, .restricted:
            phase = .denied
            if page == .region { autoLocationStatus = "위치 권한이 꺼져 있어요" }
        case .authorizedWhenInUse, .authorizedAlways:
            phase = .locating
            locationManager.requestLocation()
        @unknown default:
            phase = .denied
        }
    }

    private func retry() {
        start()
    }

    @MainActor
    private func resolveAutoRegion(_ location: CLLocation, token: UUID) async -> String? {
        let placemark = try? await CLGeocoder().reverseGeocodeLocation(location).first
        guard token == loadToken else { return nil }
        let currentName = placemark.map { regionName(from: $0) } ?? ""
        let resolvedNearby = await nearbyRegionNames(around: location,
                                                     currentPlacemark: placemark,
                                                     currentName: currentName)
        guard token == loadToken else { return nil }
        autoRegionName = currentName
        autoLocationStatus = currentName.isEmpty ? "현 위치를 확인했어요" : "현 위치: \(currentName)"
        nearbyRegions = resolvedNearby
        return currentName.isEmpty ? nil : currentName
    }

    private func regionName(from placemark: CLPlacemark) -> String {
        let province = placemark.administrativeArea?
            .replacingOccurrences(of: "특별시", with: "")
            .replacingOccurrences(of: "광역시", with: "")
            .replacingOccurrences(of: "도", with: "")
        let municipality = placemark.locality
        let unit = placemark.subLocality
        let normalizedProvince = province?.replacingOccurrences(of: " ", with: "") ?? ""
        let normalizedMunicipality = municipality?
            .replacingOccurrences(of: "특별시", with: "")
            .replacingOccurrences(of: "광역시", with: "")
            .replacingOccurrences(of: "도", with: "")
            .replacingOccurrences(of: " ", with: "") ?? ""
        let parts: [String?] = normalizedMunicipality == normalizedProvince
            ? [province, unit ?? municipality]
            : [province, municipality, unit]
        return parts.compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " ")
    }

    @MainActor
    private func nearbyRegionNames(around location: CLLocation, currentPlacemark: CLPlacemark?,
                                   currentName: String) async -> [String] {
        guard let currentPlacemark else { return currentName.isEmpty ? [] : [currentName] }
        let queries = ["행정복지센터", "주민센터", "읍사무소", "면사무소"]
        let searchRegion = MKCoordinateRegion(center: location.coordinate,
                                              span: MKCoordinateSpan(latitudeDelta: 0.24,
                                                                     longitudeDelta: 0.24))
        var candidates: [(name: String, distance: CLLocationDistance)] = []
        for query in queries {
            let request = MKLocalSearch.Request()
            request.naturalLanguageQuery = query
            request.region = searchRegion
            request.resultTypes = .pointOfInterest
            let items = (try? await MKLocalSearch(request: request).start().mapItems) ?? []
            for item in items {
                let candidateLocation = CLLocation(latitude: item.placemark.coordinate.latitude,
                                                   longitude: item.placemark.coordinate.longitude)
                let distance = location.distance(from: candidateLocation)
                guard distance <= 15_000,
                      isAdministrativeCenter(item.name),
                      sameMunicipality(currentPlacemark, item.placemark) else { continue }
                let name = regionName(from: item.placemark)
                guard !name.isEmpty, name != currentName else { continue }
                if let index = candidates.firstIndex(where: { $0.name == name }) {
                    if distance < candidates[index].distance { candidates[index].distance = distance }
                } else {
                    candidates.append((name, distance))
                }
            }
        }

        var adjacent = candidates.sorted { $0.distance < $1.distance }.map(\.name)
        if adjacent.count < 2 {
            let sampled = await sampledNearbyRegionNames(around: location,
                                                         currentPlacemark: currentPlacemark,
                                                         excluding: Set([currentName] + adjacent))
            adjacent.append(contentsOf: sampled)
        }
        let current = currentName.isEmpty ? [] : [currentName]
        return current + Array(adjacent.prefix(2))
    }

    private func isAdministrativeCenter(_ name: String?) -> Bool {
        guard let name else { return false }
        return ["행정복지센터", "주민센터", "읍사무소", "면사무소", "동사무소"]
            .contains { name.contains($0) }
    }

    private func sameMunicipality(_ current: CLPlacemark, _ candidate: CLPlacemark) -> Bool {
        normalizedRegion(current.administrativeArea) == normalizedRegion(candidate.administrativeArea)
            && normalizedRegion(current.locality) == normalizedRegion(candidate.locality)
    }

    private func normalizedRegion(_ value: String?) -> String {
        value?.replacingOccurrences(of: " ", with: "") ?? ""
    }

    @MainActor
    private func sampledNearbyRegionNames(around location: CLLocation, currentPlacemark: CLPlacemark,
                                          excluding: Set<String>) async -> [String] {
        var names: [String] = []
        let radii: [CLLocationDistance] = [4_000, 8_000, 12_000]
        let bearings = stride(from: 0.0, to: 360.0, by: 45.0)
        for radius in radii {
            for bearing in bearings {
                let radians = bearing * .pi / 180
                let latitudeDelta = cos(radians) * radius / 111_000
                let longitudeScale = max(0.2, cos(location.coordinate.latitude * .pi / 180))
                let longitudeDelta = sin(radians) * radius / (111_000 * longitudeScale)
                let sample = CLLocation(latitude: location.coordinate.latitude + latitudeDelta,
                                        longitude: location.coordinate.longitude + longitudeDelta)
                guard let placemark = try? await CLGeocoder().reverseGeocodeLocation(sample).first,
                      sameMunicipality(currentPlacemark, placemark) else { continue }
                let name = regionName(from: placemark)
                guard !name.isEmpty, !excluding.contains(name), !names.contains(name) else { continue }
                names.append(name)
                if names.count == 2 { return names }
            }
        }
        return names
    }

    private func searchRegion(_ preset: String? = nil) {
        regionSearchFocused = false
        if let preset { regionQuery = preset }
        let query = (preset ?? regionQuery).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return }
        locationModeRaw = LocationMode.manual.rawValue
        page = .result
        let token = beginLoad()
        phase = .searchingRegion
        let request = MKLocalSearch.Request()
        request.naturalLanguageQuery = query
        Task { @MainActor in
            do {
                let response = try await MKLocalSearch(request: request).start()
                guard token == loadToken else { return }
                guard let item = response.mapItems.first else {
                    phase = .failed("‘\(query)’ 지역을 찾지 못했어요. ‘서울 강남’처럼 시·구 단위로 입력해 보세요.")
                    return
                }
                let coordinate = item.placemark.coordinate
                manualRegionText = query
                manualLatitude = coordinate.latitude
                manualLongitude = coordinate.longitude
                manualResolvedName = resolvedName(for: item, fallback: query)
                refreshLunchReminder()
                await load(latitude: coordinate.latitude,
                           longitude: coordinate.longitude,
                           token: token,
                           regionForHistory: manualResolvedName)
            } catch {
                guard token == loadToken else { return }
                phase = .failed("지역 검색에 실패했어요. 네트워크 상태를 확인한 뒤 다시 시도해 주세요.")
            }
        }
    }

    private func resolvedName(for item: MKMapItem, fallback: String) -> String {
        let placemark = item.placemark
        let parts = [placemark.locality, placemark.subLocality].compactMap { $0 }
        if !parts.isEmpty { return parts.joined(separator: " ") }
        return item.name ?? fallback
    }

    @MainActor
    private func load(latitude: Double, longitude: Double, token: UUID,
                      regionForHistory: String? = nil) async {
        phase = .loading
        do {
            let response = try await APIClient.fetchRestaurants(latitude: latitude,
                                                                longitude: longitude)
            guard token == loadToken else { return }
            let eligible = response.restaurants.filter { $0.isOpenNow != false }
            if eligible.isEmpty {
                phase = .empty
            } else {
                let pool = Array(eligible.sorted {
                    ($0.distanceMeters ?? .max) < ($1.distanceMeters ?? .max)
                }.prefix(13))
                let selected = pool.shuffled()
                phase = .results(selected)
                if selected.contains(where: { $0.photoURL == nil }) {
                    Task {
                        await refreshRestaurantPhotos(latitude: latitude,
                                                      longitude: longitude,
                                                      token: token)
                    }
                }
                if let regionForHistory {
                    store.recordRegionSelection(regionForHistory,
                                                latitude: latitude,
                                                longitude: longitude)
                }
                lastTopMenu = selected.first.flatMap { MenuPolicy.menus(for: $0).first } ?? ""
                refreshLunchReminder()
            }
        } catch {
            guard token == loadToken else { return }
            phase = .failed(error.localizedDescription)
        }
    }

    /// 첫 응답이 사진 보강 시간 제한에 걸렸을 때 식당 순서는 유지하고 사진 정보만 갱신한다.
    @MainActor
    private func refreshRestaurantPhotos(latitude: Double, longitude: Double, token: UUID) async {
        for delay in [900_000_000, 1_800_000_000] as [UInt64] {
            try? await Task.sleep(nanoseconds: delay)
            guard token == loadToken,
                  case .results(let current) = phase,
                  current.contains(where: { $0.photoURL == nil }) else { return }

            guard let response = try? await APIClient.fetchRestaurants(latitude: latitude,
                                                                        longitude: longitude),
                  token == loadToken else { continue }
            let refreshedByID = Dictionary(uniqueKeysWithValues: response.restaurants.map { ($0.id, $0) })
            let refreshed = current.map { restaurant in
                guard let latest = refreshedByID[restaurant.id], latest.photoURL != nil else { return restaurant }
                return latest
            }
            guard refreshed != current else { continue }
            phase = .results(refreshed)

            if let currentDecision = decision,
               let latest = refreshedByID[currentDecision.restaurant.id],
               latest.photoURL != nil {
                decision = Decision(menu: currentDecision.menu, restaurant: latest)
            }
        }
    }

    /// 설정·지역·추천이 바뀔 때마다 알림을 다시 예약한다. 꺼져 있으면 이 기능의 요청만 제거.
    private func refreshLunchReminder() {
        guard lunchNotifyEnabled else {
            LunchReminder.cancel()
            return
        }
        LunchReminder.schedule(hour: lunchHour,
                               minute: lunchMinute,
                               leadMinutes: lunchLeadMinutes,
                               regionLabel: activeRegionLabel,
                               topMenu: lastTopMenu.isEmpty ? nil : lastTopMenu)
    }
}

// MARK: - 첨부 시안 화면

private struct ReferenceHomeScreen: View {
    let mode: LocationMode
    let onAuto: () -> Void
    let onManual: () -> Void
    let onRecommend: () -> Void
    let onSettings: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    HStack {
                        HStack(spacing: -12) {
                            Image("Wordmark")
                                .resizable()
                                .scaledToFit()
                                .frame(width: 162, height: 41, alignment: .leading)
                            Text("??")
                                .font(.system(size: 36, weight: .black, design: .rounded))
                                .tracking(-1.5)
                                .foregroundStyle(Color.charcoalText)
                                .offset(y: 1)
                        }
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel("오늘 뭐 먹지??")
                        Spacer()
                        Button(action: onSettings) {
                            ReferenceIconWell(systemName: "gearshape.fill", color: .accentRed, diameter: 34)
                        }
                        .accessibilityLabel("설정")
                    }
                    .padding(.bottom, 24)

                    VStack(alignment: .leading, spacing: 14) {
                        HStack(spacing: 16) {
                            ExactWell(name: "PinWell", diameter: 58)
                            Text("어디서 드실까요?")
                                .font(.title2.bold())
                        }
                    }
                    .padding(.bottom, 20)

                    LeatherHeroCard(action: onRecommend)
                        .padding(.bottom, 20)

                    LocationChoicePanel(onAuto: onAuto, onManual: onManual)
                }
                .padding(.horizontal, 28)
                .padding(.top, 14)
                .padding(.bottom, 18)
            }
        }
    }
}

private struct ReferenceIconWell: View {
    let systemName: String
    let color: Color
    var diameter: CGFloat = 48

    var body: some View {
        Group {
            if systemName == "mappin" {
                Image(systemName: "mappin.and.ellipse")
                    .symbolRenderingMode(.palette)
                    .foregroundStyle(color, Color.mintInk.opacity(0.72))
                    .font(.system(size: diameter * 0.46, weight: .semibold))
            } else {
                Image(systemName: systemName)
                    .font(.system(size: diameter * 0.40, weight: .semibold))
                    .foregroundStyle(color)
            }
        }
            .frame(width: diameter, height: diameter)
            .background(Circle().fill(LinearGradient(colors: [.white, .ivory], startPoint: .top, endPoint: .bottom)))
            .overlay(Circle().strokeBorder(Color.chrome.opacity(0.75), lineWidth: 1))
            .shadow(color: Color.charcoalText.opacity(0.14), radius: 2, y: 2)
    }
}

private struct LocationChoicePanel: View {
    let onAuto: () -> Void
    let onManual: () -> Void

    var body: some View {
        VStack(spacing: 14) {
            ReferenceChoiceCard(
                systemName: "location.north.fill",
                title: "현 위치",
                subtitle: "현재 위치 사용",
                highlighted: true,
                action: onAuto
            )
            ReferenceChoiceCard(
                systemName: "magnifyingglass",
                title: "지역 선택",
                subtitle: "직접 지역 선택",
                highlighted: false,
                action: onManual
            )
        }
    }
}

private struct ReferenceChoiceCard: View {
    let systemName: String
    let title: String
    let subtitle: String
    let highlighted: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 16) {
                ReferenceIconWell(
                    systemName: systemName,
                    color: systemName == "location.north.fill" ? .mintInk : .charcoalText,
                    diameter: 50
                )
                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(.headline)
                        .foregroundStyle(Color.charcoalText)
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(Color.charcoalSoft)
                }
                Spacer(minLength: 12)
                Image(systemName: "chevron.right")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(Color.charcoalText)
            }
            .padding(.horizontal, 16)
            .frame(height: 76)
            .background(highlighted ? Color.selectionMint : Color.ivory)
            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .strokeBorder(Color.canvasLine.opacity(0.8), lineWidth: 1)
            )
            .shadow(color: Color.caramelDeep.opacity(0.06), radius: 4, y: 2)
        }
        .buttonStyle(.plain)
    }
}

private struct ExactWell: View {
    let name: String
    let diameter: CGFloat

    var body: some View {
        Image(name)
            .resizable()
            .scaledToFill()
            .frame(width: diameter, height: diameter)
            .clipShape(Circle())
            .shadow(color: Color.charcoalText.opacity(0.14), radius: 2, y: 2)
            .accessibilityHidden(true)
    }
}

private struct CompactPageHeader: View {
    let icon: String
    let title: String
    let onClose: (() -> Void)?
    var onSettings: (() -> Void)? = nil

    var body: some View {
        HStack(spacing: 10) {
            ReferenceIconWell(systemName: icon, color: .accentRed, diameter: 34)
            Text(title)
                .font(AppTypography.screenTitle)
                .foregroundStyle(Color.charcoalText)
            Spacer()
            if let onSettings {
                Button(action: onSettings) {
                    ReferenceIconWell(systemName: "gearshape.fill", color: .accentRed, diameter: 34)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("설정")
            } else if let onClose {
                Button("닫기", action: onClose)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Color.charcoalText)
                    .frame(minWidth: 52, minHeight: 44)
                    .background(Capsule().fill(Color.ivory))
                    .overlay(Capsule().stroke(Color.canvasLine.opacity(0.7), lineWidth: 1))
            }
        }
        .referenceTopBarSurface()
        .padding(.horizontal, 28)
        .padding(.top, 14)
        .padding(.bottom, 8)
    }
}

private extension View {
    func referenceTopBarSurface() -> some View {
        self
            .padding(.horizontal, 8)
            .frame(height: 64)
    }
}

private struct SwipeToDeleteRow<Content: View>: View {
    let onDelete: () -> Void
    @ViewBuilder let content: () -> Content

    @State private var isOpen = false
    @GestureState private var dragX: CGFloat = 0

    private var offset: CGFloat {
        min(0, max(-76, (isOpen ? -76 : 0) + dragX))
    }

    var body: some View {
        ZStack(alignment: .trailing) {
            Button(action: onDelete) {
                VStack(spacing: 4) {
                    Image(systemName: "trash.fill")
                    Text("삭제").font(.caption2.bold())
                }
                .foregroundStyle(.white)
                .frame(width: 76)
                .frame(maxHeight: .infinity)
                .background(Color.accentRed)
            }
            .buttonStyle(.plain)

            content()
                .offset(x: offset)
                .gesture(
                    DragGesture(minimumDistance: 12)
                        .updating($dragX) { value, state, _ in
                            guard abs(value.translation.width) > abs(value.translation.height) else { return }
                            state = value.translation.width
                        }
                        .onEnded { value in
                            guard abs(value.translation.width) > abs(value.translation.height) else { return }
                            withAnimation(.easeOut(duration: 0.18)) {
                                if value.translation.width < -30 { isOpen = true }
                                if value.translation.width > 30 { isOpen = false }
                            }
                        }
                )
        }
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}

private struct WittyEmptyState: View {
    let imageName: String
    let icon: String?
    let title: String
    let message: String

    var body: some View {
        VStack(spacing: 14) {
            Image(imageName)
                .resizable()
                .scaledToFill()
                .frame(width: 210, height: 150)
                .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
                .overlay {
                    if let icon {
                        Circle()
                            .fill(Color.ivory.opacity(0.94))
                            .frame(width: 72, height: 72)
                            .overlay {
                                Image(systemName: icon)
                                    .font(.system(size: 31, weight: .semibold))
                                    .foregroundStyle(Color.accentRed)
                            }
                            .overlay(Circle().stroke(Color.canvasLine, lineWidth: 1))
                            .shadow(color: Color.charcoalText.opacity(0.16), radius: 5, y: 3)
                    }
                }
            Text(title)
                .font(.title3.bold())
                .foregroundStyle(Color.charcoalText)
            Text(message)
                .font(.subheadline)
                .foregroundStyle(Color.charcoalSoft)
                .multilineTextAlignment(.center)
                .lineSpacing(3)
        }
        .frame(maxWidth: .infinity)
        .frame(minHeight: 430)
        .accessibilityElement(children: .combine)
    }
}

@MainActor
private final class RestaurantImageLoader: ObservableObject {
    enum State {
        case idle
        case loading
        case loaded(UIImage)
        case failed
    }

    @Published private(set) var state: State = .idle
    private static let memoryCache = NSCache<NSURL, UIImage>()
    private static let ephemeralSession: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: configuration)
    }()

    func load(_ rawURL: String?, provider: String?) async {
        guard let rawURL,
              let url = URL(string: rawURL),
              url.scheme == "https" else {
            state = .failed
            return
        }
        let isFoursquare = provider?.lowercased() == "foursquare"
        if !isFoursquare, let cached = Self.memoryCache.object(forKey: url as NSURL) {
            withAnimation(.easeOut(duration: 0.18)) {
                state = .loaded(cached)
            }
            return
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 15
        request.cachePolicy = isFoursquare ? .reloadIgnoringLocalCacheData : .returnCacheDataElseLoad
        if !isFoursquare, let cached = URLCache.shared.cachedResponse(for: request),
           let image = UIImage(data: cached.data) {
            Self.memoryCache.setObject(image, forKey: url as NSURL)
            withAnimation(.easeOut(duration: 0.18)) {
                state = .loaded(image)
            }
            return
        }
        state = .loading
        do {
            let session = isFoursquare ? Self.ephemeralSession : URLSession.shared
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse,
                  (200..<300).contains(http.statusCode),
                  let image = UIImage(data: data) else {
                state = .failed
                return
            }
            if !isFoursquare {
                Self.memoryCache.setObject(image, forKey: url as NSURL)
                URLCache.shared.storeCachedResponse(CachedURLResponse(response: response, data: data), for: request)
            }
            withAnimation(.easeOut(duration: 0.18)) {
                state = .loaded(image)
            }
        } catch {
            guard !Task.isCancelled else { return }
            state = .failed
        }
    }
}

private struct RestaurantImageView: View {
    let photoURL: String?
    let photoKind: String?
    let photoProvider: String?
    let photoSourceURL: String?
    let photoAttribution: String?
    let photoCreator: String?
    let photoCreatorURL: String?
    let photoLicense: String?
    let photoLicenseURL: String?
    let photoTitle: String?
    let category: String
    let identity: String
    let menu: String?
    let fallbackAsset: String?
    @StateObject private var loader = RestaurantImageLoader()
    @State private var showPhotoInformation = false

    var body: some View {
        ZStack {
            Image(fallbackAssetName)
                .resizable()
                .scaledToFill()
            if case .loaded(let image) = loader.state {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
                    .transition(.opacity)
            }
        }
        .accessibilityLabel(accessibilityImageLabel)
        .overlay(alignment: .bottomLeading) {
            if case .loaded = loader.state, hasPhotoInformation {
                Button {
                    showPhotoInformation = true
                } label: {
                    Image(systemName: "info.circle.fill")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(Color.charcoalText)
                        .frame(width: 26, height: 26)
                        .background(Circle().fill(Color.ivory.opacity(0.94)))
                        .overlay(Circle().stroke(Color.canvasLine.opacity(0.8), lineWidth: 0.75))
                }
                .buttonStyle(.plain)
                .padding(6)
                .accessibilityLabel("사진 정보 보기")
            }
        }
        .clipped()
        .task(id: "\(photoProvider ?? "")|\(photoURL ?? "")") {
            await loader.load(photoURL, provider: photoProvider)
        }
        .sheet(isPresented: $showPhotoInformation) {
            PhotoInformationSheet(photoKind: photoKind,
                                  provider: photoProvider,
                                  attribution: photoAttribution,
                                  creator: photoCreator,
                                  creatorURL: safeHTTPSURL(photoCreatorURL),
                                  license: photoLicense,
                                  licenseURL: safeHTTPSURL(photoLicenseURL),
                                  title: photoTitle,
                                  sourceURL: safeHTTPSURL(photoSourceURL))
                .presentationDetents([.medium])
        }
    }

    private var shortCategory: String {
        category.components(separatedBy: " > ").last?.trimmingCharacters(in: .whitespaces) ?? "음식점"
    }

    private var hasPhotoInformation: Bool {
        photoKind != nil || photoAttribution != nil || photoCreator != nil || photoLicense != nil
    }

    private var accessibilityImageLabel: String {
        guard case .loaded = loader.state else { return "\(shortCategory) 음식 예시 이미지" }
        switch photoKind {
        case "restaurantVerified": return "해당 식당 사진"
        case "categoryExample": return "메뉴 예시 사진, 해당 식당 사진이 아님"
        default: return "참고 이미지"
        }
    }

    private func safeHTTPSURL(_ rawValue: String?) -> URL? {
        guard let rawValue,
              let url = URL(string: rawValue),
              url.scheme?.lowercased() == "https" else { return nil }
        return url
    }

    /// 서버 사진이 없을 때는 매장 사진으로 가장하지 않고, 메뉴·카테고리에 맞는 일반 음식 이미지를 쓴다.
    private var fallbackAssetName: String {
        fallbackAsset ?? FallbackFoodAssets.pick(menu: menu,
                                                 category: category,
                                                 identity: identity,
                                                 excluding: [])
    }
}

private enum FallbackFoodAssets {
    static let all = [
        "FoodMain", "FoodBibimbap", "FoodSide1", "FoodSide2", "FoodSide3",
        "FoodGrilledPork", "FoodJjamppong", "FoodNaengmyeon", "FoodSeafood",
        "FoodChicken", "FoodTteokbokki", "FoodSushi", "FoodShabu", "FoodBrunch",
    ]

    static func pick(menu: String?, category: String, identity: String,
                     excluding: Set<String>) -> String {
        let text = "\(menu ?? "") \(category)".lowercased()
        let preferred: [String]
        if contains(text, ["카페", "베이커리", "디저트", "브런치", "커피"]) {
            preferred = ["FoodBrunch", "FoodSide1"]
        } else if contains(text, ["파스타", "피자", "양식"]) {
            preferred = ["FoodSide1", "FoodBrunch"]
        } else if contains(text, ["짜장", "짬뽕", "탕수육", "중식", "중국요리"]) {
            preferred = ["FoodJjamppong", "FoodSide2", "FoodTteokbokki"]
        } else if contains(text, ["냉면", "막국수"]) {
            preferred = ["FoodNaengmyeon", "FoodSide2"]
        } else if contains(text, ["초밥", "스시", "일식"]) {
            preferred = ["FoodSushi", "FoodSide3", "FoodSeafood"]
        } else if contains(text, ["해물", "생선", "장어", "회", "수산"]) {
            preferred = ["FoodSeafood", "FoodSushi", "FoodJjamppong"]
        } else if contains(text, ["치킨", "닭", "백숙", "삼계탕"]) {
            preferred = ["FoodChicken", "FoodMain", "FoodShabu"]
        } else if contains(text, ["떡볶이", "김밥", "분식"]) {
            preferred = ["FoodTteokbokki", "FoodBibimbap", "FoodSide2"]
        } else if contains(text, ["샤브", "전골", "훠궈"]) {
            preferred = ["FoodShabu", "FoodMain", "FoodSeafood"]
        } else if contains(text, ["국수", "칼국수", "국밥", "탕", "찌개", "죽", "순대"]) {
            preferred = ["FoodSide2", "FoodMain", "FoodJjamppong", "FoodShabu"]
        } else if contains(text, ["돈가스", "돈까스"]) {
            preferred = ["FoodSide3", "FoodGrilledPork"]
        } else if contains(text, ["고기", "육류", "구이", "갈비", "삼겹살"]) {
            preferred = ["FoodGrilledPork", "FoodMain", "FoodChicken", "FoodShabu"]
        } else if contains(text, ["비빔밥"]) {
            preferred = ["FoodBibimbap", "FoodMain", "FoodTteokbokki"]
        } else if contains(text, ["한식", "한정식"]) {
            preferred = ["FoodBibimbap", "FoodMain", "FoodGrilledPork", "FoodSeafood",
                         "FoodTteokbokki", "FoodChicken", "FoodNaengmyeon", "FoodShabu"]
        } else {
            preferred = all
        }

        let candidates = preferred + all.filter { !preferred.contains($0) }
        let stable = identity.utf8.reduce(UInt64(14_695_981_039_346_656_037)) {
            ($0 ^ UInt64($1)) &* 1_099_511_628_211
        }
        let start = Int(stable % UInt64(candidates.count))
        let rotated = Array(candidates[start...]) + Array(candidates[..<start])
        return rotated.first(where: { !excluding.contains($0) }) ?? candidates[start]
    }

    private static func contains(_ text: String, _ keywords: [String]) -> Bool {
        keywords.contains { text.contains($0) }
    }
}

private struct PhotoInformationSheet: View {
    @Environment(\.dismiss) private var dismiss
    let photoKind: String?
    let provider: String?
    let attribution: String?
    let creator: String?
    let creatorURL: URL?
    let license: String?
    let licenseURL: URL?
    let title: String?
    let sourceURL: URL?

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                ReferenceIconWell(systemName: photoKind == "categoryExample" ? "fork.knife" : "photo",
                                  color: .accentRed, diameter: 38)
                Text(photoKind == "categoryExample" ? "메뉴 예시 사진" : "사진 정보")
                    .font(.title3.bold())
                    .foregroundStyle(Color.charcoalText)
                Spacer()
                Button("닫기") { dismiss() }
                    .foregroundStyle(Color.charcoalText)
            }

            Text(photoKind == "categoryExample"
                 ? "음식 종류를 보여 주는 예시이며, 이 식당에서 촬영한 사진은 아니에요."
                 : "사진 제공처와 원문을 확인할 수 있어요.")
                .font(.subheadline)
                .foregroundStyle(Color.charcoalSoft)

            VStack(alignment: .leading, spacing: 8) {
                if let title, !title.isEmpty { informationRow("제목", title) }
                if let creator, !creator.isEmpty { informationRow("사진", creator) }
                if let license, !license.isEmpty { informationRow("이용 조건", license.uppercased()) }
                if let attribution, !attribution.isEmpty { informationRow("제공", attribution) }
                if let provider, !provider.isEmpty { informationRow("출처", provider) }
            }

            HStack(spacing: 10) {
                if let sourceURL { photoLink("원문 보기", sourceURL) }
                if let creatorURL { photoLink("작가 보기", creatorURL) }
                if let licenseURL { photoLink("이용 조건", licenseURL) }
            }
            Spacer(minLength: 0)
        }
        .padding(24)
        .background(Color.mintBase.ignoresSafeArea())
        .preferredColorScheme(.light)
    }

    private func informationRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text(label)
                .font(.caption.bold())
                .foregroundStyle(Color.accentRed)
                .frame(width: 58, alignment: .leading)
            Text(value)
                .font(.subheadline)
                .foregroundStyle(Color.charcoalText)
                .textSelection(.enabled)
        }
    }

    private func photoLink(_ title: String, _ url: URL) -> some View {
        Link(destination: url) {
            Text(title)
                .font(.caption.bold())
                .foregroundStyle(Color.charcoalText)
                .padding(.horizontal, 12)
                .frame(minHeight: 38)
                .background(Capsule().fill(Color.ivory))
                .overlay(Capsule().stroke(Color.canvasLine, lineWidth: 1))
        }
    }
}

private struct LeatherHeroCard: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image("LunchHero")
                .resizable()
                .scaledToFit()
                .overlay {
                    GeometryReader { proxy in
                        let width = proxy.size.width
                        ZStack(alignment: .topLeading) {
                            Color.clear
                            HStack(spacing: width * 0.045) {
                                Text("메뉴 추천 받기")
                                Image(systemName: "chevron.right")
                            }
                            .font(.system(size: width * 0.04, weight: .medium))
                            .foregroundStyle(Color.charcoalText)
                            .frame(width: width * 0.46, height: width * 0.15)
                            .background(
                                RoundedRectangle(cornerRadius: width * 0.075, style: .continuous)
                                    .fill(LinearGradient(colors: [.white, Color.ivory],
                                                         startPoint: .top, endPoint: .bottom))
                                    .overlay(RoundedRectangle(cornerRadius: width * 0.075)
                                        .stroke(Color.canvasLine, lineWidth: 1))
                                    .shadow(color: Color.caramelDeep.opacity(0.2), radius: 3, y: 2)
                            )
                            .offset(x: width * 0.0825, y: width * 0.4075)
                        }
                    }
                }
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("메뉴 추천 받기")
    }
}

private struct ReferenceBottomBar: View {
    let selected: BottomDestination
    let onHome: () -> Void
    let onRegion: () -> Void
    let onRecommend: () -> Void
    let onHistory: () -> Void
    let onFavorites: () -> Void

    var body: some View {
        HStack(spacing: 0) {
            item("house.fill", "홈", destination: .home, action: onHome)
            item("map.fill", "지역", destination: .region, action: onRegion)
            rouletteItem()
            item("clock.arrow.circlepath", "최근", destination: .history, action: onHistory)
            item("heart", "찜", destination: .favorites, action: onFavorites)
        }
        .padding(.horizontal, 8)
        .padding(.top, 14)
        .padding(.bottom, 10)
        .background(alignment: .bottom) {
            RaisedCenterNavigationShape()
                .fill(Color.ivory)
                .frame(height: 84)
        }
        .overlay(alignment: .bottom) {
            RaisedCenterNavigationShape()
                .stroke(Color.canvasLine.opacity(0.7), lineWidth: 1)
                .frame(height: 84)
        }
        .padding(.horizontal, 28)
        .frame(maxWidth: 600)
        .padding(.bottom, 12)
    }

    private func item(
        _ icon: String,
        _ title: String,
        destination: BottomDestination,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            VStack(spacing: 4) {
                Image(systemName: icon).font(.system(size: 20, weight: .semibold))
                Text(title).font(.system(size: 10, weight: .medium))
            }
            .foregroundStyle(selected == destination ? Color.accentRed : Color.charcoalText)
            .frame(maxWidth: .infinity)
        }
        .buttonStyle(.plain)
    }

    private func rouletteItem() -> some View {
        Button(action: onRecommend) {
            VStack(spacing: 4) {
                Image(systemName: "die.face.5.fill")
                    .font(.system(size: 20, weight: .semibold))
                    .hidden()
                    .overlay {
                        ZStack {
                            Circle()
                                .fill(
                                    LinearGradient(
                                        colors: [Color(red: 0.96, green: 0.20, blue: 0.22), Color.accentRed],
                                        startPoint: .topLeading,
                                        endPoint: .bottomTrailing
                                    )
                                )
                                .overlay(Circle().stroke(Color.white.opacity(0.88), lineWidth: 1.2))
                                .shadow(color: Color.accentRed.opacity(0.28), radius: 5, y: 3)
                            Image(systemName: "die.face.5.fill")
                                .font(.system(size: 24, weight: .bold))
                                .foregroundStyle(.white)
                        }
                        .frame(width: 45.36, height: 45.36)
                        .offset(y: -14)
                    }

                Text("추천")
                    .font(.system(size: 10, weight: selected == .recommend ? .bold : .medium))
                    .foregroundStyle(selected == .recommend ? Color.accentRed : Color.charcoalText)
            }
            .frame(maxWidth: .infinity)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("추천 다시 고르기")
    }
}

/// 선택된 추천 버튼과 하단 바를 하나의 물성으로 연결하는 중앙 돌출형 표면.
private struct RaisedCenterNavigationShape: Shape {
    func path(in rect: CGRect) -> Path {
        let radius: CGFloat = 22
        let top: CGFloat = 20
        let center = rect.midX
        let shoulder: CGFloat = 44
        let crown: CGFloat = 0
        var path = Path()

        path.move(to: CGPoint(x: radius, y: top))
        path.addLine(to: CGPoint(x: center - shoulder, y: top))
        path.addCurve(
            to: CGPoint(x: center, y: crown),
            control1: CGPoint(x: center - 32, y: top),
            control2: CGPoint(x: center - 30, y: crown)
        )
        path.addCurve(
            to: CGPoint(x: center + shoulder, y: top),
            control1: CGPoint(x: center + 30, y: crown),
            control2: CGPoint(x: center + 32, y: top)
        )
        path.addLine(to: CGPoint(x: rect.maxX - radius, y: top))
        path.addArc(
            center: CGPoint(x: rect.maxX - radius, y: top + radius),
            radius: radius,
            startAngle: .degrees(-90),
            endAngle: .degrees(0),
            clockwise: false
        )
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - radius))
        path.addArc(
            center: CGPoint(x: rect.maxX - radius, y: rect.maxY - radius),
            radius: radius,
            startAngle: .degrees(0),
            endAngle: .degrees(90),
            clockwise: false
        )
        path.addLine(to: CGPoint(x: radius, y: rect.maxY))
        path.addArc(
            center: CGPoint(x: radius, y: rect.maxY - radius),
            radius: radius,
            startAngle: .degrees(90),
            endAngle: .degrees(180),
            clockwise: false
        )
        path.addLine(to: CGPoint(x: 0, y: top + radius))
        path.addArc(
            center: CGPoint(x: radius, y: top + radius),
            radius: radius,
            startAngle: .degrees(180),
            endAngle: .degrees(270),
            clockwise: false
        )
        path.closeSubpath()
        return path
    }
}

private struct ReferenceRegionScreen: View {
    @Binding var query: String
    let mode: LocationMode
    let locationStatus: String
    let nearby: [String]
    let frequent: [String]
    let searchFocused: FocusState<Bool>.Binding
    let onSettings: () -> Void
    let onAuto: () -> Void
    let onManual: () -> Void
    let onSearch: () -> Void
    let onPreset: (String) -> Void

    var body: some View {
        VStack(spacing: 0) {
            CompactPageHeader(icon: "map.fill", title: "지역 선택", onClose: nil, onSettings: {
                searchFocused.wrappedValue = false
                onSettings()
            })
            ScrollView {
                VStack(spacing: 22) {
                HStack(spacing: 0) {
                    modeButton("magnifyingglass", "지역 지정", selected: mode == .manual, iconColor: .charcoalText, action: onManual)
                    modeButton("location.north.fill", "현 위치", selected: mode == .auto, iconColor: .accentRed, action: onAuto)
                }
                .referenceCard(cornerRadius: 18)

                Text(locationStatus)
                    .font(.caption)
                    .foregroundStyle(Color.charcoalSoft)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 4)
                    .padding(.top, -12)

                HStack {
                    TextField("지역명으로 검색 (예: 강남, 판교)", text: $query)
                        .focused(searchFocused)
                        .submitLabel(.search)
                        .onSubmit {
                            searchFocused.wrappedValue = false
                            onSearch()
                        }
                    Button {
                        searchFocused.wrappedValue = false
                        onSearch()
                    } label: {
                        Image(systemName: "magnifyingglass").font(.title3.bold())
                    }
                }
                .foregroundStyle(Color.charcoalText)
                .padding(.horizontal, 18)
                .padding(.vertical, 16)
                .referenceCard(cornerRadius: 22)

                regionSection(icon: "scope", title: "내 주변", rows: nearby, recommended: true,
                              emptyMessage: "현 위치를 확인하면 주변 지역을 보여드려요.")
                regionSection(icon: "star", title: "자주 찾는 지역", rows: frequent, recommended: false,
                              emptyMessage: "아직 자주 찾는 지역이 없어요.")
                }
                .padding(.horizontal, 28)
                .padding(.top, 8)
                .padding(.bottom, 30)
                .background {
                    Color.clear
                        .contentShape(Rectangle())
                        .onTapGesture { searchFocused.wrappedValue = false }
                }
            }
            .scrollDismissesKeyboard(.interactively)
        }
    }

    private func modeButton(
        _ icon: String,
        _ title: String,
        selected: Bool,
        iconColor: Color,
        action: @escaping () -> Void
    ) -> some View {
        Button {
            searchFocused.wrappedValue = false
            action()
        } label: {
            HStack(spacing: 8) {
                Image(systemName: icon)
                    .foregroundStyle(selected ? Color.accentRed : iconColor)
                Text(title)
            }
                .font(.subheadline)
                .foregroundStyle(selected ? Color.accentRed : Color.charcoalText)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 16)
                .background(selected ? Color.selectionMint : Color.clear)
                .clipShape(RoundedRectangle(cornerRadius: 18))
        }
        .buttonStyle(.plain)
    }

    private func regionSection(icon: String, title: String, rows: [String], recommended: Bool,
                               emptyMessage: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(title, systemImage: icon).font(.headline)
            if rows.isEmpty {
                Text(emptyMessage)
                    .font(.subheadline)
                    .foregroundStyle(Color.charcoalSoft)
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .referenceCard(cornerRadius: 18)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(rows.enumerated()), id: \.element) { index, row in
                        Button {
                            searchFocused.wrappedValue = false
                            onPreset(row)
                        } label: {
                            HStack {
                                Text(row).foregroundStyle(Color.charcoalText)
                                if recommended && index == 0 {
                                    Text("현 위치").font(.caption).foregroundStyle(Color.mintInk)
                                        .padding(.horizontal, 8).padding(.vertical, 4).background(Capsule().fill(Color.selectionMint))
                                }
                                Spacer()
                                Image(systemName: "chevron.right").foregroundStyle(Color.charcoalSoft)
                            }
                            .padding(15)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        if index < rows.count - 1 { Divider().padding(.leading, 14) }
                    }
                }
                .referenceCard(cornerRadius: 18)
            }
        }
    }
}

private struct ReferenceResultsPage: View {
    let phase: Phase
    let regionLabel: String
    @ObservedObject var store: ChoiceStore
    let onBack: () -> Void
    let onSettings: () -> Void
    let onPick: (Decision) -> Void
    let onRetry: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            CompactPageHeader(icon: "die.face.5.fill", title: "오늘의 한 끼", onClose: nil, onSettings: onSettings)

            Button(action: onBack) {
                HStack(spacing: 6) {
                    Text(regionLabel)
                        .font(.headline)
                        .foregroundStyle(Color.charcoalText)
                        .lineLimit(1)
                        .truncationMode(.tail)
                    Image(systemName: "chevron.down")
                        .font(.caption.bold())
                        .foregroundStyle(Color.charcoalText)
                    Spacer(minLength: 0)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("지역 다시 선택")
            .padding(.horizontal, 28)
            .padding(.top, 6)
            .padding(.bottom, 10)

            Group {
                switch phase {
                case .results(let restaurants):
                    ReferenceRestaurantResults(restaurants: restaurants,
                                               regionLabel: regionLabel,
                                               store: store,
                                               onPick: onPick,
                                               onRetry: onRetry)
                case .failed(let message):
                    StatusView(icon: "exclamationmark.triangle", title: "문제가 생겼어요", message: message) {
                        Button("다시 시도", action: onRetry).buttonStyle(SecondaryButtonStyle())
                    }
                case .denied:
                    StatusView(icon: "location.slash", title: "위치를 찾을 수 없어요", message: "지역을 직접 지정해 주세요.") {
                        Button("지역 지정", action: onBack).buttonStyle(SecondaryButtonStyle())
                    }
                case .empty:
                    StatusView(icon: "fork.knife", title: "주변 음식점을 찾지 못했어요", message: "지역을 바꾸거나 다시 골라 주세요.")
                default:
                    LoadingView(text: "\(regionLabel) 주변 오늘의 한 끼를 고르는 중…",
                                onRetry: onRetry)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}

private struct ReferenceRestaurantResults: View {
    @Environment(\.openURL) private var openURL
    let restaurants: [Restaurant]
    let regionLabel: String
    @ObservedObject var store: ChoiceStore
    let onPick: (Decision) -> Void
    let onRetry: () -> Void

    private var fallbackAssets: [String] {
        var used = Set<String>()
        return restaurants.map { restaurant in
            let menu = MenuPolicy.menus(for: restaurant).first
                ?? restaurant.category.components(separatedBy: " > ").last
            let asset = FallbackFoodAssets.pick(menu: menu,
                                                category: restaurant.category,
                                                identity: restaurant.id,
                                                excluding: used)
            used.insert(asset)
            return asset
        }
    }

    var body: some View {
        GeometryReader { geometry in
            let contentWidth = max(0, geometry.size.width - 56)
            let gridSpacing: CGFloat = 10
            let smallCardWidth = max(88, (contentWidth - gridSpacing) / 2)
            let columns = [
                GridItem(.flexible(), spacing: gridSpacing),
                GridItem(.flexible()),
            ]
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if let first = decision(at: 0) {
                        topCard(first)
                    }

                    Text("함께 보면 좋은 맛집")
                        .font(.headline)
                        .padding(.top, 20)
                    LazyVGrid(columns: columns, alignment: .leading, spacing: gridSpacing) {
                        ForEach(1..<restaurants.count, id: \.self) { index in
                            if let item = decision(at: index) {
                                smallCard(item, index: index, width: smallCardWidth)
                            }
                        }
                    }
                    .padding(.top, -9)
                }
                .frame(width: contentWidth, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.bottom, 30)
            }
        }
    }

    private func decision(at index: Int) -> Decision? {
        guard restaurants.indices.contains(index) else { return nil }
        let restaurant = restaurants[index]
        let menu = MenuPolicy.menus(for: restaurant).first
            ?? restaurant.category.components(separatedBy: " > ").last
            ?? "오늘의 메뉴"
        return Decision(menu: menu, restaurant: restaurant)
    }

    private func topCard(_ decision: Decision) -> some View {
        ZStack(alignment: .topTrailing) {
            HStack(spacing: 0) {
                RestaurantImageView(photoURL: decision.restaurant.photoURL,
                                    photoKind: decision.restaurant.photoKind,
                                    photoProvider: decision.restaurant.photoProvider,
                                    photoSourceURL: decision.restaurant.photoSourceURL,
                                    photoAttribution: decision.restaurant.photoAttribution,
                                    photoCreator: decision.restaurant.photoCreator,
                                    photoCreatorURL: decision.restaurant.photoCreatorURL,
                                    photoLicense: decision.restaurant.photoLicense,
                                    photoLicenseURL: decision.restaurant.photoLicenseURL,
                                    photoTitle: decision.restaurant.photoTitle,
                                    category: decision.restaurant.category,
                                    identity: decision.restaurant.id,
                                    menu: decision.menu,
                                    fallbackAsset: fallbackAssets.first)
                    .frame(width: 158, height: 230)
                    .clipped()

                VStack(alignment: .leading, spacing: 9) {
                    Text(decision.menu).font(.caption).foregroundStyle(Color.mintInk)
                        .padding(.horizontal, 9).padding(.vertical, 5).background(Capsule().fill(Color.selectionMint))
                    Text(decision.restaurant.name).font(.title2.bold()).foregroundStyle(Color.charcoalText).lineLimit(2)
                    Text(decision.restaurant.category.components(separatedBy: " > ").last ?? decision.menu)
                        .font(.subheadline).foregroundStyle(Color.charcoalSoft).lineLimit(1)
                    if let meters = decision.restaurant.distanceMeters {
                        HStack(spacing: 4) {
                            Image(systemName: "location.fill").foregroundStyle(Color.accentRed)
                            Text("약 \(meters)m")
                        }
                            .font(.subheadline).foregroundStyle(Color.charcoalSoft).lineLimit(1)
                    }
                    Text("가까워서 더 반가운 한 끼").font(.caption).foregroundStyle(Color.charcoalSoft).lineLimit(2)
                    Spacer(minLength: 12)
                    HStack(spacing: 6) {
                        if let url = decision.restaurant.telURL {
                            Button {
                                openURL(url)
                            } label: {
                                Image(systemName: "phone.fill")
                                    .frame(width: 36, height: 36)
                                    .background(Circle().fill(Color.ivory))
                                    .overlay(Circle().stroke(Color.canvasLine))
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("\(decision.restaurant.name)에 전화걸기")
                            .accessibilityHint("영업 여부를 확인하기 위해 전화 앱을 열어요")
                        }
                        HStack(spacing: 4) {
                            Text("이곳 보기")
                                .lineLimit(1)
                                .minimumScaleFactor(0.75)
                            Image(systemName: "chevron.right")
                        }
                        .padding(.horizontal, 6)
                        .frame(maxWidth: .infinity)
                        .frame(minHeight: 36)
                        .background(Capsule().fill(Color.ivory).overlay(Capsule().stroke(Color.canvasLine)))
                    }
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.charcoalText)
                    .frame(maxWidth: .infinity)
                }
                .padding(.horizontal, 14)
                .padding(.top, 16)
                .padding(.bottom, 14)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(height: 230)
            .background(Color.ivory)
            .clipShape(RoundedRectangle(cornerRadius: 20))
            .overlay(RoundedRectangle(cornerRadius: 20).stroke(Color.caramel.opacity(0.55), lineWidth: 1))
            .shadow(color: Color.caramelDeep.opacity(0.12), radius: 4, y: 2)
            .contentShape(RoundedRectangle(cornerRadius: 20))
            .onTapGesture { onPick(decision) }
            .accessibilityAddTraits(.isButton)
            .accessibilityHint("식당 상세 보기")

            favoriteButton(for: decision, imageName: "RestaurantCategory")
                .padding(.top, 14)
                .padding(.trailing, 14)
        }
        .frame(maxWidth: .infinity)
    }

    private func smallCard(_ decision: Decision, index: Int, width: CGFloat) -> some View {
        let imageName = "RestaurantCategory"
        return ZStack(alignment: .topTrailing) {
            VStack(alignment: .leading, spacing: 7) {
                RestaurantImageView(photoURL: decision.restaurant.photoURL,
                                    photoKind: decision.restaurant.photoKind,
                                    photoProvider: decision.restaurant.photoProvider,
                                    photoSourceURL: decision.restaurant.photoSourceURL,
                                    photoAttribution: decision.restaurant.photoAttribution,
                                    photoCreator: decision.restaurant.photoCreator,
                                    photoCreatorURL: decision.restaurant.photoCreatorURL,
                                    photoLicense: decision.restaurant.photoLicense,
                                    photoLicenseURL: decision.restaurant.photoLicenseURL,
                                    photoTitle: decision.restaurant.photoTitle,
                                    category: decision.restaurant.category,
                                    identity: decision.restaurant.id,
                                    menu: decision.menu,
                                    fallbackAsset: fallbackAssets[index])
                    .frame(width: width, height: 105)
                    .clipped()
                VStack(alignment: .leading, spacing: 7) {
                    Text(decision.restaurant.name)
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(Color.charcoalText)
                        .lineLimit(1)
                    Text(decision.restaurant.category.components(separatedBy: " > ").last ?? decision.menu)
                        .font(.system(size: 10))
                        .foregroundStyle(Color.charcoalSoft)
                        .lineLimit(1)
                    if let meters = decision.restaurant.distanceMeters {
                        Text("약 \(meters)m")
                            .font(.system(size: 10))
                            .foregroundStyle(Color.charcoalSoft)
                            .lineLimit(1)
                    }
                }
                .padding(.horizontal, 9)
                .padding(.trailing, decision.restaurant.telURL == nil ? 0 : 40)
            }
            .padding(.bottom, 14)
            .frame(width: width, alignment: .leading)
            .background(Color.ivory)
            .clipShape(RoundedRectangle(cornerRadius: 16))
            .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.canvasLine.opacity(0.7), lineWidth: 1))
            .contentShape(RoundedRectangle(cornerRadius: 16))
            .onTapGesture { onPick(decision) }
            .accessibilityAddTraits(.isButton)
            .accessibilityHint("식당 상세 보기")

            favoriteButton(for: decision, imageName: imageName)
                .padding(5)

            if let url = decision.restaurant.telURL {
                Button {
                    openURL(url)
                } label: {
                    Image(systemName: "phone.fill")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Color.charcoalText)
                        .frame(width: 36, height: 36)
                        .background(Circle().fill(Color.ivory))
                        .overlay(Circle().stroke(Color.canvasLine.opacity(0.7), lineWidth: 1))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(decision.restaurant.name)에 전화걸기")
                .accessibilityHint("영업 여부를 확인하기 위해 전화 앱을 열어요")
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
                .padding(5)
            }
        }
        .frame(width: width)
    }

    private func favoriteButton(for decision: Decision, imageName: String) -> some View {
        let selected = store.isFavorite(decision.restaurant.id)
        return Button {
            store.toggleFavorite(decision.restaurant,
                                 regionName: regionLabel,
                                 imageName: imageName)
        } label: {
            Image(systemName: selected ? "heart.fill" : "heart")
                .font(.headline)
                .foregroundStyle(selected ? Color.accentRed : Color.charcoalText)
                .frame(width: 40, height: 40)
                .background(Circle().fill(Color.ivory.opacity(0.94)))
                .overlay(Circle().stroke(Color.canvasLine.opacity(0.7), lineWidth: 1))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(selected ? "찜 해제" : "찜하기")
    }
}

// MARK: - 상태 화면 공통

struct StatusView<Actions: View>: View {
    let icon: String
    let title: String
    let message: String
    @ViewBuilder var actions: Actions

    init(icon: String, title: String, message: String,
         @ViewBuilder actions: () -> Actions) {
        self.icon = icon
        self.title = title
        self.message = message
        self.actions = actions()
    }

    var body: some View {
        VStack(spacing: 16) {
            IconWell(systemName: icon, diameter: 64)
            Text(title)
                .font(.title2.bold())
                .foregroundStyle(Color.charcoalText)
            Text(message)
                .font(.body)
                .foregroundStyle(Color.charcoalSoft)
                .multilineTextAlignment(.center)
            actions
        }
        .padding(24)
        .accessibilityElement(children: .combine)
    }
}

extension StatusView where Actions == EmptyView {
    init(icon: String, title: String, message: String) {
        self.init(icon: icon, title: title, message: message) { EmptyView() }
    }
}

struct LoadingView: View {
    let text: String
    let onRetry: (() -> Void)?
    @State private var showRetry = false

    init(text: String, onRetry: (() -> Void)? = nil) {
        self.text = text
        self.onRetry = onRetry
    }

    var body: some View {
        VStack(spacing: 16) {
            VStack(spacing: 16) {
                MealShuffleAnimation()
                Text(text)
                    .font(.body)
                    .foregroundStyle(Color.charcoalSoft)
                    .multilineTextAlignment(.center)
            }

            if showRetry, let onRetry {
                Button("다시 고르기", action: onRetry)
                    .buttonStyle(SecondaryButtonStyle())
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
            }
        }
        .padding(24)
        .task {
            try? await Task.sleep(nanoseconds: 10_000_000_000)
            guard !Task.isCancelled, onRetry != nil else { return }
            withAnimation(.easeOut(duration: 0.2)) { showRetry = true }
        }
    }
}

private enum MealShufflePhase: CaseIterable, Hashable {
    case ready
    case toss
    case roll
    case select

    var dieRotation: Double {
        switch self {
        case .ready: 0
        case .toss: -18
        case .roll: 94
        case .select: 180
        }
    }

    var dieOffset: CGFloat {
        switch self {
        case .ready: 0
        case .toss: -13
        case .roll: 3
        case .select: 0
        }
    }

    var orbitRotation: Double {
        switch self {
        case .ready: 0
        case .toss: 55
        case .roll: 150
        case .select: 180
        }
    }

    var isSelecting: Bool { self == .select }
}

private struct MealShuffleAnimation: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private let symbols = [
        "fork.knife",
        "takeoutbag.and.cup.and.straw.fill",
        "cup.and.saucer.fill",
        "leaf.fill",
    ]

    var body: some View {
        Group {
            if reduceMotion {
                Color.clear
                    .phaseAnimator([false, true]) { _, pulse in
                        shuffleScene(phase: .select)
                            .scaleEffect(pulse ? 1.012 : 1)
                    } animation: { _ in
                        .easeInOut(duration: 1.25)
                    }
            } else {
                Color.clear
                    .phaseAnimator(MealShufflePhase.allCases) { _, phase in
                        shuffleScene(phase: phase)
                    } animation: { phase in
                        phase.isSelecting
                            ? .easeInOut(duration: 0.28)
                            : .spring(duration: 0.58, bounce: 0.24)
                    }
            }
        }
        .frame(width: 210, height: 190)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("오늘의 한 끼를 고르는 중")
    }

    private func shuffleScene(phase: MealShufflePhase) -> some View {
        ZStack {
            ForEach(Array(symbols.enumerated()), id: \.offset) { index, symbol in
                candidateToken(symbol)
                    .rotationEffect(.degrees(-phase.orbitRotation - Double(index * 90)))
                    .offset(y: -65)
                    .rotationEffect(.degrees(phase.orbitRotation + Double(index * 90)))
            }

            die(isSelecting: phase.isSelecting)
                .rotationEffect(.degrees(phase.dieRotation))
                .offset(y: phase.dieOffset)
        }
        .frame(width: 180, height: 180)
    }

    private func candidateToken(_ symbol: String) -> some View {
        ZStack {
            Circle()
                .fill(
                    LinearGradient(colors: [.white, Color.ivory],
                                   startPoint: .topLeading,
                                   endPoint: .bottomTrailing)
                )
            Circle()
                .stroke(
                    LinearGradient(colors: [.white, Color.canvasLine, .white],
                                   startPoint: .topLeading,
                                   endPoint: .bottomTrailing),
                    lineWidth: 1
                )
            Image(systemName: symbol)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Color.charcoalText)
        }
        .frame(width: 34, height: 34)
        .shadow(color: Color.caramelDeep.opacity(0.12), radius: 3, y: 2)
    }

    private func die(isSelecting: Bool) -> some View {
        ZStack {
            RoundedRectangle(cornerRadius: 17, style: .continuous)
                .fill(
                    LinearGradient(colors: [.white, Color.ivory],
                                   startPoint: .topLeading,
                                   endPoint: .bottomTrailing)
                )
            RoundedRectangle(cornerRadius: 17, style: .continuous)
                .stroke(
                    LinearGradient(colors: [.white, Color.canvasLine, .white],
                                   startPoint: .topLeading,
                                   endPoint: .bottomTrailing),
                    lineWidth: 1.5
                )
            Image(systemName: "die.face.5.fill")
                .font(.system(size: 42, weight: .medium))
                .foregroundStyle(Color.charcoalText)
            Capsule()
                .fill(Color.accentRed)
                .frame(width: 30, height: 3)
                .offset(y: 28)
                .opacity(isSelecting ? 1 : 0)
        }
        .frame(width: 76, height: 76)
        .shadow(color: Color.caramelDeep.opacity(0.18), radius: 7, y: 5)
        .shadow(color: .white.opacity(0.9), radius: 1, x: -1, y: -1)
    }
}

// MARK: - 결과 화면

struct ResultsView: View {
    let restaurants: [Restaurant]
    let onPick: (Decision) -> Void
    let onRefresh: () -> Void

    private var candidates: [MenuCandidate] { MenuPolicy.candidates(from: restaurants) }
    private var withoutMenu: [Restaurant] {
        restaurants.filter { MenuPolicy.menus(for: $0).isEmpty }
    }

    var body: some View {
        List {
            Section {
                Button("아무거나 골라줘") { pickRandom() }
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(candidates.isEmpty)
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets(top: 8, leading: 0, bottom: 8, trailing: 0))
                    .accessibilityHint("근거 있는 대표 메뉴 후보 중에서 무작위로 하나를 고릅니다")
            }

            Section {
                if candidates.isEmpty {
                    Text("주변 음식점 이름·카테고리에서 확실한 음식명을 찾지 못했어요. 메뉴를 추측해서 보여 드리지는 않습니다.")
                        .font(.subheadline)
                        .foregroundStyle(Color.charcoalSoft)
                } else {
                    ForEach(candidates) { candidate in
                        Button {
                            onPick(Decision(menu: candidate.menu,
                                            restaurant: candidate.restaurants[0]))
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(candidate.menu)
                                        .font(.headline)
                                        .foregroundStyle(Color.charcoalText)
                                    Text("가까운 곳: \(candidate.restaurants[0].name)")
                                        .font(.subheadline)
                                        .foregroundStyle(Color.charcoalSoft)
                                }
                                Spacer()
                                Text("\(candidate.restaurants.count)곳")
                                    .font(.subheadline)
                                    .foregroundStyle(Color.charcoalSoft)
                                Image(systemName: "chevron.right")
                                    .font(.caption)
                                    .foregroundStyle(Color.chrome)
                            }
                        }
                        .accessibilityLabel("\(candidate.menu), \(candidate.restaurants.count)곳, 가까운 곳 \(candidate.restaurants[0].name)")
                    }
                }
            } header: {
                SectionTitle(icon: "list.bullet.rectangle.portrait", title: "대표 메뉴 후보")
            }
            .listRowBackground(Color.ivory)

            if !withoutMenu.isEmpty {
                Section {
                    ForEach(withoutMenu.prefix(10)) { restaurant in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(restaurant.name)
                                .font(.body)
                                .foregroundStyle(Color.charcoalText)
                            Text(restaurant.category + distanceSuffix(restaurant))
                                .font(.caption)
                                .foregroundStyle(Color.charcoalSoft)
                        }
                    }
                } header: {
                    SectionTitle(icon: "questionmark.circle", title: "대표 메뉴 정보가 없는 음식점")
                }
                .listRowBackground(Color.ivory)
            }

            Section {
                Text(AppText.dataDisclaimer)
                    .font(.footnote)
                    .foregroundStyle(Color.charcoalSoft)
                    .listRowBackground(Color.ivory)
                Button("새로 고침") { onRefresh() }
                    .buttonStyle(SecondaryButtonStyle())
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets(top: 8, leading: 0, bottom: 8, trailing: 0))
            }
        }
        .scrollContentBackground(.hidden)
        .contentMargins(.horizontal, 28, for: .scrollContent)
    }

    private func pickRandom() {
        guard let candidate = candidates.randomElement() else { return }
        onPick(Decision(menu: candidate.menu, restaurant: candidate.restaurants[0]))
    }

    private func distanceSuffix(_ restaurant: Restaurant) -> String {
        guard let meters = restaurant.distanceMeters else { return "" }
        return " · 약 \(meters)m"
    }
}

// MARK: - 결정 화면

struct DecisionView: View {
    @Environment(\.openURL) private var openURL
    let decision: Decision
    let regionLabel: String
    @ObservedObject var store: ChoiceStore
    let onClose: () -> Void
    @State private var recorded = false
    @State private var showInformation = false
    @State private var missingMapProvider: MapProvider?
    @State private var showMissingMapActions = false
    @State private var showMapProviderPicker = false
    @State private var showAppleMapFailure = false
    @AppStorage("mapProvider") private var mapProviderRaw = MapProvider.naver.rawValue

    private var mapProvider: MapProvider {
#if targetEnvironment(macCatalyst)
        .apple
#else
        MapProvider(rawValue: mapProviderRaw) ?? .naver
#endif
    }
    private var categoryLabel: String {
        decision.restaurant.category.components(separatedBy: " > ").last ?? decision.restaurant.category
    }

    var body: some View {
        VStack(spacing: 0) {
            CompactPageHeader(icon: "checkmark.seal.fill", title: "오늘의 결정", onClose: onClose)

            VStack(spacing: 11) {
                HStack(spacing: 0) {
                    RestaurantImageView(photoURL: decision.restaurant.photoURL,
                                        photoKind: decision.restaurant.photoKind,
                                        photoProvider: decision.restaurant.photoProvider,
                                        photoSourceURL: decision.restaurant.photoSourceURL,
                                        photoAttribution: decision.restaurant.photoAttribution,
                                        photoCreator: decision.restaurant.photoCreator,
                                        photoCreatorURL: decision.restaurant.photoCreatorURL,
                                        photoLicense: decision.restaurant.photoLicense,
                                        photoLicenseURL: decision.restaurant.photoLicenseURL,
                                        photoTitle: decision.restaurant.photoTitle,
                                        category: decision.restaurant.category,
                                        identity: decision.restaurant.id,
                                        menu: decision.menu,
                                        fallbackAsset: nil)
                        .frame(width: 132, height: 136)
                        .clipped()

                    VStack(alignment: .leading, spacing: 5) {
                        if categoryLabel != decision.menu {
                            Text(categoryLabel)
                                .font(.caption.bold())
                                .foregroundStyle(Color.mintInk)
                                .padding(.horizontal, 9)
                                .padding(.vertical, 4)
                                .background(Capsule().fill(Color.selectionMint))
                        }
                        Text(decision.menu)
                            .font(.system(size: 24, weight: .bold))
                            .foregroundStyle(Color.mintInk)
                            .lineLimit(1)
                            .minimumScaleFactor(0.76)
                        Text(decision.restaurant.name)
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundStyle(Color.charcoalText)
                            .lineLimit(1)
                        if let meters = decision.restaurant.distanceMeters {
                            Label("여기서 약 \(meters)m", systemImage: "location.fill")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(Color.accentRed)
                        }
                    }
                    .padding(.horizontal, 14)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
                    .background(
                        LinearGradient(colors: [Color.selectionMint.opacity(0.72), Color.ivory],
                                       startPoint: .leading, endPoint: .trailing)
                    )
                }
                .frame(height: 136)
                .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 20).stroke(Color.caramel.opacity(0.48), lineWidth: 1))
                .shadow(color: Color.caramelDeep.opacity(0.12), radius: 5, y: 2)

                if let phone = decision.restaurant.displayPhone,
                   let url = decision.restaurant.telURL {
                    Button { openURL(url) } label: {
                        HStack(spacing: 10) {
                            ReferenceIconWell(systemName: "phone.fill", color: .accentRed, diameter: 34)
                            Text(phone)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(Color.charcoalText)
                                .lineLimit(1)
                            Spacer(minLength: 8)
                            Text("전화걸기")
                                .font(.caption.bold())
                                .foregroundStyle(Color.mintInk)
                                .lineLimit(1)
                            Image(systemName: "chevron.right")
                                .font(.caption.bold())
                                .foregroundStyle(Color.charcoalSoft)
                        }
                        .padding(.horizontal, 14)
                        .frame(height: 48)
                        .background(Color.ivory)
                        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.canvasLine, lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("\(decision.restaurant.name) \(phone)에 전화걸기")
                    .accessibilityHint("영업 여부를 확인하기 위해 전화 앱을 열어요")
                }

                Button(action: openMap) {
                    ZStack {
                        Map(initialPosition: .region(MKCoordinateRegion(
                            center: CLLocationCoordinate2D(latitude: decision.restaurant.latitude,
                                                           longitude: decision.restaurant.longitude),
                            span: MKCoordinateSpan(latitudeDelta: 0.008, longitudeDelta: 0.008)
                        ))) {
                            Marker(decision.restaurant.name,
                                   coordinate: CLLocationCoordinate2D(latitude: decision.restaurant.latitude,
                                                                      longitude: decision.restaurant.longitude))
                                .tint(Color.accentRed)
                        }
                        .mapStyle(.standard(pointsOfInterest: .excludingAll))
                        .allowsHitTesting(false)

                        Color.clear.contentShape(Rectangle())
                    }
                }
                .buttonStyle(.plain)
                .frame(height: 244)
                .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
                .overlay(alignment: .topLeading) {
                    Label("\(mapProvider.shortName)로 길 찾기", systemImage: "map.fill")
                        .font(.caption.bold())
                        .foregroundStyle(Color.charcoalText)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(Capsule().fill(Color.ivory.opacity(0.95)))
                        .padding(8)
                }
                .overlay(alignment: .bottomLeading) {
                    if let address = decision.restaurant.roadAddress ?? decision.restaurant.address {
                        Label(address, systemImage: "mappin.and.ellipse")
                            .font(.caption)
                            .foregroundStyle(Color.charcoalText)
                            .lineLimit(1)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(Capsule().fill(Color.ivory.opacity(0.95)))
                            .padding(8)
                    }
                }
                .overlay(RoundedRectangle(cornerRadius: 20).stroke(Color.canvasLine, lineWidth: 1))

                if recorded {
                    HStack(spacing: 10) {
                        ReferenceIconWell(systemName: "checkmark.seal.fill", color: .accentRed, diameter: 34)
                        Text("최근 한 끼에 담았어요")
                            .font(.subheadline.bold())
                            .foregroundStyle(Color.mintInk)
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, 14)
                    .frame(height: 56)
                    .background(Color.selectionMint)
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                } else {
                    Button {
                        store.record(menu: decision.menu,
                                     restaurantName: decision.restaurant.name,
                                     regionName: regionLabel,
                                     imageName: "RestaurantCategory",
                                     imageURL: decision.restaurant.photoURL,
                                     category: decision.restaurant.category,
                                     photoKind: decision.restaurant.photoKind,
                                     photoProvider: decision.restaurant.photoProvider,
                                     photoSourceURL: decision.restaurant.photoSourceURL,
                                     photoAttribution: decision.restaurant.photoAttribution,
                                     photoCreator: decision.restaurant.photoCreator,
                                     photoCreatorURL: decision.restaurant.photoCreatorURL,
                                     photoLicense: decision.restaurant.photoLicense,
                                     photoLicenseURL: decision.restaurant.photoLicenseURL,
                                     photoTitle: decision.restaurant.photoTitle)
                        recorded = true
                        openMap()
                    } label: {
                        HStack(spacing: 10) {
                            ReferenceIconWell(systemName: "checkmark.seal.fill", color: .accentRed, diameter: 34)
                            Text("오늘은 여기로")
                                .font(.headline)
                                .foregroundStyle(Color.charcoalText)
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.caption.bold())
                                .foregroundStyle(Color.charcoalSoft)
                        }
                        .padding(.horizontal, 14)
                        .frame(height: 56)
                        .background(
                            LinearGradient(colors: [.white, Color.ivory], startPoint: .top, endPoint: .bottom)
                        )
                        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.caramel.opacity(0.55), lineWidth: 1))
                        .shadow(color: Color.caramelDeep.opacity(0.12), radius: 4, y: 2)
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("선택을 이 기기의 최근 한 끼에 담습니다")
                }

                Button { showInformation = true } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "clock.badge.questionmark")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(Color.accentRed)
                        Text("영업 정보는 지도에서 확인")
                            .font(AppTypography.supporting)
                            .foregroundStyle(Color.charcoalSoft)
                        Spacer(minLength: 0)
                        Image(systemName: "info.circle")
                            .font(.caption)
                            .foregroundStyle(Color.charcoalSoft)
                    }
                    .frame(height: 40)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 28)
            .padding(.vertical, 6)
            .frame(maxWidth: 600, maxHeight: .infinity, alignment: .top)
            .frame(maxWidth: .infinity)
        }
        .background(Color.mintBase.ignoresSafeArea())
        .preferredColorScheme(.light)
        .alert("영업 정보", isPresented: $showInformation) {
            Button("확인", role: .cancel) {}
        } message: {
            Text(AppText.dataDisclaimer)
        }
        .confirmationDialog(
            "\(missingMapProvider?.displayName ?? "지도 앱")가 필요해요",
            isPresented: $showMissingMapActions,
            titleVisibility: .visible
        ) {
            if let provider = missingMapProvider {
                Button("설치하기") { openMapAppStore(provider) }
            }
            Button("다른 지도 선택") {
                DispatchQueue.main.async { showMapProviderPicker = true }
            }
            Button("취소", role: .cancel) {}
        } message: {
            Text("설치하거나, 이미 있는 다른 지도로 이 식당을 열 수 있어요.")
        }
        .confirmationDialog("다른 지도 선택", isPresented: $showMapProviderPicker, titleVisibility: .visible) {
            ForEach(installedMapProviders) { provider in
                Button(provider.displayName) { selectAndOpenMap(provider) }
            }
            Button("취소", role: .cancel) {}
        }
        .alert("Apple 지도를 열 수 없어요", isPresented: $showAppleMapFailure) {
            Button("확인", role: .cancel) {}
        }
    }

    /// 지도 카드와 결정 버튼이 함께 재사용하는 단일 네이티브 지도 실행 경로다.
    private func openMap() {
        openMap(using: mapProvider)
    }

    private func openMap(using provider: MapProvider) {
        let restaurant = decision.restaurant
        if provider == .apple {
            let coordinate = CLLocationCoordinate2D(latitude: restaurant.latitude,
                                                     longitude: restaurant.longitude)
            let item = MKMapItem(placemark: MKPlacemark(coordinate: coordinate))
            item.name = restaurant.name
            if !item.openInMaps() { showAppleMapFailure = true }
            return
        }
        guard let url = nativeMapURL(for: provider),
              UIApplication.shared.canOpenURL(url) else {
            presentMissingMapActions(for: provider)
            return
        }
        UIApplication.shared.open(url, options: [:]) { opened in
            guard !opened else { return }
            Task { @MainActor in presentMissingMapActions(for: provider) }
        }
    }

    private var installedMapProviders: [MapProvider] {
#if targetEnvironment(macCatalyst)
        return [.apple]
#else
        MapProvider.allCases.filter { provider in
            guard provider != missingMapProvider else { return false }
            if provider == .apple { return true }
            guard let url = nativeMapURL(for: provider) else { return false }
            return UIApplication.shared.canOpenURL(url)
        }
#endif
    }

    private func presentMissingMapActions(for provider: MapProvider) {
        missingMapProvider = provider
        showMissingMapActions = true
    }

    private func selectAndOpenMap(_ provider: MapProvider) {
        mapProviderRaw = provider.rawValue
        openMap(using: provider)
    }

    private func nativeMapURL(for provider: MapProvider) -> URL? {
        let restaurant = decision.restaurant
        var components = URLComponents()
        switch provider {
        case .naver:
            components.scheme = "nmap"
            components.host = "place"
            components.queryItems = [
                URLQueryItem(name: "lat", value: String(restaurant.latitude)),
                URLQueryItem(name: "lng", value: String(restaurant.longitude)),
                URLQueryItem(name: "name", value: restaurant.name),
                URLQueryItem(name: "appname", value: Bundle.main.bundleIdentifier ?? "com.nasfinder.WhattoEat")
            ]
        case .kakao:
            components.scheme = "kakaomap"
            if !restaurant.id.isEmpty {
                components.host = "place"
                components.queryItems = [URLQueryItem(name: "id", value: restaurant.id)]
            } else {
                components.host = "look"
                components.queryItems = [
                    URLQueryItem(name: "p", value: "\(restaurant.latitude),\(restaurant.longitude)")
                ]
            }
        case .google:
            components.scheme = "comgooglemaps"
            components.host = ""
            components.queryItems = [
                URLQueryItem(name: "q", value: "\(restaurant.latitude),\(restaurant.longitude)"),
                URLQueryItem(name: "center", value: "\(restaurant.latitude),\(restaurant.longitude)")
            ]
        case .apple:
            return nil
        }
        return components.url
    }

    private func openMapAppStore(_ provider: MapProvider) {
        let appID: String
        switch provider {
        case .naver: appID = "311867728"
        case .kakao: appID = "304608425"
        case .google: appID = "585027354"
        case .apple: return
        }
        guard let url = URL(string: "itms-apps://itunes.apple.com/app/id\(appID)") else { return }
        UIApplication.shared.open(url)
    }
}

// MARK: - 설정 화면

struct SettingsView: View {
    let regionLabel: String
    /// 알림 관련 값이 바뀔 때마다 호출되어 예약을 갱신한다.
    let onReminderSettingsChanged: () -> Void
    let onClose: () -> Void

    @AppStorage("mapProvider") private var mapProviderRaw = MapProvider.naver.rawValue
    @AppStorage("lunchNotifyEnabled") private var lunchNotifyEnabled = false
    @AppStorage("lunchHour") private var lunchHour = 12
    @AppStorage("lunchMinute") private var lunchMinute = 0
    @AppStorage("lunchLeadMinutes") private var lunchLeadMinutes = 5
    @Environment(\.openURL) private var openURL
    @StateObject private var permissionLocationManager = LocationManager()
    @State private var showPermissionDeniedAlert = false
    @State private var photoCopyrightExpanded = false

    private static let leadOptions = [0, 5, 10, 15, 30]

    private var availableMapProviders: [MapProvider] {
#if targetEnvironment(macCatalyst)
        [.apple]
#else
        MapProvider.allCases
#endif
    }

    init(
        regionLabel: String,
        onReminderSettingsChanged: @escaping () -> Void,
        onClose: @escaping () -> Void
    ) {
        self.regionLabel = regionLabel
        self.onReminderSettingsChanged = onReminderSettingsChanged
        self.onClose = onClose
    }

    private var lunchTimeBinding: Binding<Date> {
        Binding {
            Calendar.current.date(from: DateComponents(hour: lunchHour, minute: lunchMinute)) ?? Date()
        } set: { newValue in
            let components = Calendar.current.dateComponents([.hour, .minute], from: newValue)
            lunchHour = components.hour ?? 12
            lunchMinute = components.minute ?? 0
        }
    }

    private var notifyToggleBinding: Binding<Bool> {
        Binding {
            lunchNotifyEnabled
        } set: { newValue in
            if newValue {
                enableNotifications()
            } else {
                lunchNotifyEnabled = false
            }
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            CompactPageHeader(icon: "gearshape.fill", title: "설정", onClose: onClose)
            ScrollView {
                VStack(spacing: 20) {
                    settingsGroup(
                        icon: "location.fill",
                        title: "위치 권한",
                        footnote: "현재 위치를 다시 잡고 주변 음식점을 찾을 때만 사용해요."
                    ) {
                        HStack(spacing: 12) {
                            Label(locationPermissionLabel, systemImage: locationPermissionIcon)
                                .font(AppTypography.rowTitle)
                                .foregroundStyle(Color.charcoalText)
                            Spacer(minLength: 8)
                            Button(locationPermissionButtonTitle) {
                                openLocationPermission()
                            }
                            .buttonStyle(.bordered)
                            .tint(Color.accentRed)
                        }
                    }

                    settingsGroup(
                        icon: "map.fill",
                        title: "길 찾기 지도",
                        footnote: "음식점 지도를 누르면 여기서 고른 지도로 열어요."
                    ) {
                        HStack(spacing: 8) {
                            ForEach(availableMapProviders) { provider in
                                Button {
                                    mapProviderRaw = provider.rawValue
                                } label: {
                                    VStack(spacing: 6) {
                                        Image(provider.iconAsset)
                                            .resizable()
                                            .interpolation(.high)
                                            .scaledToFit()
                                            .frame(width: 32, height: 32)
                                            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                                        Text(provider.shortName)
                                            .font(.system(size: 11, weight: .medium))
                                            .lineLimit(1)
                                    }
                                    .foregroundStyle(mapProviderRaw == provider.rawValue ? Color.accentRed : Color.charcoalText)
                                    .frame(maxWidth: .infinity, minHeight: 64)
                                    .background(mapProviderRaw == provider.rawValue ? Color.selectionMint : Color.clear)
                                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                                    .overlay(
                                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                                            .stroke(mapProviderRaw == provider.rawValue ? Color.accentRed.opacity(0.55) : Color.canvasLine.opacity(0.55), lineWidth: 1)
                                    )
                                }
                                .buttonStyle(.plain)
                                .accessibilityLabel(provider.displayName)
                                .accessibilityAddTraits(mapProviderRaw == provider.rawValue ? .isSelected : [])
                            }
                        }
                    }

                    settingsGroup(
                        icon: "bell.fill",
                        title: "점심 알림",
                        footnote: "점심시간 전에 오늘의 추천을 알려드려요.\n알림은 이 기기에서만 울려요."
                    ) {
                        Toggle("알림 받기", isOn: notifyToggleBinding)
                            .font(AppTypography.rowTitle)
                            .foregroundStyle(Color.charcoalText)
                            .accessibilityHint("켜면 알림 권한을 요청하고, 매일 점심시간 전에 이 기기에서 알림을 보냅니다")
                        if lunchNotifyEnabled {
                            Divider()
                            HStack(spacing: 8) {
                                Text("점심시간")
                                    .font(AppTypography.rowTitle)
                                    .foregroundStyle(Color.charcoalText)
                                    .lineLimit(1)
                                    .layoutPriority(1)
                                Spacer(minLength: 4)
                                DatePicker("점심시간", selection: lunchTimeBinding,
                                           displayedComponents: .hourAndMinute)
                                    .labelsHidden()
                                    .font(AppTypography.rowTitle)
                                    .foregroundStyle(Color.charcoalText)
                                    .fixedSize()
                                Picker("몇 분 전에", selection: $lunchLeadMinutes) {
                                    ForEach(Self.leadOptions, id: \.self) { minutes in
                                        Text(minutes == 0 ? "정각" : "\(minutes)분 전").tag(minutes)
                                    }
                                }
                                .labelsHidden()
                                .pickerStyle(.menu)
                                .font(AppTypography.rowTitle)
                                .foregroundStyle(Color.charcoalText)
                                .fixedSize()
                            }
                        }
                    }

                    settingsGroup(
                        icon: "photo.on.rectangle.angled",
                        title: "사진 출처와 이용 조건",
                        footnote: "사진의 출처와 사용 기준을 한곳에서 확인해요."
                    ) {
                        Button {
                            withAnimation(.easeInOut(duration: 0.22)) {
                                photoCopyrightExpanded.toggle()
                            }
                        } label: {
                            HStack {
                                Text("카피라이트 안내")
                                    .font(AppTypography.rowTitle)
                                    .foregroundStyle(Color.charcoalText)
                                Spacer()
                                Image(systemName: "chevron.down")
                                    .font(.caption.bold())
                                    .foregroundStyle(Color.charcoalSoft)
                                    .rotationEffect(.degrees(photoCopyrightExpanded ? 180 : 0))
                            }
                            .frame(minHeight: 44)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityValue(photoCopyrightExpanded ? "펼쳐짐" : "접힘")

                        if photoCopyrightExpanded {
                            Divider()
                            VStack(alignment: .leading, spacing: 14) {
                                copyrightRow(
                                    icon: "fork.knife",
                                    title: "메뉴 예시 사진",
                                    text: "Openverse에서 상업적으로 사용할 수 있는 CC0, PDM, CC BY 사진만 사용해요."
                                )
                                copyrightRow(
                                    icon: "storefront.fill",
                                    title: "실제 식당 사진",
                                    text: "한국관광공사 사진은 식당명과 위치가 정확히 맞을 때만 해당 식당 사진으로 사용해요."
                                )
                                copyrightRow(
                                    icon: "info.circle.fill",
                                    title: "사진별 상세 정보",
                                    text: "각 사진의 ‘사진 정보’에서 저작자, 원문과 이용 조건을 확인할 수 있어요."
                                )
                            }
                            .transition(.opacity.combined(with: .move(edge: .top)))
                        }
                    }
                }
                .padding(.horizontal, 28)
                .padding(.vertical, 12)
            }
        }
        .background(Color.mintBase.ignoresSafeArea())
        .onAppear {
#if targetEnvironment(macCatalyst)
            mapProviderRaw = MapProvider.apple.rawValue
#endif
        }
        .onChange(of: lunchNotifyEnabled) { _, _ in onReminderSettingsChanged() }
        .onChange(of: lunchHour) { _, _ in onReminderSettingsChanged() }
        .onChange(of: lunchMinute) { _, _ in onReminderSettingsChanged() }
        .onChange(of: lunchLeadMinutes) { _, _ in onReminderSettingsChanged() }
        .alert("알림이 꺼져 있어요", isPresented: $showPermissionDeniedAlert) {
            Button("확인", role: .cancel) {}
        } message: {
            Text("iPhone 설정의 알림에서 ‘오늘 뭐 먹지’를 허용해 주세요.")
        }
        .preferredColorScheme(.light)
    }

    private func copyrightRow(icon: String, title: String, text: String) -> some View {
        HStack(alignment: .top, spacing: 11) {
            Image(systemName: icon)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Color.accentRed)
                .frame(width: 22, height: 22)
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Color.charcoalText)
                Text(text)
                    .font(.system(size: 13, weight: .regular))
                    .foregroundStyle(Color.charcoalSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var locationPermissionLabel: String {
        switch permissionLocationManager.authorization {
        case .authorizedAlways, .authorizedWhenInUse: return "사용 중"
        case .denied, .restricted: return "꺼짐"
        case .notDetermined: return "아직 선택하지 않음"
        @unknown default: return "확인 필요"
        }
    }

    private var locationPermissionIcon: String {
        switch permissionLocationManager.authorization {
        case .authorizedAlways, .authorizedWhenInUse: return "checkmark.circle.fill"
        case .denied, .restricted: return "xmark.circle.fill"
        default: return "questionmark.circle.fill"
        }
    }

    private var locationPermissionButtonTitle: String {
        permissionLocationManager.authorization == .notDetermined ? "허용하기" : "설정 열기"
    }

    private func openLocationPermission() {
        if permissionLocationManager.authorization == .notDetermined {
            permissionLocationManager.requestPermission()
        } else if let url = URL(string: UIApplication.openSettingsURLString) {
            openURL(url)
        }
    }

    private func settingsGroup<Content: View>(
        icon: String,
        title: String,
        footnote: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 9) {
                ReferenceIconWell(systemName: icon, color: .accentRed, diameter: 30)
                Text(title)
                    .font(AppTypography.sectionTitle)
                    .foregroundStyle(Color.charcoalText)
            }
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isHeader)
            content()
            Text(footnote)
                .font(AppTypography.supporting)
                .foregroundStyle(Color.charcoalSoft)
                .lineSpacing(2)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.ivory)
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(Color.canvasLine.opacity(0.8), lineWidth: 1))
        .shadow(color: Color.caramelDeep.opacity(0.07), radius: 4, y: 2)
    }

    /// 알림 권한은 사용자가 스위치를 켤 때만 요청한다.
    private func enableNotifications() {
        Task { @MainActor in
            let center = UNUserNotificationCenter.current()
            let settings = await center.notificationSettings()
            switch settings.authorizationStatus {
            case .notDetermined:
                let granted = (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
                lunchNotifyEnabled = granted
                if !granted { showPermissionDeniedAlert = true }
            case .denied:
                lunchNotifyEnabled = false
                showPermissionDeniedAlert = true
            default:
                lunchNotifyEnabled = true
            }
        }
    }
}

// MARK: - 최근 한 끼

struct RecentMealsView: View {
    @ObservedObject var store: ChoiceStore
    let onSettings: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            CompactPageHeader(icon: "clock.arrow.circlepath", title: "최근 한 끼", onClose: nil, onSettings: onSettings)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    if store.records.isEmpty {
                        WittyEmptyState(imageName: "EmptyRecent",
                                        icon: nil,
                                        title: "첫 한 끼를 기다리고 있어요",
                                        message: "오늘 메뉴를 고르면 맛있는 기억이\n여기에 차곡차곡 쌓여요.")
                    } else {
                        ForEach(groupedRecords, id: \.region) { group in
                            VStack(alignment: .leading, spacing: 10) {
                                Label(group.region, systemImage: "mappin.and.ellipse")
                                    .font(.headline)
                                    .foregroundStyle(Color.charcoalText)
                                ForEach(Array(group.records.enumerated()), id: \.offset) { _, record in
                                    SwipeToDeleteRow(onDelete: { store.delete(record) }) {
                                        mealCard(record)
                                    }
                                }
                            }
                        }
                        Text("최근에 결정한 메뉴와 음식점은 이 기기에만 보관돼요.")
                            .font(.caption)
                            .foregroundStyle(Color.charcoalSoft)
                    }
                }
                .padding(.horizontal, 28)
                .padding(.vertical, 6)
            }
        }
        .background(Color.mintBase.ignoresSafeArea())
        .preferredColorScheme(.light)
    }

    private var groupedRecords: [(region: String, records: [ChoiceRecord])] {
        Dictionary(grouping: store.records.reversed()) { $0.regionName ?? "이전 기록" }
            .map { (region: $0.key, records: Array($0.value)) }
            .sorted { ($0.records.first?.date ?? .distantPast) > ($1.records.first?.date ?? .distantPast) }
    }

    private func mealCard(_ record: ChoiceRecord) -> some View {
        HStack(spacing: 12) {
            RestaurantImageView(photoURL: record.imageURL,
                                photoKind: record.photoKind,
                                photoProvider: record.photoProvider,
                                photoSourceURL: record.photoSourceURL,
                                photoAttribution: record.photoAttribution,
                                photoCreator: record.photoCreator,
                                photoCreatorURL: record.photoCreatorURL,
                                photoLicense: record.photoLicense,
                                photoLicenseURL: record.photoLicenseURL,
                                photoTitle: record.photoTitle,
                                category: record.category ?? record.menu,
                                identity: record.restaurantName,
                                menu: record.menu,
                                fallbackAsset: nil)
                .frame(width: 82, height: 76)
                .clipped()
            VStack(alignment: .leading, spacing: 4) {
                Text(record.menu).font(.headline).foregroundStyle(Color.charcoalText)
                Text(record.restaurantName).font(.subheadline).foregroundStyle(Color.charcoalSoft).lineLimit(1)
                Text(record.date.formatted(date: .abbreviated, time: .shortened))
                    .font(.caption).foregroundStyle(Color.charcoalSoft)
            }
            Spacer(minLength: 0)
            Button {
                store.delete(record)
            } label: {
                Image(systemName: "trash")
                    .font(.headline)
                    .foregroundStyle(Color.accentRed)
                    .frame(width: 36, height: 36)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("최근 한 끼 삭제")
        }
        .padding(.trailing, 14)
        .background(Color.ivory)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.canvasLine.opacity(0.75), lineWidth: 1))
        .accessibilityElement(children: .contain)
    }
}

// MARK: - 찜한 맛집

struct FavoritesView: View {
    @ObservedObject var store: ChoiceStore
    let onSettings: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            CompactPageHeader(icon: "heart.fill", title: "찜한 맛집", onClose: nil, onSettings: onSettings)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    if store.favorites.isEmpty {
                        WittyEmptyState(imageName: "EmptyFavorites",
                                        icon: nil,
                                        title: "첫 하트를 기다리고 있어요",
                                        message: "추천에서 마음에 드는 곳을\n콕 눌러 주세요.")
                    } else {
                        ForEach(groupedFavorites, id: \.region) { group in
                            VStack(alignment: .leading, spacing: 10) {
                                Label(group.region, systemImage: "mappin.and.ellipse")
                                    .font(.headline)
                                    .foregroundStyle(Color.charcoalText)
                                    .lineLimit(1)
                                ForEach(group.favorites) { favorite in
                                    SwipeToDeleteRow(onDelete: { store.delete(favorite) }) {
                                        favoriteCard(favorite)
                                    }
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 28)
                .padding(.vertical, 6)
            }
        }
        .background(Color.mintBase.ignoresSafeArea())
        .preferredColorScheme(.light)
    }

    private var groupedFavorites: [(region: String, favorites: [FavoriteRecord])] {
        Dictionary(grouping: store.favorites.reversed(), by: \.regionName)
            .map { (region: $0.key, favorites: Array($0.value)) }
            .sorted { ($0.favorites.first?.date ?? .distantPast) > ($1.favorites.first?.date ?? .distantPast) }
    }

    private func favoriteCard(_ favorite: FavoriteRecord) -> some View {
        HStack(spacing: 12) {
            RestaurantImageView(photoURL: favorite.imageURL,
                                photoKind: favorite.photoKind,
                                photoProvider: favorite.photoProvider,
                                photoSourceURL: favorite.photoSourceURL,
                                photoAttribution: favorite.photoAttribution,
                                photoCreator: favorite.photoCreator,
                                photoCreatorURL: favorite.photoCreatorURL,
                                photoLicense: favorite.photoLicense,
                                photoLicenseURL: favorite.photoLicenseURL,
                                photoTitle: favorite.photoTitle,
                                category: favorite.category,
                                identity: favorite.id,
                                menu: nil,
                                fallbackAsset: nil)
                .frame(width: 82, height: 76)
                .clipped()
            VStack(alignment: .leading, spacing: 4) {
                Text(favorite.name).font(.headline).foregroundStyle(Color.charcoalText).lineLimit(1)
                Text(favorite.category).font(.subheadline).foregroundStyle(Color.charcoalSoft).lineLimit(1)
                Text(favorite.date.formatted(date: .abbreviated, time: .omitted))
                    .font(.caption).foregroundStyle(Color.charcoalSoft)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Spacer(minLength: 0)
            Button {
                store.toggleFavorite(favorite)
            } label: {
                Image(systemName: "heart.fill")
                    .font(.headline)
                    .foregroundStyle(Color.accentRed)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("찜 해제")
            .accessibilityValue("선택됨")
        }
        .padding(.trailing, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.ivory)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.canvasLine.opacity(0.75), lineWidth: 1))
        .accessibilityElement(children: .contain)
    }
}

#Preview {
    ContentView()
}
