import Foundation
import CoreLocation

/// 실내 등 GPS/Wi-Fi/셀룰러 융합 위치가 지연되거나 실패할 때 쓸 최근 캐시 위치를 얼마나 신뢰할지 결정하는 순수 로직.
enum LocationFreshness {
    static let maxCacheAge: TimeInterval = 5 * 60
    static let requestTimeout: TimeInterval = 12

    static func isUsable(accuracy: CLLocationAccuracy, timestamp: Date, now: Date) -> Bool {
        guard accuracy >= 0 else { return false }
        let age = now.timeIntervalSince(timestamp)
        return age >= 0 && age <= maxCacheAge
    }
}

final class LocationManager: NSObject, ObservableObject, CLLocationManagerDelegate {
    @Published var authorization: CLAuthorizationStatus = .notDetermined
    @Published var lastLocation: CLLocation?
    @Published var errorMessage: String?

    private let manager = CLLocationManager()
    private var timeoutWorkItem: DispatchWorkItem?

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
        authorization = manager.authorizationStatus
    }

    func requestPermission() {
        manager.requestWhenInUseAuthorization()
    }

    func requestLocation() {
        errorMessage = nil
        lastLocation = nil
        timeoutWorkItem?.cancel()
        manager.requestLocation()

        let workItem = DispatchWorkItem { [weak self] in
            self?.resolveWithCacheOrFailure()
        }
        timeoutWorkItem = workItem
        DispatchQueue.main.asyncAfter(deadline: .now() + LocationFreshness.requestTimeout,
                                      execute: workItem)
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        DispatchQueue.main.async {
            self.authorization = manager.authorizationStatus
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        DispatchQueue.main.async {
            self.timeoutWorkItem?.cancel()
            self.lastLocation = locations.last
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        DispatchQueue.main.async {
            self.timeoutWorkItem?.cancel()
            self.resolveWithCacheOrFailure()
        }
    }

    /// 요청 실패나 타임아웃 시 시스템이 보관한 5분 이내 유효 위치를 재사용한다.
    private func resolveWithCacheOrFailure() {
        if let cached = manager.location,
           LocationFreshness.isUsable(accuracy: cached.horizontalAccuracy,
                                      timestamp: cached.timestamp,
                                      now: Date()) {
            lastLocation = cached
        } else {
            errorMessage = "현재 위치를 확인하지 못했습니다. 지역을 직접 선택하거나 다시 시도해 주세요."
        }
    }
}
