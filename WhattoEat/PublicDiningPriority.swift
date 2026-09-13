import Foundation

// MARK: - 데이터 계약 (PublicDiningCatalog.json, schemaVersion 1)

/// 앱 번들에 포함되는 공공기관 식당 이용 기록 카탈로그. 서버 응답 모델과 독립된 Codable 타입이다.
struct PublicDiningCatalog: Codable {
    let schemaVersion: Int
    let generatedAt: String
    let coverageDescription: String
    let sourceURLs: [String]
    let restaurants: [PublicDiningRecord]
}

/// 공식 원자료에서 집계한 식당 한 곳의 기록.
struct PublicDiningRecord: Codable, Hashable {
    let name: String
    let address: String
    let departmentCount: Int
    /// YYYY-MM-DD
    let lastPaymentDate: String
    /// 공식 원문 HTTPS 주소
    let sourceURL: String
    /// 내부 17개 권역 약칭 (선택, 하위 호환)
    let region: String?
    /// 해당 지역/식당 수집 범위 설명 (선택, 하위 호환)
    let coverageDescription: String?
    /// 해당 지역/식당 공식 출처 URL 목록 (선택, 하위 호환)
    let sourceURLs: [String]?
    let nameVariants: [String]?
}

/// 화면에 보여 줄 확정된 일치 결과. 기록 자체와 카탈로그의 범위 설명을 함께 전달한다.
struct PublicDiningMatch: Hashable {
    let record: PublicDiningRecord
    let coverageDescription: String
    let generatedAt: String
    let sourceURLs: [String]

    var region: String? { record.region }
    var sourceURL: URL? { PublicDiningPriority.httpsURL(record.sourceURL) }
    var officialSourceURLs: [URL] {
        var seen = Set<URL>()
        var result: [URL] = []
        for raw in sourceURLs {
            guard let url = PublicDiningPriority.httpsURL(raw), !seen.contains(url) else { continue }
            seen.insert(url)
            result.append(url)
        }
        return result
    }
    var displayedOfficialSourceURLs: [URL] {
        Array(officialSourceURLs.prefix(PublicDiningPriority.maximumDisplayedOfficialSources))
    }
    var lastPaymentDate: Date? { PublicDiningPriority.date(fromDay: record.lastPaymentDate) }
    var generatedDate: Date? { PublicDiningPriority.date(fromISO8601: generatedAt) }
}

// MARK: - 우선 표시 정책

/// 공공기관 이용 기록이 있는 식당을 추천 목록 앞쪽에 거리순으로 두는 정책.
///
/// - 카탈로그 파일이 없거나, 스키마가 다르거나, 비어 있으면 아무것도 우선하지 않고 기존 추천으로 돌아간다.
/// - 일치는 정규화한 식당명이 정확히 같고, 도로명 또는 지번 주소가 같은 형식끼리 토큰 단위로 일치할 때만 인정한다.
/// - 부서 수는 순위 점수가 아니라 표시용 참고 정보이며 자격 조건으로 사용하지 않는다.
/// - 마지막 결제일이 실행 시점 기준 `maximumRecordAgeMonths`를 넘으면 우선하지 않아 오래된 자료가 영구 추천되지 않게 한다.
enum PublicDiningPriority {
    static let supportedSchemaVersion = 1
    static let resourceName = "PublicDiningCatalog"
    /// 수집 이후에도 오래된 기록이 계속 우선되지 않도록 실행일 기준 최근 18개월만 인정한다.
    static let maximumRecordAgeMonths = 18
    /// 화면에 과도한 출처 링크가 나열되지 않도록 보여 줄 공식 자료 출처 최대 개수.
    static let maximumDisplayedOfficialSources = 3

    // MARK: 로드

    private static let loadedCatalog: PublicDiningCatalog? = loadCatalog(from: Bundle.main)

    /// 정규화한 식당명 → 유효 기록 목록. 실행 중 한 번만 만든다.
    private static let recordsByNormalizedName: [String: [PublicDiningRecord]] = {
        guard let catalog = loadedCatalog else { return [:] }
        var index: [String: [PublicDiningRecord]] = [:]
        for record in Set(catalog.restaurants) {
            for key in Set(([record.name] + (record.nameVariants ?? [])).map(normalizedName)) where !key.isEmpty {
                index[key, default: []].append(record)
            }
        }
        return index
    }()

    static var catalog: PublicDiningCatalog? { loadedCatalog }

    /// 번들 리소스를 읽어 검증한다. 어떤 문제든 nil을 돌려주어 기존 추천으로 복귀한다.
    static func loadCatalog(from bundle: Bundle) -> PublicDiningCatalog? {
        guard let url = bundle.url(forResource: resourceName, withExtension: "json"),
              let data = try? Data(contentsOf: url) else { return nil }
        return catalog(from: data)
    }

    static func catalog(from data: Data) -> PublicDiningCatalog? {
        guard !data.isEmpty,
              let decoded = try? JSONDecoder().decode(PublicDiningCatalog.self, from: data),
              decoded.schemaVersion == supportedSchemaVersion,
              date(fromISO8601: decoded.generatedAt) != nil,
              !decoded.coverageDescription.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !decoded.sourceURLs.isEmpty,
              decoded.sourceURLs.allSatisfy({ httpsURL($0) != nil }) else { return nil }
        let valid = decoded.restaurants.filter(isWellFormed)
        guard !valid.isEmpty else { return nil }
        return PublicDiningCatalog(schemaVersion: decoded.schemaVersion,
                                   generatedAt: decoded.generatedAt,
                                   coverageDescription: decoded.coverageDescription,
                                   sourceURLs: decoded.sourceURLs,
                                   restaurants: valid)
    }

    /// 항목 단위 형식 검증. 잘못된 항목은 조용히 제외한다.
    private static func isWellFormed(_ record: PublicDiningRecord) -> Bool {
        !record.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !record.address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && record.departmentCount >= 0
            && date(fromDay: record.lastPaymentDate) != nil
            && httpsURL(record.sourceURL) != nil
            && (record.region == nil || regionAliases.values.contains(record.region!))
            && (record.sourceURLs == nil || record.sourceURLs!.allSatisfy { httpsURL($0) != nil })
    }

    // MARK: 정렬

    /// 기존 후보 풀을 받아 확정된 일치 식당을 거리순으로 앞에 두고, 나머지는 기존 무작위 순서를 유지한다.
    /// 일치가 하나도 없으면 입력 순서를 그대로 돌려준다.
    static func prioritized(_ pool: [Restaurant], now: Date = Date()) -> [Restaurant] {
        let matched = pool.filter { match(for: $0, now: now) != nil }
        guard !matched.isEmpty else { return pool }
        let matchedIDs = Set(matched.map(\.id))
        let front = matched.sorted { ($0.distanceMeters ?? .max) < ($1.distanceMeters ?? .max) }
        let rest = pool.filter { !matchedIDs.contains($0.id) }
        return front + rest
    }

    // MARK: 일치 판정

    /// 식당 한 곳에 대한 확정 일치. 후보가 둘 이상이면 미확정으로 보고 nil을 돌려준다.
    static func match(for restaurant: Restaurant, now: Date = Date()) -> PublicDiningMatch? {
        guard let catalog = loadedCatalog else { return nil }
        let key = normalizedName(restaurant.name)
        guard !key.isEmpty, let candidates = recordsByNormalizedName[key] else { return nil }

        let addressCandidates = [restaurant.roadAddress, restaurant.address]
            .compactMap { $0 }
            .map(addressTokens)
            .filter { $0.count >= 3 }
        guard !addressCandidates.isEmpty else { return nil }

        let confirmed = candidates.filter { record in
            let recordTokens = addressTokens(record.address)
            return addressCandidates.contains { addressesMatch(catalog: recordTokens, kakao: $0, recordRegion: record.region) }
        }
        guard confirmed.count == 1, let record = confirmed.first,
              isEligible(record, now: now) else { return nil }

        let effectiveCoverage: String
        if let recCov = record.coverageDescription?.trimmingCharacters(in: .whitespacesAndNewlines), !recCov.isEmpty {
            effectiveCoverage = recCov
        } else {
            effectiveCoverage = catalog.coverageDescription
        }

        let effectiveURLs: [String]
        if let recURLs = record.sourceURLs, !recURLs.isEmpty {
            effectiveURLs = recURLs
        } else {
            effectiveURLs = catalog.sourceURLs
        }

        return PublicDiningMatch(record: record,
                                 coverageDescription: effectiveCoverage,
                                 generatedAt: catalog.generatedAt,
                                 sourceURLs: effectiveURLs)
    }

    /// 실행일 기준 최근 18개월의 식사 이용 기록만 우선한다.
    static func isEligible(_ record: PublicDiningRecord, now: Date = Date()) -> Bool {
        guard let paymentDate = date(fromDay: record.lastPaymentDate) else { return false }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Asia/Seoul") ?? .current
        let latestAllowed = calendar.startOfDay(for: now)
        guard let oldestAllowed = calendar.date(byAdding: .month, value: -maximumRecordAgeMonths, to: latestAllowed) else { return false }
        return paymentDate >= oldestAllowed && paymentDate <= latestAllowed
    }

    // MARK: 정규화

    /// 식당명 정규화: 유니코드 정규화, 소문자, 공백 제거, 법인 표기(주식회사·(주)·㈜ 등)는 앞뒤에 붙은 경우에만 제거.
    /// 괄호·지점명·짧은 상호는 그대로 두어 다른 지점이나 다른 가게를 같은 곳으로 보지 않는다.
    static func normalizedName(_ raw: String) -> String {
        var text = raw.precomposedStringWithCompatibilityMapping
            .lowercased()
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let corporateMarkers = ["주식회사", "(주)", "㈜", "유한회사", "(유)", "합자회사", "(합)", "농업회사법인", "영농조합법인"]
        var stripped = true
        while stripped {
            stripped = false
            for marker in corporateMarkers {
                if text.hasPrefix(marker) {
                    text = String(text.dropFirst(marker.count)).trimmingCharacters(in: .whitespaces)
                    stripped = true
                } else if text.hasSuffix(marker) {
                    text = String(text.dropLast(marker.count)).trimmingCharacters(in: .whitespaces)
                    stripped = true
                }
            }
        }
        return text.filter { !$0.isWhitespace }
    }

    private static let gwangjuDistricts: Set<String> = [
        "동구", "서구", "남구", "북구", "광산구"
    ]
    private static let integratedSpecialCityPrefixes: Set<String> = [
        "전남광주통합특별시", "전남광주시", "전남광주"
    ]

    private static let regionAliases: [String: String] = [
        "서울특별시": "서울", "서울시": "서울", "서울": "서울",
        "부산광역시": "부산", "부산시": "부산", "부산": "부산",
        "대구광역시": "대구", "대구시": "대구", "대구": "대구",
        "인천광역시": "인천", "인천시": "인천", "인천": "인천",
        "광주광역시": "광주", "광주시": "광주", "광주": "광주",
        "대전광역시": "대전", "대전시": "대전", "대전": "대전",
        "울산광역시": "울산", "울산시": "울산", "울산": "울산",
        "세종특별자치시": "세종", "세종시": "세종", "세종": "세종",
        "경기도": "경기", "경기": "경기",
        "강원특별자치도": "강원", "강원도": "강원", "강원": "강원",
        "충청북도": "충북", "충북": "충북",
        "충청남도": "충남", "충남": "충남",
        "전북특별자치도": "전북", "전라북도": "전북", "전북": "전북",
        "전라남도": "전남", "전남": "전남",
        "경상북도": "경북", "경북": "경북",
        "경상남도": "경남", "경남": "경남",
        "제주특별자치도": "제주", "제주도": "제주", "제주": "제주",
    ]

    /// 주소를 토큰으로 나눈다. 광역 지명은 내부 약칭(서울, 경기…)으로 맞추고, 괄호 안 참고 표기와
    /// 번지 접미사만 정리한다. 숫자 토큰은 그대로 두어 부분 일치를 허용하지 않는다.
    ///
    /// - 전남광주통합특별시 표기는 바로 뒤 시군구가 광주 자치구(동구·서구·남구·북구·광산구)이면 "광주",
    ///   그 외는 "전남"으로 분류하여 정규화한다.
    /// - 공식 세종 주소의 연속 중복 세종("세종특별자치시 세종특별자치시 ...") 토큰을 단일화하여 카카오 주소와의 동일성을 보장한다.
    static func addressTokens(_ raw: String) -> [String] {
        let cleaned = raw.precomposedStringWithCompatibilityMapping
            .replacingOccurrences(of: "\\([^)]*\\)", with: " ", options: .regularExpression)
            .replacingOccurrences(of: ",", with: " ")
        let pieces = cleaned.split(whereSeparator: \.isWhitespace).map(String.init)
        guard !pieces.isEmpty else { return [] }

        var tokens: [String] = []
        for (index, piece) in pieces.enumerated() {
            var token = piece
            if index == 0, integratedSpecialCityPrefixes.contains(token) {
                // 첫 광역 토큰이 전남광주통합특별시인 경우 바로 뒤 시군구로만 분류하여 광주/전남으로 alias 정규화
                if pieces.count > 1 {
                    let nextPiece = pieces[1]
                    if gwangjuDistricts.contains(nextPiece) {
                        token = "광주"
                    } else {
                        token = "전남"
                    }
                }
            } else if let alias = regionAliases[token] {
                token = alias
            }

            if token.hasSuffix("번지"), token.dropLast(2).allSatisfy({ $0.isNumber || $0 == "-" }) {
                token = String(token.dropLast(2))
            }
            guard !token.isEmpty else { continue }

            // 공식 세종 주소의 연속 중복 세종만 제거해 카카오 주소 동일성 보장
            if token == "세종", tokens.last == "세종" {
                continue
            }
            tokens.append(token)
        }
        return tokens
    }

    /// 카카오 주소 토큰 전체(또는 광역 지명만 뺀 나머지)가 카탈로그 주소 토큰 안에 연속으로 있어야 한다.
    /// 도로명과 지번을 섞어 비교하지 않으며, 건물 번호는 토큰 단위로 정확히 같아야 한다.
    /// 광역 지명이 확인된 경우 서로 다른 지역이면 절대 일치하지 않아 다른 지역의 동명·하이픈 번호 충돌을 방지한다.
    static func addressesMatch(catalog: [String], kakao: [String], recordRegion: String? = nil) -> Bool {
        guard kakao.count >= 3, catalog.count >= 2 else { return false }
        let kakaoRegion = regionAliases[kakao[0]]
        let catalogRegion = catalog.first.flatMap { regionAliases[$0] }
        let effectiveRecordRegion = recordRegion.flatMap { regionAliases[$0] ?? $0 } ?? catalogRegion

        // 광역 지명이 확인된 경우, 서로 다른 지역이면 절대 일치하지 않는다.
        if let kr = kakaoRegion, let rr = effectiveRecordRegion, kr != rr {
            return false
        }

        if containsContiguous(catalog, kakao) { return true }
        // 카탈로그 주소가 광역 지명 없이 시군구로 시작하는 경우만 허용하되,
        // 카카오 광역과 카탈로그의 지역이 명시적으로 다른 경우는 위에서 이미 차단됨.
        if kakao.count >= 4, kakaoRegion != nil,
           catalogRegion == nil {
            return containsContiguous(catalog, Array(kakao.dropFirst()))
        }
        return false
    }

    private static func containsContiguous(_ haystack: [String], _ needle: [String]) -> Bool {
        guard !needle.isEmpty, haystack.count >= needle.count else { return false }
        for start in 0...(haystack.count - needle.count) {
            if Array(haystack[start..<(start + needle.count)]) == needle { return true }
        }
        return false
    }

    // MARK: 형식 도우미

    static func httpsURL(_ raw: String) -> URL? {
        guard let url = URL(string: raw.trimmingCharacters(in: .whitespacesAndNewlines)),
              url.scheme?.lowercased() == "https", url.host != nil else { return nil }
        return url
    }

    private static let dayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "Asia/Seoul")
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.isLenient = false
        return formatter
    }()

    static func date(fromDay raw: String) -> Date? {
        guard raw.count == 10 else { return nil }
        return dayFormatter.date(from: raw)
    }

    static func date(fromISO8601 raw: String) -> Date? {
        let withFraction = ISO8601DateFormatter()
        withFraction.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = withFraction.date(from: raw) { return date }
        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        if let date = plain.date(from: raw) { return date }
        return date(fromDay: raw)
    }

    private static let displayDayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "ko_KR")
        formatter.timeZone = TimeZone(identifier: "Asia/Seoul")
        formatter.dateFormat = "yyyy년 M월 d일"
        return formatter
    }()

    static func displayDay(_ date: Date?) -> String? {
        date.map(displayDayFormatter.string(from:))
    }
}
