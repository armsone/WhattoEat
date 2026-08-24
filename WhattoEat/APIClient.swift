import Foundation

/// 백엔드 호출 전용 클라이언트. 카카오 API 키는 서버에만 있으며 앱에는 존재하지 않는다.
enum APIClient {
    enum APIError: LocalizedError {
        case notConfigured
        case badResponse(Int)
        case decoding
        case transport

        var errorDescription: String? {
            switch self {
            case .notConfigured:
                return "백엔드 주소가 설정되지 않았습니다. Info.plist의 APIBaseURL 값을 배포한 HTTPS 서버 주소로 바꿔 주세요."
            case .badResponse(let code):
                return "서버 응답 오류(\(code))가 발생했습니다. 잠시 후 다시 시도해 주세요."
            case .decoding:
                return "서버 응답을 해석하지 못했습니다. 앱과 서버 버전이 맞는지 확인해 주세요."
            case .transport:
                return "서버에 연결하지 못했습니다. 네트워크 상태를 확인해 주세요."
            }
        }
    }

    static func baseURL() throws -> URL {
        guard let raw = Bundle.main.object(forInfoDictionaryKey: "APIBaseURL") as? String,
              !raw.isEmpty,
              !raw.contains("REPLACE-ME"),
              let url = URL(string: raw),
              url.scheme == "https"
        else { throw APIError.notConfigured }
        return url
    }

    static func fetchRestaurants(latitude: Double, longitude: Double) async throws -> RestaurantsResponse {
        let base = try baseURL()
        var components = URLComponents(url: base.appending(path: "/api/restaurants"),
                                       resolvingAgainstBaseURL: false)
        components?.queryItems = [
            URLQueryItem(name: "latitude", value: String(latitude)),
            URLQueryItem(name: "longitude", value: String(longitude)),
        ]
        guard let url = components?.url else { throw APIError.notConfigured }

        var request = URLRequest(url: url)
        request.timeoutInterval = 15

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw APIError.transport
        }
        guard let http = response as? HTTPURLResponse else { throw APIError.transport }
        guard (200 ..< 300).contains(http.statusCode) else { throw APIError.badResponse(http.statusCode) }

        do {
            return try JSONDecoder().decode(RestaurantsResponse.self, from: data)
        } catch {
            throw APIError.decoding
        }
    }
}
