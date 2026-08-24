import Foundation
import CoreLocation

final class LocationManager: NSObject, ObservableObject, CLLocationManagerDelegate {
    @Published var authorization: CLAuthorizationStatus = .notDetermined
    @Published var lastLocation: CLLocation?
    @Published var errorMessage: String?

    private let manager = CLLocationManager()

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
        manager.requestLocation()
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        DispatchQueue.main.async {
            self.authorization = manager.authorizationStatus
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        DispatchQueue.main.async {
            self.lastLocation = locations.last
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        DispatchQueue.main.async {
            self.errorMessage = "현재 위치를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요."
        }
    }
}
