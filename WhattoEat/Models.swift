import Foundation

/// 서버 GET /api/restaurants 응답 계약.
struct RestaurantsResponse: Codable {
    let restaurants: [Restaurant]
    let source: String?
    let disclaimer: String?
}

struct Restaurant: Codable, Identifiable, Hashable {
    let id: String              // Kakao place id
    let name: String
    let category: String        // 예: "음식점 > 한식 > 국밥"
    let latitude: Double
    let longitude: Double
    let distanceMeters: Int?
    let address: String?
    let roadAddress: String?
    let phone: String?
    let placeURL: String?
    /// 제공자가 명확히 알려 준 현재 영업 상태. 알 수 없으면 nil.
    let isOpenNow: Bool?
    /// 운영자가 확인한 대표 메뉴(서버 curated-menus.json). 없으면 nil.
    let curatedMenus: [String]?
    /// 서버가 검증 가능한 식당 사진의 HTTPS 주소를 제공할 때만 사용한다.
    /// 현재 배포 API 응답에는 이 필드가 없으므로 nil이며 카테고리 플레이스홀더를 표시한다.
    let photoURL: String?
    /// 실제 식당 사진인지, 어느 제공처에서 왔는지를 밝히는 서버 계약.
    let photoKind: String?
    let photoProvider: String?
    let photoSourceURL: String?
    let photoAttribution: String?
    let photoCreator: String?
    let photoCreatorURL: String?
    let photoLicense: String?
    let photoLicenseURL: String?
    let photoTitle: String?
    let photoMatchEvidence: PhotoMatchEvidence?
}

struct PhotoMatchEvidence: Codable, Hashable {
    let exactNormalizedName: Bool?
    let addressMatch: Bool?
    let distanceMeters: Int?
    let phoneMatch: Bool?
    let previouslyVerifiedPlaceId: Bool?
}

/// 화이트리스트/운영자 데이터로 근거가 있는 메뉴 후보와 그 메뉴를 파는 음식점 목록.
struct MenuCandidate: Identifiable, Hashable {
    let menu: String
    let restaurants: [Restaurant]   // 거리순 정렬
    var id: String { menu }
}

/// 사용자가 확정한 선택(기기 내 저장 전용).
struct ChoiceRecord: Codable {
    let menu: String
    let restaurantName: String
    let date: Date
    let regionName: String?
    let imageName: String?
    let imageURL: String?
    let category: String?
    let photoKind: String?
    let photoProvider: String?
    let photoSourceURL: String?
    let photoAttribution: String?
    let photoCreator: String?
    let photoCreatorURL: String?
    let photoLicense: String?
    let photoLicenseURL: String?
    let photoTitle: String?
}

/// 사용자가 찜한 음식점(기기 내 저장 전용).
struct FavoriteRecord: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let category: String
    let regionName: String
    let imageName: String
    let imageURL: String?
    let photoKind: String?
    let photoProvider: String?
    let photoSourceURL: String?
    let photoAttribution: String?
    let photoCreator: String?
    let photoCreatorURL: String?
    let photoLicense: String?
    let photoLicenseURL: String?
    let photoTitle: String?
    let date: Date
}

/// 사용자가 추천에 실제 사용한 지역의 기기 내 집계.
struct RegionUsage: Codable, Identifiable, Hashable {
    var id: String { name }
    let name: String
    var count: Int
    var lastUsedAt: Date
    let latitude: Double
    let longitude: Double
}

enum AppText {
    static let dataDisclaimer = """
    식당 정보는 지도 제공처의 최신 상태와 다를 수 있어요. \
    방문하기 전에 영업 여부와 실제 메뉴를 지도에서 한 번 확인해 주세요.
    """
    static let rankingLabel = "이 기기에서 많이 고른 메뉴"
}
