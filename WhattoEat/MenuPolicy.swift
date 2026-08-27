import Foundation

enum MealSituation: String, CaseIterable, Identifiable {
    case all = "전체"
    case hearty = "든든한 식사"
    case quick = "간단하게"
    case fastFood = "패스트푸드"
    case dessertCafe = "디저트·카페"
    case gathering = "회식·모임"
    case lateNight = "야식"

    var id: String { rawValue }

    fileprivate var keywords: [String] {
        switch self {
        case .all:
            return []
        case .hearty:
            return ["국밥", "설렁탕", "갈비탕", "삼계탕", "샤브샤브", "칼국수", "냉면", "비빔밥", "백반", "찌개", "전골", "덮밥", "쌀국수", "짜장면", "자장면", "마라탕", "파스타"]
        case .quick:
            return ["김밥", "떡볶이", "토스트", "샌드위치", "샐러드", "라면", "우동", "분식"]
        case .fastFood:
            return ["햄버거", "버거", "피자", "치킨", "핫도그"]
        case .dessertCafe:
            return ["커피", "카페", "디저트", "케이크", "아이스크림", "빙수", "빵", "베이커리", "와플", "도넛"]
        case .gathering:
            return ["곱창", "보쌈", "족발", "샤브샤브", "삼겹살", "갈비", "고기", "전골", "회", "치킨", "피자"]
        case .lateNight:
            return ["치킨", "피자", "떡볶이", "곱창", "보쌈", "족발", "닭발", "라면"]
        }
    }

    func matches(menu: String) -> Bool {
        self == .all || keywords.contains(where: menu.contains)
    }
}

/// 데이터 정직성 정책: 메뉴는 절대 추측하지 않는다.
/// 1) 서버의 운영자 확인 데이터(curatedMenus), 또는
/// 2) 가게 이름/최종 카테고리 텍스트에 아래 화이트리스트의 음식명이 그대로 포함된 경우에만 표시.
/// '한식' 같은 넓은 분류를 특정 요리로 바꾸지 않는다.
enum MenuPolicy {
    /// 각 항목의 첫 번째가 대표 표기, 나머지는 동의 표기(예: 돈까스 → 돈가스).
    static let whitelist: [[String]] = [
        ["김밥"], ["냉면"], ["돈가스", "돈까스"], ["초밥"], ["국밥"],
        ["설렁탕"], ["칼국수"], ["햄버거"], ["피자"], ["치킨"],
        ["떡볶이"], ["샤브샤브"], ["갈비탕"], ["짜장면", "자장면"],
        ["쌀국수"], ["마라탕"], ["파스타"], ["곱창"], ["삼계탕"], ["보쌈"],
    ]

    /// 근거가 있는 대표 메뉴 목록. 근거가 없으면 빈 배열(메뉴를 지어내지 않음).
    static func menus(for restaurant: Restaurant) -> [String] {
        var found: [String] = restaurant.curatedMenus ?? []
        let lastCategory = restaurant.category
            .components(separatedBy: ">")
            .last?
            .trimmingCharacters(in: .whitespaces) ?? ""
        let haystacks = [restaurant.name, lastCategory]
        for variants in whitelist {
            let canonical = variants[0]
            guard !found.contains(canonical) else { continue }
            if variants.contains(where: { term in haystacks.contains(where: { $0.contains(term) }) }) {
                found.append(canonical)
            }
        }
        return found
    }

    /// 서버가 확인했거나 화이트리스트에서 찾은 메뉴만 상황별로 분류한다.
    /// 분류되지 않은 메뉴를 임의로 특정 상황에 넣지 않는다.
    static func menu(for restaurant: Restaurant, situation: MealSituation) -> String? {
        let supportedMenus = menus(for: restaurant)
        if situation == .all { return supportedMenus.first }
        return supportedMenus.first(where: situation.matches(menu:))
    }

    static func restaurants(_ restaurants: [Restaurant], matching situation: MealSituation) -> [Restaurant] {
        guard situation != .all else { return restaurants }
        return restaurants.filter { menu(for: $0, situation: situation) != nil }
    }

    /// 메뉴별로 묶은 후보 목록. 각 후보의 음식점은 거리순, 후보는 음식점 수 → 최단 거리순.
    static func candidates(from restaurants: [Restaurant]) -> [MenuCandidate] {
        var byMenu: [String: [Restaurant]] = [:]
        for restaurant in restaurants {
            for menu in menus(for: restaurant) {
                byMenu[menu, default: []].append(restaurant)
            }
        }
        return byMenu.map { menu, list in
            MenuCandidate(menu: menu, restaurants: list.sorted {
                ($0.distanceMeters ?? .max) < ($1.distanceMeters ?? .max)
            })
        }
        .sorted {
            if $0.restaurants.count != $1.restaurants.count {
                return $0.restaurants.count > $1.restaurants.count
            }
            return ($0.restaurants.first?.distanceMeters ?? .max) < ($1.restaurants.first?.distanceMeters ?? .max)
        }
    }
}
